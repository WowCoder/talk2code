# -*- coding: utf-8 -*-
"""任务队列「任务内部再排一个后续任务」的守卫。

守的是 req 204 实测出来的一个死锁：用户在主流程里给 plan 提修改意见时，
`confirm_plan` 会把需求状态重置为 pending 并**再提交一个 process_requirement
任务**。但 confirm_plan 自己就跑在「该需求的任务」里，而 submit 默认按需求去重
——于是这个后续任务被判定成"已在处理中"直接丢弃：

    需求状态被置回 pending，却没有任何 worker 接手 → 需求永久卡住。

同时防住对偶问题：不能为了让后续任务通过就把去重整个削弱（同一需求被并发跑
多次会更糟），也不能让外层任务退出时把后续任务的映射删掉。
"""

import threading
import time

from services.task_queue import TaskQueue, TaskStatus


def _fresh_queue(max_workers: int = 2) -> TaskQueue:
    """绕开单例，得到一个可独立关闭的队列实例。

    TaskQueue 是进程级单例（__new__ 里缓存），直接构造会拿到别的用例共享的
    实例与线程池；测试必须隔离，否则用例间互相干扰。
    """
    q = object.__new__(TaskQueue)
    q._initialized = False
    q.__init__(max_workers=max_workers)
    return q


def _wait_status(q: TaskQueue, task_id: str, wanted, timeout_s: float = 10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = q.get_task_info(task_id)
        if info and info.status in wanted:
            return info.status
        time.sleep(0.05)
    info = q.get_task_info(task_id)
    return info.status if info else None


def test_followup_submit_survives_own_task_dedupe():
    """任务内部用 dedupe=False 提交的后续任务必须真的被执行。"""
    q = _fresh_queue()
    ran = threading.Event()
    # 必须等外层任务真的把返回值写进 captured 再断言：后续任务是在**另一个 worker**
    # 上跑的，它可能在外层 submit 返回之前就置位 ran（实测 2/3 概率），
    # 直接读 captured 会读到空 dict，那是测试自己的竞态，不是产品缺陷。
    outer_returned = threading.Event()
    captured = {}

    def _followup():
        # 名字不匹配任何 Celery 任务 → 走线程池，不依赖 broker
        ran.set()

    def _outer():
        captured["task_id"] = q.submit(7, _followup, dedupe=False)
        outer_returned.set()

    try:
        assert q.submit(7, _outer) is not None
        assert outer_returned.wait(timeout=10), "外层任务没有跑起来"
        assert captured.get("task_id"), "submit 返回 None，后续任务没排上"
        assert ran.wait(timeout=10), "后续任务从未执行（被自己任务的去重挡掉了）"
    finally:
        q.shutdown()


def test_default_dedupe_still_blocks_resubmit():
    """默认去重不能被削弱：同一需求在跑时再次提交必须被挡下。"""
    q = _fresh_queue()
    outer_done = threading.Event()
    captured = {}

    def _other():
        raise AssertionError("这个任务不该被排上")

    def _outer():
        captured["task_id"] = q.submit(7, _other)  # dedupe 默认 True
        outer_done.set()

    try:
        assert q.submit(7, _outer) is not None
        assert outer_done.wait(timeout=10)
        assert captured.get("task_id") is None, "默认去重失效，同一需求会被并发跑"
    finally:
        q.shutdown()


def test_cleanup_keeps_followup_mapping():
    """外层任务的收尾不能删掉后续任务的映射，否则后续去重失效。"""
    q = _fresh_queue()
    followup_started = threading.Event()
    release = threading.Event()

    def _followup():
        followup_started.set()
        release.wait(timeout=10)

    def _outer():
        q.submit(7, _followup, dedupe=False)

    try:
        outer_id = q.submit(7, _outer)
        assert followup_started.wait(timeout=10), "后续任务未启动"
        # 等外层任务真正走完（含 finally 里的清理）
        assert _wait_status(q, outer_id, {TaskStatus.COMPLETED}) == TaskStatus.COMPLETED
        assert q._requirement_tasks.get(7) is not None, \
            "后续任务的映射被外层任务的清理删掉了"
    finally:
        release.set()
        q.shutdown()
