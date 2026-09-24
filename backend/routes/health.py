# -*- coding: utf-8 -*-
"""健康检查 API 路由"""
from flask import jsonify
from config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, settings
from utils.db import get_db
from utils.sse import get_current_timestamp
from factory import app
from services.task_queue import task_queue
from harness.observability.logger import get_logger

logger = get_logger(__name__)


def _safe_error(context: str, e: Exception) -> str:
    """组件异常只进日志（可能含路径/凭据），对外返回通用文案"""
    logger.warning(f"健康检查：{context} 异常: {e}")
    return f'{context}异常'

# ==================== 健康检查 API ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    健康检查接口

    检查项目：
    - 数据库连接状态
    - LLM API 配置状态
    - 系统基本信息

    Returns:
        {
            'status': 'healthy' | 'degraded' | 'unhealthy',
            'checks': {
                'database': {'status': 'ok' | 'error', ...},
                'llm': {'status': 'ok' | 'not_configured' | 'error', ...},
            },
            'version': '2.1.0',
            'timestamp': '...'
        }
    """
    import time

    checks = {}
    overall_status = 'healthy'

    # 1. 检查数据库连接
    db_status = 'ok'
    db_error = None
    try:
        with get_db() as db:
            # 执行简单查询验证连接 (SQLAlchemy 2.x 需要 text())
            from sqlalchemy import text
            db.execute(text('SELECT 1'))
    except Exception as e:
        db_status = 'error'
        # 完整异常（可能含连接串凭据）只进日志，对外仅返回通用文案
        logger.warning(f"健康检查：数据库连接失败: {e}")
        db_error = '数据库连接失败'
        overall_status = 'unhealthy'

    checks['database'] = {
        'status': db_status,
        'type': 'postgresql' if settings.IS_POSTGRES else 'sqlite',
        'error': db_error
    }

    # 2. 检查 LLM API 配置
    llm_status = 'ok'
    llm_error = None
    try:
        if not LLM_API_KEY:
            llm_status = 'not_configured'
            overall_status = 'degraded'
        else:
            llm_status = 'configured'
    except Exception as e:
        llm_status = 'error'
        llm_error = _safe_error('LLM 配置检查', e)
        overall_status = 'degraded'

    checks['llm'] = {
        'status': llm_status,
        'provider': LLM_PROVIDER,
        'model': LLM_MODEL,
        'error': llm_error
    }

    # 3. 检查任务队列
    queue_status = 'ok'
    try:
        active_tasks = len(task_queue._tasks) if hasattr(task_queue, '_tasks') else 0
        checks['task_queue'] = {
            'status': queue_status,
            'active_tasks': active_tasks
        }
    except Exception as e:
        checks['task_queue'] = {
            'status': 'error',
            'error': _safe_error('任务队列', e)
        }

    # 4. 检查 Harness 层状态
    try:
        from harness.tools.registry import create_tool_registry
        tools = create_tool_registry()
        checks['tool_registry'] = {
            'status': 'ok',
            'tools_count': len(tools.list_tools()),
        }
    except Exception as e:
        checks['tool_registry'] = {'status': 'error', 'error': _safe_error('工具注册表', e)}

    try:
        from harness.environment.sandbox import SandboxExecutor
        sandbox = SandboxExecutor()
        checks['sandbox'] = {'status': 'ok'}
    except Exception as e:
        checks['sandbox'] = {'status': 'error', 'error': _safe_error('沙箱', e)}

    try:
        from harness.state.memory_store import MemoryStore
        memory = MemoryStore()
        checks['memory_store'] = {'status': 'ok'}
    except Exception as e:
        checks['memory_store'] = {'status': 'error', 'error': _safe_error('记忆库', e)}

    return jsonify({
        'status': overall_status,
        'checks': checks,
        'version': '2.1.0',
        'timestamp': get_current_timestamp()
    }), 200 if overall_status != 'unhealthy' else 503


@app.route('/api/health/live', methods=['GET'])
def liveness_check():
    """
    Kubernetes liveness probe

    只检查应用是否存活，不检查依赖服务
    """
    return jsonify({'status': 'alive'}), 200


@app.route('/api/health/ready', methods=['GET'])
def readiness_check():
    """
    Kubernetes readiness probe

    检查应用是否准备好接收请求
    """
    
    # 只检查数据库
    try:
        with get_db() as db:
            from sqlalchemy import text
            db.execute(text('SELECT 1'))
        return jsonify({'status': 'ready'}), 200
    except Exception as e:
        logger.warning(f"就绪检查：数据库连接失败: {e}")
        return jsonify({'status': 'not_ready', 'error': '数据库连接失败'}), 503


@app.route('/api/metrics', methods=['GET'])
def metrics():
    """Prometheus 监控指标端点

    指标命名约定：指标名不带 talk2code_ 前缀，输出时统一拼接，
    避免 key 与前缀重复叠加导致采集端按名称抓不到。
    """
    import time
    metrics_data = {
        # requirements 表行数（历史需求总数），不是请求数
        'requirements_total': 0,
        # 处理中的需求数 —— 真实"活跃度"，取代原硬编码 0 的 active_sessions
        'requirements_processing': 0,
        'uptime_seconds': round(time.time() - app.config.get('START_TIME', time.time()), 1),
    }

    try:
        with get_db() as db:
            from sqlalchemy import text
            total = db.execute(text("SELECT COUNT(*) FROM requirements")).fetchone()
            processing = db.execute(
                text("SELECT COUNT(*) FROM requirements WHERE status = 'processing'")
            ).fetchone()
            metrics_data['requirements_total'] = total[0] if total else 0
            metrics_data['requirements_processing'] = processing[0] if processing else 0
    except Exception:
        # 查询失败输出 0 而不是 500：监控端点自身挂掉比数据缺失更糟
        pass

    lines = [f"talk2code_{key} {value}" for key, value in metrics_data.items()]
    return '\n'.join(lines) + '\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}


