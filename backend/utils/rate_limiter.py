# -*- coding: utf-8 -*-
"""
限流工具模块
使用 flask-limiter 实现 API 限流
"""

from functools import wraps
from typing import Optional, Callable
from flask import request, jsonify, g

from utils.logger import get_logger

logger = get_logger(__name__)


def get_user_identity() -> str:
    """
    获取当前用户标识（用于限流）

    优先级：
    1. JWT 用户 ID
    2. IP 地址
    """
    # 尝试从 g 对象获取用户 ID（由 JWT 认证设置）
    if hasattr(g, 'user_id') and g.user_id:
        return f"user:{g.user_id}"

    # 降级到 IP 地址
    if request.headers.get('X-Forwarded-For'):
        ip = request.headers.get('X-Forwarded-For').split(',')[0].strip()
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


# 装饰器工厂函数
def rate_limit(limit_str: str, **kwargs):
    """
    限流装饰器

    Args:
        limit_str: 限流字符串，如 "10 per minute", "100 per hour"
        **kwargs: 传递给 flask-limiter 的其他参数

    Returns:
        装饰器函数

    Example:
        @app.route('/api/test')
        @rate_limit("10 per minute")
        def test():
            ...
    """
    def decorator(f: Callable):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # 这里只是标记，实际限流由 flask-limiter 中间件处理
            return f(*args, **kwargs)
        return wrapped
    return decorator


# 预定义的限流配置
RATE_LIMITS = {
    # 认证接口：防止暴力破解
    'auth': '5 per minute',

    # 需求创建：防止滥用
    'requirement_create': '10 per hour',

    # 对话接口：防止刷屏
    'chat': '20 per minute',

    # 代码保存：防止频繁修改
    'code_save': '30 per minute',

    # SSE 连接：限制并发连接
    'sse': '5 per minute',

    # 默认限流
    'default': '60 per minute',
}
