# -*- coding: utf-8 -*-
"""
任务队列模块
==============
统一任务调度入口，支持两种后端：
- Celery（推荐）: 跨进程任务分发，独立 worker 进程，崩溃恢复
- ThreadPoolExecutor（降级）: 进程内线程池，无需额外进程

通过 config.CELERY_ENABLED 控制切换，对调用方完全透明。
"""

import threading
import queue
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Dict, Optional, Union
from enum import Enum

from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== Celery 检测 ====================

_celery_available = False
_celery_tasks = {}

try:
    from config import settings
    if settings.CELERY_ENABLED:
        from celery_app import celery_app, process_requirement_task, confirm_plan_task
        _celery_available = True
        # 函数名 → Celery task 映射
        _celery_tasks = {
            'process_requirement_async': process_requirement_task,
            'RequirementService.confirm_plan': confirm_plan_task,
            'confirm_plan_async': confirm_plan_task,
        }
        logger.info("Celery 任务队列已启用")
    else:
        logger.info("Celery 已禁用，使用 ThreadPoolExecutor")
except Exception as e:
    logger.warning(f"Celery 初始化失败，降级到 ThreadPoolExecutor: {e}")


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: str
    requirement_id: int
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    future: Optional[Union[Future, Any]] = None  # Future 或 Celery AsyncResult
    backend: str = "threadpool"  # "celery" 或 "threadpool"


