# -*- coding: utf-8 -*-
"""用户认证 API 路由"""
import re

from flask import request, jsonify
from flask_jwt_extended import (
    create_access_token, jwt_required, get_jwt_identity, get_jwt,
    set_access_cookies, unset_jwt_cookies,
)
from sqlalchemy import update

from config import JWT_ACCESS_TOKEN_EXPIRES, settings
from utils.db import get_db, transactional_db
from factory import app, rate_limit_auth, logger

# 形态校验而非严格 RFC 校验 —— 过严的正则会拒掉真实用户的合法地址
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PHONE_RE = re.compile(r'^[0-9+\-\s()]{6,20}$')


class _RegisterError(Exception):
    """注册业务失败。用它而不是在事务块里 return —— 后者会让 transactional_db
    在退出时照常 commit（return 不等于异常）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _claims_for(user_id: int, *, demo: bool = False) -> dict:
    """JWT 附加声明。

    `type` 区分用途（user / admin），`demo` 标记演示会话 —— 演示模式的只读由
    utils/demo_guard.py 依据这个 claim 在 before_request 阶段强制执行。
    """
    claims = {'type': 'user'}
    if demo:
        claims['demo'] = True
    return claims


# ==================== 用户认证 API ====================

@app.route('/api/register', methods=['POST'])
@rate_limit_auth
def register():
    """用户注册接口（邀请码必填）"""
    from models import User, InviteCode
    from utils.security import hash_password
    from services.invite import consume_code, normalize_code

    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')
    invite_code = normalize_code(data.get('invite_code', ''))

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    if len(username) < settings.USERNAME_MIN_LENGTH:
        return jsonify({'error': f'用户名至少 {settings.USERNAME_MIN_LENGTH} 个字符'}), 400
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return jsonify({'error': f'密码至少 {settings.PASSWORD_MIN_LENGTH} 个字符'}), 400
    if not invite_code:
        return jsonify({'error': '邀请码为必填项'}), 400

    try:
        with transactional_db() as db:
            if db.query(User).filter(User.username == username).first():
                raise _RegisterError('用户名已存在', 409)

            # 先 flush 出 user id，再在**同一事务**里核销邀请码：
            # consume_code 用 UPDATE ... WHERE status='issued' + rowcount 检查扛并发，
            # 失败则整个事务回滚，不会留下「用户建了但码没销」的中间态。
            new_user = User(username=username, password_hash=hash_password(password))
            db.add(new_user)
            db.flush()

            if not consume_code(db, invite_code, new_user.id):
                # 文案刻意不区分「不存在 / 已使用 / 已过期」：区分了等于给攻击者
                # 一个邀请码枚举探针
                raise _RegisterError('邀请码无效或已被使用', 400)

        logger.info(f"用户注册成功：{username}")
        return jsonify({
            'message': '注册成功',
            'user': {'id': new_user.id, 'username': new_user.username}
        }), 201
    except _RegisterError as e:
        return jsonify({'error': e.message}), e.status
    except Exception:
        logger.exception("注册失败")
        return jsonify({'error': '注册失败，请稍后重试'}), 500


@app.route('/api/login', methods=['POST'])
@rate_limit_auth
def login():
    """用户登录接口"""
    from models import User
    from utils.security import verify_password

    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    with get_db() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return jsonify({'error': '用户名或密码错误'}), 401

        access_token = create_access_token(
            identity=str(user.id),
            expires_delta=JWT_ACCESS_TOKEN_EXPIRES,
            additional_claims=_claims_for(user.id),
        )
        logger.info(f"用户登录成功：{username}")

        # token 仅通过 httpOnly cookie 下发（规避 XSS 窃取），不在 JSON body 回传
        resp = jsonify({
            'message': '登录成功',
            'user': {'id': user.id, 'username': user.username, 'is_demo': False}
        })
        set_access_cookies(resp, access_token)
        return resp, 200


@app.route('/api/demo/enter', methods=['POST'])
@rate_limit_auth
def enter_demo():
    """进入演示模式。

    演示帐号是 users 表里的一行真实用户 —— 复用真实身份，所有按 user_id 过滤的
    既有查询一行都不用改。区别只在 JWT 多了 demo claim，写操作会被
    utils/demo_guard.py 在 before_request 阶段拦下。
    """
    from models import User

    with get_db() as db:
        user = db.query(User).filter(User.username == settings.DEMO_USERNAME).first()
        if not user:
            logger.warning(f"演示帐号不存在：{settings.DEMO_USERNAME}")
            return jsonify({
                'error': '演示帐号尚未初始化',
                'hint': 'python manage.py demo init'
            }), 404

        access_token = create_access_token(
            identity=str(user.id),
            expires_delta=JWT_ACCESS_TOKEN_EXPIRES,
            additional_claims=_claims_for(user.id, demo=True),
        )
        resp = jsonify({
            'message': '已进入演示模式',
            'user': {'id': user.id, 'username': user.username, 'is_demo': True}
        })
        set_access_cookies(resp, access_token)
        return resp, 200


@app.route('/api/logout', methods=['POST'])
def logout():
    """用户登出：清除 httpOnly cookie"""
    resp = jsonify({'message': '已登出'})
    unset_jwt_cookies(resp)
    return resp, 200


@app.route('/api/user/info', methods=['GET'])
@jwt_required()
def get_user_info():
    """获取当前用户信息"""
    from models import User

    current_user_id = int(get_jwt_identity())
    with get_db() as db:
        user = db.query(User).filter(User.id == current_user_id).first()
        if not user:
            return jsonify({'error': '用户不存在'}), 404

        return jsonify({
            'user': {
                'id': user.id,
                'username': user.username,
                'create_time': user.create_time.isoformat() if user.create_time else None,
                'is_demo': bool(get_jwt().get('demo')),
            }
        }), 200


@app.route('/api/user/password', methods=['POST'])
@jwt_required()
@rate_limit_auth
def change_password():
    """修改当前用户密码：验证旧密码后写入新哈希。

    挂 rate_limit_auth（和登录同一桶）—— 改密码接口同样适合暴力试旧密码，
    没有理由比登录更宽松。演示会话的写操作由 demo_guard 在 before_request 拦截。
    """
    from models import User
    from utils.security import hash_password, verify_password

    current_user_id = int(get_jwt_identity())
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    old_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password:
        return jsonify({'error': '当前密码和新密码不能为空'}), 400
    if len(new_password) < settings.PASSWORD_MIN_LENGTH:
        return jsonify({'error': f'新密码至少 {settings.PASSWORD_MIN_LENGTH} 个字符'}), 400

    # 校验失败/改密成功都要么走异常回滚、要么走 commit，用事务型上下文
    with transactional_db() as db:
        user = db.query(User).filter(User.id == current_user_id).first()
        # 统一文案不区分「旧密码错」和「用户不存在」，不给枚举探针。
        # 刻意用 400 而非 401 —— 前端 useApi 把 401 视为登录过期会清登录态跳登录页，
        # 用户只是输错了旧密码，不该被登出。
        if not user or not verify_password(old_password, user.password_hash):
            return jsonify({'error': '当前密码不正确'}), 400
        if verify_password(new_password, user.password_hash):
            return jsonify({'error': '新密码不能与当前密码相同'}), 400

        # 只改需要的列，避免整行 update 带动 create_time 等字段
        db.execute(
            update(User)
            .where(User.id == current_user_id)
            .values(password_hash=hash_password(new_password))
        )

    logger.info(f"用户修改密码成功：user_id={current_user_id}")
    return jsonify({'message': '密码已更新'}), 200
