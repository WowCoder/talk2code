# -*- coding: utf-8 -*-
"""
Celery 任务队列配置
==================

本模块负责创建并配置 Celery 应用实例，用于异步处理需求分析与代码生成任务。

- Broker / Result Backend 均使用 Redis（连接串来源于 config.py 的 settings）
- 任务函数内部使用延迟导入（lazy import）避免与 Flask 应用产生循环依赖
- 可通过 celery worker 命令独立启动 worker 进程：
    celery -A celery_app.celery_app worker --loglevel=info
"""

import os
import sys

# 确保 backend 目录在 sys.path 中，使 worker 进程能找到 services / models 等模块
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from celery import Celery

from config import settings

# ==================== 创建 Celery 应用 ====================

celery_app = Celery(
    'talk2code',
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_RESULT,
)

# ==================== Celery 配置 ====================

celery_app.conf.update(
    # 时区设置
    timezone='Asia/Shanghai',
    enable_utc=True,

    # 任务执行追踪：记录 STARTED 状态，便于前端查询任务进度
    task_track_started=True,

    # 任务超时（秒）：只用软限制——触发 SoftTimeLimitExceeded 异常，
    # 由 process_requirement 的 except 捕获并落库 failed。
    # 不设置 task_time_limit（硬限制会 SIGKILL worker，绕过 except 使需求永久卡死 processing）
    task_soft_time_limit=1800,

    # 可靠性：任务确认后执行，worker 崩溃时重新投递，避免静默丢失
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Worker 并发数
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,

    # 结果序列化格式
    result_serializer='json',
    accept_content=['json'],
    task_serializer='json',
)


# ==================== Celery 任务定义 ====================
# 所有任务均使用延迟导入，避免与 Flask app / services 层产生循环依赖


@celery_app.task(name='process_requirement', bind=True)
def process_requirement_task(self, requirement_id: int):
    """Celery 任务: 异步处理需求"""
    from services.requirement_service import RequirementService
    service = RequirementService()
    return service.process_requirement(requirement_id)


@celery_app.task(name='confirm_plan', bind=True)
def confirm_plan_task(self, requirement_id: int, feedback: str = ""):
    """Celery 任务: 确认 Plan 并继续编码"""
    from services.requirement_service import RequirementService
    service = RequirementService()
    return service.confirm_plan(requirement_id, feedback)


# ==================== 辅助函数 ====================


def get_celery_app():
    """获取 Celery app 实例（供 worker 启动使用）"""
    return celery_app
