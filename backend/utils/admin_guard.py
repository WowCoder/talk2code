# -*- coding: utf-8 -*-
"""后台管理员鉴权装饰器。

管理员 token 与前台 token 用同一 JWTManager / 同一密钥签发，靠 claim 区分：
    前台用户: {"type": "user", "demo"?: bool}  —— httpOnly cookie
    管理员:   {"type": "admin"}                —— Authorization header

后台 token 走 header 而不是 cookie 的原因：flask-jwt-extended 的 cookie 名
（JWT_ACCESS_COOKIE_NAME）是全局配置，无法按 token 动态切换。若管理员也写
同一个 cookie，前台登录态与后台登录态会互相覆盖（登后台 → 前台掉线）。

代价：header token 由前端存 sessionStorage，有 XSS 面。缓解三件套：
① 有效期短（ADMIN_TOKEN_EXPIRES_HOURS，默认 2h）；
② 后台页面只渲染结构化数据，绝不渲染任何用户生成内容（不 innerHTML
   需求正文、不加载 AI 生成的代码），XSS 面被结构性消除；
③ 本装饰器在每个 /api/admin/* 路由上强制校验 type claim。
"""
from functools import wraps

from flask import jsonify
from flask_jwt_extended import jwt_required, get_jwt


def admin_required(fn):
    """要求 admin type claim 的 JWT。缺 token → 401；user token → 403。"""
    @wraps(fn)
    @jwt_required()
    def _wrapped(*args, **kwargs):
        if get_jwt().get("type") != "admin":
            return jsonify({"error": "需要管理员权限"}), 403
        return fn(*args, **kwargs)
    return _wrapped