class TaskQueue:
    """任务队列管理器（单例）"""

    _instance: Optional['TaskQueue'] = None
    _lock = threading.Lock()

    def __new__(cls, max_workers: int = 5) -> 'TaskQueue':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_workers: int = 5):
        if self._initialized:
            return
        self._initialized = True

        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-worker"
        )

        # 任务队列（用于限制并发）
        self._queue: queue.Queue = queue.Queue()

        # 任务信息存储
        self._tasks: Dict[str, TaskInfo] = {}
        self._tasks_lock = threading.Lock()

        # 需求 ID -> task_id 映射（一个需求对应一个任务）
        self._requirement_tasks: Dict[int, str] = {}
        self._requirement_tasks_lock = threading.Lock()

        # 启动任务调度线程
        self._running = True
        self._scheduler_thread = threading.Thread(
            target=self._schedule_loop,
            daemon=True,
            name="Task-Scheduler"
        )
        self._scheduler_thread.start()

        logger.info(f"TaskQueue 已初始化：max_workers={max_workers}")

    def submit(
        self,
        requirement_id: int,
        task_func: Callable,
        *args,
        **kwargs
    ) -> Optional[str]:
        """
        提交任务

        当 Celery 可用且 task_func 匹配已知 Celery 任务时，通过 Celery 分发。
        否则降级到 ThreadPoolExecutor。

        Args:
            requirement_id: 需求 ID
            task_func: 任务函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            task_id 或 None（如果任务已存在）
        """
        # 检查该需求是否已有任务在处理中
        with self._requirement_tasks_lock:
            if requirement_id in self._requirement_tasks:
                existing_task_id = self._requirement_tasks[requirement_id]
                with self._tasks_lock:
                    existing_task = self._tasks.get(existing_task_id)
                    if existing_task and existing_task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                        logger.warning(f"需求 {requirement_id} 已有任务在处理中：{existing_task_id}")
                        return None

        task_id = f"task_{requirement_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 创建任务信息
        task_info = TaskInfo(
            task_id=task_id,
            requirement_id=requirement_id,
            status=TaskStatus.PENDING
        )

        with self._tasks_lock:
            self._tasks[task_id] = task_info

        with self._requirement_tasks_lock:
            self._requirement_tasks[requirement_id] = task_id

        # 尝试通过 Celery 分发
        celery_task = self._match_celery_task(task_func)
        if celery_task is not None:
            task_info.backend = "celery"
            try:
                async_result = celery_task.delay(*args, **kwargs)
                task_info.future = async_result
                # Celery task ID 作为追踪 ID
                logger.info(
                    f"任务已提交到 Celery：task_id={task_id}, "
                    f"celery_id={async_result.id}, requirement_id={requirement_id}"
                )
                return task_id
            except Exception as e:
                logger.warning(f"Celery 分发失败，降级到 ThreadPoolExecutor: {e}")
                task_info.backend = "threadpool"

        # 降级：提交到线程池
        future = self._executor.submit(
            self._run_task,
            task_id,
            requirement_id,
            task_func,
            *args,
            **kwargs
        )
        task_info.future = future

        logger.info(f"任务已提交：task_id={task_id}, requirement_id={requirement_id}")
        return task_id

    def _run_task(
        self,
        task_id: str,
        requirement_id: int,
        task_func: Callable,
        *args,
        **kwargs
    ):
        """运行任务（内部使用）"""
        # 更新状态为运行中
        with self._tasks_lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = TaskStatus.RUNNING
                self._tasks[task_id].started_at = datetime.now()

        logger.info(f"任务开始执行：task_id={task_id}")

        try:
            # 执行任务函数
            result = task_func(*args, **kwargs)

            # 更新状态为完成（若期间被取消，保持 CANCELLED，避免竞态覆盖）
            with self._tasks_lock:
                if task_id in self._tasks:
                    if self._tasks[task_id].status == TaskStatus.CANCELLED:
                        logger.info(f"任务 {task_id} 执行完毕但已被取消，保持 CANCELLED 状态")
                    else:
                        self._tasks[task_id].status = TaskStatus.COMPLETED
                        self._tasks[task_id].completed_at = datetime.now()

            logger.info(f"任务执行完成：task_id={task_id}")
            return result

        except Exception as e:
            # 更新状态为失败（若已被取消则保持 CANCELLED）
            error_msg = str(e)
            with self._tasks_lock:
                if task_id in self._tasks:
                    if self._tasks[task_id].status == TaskStatus.CANCELLED:
                        logger.info(f"任务 {task_id} 已取消，保持 CANCELLED 状态（忽略运行期异常）")
                    else:
                        self._tasks[task_id].status = TaskStatus.FAILED
                        self._tasks[task_id].completed_at = datetime.now()
                        self._tasks[task_id].error = error_msg

            logger.error(f"任务执行失败：task_id={task_id}, error={error_msg}")
            raise

        finally:
            # 清理需求映射
            with self._requirement_tasks_lock:
                if requirement_id in self._requirement_tasks:
                    del self._requirement_tasks[requirement_id]

    @staticmethod
    def _match_celery_task(task_func: Callable) -> Optional[Any]:
        """将传入的函数匹配到已注册的 Celery task

        匹配规则（按优先级）:
        1. 函数 __name__（如 'process_requirement_async'）
        2. 函数 __qualname__（如 'RequirementService.confirm_plan'）
        """
        if not _celery_available:
            return None

        # 获取函数名
        func_name = getattr(task_func, '__name__', '')
        qualname = getattr(task_func, '__qualname__', '')

        # 先按 __name__ 匹配
        if func_name in _celery_tasks:
            return _celery_tasks[func_name]

        # 再按 __qualname__ 匹配（处理 bound method）
        if qualname in _celery_tasks:
            return _celery_tasks[qualname]

        return None

    def _schedule_loop(self):
        """任务调度循环（目前主要用于监控）"""
        while self._running:
            try:
                # 定期检查任务状态
                self._check_tasks_status()
            except Exception as e:
                logger.error(f"任务调度循环异常：{e}")

            threading.Event().wait(10)  # 每 10 秒检查一次

    def _check_tasks_status(self):
        """检查任务状态，同步 Celery 结果并清理完成的任务"""
        with self._tasks_lock:
            # 同步 Celery 任务状态
            for tid, task_info in self._tasks.items():
                if (task_info.backend == "celery" and task_info.future
                        and task_info.status in (TaskStatus.PENDING, TaskStatus.RUNNING)):
                    try:
                        celery_state = task_info.future.state
                        if celery_state == 'STARTED' and task_info.status == TaskStatus.PENDING:
                            task_info.status = TaskStatus.RUNNING
                            task_info.started_at = datetime.now()
                        elif celery_state == 'SUCCESS':
                            task_info.status = TaskStatus.COMPLETED
                            task_info.completed_at = datetime.now()
                        elif celery_state == 'FAILURE':
                            task_info.status = TaskStatus.FAILED
                            task_info.completed_at = datetime.now()
                            task_info.error = str(task_info.future.result)
                    except Exception:
                        pass  # Celery 状态查询失败不影响主循环

            completed_tasks = [
                tid for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            ]

            # 清理已终态任务的"需求 → 任务"映射（覆盖 Celery 路径，
            # 线程池路径的 _run_task finally 也会删，双保险）
            terminal_ids = set(completed_tasks)
            with self._requirement_tasks_lock:
                stale_reqs = [req for req, tid in self._requirement_tasks.items() if tid in terminal_ids]
                for req in stale_reqs:
                    del self._requirement_tasks[req]

            # 清理完成的任务（保留最近 100 个）
            if len(self._tasks) > 100:
                for tid in completed_tasks[:len(completed_tasks) - 50]:
                    del self._tasks[tid]
                    logger.debug(f"清理完成的任务：{tid}")

    def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """获取任务信息"""
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def get_task_status(self, requirement_id: int) -> Optional[TaskStatus]:
        """根据需求 ID 获取任务状态"""
        with self._requirement_tasks_lock:
            task_id = self._requirement_tasks.get(requirement_id)
            if task_id:
                with self._tasks_lock:
                    task = self._tasks.get(task_id)
                    if task:
                        return task.status
        return None

    def cancel_task(self, requirement_id: int) -> bool:
        """取消指定需求的任务"""
        with self._requirement_tasks_lock:
            task_id = self._requirement_tasks.get(requirement_id)
            if not task_id:
                logger.warning(f"需求 {requirement_id} 没有关联的任务")
                return False

        with self._tasks_lock:
            task_info = self._tasks.get(task_id)
            if not task_info:
                return False
            if task_info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                logger.info(f"任务 {task_id} 已结束，状态：{task_info.status.value}")
                return False

            # 标记为 CANCELLED
            task_info.status = TaskStatus.CANCELLED
            task_info.completed_at = datetime.now()

            if task_info.backend == "celery" and task_info.future:
                # Celery: 使用 revoke 取消任务
                try:
                    task_info.future.revoke(terminate=True, signal='SIGTERM')
                    logger.info(f"任务 {task_id} 已通过 Celery revoke 取消")
                except Exception as e:
                    logger.warning(f"Celery revoke 失败: {e}")
            elif task_info.future and not task_info.future.done():
                # ThreadPoolExecutor: 尝试取消 Future
                cancelled = task_info.future.cancel()
                logger.info(f"任务 {task_id} 已取消：future.cancel()={cancelled}")
            else:
                logger.info(f"任务 {task_id} 已标记为 CANCELLED（任务正在执行中，将由信号机制终止）")

        # 需求映射不再在此处立即删除：任务仍在运行时删除映射，
        # 会让同一需求被重复提交、两个任务并发执行同一需求。
        # 映射改由 _run_task 的 finally（线程池）或 _check_tasks_status
        # （Celery 终态）在任务真正结束时清理。
        return True

    def get_pending_count(self) -> int:
        """获取待处理任务数量"""
        with self._tasks_lock:
            return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    def get_running_count(self) -> int:
        """获取运行中任务数量"""
        with self._tasks_lock:
            return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def shutdown(self, wait: bool = True):
        """关闭任务队列"""
        logger.info("关闭 TaskQueue...")
        self._running = False

        if self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=5)

        self._executor.shutdown(wait=wait)

        with self._tasks_lock:
            self._tasks.clear()

        with self._requirement_tasks_lock:
            self._requirement_tasks.clear()

        logger.info("TaskQueue 已关闭")


# 全局单例
task_queue = TaskQueue(max_workers=3)  # 默认最多 3 个并发任务
