# -*- coding: utf-8 -*-
"""用户认证 API 路由"""
from flask import request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from config import JWT_ACCESS_TOKEN_EXPIRES
from utils.db import get_db, transactional_db
from factory import app, rate_limit_auth, logger

# ==================== 用户认证 API ====================

@app.route('/api/register', methods=['POST'])
@rate_limit_auth
def register():
    """用户注册接口"""
    from models import User
    from utils.security import hash_password

    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    if len(username) < 3:
        return jsonify({'error': '用户名至少 3 个字符'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码至少 6 个字符'}), 400

    with transactional_db() as db:
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            return jsonify({'error': '用户名已存在'}), 409

        password_hash = hash_password(password)
        new_user = User(username=username, password_hash=password_hash)
        db.add(new_user)
        db.flush()  # flush 执行 INSERT 并填充自增 ID，但不提交事务

        logger.info(f"用户注册成功：{username}")
        return jsonify({
            'message': '注册成功',
            'user': {'id': new_user.id, 'username': new_user.username}
        }), 201


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

        access_token = create_access_token(identity=str(user.id), expires_delta=JWT_ACCESS_TOKEN_EXPIRES)
        logger.info(f"用户登录成功：{username}")

        return jsonify({
            'message': '登录成功',
            'token': access_token,
            'user': {'id': user.id, 'username': user.username}
        }), 200


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
                'create_time': user.create_time.isoformat() if user.create_time else None
            }
        }), 200


