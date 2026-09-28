# -*- coding: utf-8 -*-
"""运营后台 API：管理员登录、邀请码审批、指标看板。

登录态与前台物理隔离 —— 见 utils/admin_guard.py 顶部说明。
"""
from datetime import datetime

from flask import request, jsonify
from sqlalchemy import or_
from flask_jwt_extended import (
    create_access_token, get_jwt_identity, unset_jwt_cookies,
)

from config import settings
from utils.db import get_db, transactional_db
from utils.admin_guard import admin_required
from factory import app, rate_limit_auth, logger


def _issue_admin_token(admin_id: int):
    """签发后台 token。

    刻意**不写 cookie**（不像前台那样 set_access_cookies）：后台与前台共用同一个
    cookie 名会互相覆盖登录态，所以后台 token 由前端自己带在 Authorization 头上。
    """
    return create_access_token(
        identity=str(admin_id),
        expires_delta=settings.ADMIN_TOKEN_EXPIRES,
        additional_claims={'type': 'admin'},
    )


# ==================== 登录 ====================

@app.route('/api/admin/login', methods=['POST'])
@rate_limit_auth
def admin_login():
    from models import AdminUser
    from utils.security import verify_password

    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    with transactional_db() as db:
        admin = db.query(AdminUser).filter(AdminUser.username == username).first()
        if not admin or not verify_password(password, admin.password_hash):
            return jsonify({'error': '用户名或密码错误'}), 401
        admin.last_login_at = datetime.utcnow()

    token = _issue_admin_token(admin.id)
    logger.info(f"管理员登录：{username}")
    return jsonify({
        'message': '登录成功',
        'token': token,
        'admin': {'id': admin.id, 'username': admin.username},
        'expires_in_hours': settings.ADMIN_TOKEN_EXPIRES_HOURS,
    }), 200


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    resp = jsonify({'message': '已登出'})
    unset_jwt_cookies(resp)
    return resp, 200


@app.route('/api/admin/me', methods=['GET'])
@admin_required
def admin_me():
    from models import AdminUser

    admin_id = int(get_jwt_identity())
    with get_db() as db:
        admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
        if not admin:
            return jsonify({'error': '管理员不存在'}), 404
        return jsonify({
            'admin': {'id': admin.id, 'username': admin.username, 'role': admin.role}
        }), 200


# ==================== 邀请码审批 ====================

def _serialize_invite(row) -> dict:
    return {
        'id': row.id,
        'code': row.code,
        'applicant_email': row.applicant_email,
        'applicant_phone': row.applicant_phone,
        'applicant_note': row.applicant_note,
        'status': row.status,
        'delivery_status': row.delivery_status,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'decided_at': row.decided_at.isoformat() if row.decided_at else None,
        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
        'used_at': row.used_at.isoformat() if row.used_at else None,
        'reject_reason': row.reject_reason,
    }


