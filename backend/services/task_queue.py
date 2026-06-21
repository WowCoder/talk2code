# -*- coding: utf-8 -*-
"""
任务队列模块
提供线程池和任务队列，控制 AI 智能体任务并发，防止系统过载
"""

import threading
import queue
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Dict, Optional
from enum import Enum

from harness.observability.logger import get_logger

logger = get_logger(__name__)


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
    future: Optional[Future] = None


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

        # 提交到线程池
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

            # 更新状态为完成
            with self._tasks_lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = TaskStatus.COMPLETED
                    self._tasks[task_id].completed_at = datetime.now()

            logger.info(f"任务执行完成：task_id={task_id}")
            return result

        except Exception as e:
            # 更新状态为失败
            error_msg = str(e)
            with self._tasks_lock:
                if task_id in self._tasks:
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
        """检查任务状态，清理完成的任务"""
        with self._tasks_lock:
            completed_tasks = [
                tid for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            ]

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
