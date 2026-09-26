# -*- coding: utf-8 -*-
"""
Talk2Code - Flask 主应用
重构版本：使用模块化架构
"""

import os
import sys
from flask import Flask, request, jsonify, Response, send_from_directory, g
from werkzeug.exceptions import NotFound
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
from utils.rate_limiter import (
    get_user_identity, rate_limit_handler, RATE_LIMITS, is_published_site_request,
)

# ==================== 日志配置 ====================

# 初始化根 logger 的文件处理器（app.log / agent.log / llm.log）
# 目录、级别、保留天数统一由 setup_logging 从 config.settings 读取，
# 不再在此处用环境变量重复解析（此前两处读取可能不一致）
setup_logging()
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
# ⚠️ 隔离红线：已发布站点跑在 <slug>.<PUBLISH_APEX>，与主站同属一个可注册域。
# 主站登录态之所以不会被「任意一份 AI 生成的代码」读走，唯一依赖就是这里的
# cookie 是 host-only —— 即**没有**设置 JWT_COOKIE_DOMAIN。
# 一旦给它设上 '.wowcoder.cn' 这类 Domain，所有已发布的子域就都能读到登录态，
# 而生成代码是不可信输入。改动此项前先看 tests/unit/test_publish_host_isolation.py。
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


# 演示模式只读守卫：注册在限流身份解析之后（后者已把 JWT 上下文准备好）。
# 默认拒绝所有写方法，白名单放行 —— 详见 utils/demo_guard.py 的取舍说明。
from utils.demo_guard import register_demo_guard  # noqa: E402
register_demo_guard(app)


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
        headers_enabled=True,
        # 已发布站点（<slug>.<PUBLISH_APEX>）豁免默认限流：一个页面加载会打出
        # 十几个静态资源请求，套 60/min 会 429；详见 utils/rate_limiter.py
        default_limits_exempt_when=is_published_site_request,
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
    rate_limit_market = limiter.limit(RATE_LIMITS['market'])
else:
    # No-op decorator for tests
    rate_limit_auth = rate_limit_requirement = rate_limit_chat = rate_limit_market = lambda f: f

# 初始化数据库
init_db()

# 启动期僵尸清理：上一进程在跑的需求会永久停在 processing（队列是纯内存的），
# 这里改判为 interrupted，使其可经 /api/requirements/<id>/resume 续跑。
# 必须放在服务器开始接收请求之前，且失败不阻断启动。
from services.stale_sweeper import sweep_stale_processing  # noqa: E402

sweep_stale_processing()

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

# 前端构建产物（Vite 输出目录）。文件名内容寻址（自带 hash），故：
# - 命中时允许客户端长期缓存（文件名变即内容变，不存在"更新了却拿到旧的"）；
# - 未命中时必须 404，**绝不回退 index.html**（理由见 serve_spa）。
SPA_ASSET_PREFIX = 'assets/'
SPA_ASSET_CACHE_CONTROL = 'public, max-age=31536000, immutable'


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    """前端页面路由。

    这里必须分清两类路径 —— 把两者同等对待会制造极难排查的假象：

    **一、前端路由**（``/detail/162``、``/history``）
    服务端没有对应文件是正常的，必须回退 ``index.html`` 交给 Vue Router，
    否则用户刷新页面直接 404。

    **二、构建产物**（``/assets/<name>-<hash>.js``）
    文件名内容寻址，**不存在就是真的不存在**。最常见的原因是页面还停留在
    旧版本：浏览器里的旧入口引用了上一次构建产出的 chunk，而新构建已把它删掉。
    此时若回退 ``index.html``，浏览器会拿到 ``200`` + ``text/html`` 去当 ES
    module 执行，报出

        Expected a JavaScript-or-Wasm module script but the server responded
        with a MIME type of "text/html"

    —— 把「这个 chunk 已经不存在了，重新加载页面即可」这个清晰事实，伪装成
    一个看起来像 MIME 配置错误的乱麻。所以产物路径必须返回 404：动态 import
    失败会得到 ``Failed to fetch dynamically imported module``，指向真正的原因。
    """
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    if path.startswith(SPA_ASSET_PREFIX):
        try:
            resp = send_from_directory(SPA_DIST, path)
        except NotFound:
            return jsonify({
                'error': f'静态资源不存在: {path}',
                'hint': '前端产物已更新，请强制刷新页面（Cmd/Ctrl+Shift+R）',
            }), 404
        resp.headers['Cache-Control'] = SPA_ASSET_CACHE_CONTROL
        return resp
    # 非产物路径（前端路由 / 根目录静态文件）→ 命中即返回，否则回退 index.html
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