@app.route('/api/admin/invites', methods=['GET'])
@admin_required
def list_invites():
    from models import InviteCode

    status = (request.args.get('status') or '').strip()
    # 搜索：邮箱或邀请码模糊匹配。放在 DB 侧而不是前端过滤 —— 前端只持有当前页
    # 50 条，在那一页里搜「找不到」会让人误以为数据不存在。
    keyword = (request.args.get('keyword') or '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    page_size = min(50, max(1, int(request.args.get('page_size', 20) or 20)))

    with get_db() as db:
        q = db.query(InviteCode)
        if status:
            q = q.filter(InviteCode.status == status)
        if keyword:
            like = f'%{keyword}%'
            q = q.filter(
                or_(
                    InviteCode.applicant_email.ilike(like),
                    InviteCode.code.ilike(like),
                )
            )
        total = q.count()
        rows = (
            q.order_by(InviteCode.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return jsonify({
            'items': [_serialize_invite(r) for r in rows],
            'total': total,
            'page': page,
            'page_size': page_size,
            'pending': (
                db.query(InviteCode).filter(InviteCode.status == 'pending').count()
            ),
        }), 200


@app.route('/api/admin/invites/<int:invite_id>/approve', methods=['POST'])
@admin_required
def approve_invite(invite_id: int):
    """审批通过：生成码 → 尝试发信。

    **发信失败不影响审批结果** —— 发信是外部依赖，失败只记 delivery_status，
    管理员可在后台重发。若发信失败就回滚审批，会出现「点了通过但没生效」的黑洞。
    """
    from models import InviteCode
    from services.invite import issue_code
    from services.notify import email as notify

    admin_id = int(get_jwt_identity())
    try:
        with transactional_db() as db:
            invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
            if not invite:
                return jsonify({'error': '申请不存在'}), 404
            if invite.status not in ('pending', 'rejected'):
                return jsonify({'error': f'当前状态（{invite.status}）不可审批'}), 409

            code = issue_code(db, invite)
            invite.decided_by = admin_id
            invite.reject_reason = None

        # 事务提交后再发信 —— 发信失败不回滚已发放的码
        delivery = notify.send_invite_code(
            invite.applicant_email, code, notify.registration_hint()
        )
        with transactional_db() as db:
            db.query(InviteCode).filter(InviteCode.id == invite_id).update(
                {'delivery_status': delivery}
            )
        # 同步内存对象：否则响应里的 invite 还是发信前的 delivery_status
        invite.delivery_status = delivery

        logger.info(f"邀请码审批通过：{invite.applicant_email}（发信：{delivery}）")
        return jsonify({
            'message': '已通过',
            'code': code,
            'delivery_status': delivery,
            'invite': _serialize_invite(invite),
        }), 200
    except Exception:
        logger.exception("邀请码审批失败")
        return jsonify({'error': '审批失败，请稍后重试'}), 500


@app.route('/api/admin/invites/<int:invite_id>/reject', methods=['POST'])
@admin_required
def reject_invite(invite_id: int):
    from models import InviteCode
    from services.notify import email as notify

    admin_id = int(get_jwt_identity())
    reason = ((request.get_json() or {}).get('reason') or '').strip()[:200]

    try:
        with transactional_db() as db:
            invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
            if not invite:
                return jsonify({'error': '申请不存在'}), 404
            invite.status = 'rejected'
            invite.reject_reason = reason
            invite.decided_by = admin_id
            invite.decided_at = datetime.utcnow()

        delivery = notify.send_rejection(invite.applicant_email, reason)
        with transactional_db() as db:
            db.query(InviteCode).filter(InviteCode.id == invite_id).update(
                {'delivery_status': delivery}
            )
        invite.delivery_status = delivery

        return jsonify({
            'message': '已拒绝',
            'delivery_status': delivery,
            'invite': _serialize_invite(invite),
        }), 200
    except Exception:
        logger.exception("邀请码拒绝失败")
        return jsonify({'error': '操作失败，请稍后重试'}), 500


@app.route('/api/admin/invites/<int:invite_id>/resend', methods=['POST'])
@admin_required
def resend_invite(invite_id: int):
    """重发邀请码邮件。仅当上一次未成功时才有意义。"""
    from models import InviteCode
    from services.notify import email as notify

    with get_db() as db:
        invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
        if not invite:
            return jsonify({'error': '申请不存在'}), 404
        if invite.status != 'issued' or not invite.code:
            return jsonify({'error': '该申请尚未发放邀请码'}), 409

        delivery = notify.send_invite_code(
            invite.applicant_email, invite.code, notify.registration_hint()
        )

    with transactional_db() as db:
        db.query(InviteCode).filter(InviteCode.id == invite_id).update(
            {'delivery_status': delivery}
        )
    return jsonify({'message': '已重新发送', 'delivery_status': delivery}), 200


@app.route('/api/admin/invites/<int:invite_id>/revoke', methods=['POST'])
@admin_required
def revoke_invite(invite_id: int):
    """吊销已发出但未使用的码（疑似泄露时用）。"""
    from models import InviteCode

    admin_id = int(get_jwt_identity())
    with transactional_db() as db:
        invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
        if not invite:
            return jsonify({'error': '申请不存在'}), 404
        if invite.status != 'issued':
            return jsonify({'error': f'当前状态（{invite.status}）不可吊销'}), 409
        invite.status = 'revoked'
        invite.decided_by = admin_id
        invite.decided_at = datetime.utcnow()
    return jsonify({'message': '已吊销', 'invite': _serialize_invite(invite)}), 200


# ==================== 指标看板 ====================

@app.route('/api/admin/metrics', methods=['GET'])
@admin_required
def admin_metrics():
    from services.admin_metrics import collect_metrics

    try:
        return jsonify(collect_metrics()), 200
    except Exception:
        logger.exception("后台指标聚合失败")
        return jsonify({'error': '指标聚合失败'}), 500
