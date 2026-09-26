# -*- coding: utf-8 -*-
"""邀请码申请 API 路由（用户侧）"""
from flask import request, jsonify

from config import settings
from utils.db import get_db, transactional_db
from factory import app, rate_limit_auth, logger


def _validate(email: str, phone: str):
    """返回错误文案或 None。形态校验而非严格 RFC 校验。"""
    if not email or '@' not in email or email.startswith('@') or email.endswith('@'):
        return '请填写有效的邮箱地址'
    if len(email) > 255:
        return '邮箱地址过长'
    if not phone or len(phone) < 6 or len(phone) > 20:
        return '请填写有效的手机号'
    return None


@app.route('/api/invite/requests', methods=['POST'])
@rate_limit_auth
def create_invite_request():
    """提交邀请码申请。

    幂等：同一邮箱在冷却期内已有 pending 申请时，返回统一的「已收到」提示。
    **不泄露**该邮箱是否已有已通过的码 —— 否则这就是一个账户枚举探针。
    """
    from models import InviteCode
    from services.invite import find_pending_by_email

    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    note = (data.get('note') or '').strip()[:500]

    err = _validate(email, phone)
    if err:
        return jsonify({'error': err}), 400

    with get_db() as db:
        existing = find_pending_by_email(
            db, email, settings.INVITE_REQUEST_COOLDOWN_HOURS
        )
        if existing:
            logger.info(f"邀请码申请重复提交（冷却期内）：{email}")
            return jsonify({
                'message': '已收到你的申请，请等待审批结果',
                'duplicate': True,
            }), 200

    try:
        with transactional_db() as db:
            invite = InviteCode(
                applicant_email=email,
                applicant_phone=phone,
                applicant_note=note,
                status='pending',
                delivery_status='pending',
            )
            db.add(invite)
            db.flush()
            logger.info(f"收到邀请码申请：{email}")
            return jsonify({
                'message': '申请已提交，审批通过后邀请码将发送到你的邮箱',
                'id': invite.id,
            }), 201
    except Exception:
        logger.exception("邀请码申请提交失败")
        return jsonify({'error': '提交失败，请稍后重试'}), 500
