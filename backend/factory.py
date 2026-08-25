# -*- coding: utf-8 -*-
"""
Talk2Code - Flask 主应用
重构版本：使用模块化架构
"""

import os
import sys
from flask import Flask, request, jsonify, Response, send_from_directory, g
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity,
    verify_jwt_in_request,
)
from flask_cors import CORS
from flask_limiter import Limiter

from config import JWT_SECRET_KEY, JWT_ACCESS_TOKEN_EXPIRES, LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, settings
from models import init_db
from utils.db import get_db, transactional_db
from services.sse_manager import sse_manager
from services.task_queue import task_queue
from services.requirement_service import process_requirement_async
from harness.observability.logger import setup_logger, get_logger, setup_logging
from harness.agent_names import TL_NAME
from utils.rate_limiter import get_user_identity, rate_limit_handler, RATE_LIMITS

# ==================== 日志配置 ====================

# 初始化根 logger 的文件处理器（app.log / agent.log / llm.log）
setup_logging(log_dir="logs", level=os.environ.get("LOG_LEVEL", "INFO"))
setup_logger('sqlalchemy.engine', level=30)  # WARNING 级别
logger = get_logger(__name__)
logger.info("日志系统已初始化")

# ==================== 应用初始化 ====================

app = Flask(__name__, static_folder=None)

# 请求体大小上限，防止超大 JSON 耗尽内存
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB

# 进程启动时间（供 /api/metrics 计算 uptime）
import time as _time
app.config['START_TIME'] = _time.time()

# CORS 配置 - 使用白名单而非全开
# supports_credentials: 允许携带 httpOnly cookie（SSE / preview iframe 鉴权需要）
CORS(app, origins=settings.cors_origins_list, supports_credentials=True)

# 凭证模式下的通配符防护：supports_credentials=True 时反射任意 Origin
# 会把跨站请求升级为带凭证请求，直接拒绝启动（debug 下仅告警）
if '*' in settings.cors_origins_list:
    _cors_msg = "CORS_ORIGINS 含 '*' 且开启了 supports_credentials，存在凭证泄露风险"
    if settings.APP_DEBUG:
        logger.warning(f"⚠️  {_cors_msg}")
    else:
        raise RuntimeError(_cors_msg)

# JWT 配置
app.config['JWT_SECRET_KEY'] = JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = JWT_ACCESS_TOKEN_EXPIRES
app.config['JWT_TOKEN_LOCATION'] = ['headers', 'cookies']
app.config['JWT_HEADER_NAME'] = 'Authorization'
app.config['JWT_HEADER_TYPE'] = 'Bearer'
app.config['JWT_COOKIE_SECURE'] = not settings.APP_DEBUG  # 生产环境仅通过 HTTPS 下发
app.config['JWT_COOKIE_SAMESITE'] = 'Lax'
app.config['JWT_COOKIE_CSRF_PROTECT'] = False

jwt = JWTManager(app)


# 在限流 key 计算前解析 JWT 身份（写入 g.user_id），使限流按用户维度生效。
# 注意：必须注册在 Limiter 之前（Flask 按注册顺序执行 before_request）。
@app.before_request
def _set_rate_limit_identity():
    try:
        verify_jwt_in_request(optional=True)
        ident = get_jwt_identity()
        g.user_id = ident if ident is not None else None
    except Exception:
        g.user_id = None


# 限流配置 - 测试环境下禁用
DISABLE_RATE_LIMIT = os.environ.get('DISABLE_RATE_LIMIT', 'false').lower() == 'true'
if DISABLE_RATE_LIMIT:
    logger.info("测试环境：限流已禁用")
    limiter = None
else:
    limiter = Limiter(
        key_func=get_user_identity,
        app=app,
        default_limits=[RATE_LIMITS['default']],
        storage_uri="memory://",
        headers_enabled=True
    )

# 限流触发处理
@app.errorhandler(429)
def handle_rate_limit_exceeded(e):
    return rate_limit_handler(e)

# 测试环境下使用无操作装饰器
if limiter:
    rate_limit_auth = limiter.limit(RATE_LIMITS['auth'])
    rate_limit_requirement = limiter.limit(RATE_LIMITS['requirement_create'])
    rate_limit_chat = limiter.limit(RATE_LIMITS['chat'])
