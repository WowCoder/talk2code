# -*- coding: utf-8 -*-
"""
限流工具模块
使用 flask-limiter 实现 API 限流
"""

from flask import request, jsonify, g

from config import settings
from harness.observability.logger import get_logger

logger = get_logger(__name__)


def get_user_identity() -> str:
    """
    获取当前用户标识（用于限流）

    优先级：
    1. JWT 用户 ID
    2. IP 地址（仅在 TRUST_PROXY_HEADERS=true 时信任 X-Forwarded-For，
       否则直接取 TCP 对端地址，防止伪造头绕过限流）
    """
    # 尝试从 g 对象获取用户 ID（由 JWT 认证设置）
    if hasattr(g, 'user_id') and g.user_id:
        return f"user:{g.user_id}"

    xff = request.headers.get('X-Forwarded-For')
    if settings.TRUST_PROXY_HEADERS and xff:
        ip = xff.split(',')[0].strip()
    else:
        ip = request.remote_addr or 'unknown'
    return f"ip:{ip}"


def rate_limit_handler(e=None):
    """限流触发时的处理函数"""
    # Handle both exception and response object
    retry_after = '60'
    if hasattr(e, 'retry_after'):
        retry_after = str(e.retry_after)
    elif hasattr(e, 'headers') and e.headers.get('Retry-After'):
        retry_after = e.headers.get('Retry-After', '60')

    logger.warning(f"限流触发：{get_user_identity()}")
    return jsonify({
        'error': '请求过于频繁，请稍后再试',
        'retry_after': retry_after
    }), 429


# 预定义的限流配置
# 实际限流由 factory.py 的 Limiter(key_func=get_user_identity) 按 RATE_LIMITS 执行；
# 路由代码通过 rate_limit_auth / rate_limit_chat 等装饰器引用对应档位。
RATE_LIMITS = {
    # 认证接口：防止暴力破解
    'auth': '5 per minute',

    # 需求创建：防止滥用
    'requirement_create': '10 per hour',

    # 对话接口（chat/clarify/confirm/cancel 等写操作）
    'chat': '30 per minute',

    # 代码保存：防止频繁修改
    'code_save': '30 per minute',

    # SSE 连接：不限制（长连接，不计入请求频率）
    'sse_exempt': None,

    # 详情页只读操作：宽松限制
    'detail_read': '120 per minute',

    # 默认限流
    'default': '60 per minute',
}