else:
    # No-op decorator for tests
    rate_limit_auth = rate_limit_requirement = rate_limit_chat = lambda f: f

# 初始化数据库
init_db()

# ==================== 生产环境安全检查 ====================

def check_production_security():
    """
    生产环境安全检查
    在应用启动时验证关键安全配置
    非调试模式下，关键安全问题将阻止应用启动
    """
    issues = []
    fatal_issues = []

    # 1. 检查 JWT 密钥
    # "暴露"判定：非调试模式，或监听在非回环地址（0.0.0.0/:: 会被局域网直接访问）。
    # 默认密钥 + 暴露 ⇒ 拒绝启动，除非显式设置 ALLOW_INSECURE_SECRETS=true 豁免。
    if JWT_SECRET_KEY == 'talk2code-secret-key-change-in-production':
        exposed = (not settings.APP_DEBUG) or settings.APP_HOST in ('0.0.0.0', '::')
        allow_insecure = settings.ALLOW_INSECURE_SECRETS
        msg = "JWT_SECRET_KEY 使用默认值，可被任何人伪造任意用户令牌！"
        if exposed and not allow_insecure:
            fatal_issues.append(msg)
            logger.critical("🔴 JWT_SECRET_KEY 使用默认值且服务对外可达，拒绝启动"
                            "（配置真实密钥，或隔离环境显式设 ALLOW_INSECURE_SECRETS=true）")
        else:
            issues.append(msg)
            logger.warning("⚠️  JWT_SECRET_KEY 使用默认值（仅限本机调试）")

    # 2. 检查 API Key
    if not LLM_API_KEY:
        msg = "LLM_API_KEY 未配置，AI 功能将不可用"
        issues.append(msg)
        if not settings.APP_DEBUG:
            fatal_issues.append(msg)
            logger.critical("🔴 LLM_API_KEY 未配置")

    # 3. 检查调试模式
    if settings.APP_DEBUG:
        issues.append("APP_DEBUG 已开启，生产环境应关闭")
        logger.warning("⚠️  调试模式已开启，生产环境应关闭")

    if issues:
        logger.warning("=" * 50)
        logger.warning("生产环境安全检查发现问题：")
        for issue in issues:
            logger.warning(f"  - {issue}")
        logger.warning("请修改 .env 文件或环境变量")
        logger.warning("=" * 50)

    if fatal_issues:
        raise RuntimeError(
            "生产环境安全配置不完整，应用拒绝启动。"
            "请在 .env 文件中配置以下变量：\n  "
            + "\n  ".join(fatal_issues)
            + "\n（开发环境可设置 APP_DEBUG=true 跳过此检查）"
        )

    return len(issues) == 0


# 执行安全检查
check_production_security()

logger.info("Talk2Code 应用启动")


# ==================== 应用关闭处理 ====================

import atexit

def cleanup():
    """清理资源（进程退出阶段，日志流可能已关闭，异常不阻断退出）"""
    import logging as _logging
    try:
        # 先 flush 日志处理器，避免退出期 write 到已关闭流报 ValueError
        for handler in _logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
    except Exception:
        pass
    try:
        logger.info("清理资源...")
        sse_manager.shutdown()
        task_queue.shutdown(wait=False)
    except Exception as e:
        # 退出阶段尽力而为，任何异常不得影响进程退出
        print(f"[cleanup] 清理资源异常（忽略）: {e}")

atexit.register(cleanup)


# ==================== 前端页面路由 ====================

# Vue SPA 静态文件目录
SPA_DIST = os.path.join(os.path.dirname(__file__), '..', 'frontend-vue', 'dist')


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    """SPA fallback — 非 API 路径返回 index.html，由 Vue Router 处理"""
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    # 尝试返回静态文件（js/css/assets）
    if path:
        try:
            return send_from_directory(SPA_DIST, path)
        except Exception:
            pass
    # 否则返回 index.html，由 Vue Router 处理路由
    try:
        return send_from_directory(SPA_DIST, 'index.html')
    except Exception:
        return jsonify({'error': 'Frontend not built. Run: cd frontend-vue && npm run build'}), 503



def create_app():
    """Application factory."""
    return app

# ==================== 主程序入口 ====================

if __name__ == '__main__':
    logger.info("启动 Flask 应用，端口 5001")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
