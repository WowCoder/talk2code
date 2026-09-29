# -*- coding: utf-8 -*-
"""SSE 连接的存活判定：健康但静默的连接绝不能被清理线程摘除。

背景（需求 207 实测）：
`SSEClient.last_heartbeat` 原先只在 `send()` 里刷新，而 `cleanup_stale()` 每 60 秒
摘除心跳超过 300 秒的连接。心跳是**路由生成器自己 yield** 的（不经过 SSEClient），
所以「5 分钟没有业务事件」的健康连接会被判死摘除 —— HTTP 连接还在，浏览器
EventSource 也没有任何错误，但此后所有 broadcast 都找不到它，事件静默丢失。

用户看到的现象：TL 出完计划等待确认期间（本次实测 33 分钟）连接被摘掉，
之后整个编码阶段与验收阶段的实时消息一条都推不到前端，必须手动刷新才能看到。

这里锁住两件事：
1. `touch()` 能把一条静默连接从清理名单里救回来；
2. 路由的心跳分支必须真的调用 `touch()`（否则第 1 条永远不会发生）。
"""

import queue
import threading
from datetime import datetime, timedelta
from pathlib import Path

from services.sse_manager import SSEClient, SSEManager

# 清理阈值：cleanup_stale 的默认超时（秒）
_STALE_TIMEOUT = 300


def _bare_manager() -> SSEManager:
    """绕开单例 __init__（不连 Redis、不启线程），只造一个可测的干净管理器。"""
    mgr = object.__new__(SSEManager)
    mgr._lock = threading.RLock()
    mgr._clients = {}
    mgr._message_buffers = {}
    mgr._buffer_ts = {}
    return mgr


def _register(mgr: SSEManager, client_id: str, idle_seconds: int) -> tuple:
    q = queue.Queue()
    client = SSEClient(q)
    client.last_heartbeat = datetime.now() - timedelta(seconds=idle_seconds)
    mgr._clients[client_id] = [client]
    return client, q


def test_idle_client_is_reaped_without_touch():
    """不调 touch：静默超过阈值的连接会被摘除 —— 这正是 bug 的成因，行为要写明。"""
    mgr = _bare_manager()
    _register(mgr, "207", _STALE_TIMEOUT + 1)

    mgr.cleanup_stale(_STALE_TIMEOUT)

    assert "207" not in mgr._clients, (
        "静默超阈值的连接本就该被清理（清理逻辑本身没错，错在心跳没刷新它）"
    )


def test_touch_keeps_idle_client_alive():
    """调 touch：同样静默超阈值，但连接活着，必须留下。"""
    mgr = _bare_manager()
    client, q = _register(mgr, "207", _STALE_TIMEOUT + 1)

    assert mgr.touch("207", q) is True
    mgr.cleanup_stale(_STALE_TIMEOUT)

    assert "207" in mgr._clients, (
        "touch 后连接仍是活的，不该被摘除——否则等用户确认期间（数十分钟无业务事件）"
        "连接会被判死，后续编码/验收事件全部静默丢失"
    )
    assert mgr._clients["207"][0] is client


def test_touch_does_not_revive_a_removed_client():
    """已经被摘除的连接，touch 不能凭空空手把它加回来（避免掩盖真实断线）。"""
    mgr = _bare_manager()
    _, q = _register(mgr, "207", _STALE_TIMEOUT + 1)
    mgr.cleanup_stale(_STALE_TIMEOUT)

    assert mgr.touch("207", q) is False
    assert "207" not in mgr._clients


def test_touch_only_matches_its_own_queue():
    """同一需求可能挂着多个标签页，touch 只应续命它自己那条连接。"""
    mgr = _bare_manager()
    touched, touched_q = _register(mgr, "207", _STALE_TIMEOUT + 1)
    # 另一个同样静默超阈值的连接，但这次不 touch 它
    other_q = queue.Queue()
    other = SSEClient(other_q)
    other.last_heartbeat = datetime.now() - timedelta(seconds=_STALE_TIMEOUT + 1)
    mgr._clients["207"].append(other)

    mgr.touch("207", touched_q)
    mgr.cleanup_stale(_STALE_TIMEOUT)

    remaining = mgr._clients.get("207", [])
    assert touched in remaining, "被 touch 的那条应留下"
    assert other not in remaining, (
        "touch 必须按 queue 精确匹配：把别人的连接一起续命会掩盖真实断线"
    )


def test_heartbeat_branch_touches_the_manager():
    """路由源码守卫：心跳分支必须调用 touch()。

    这一条防的是「以后有人把 touch 删了」—— 删掉之后上面的用例仍然全绿
    （它们直接调 touch），线上却会退回「静默 5 分钟即失联」。
    """
    src = (Path(__file__).resolve().parents[2] / "routes" / "requirements.py").read_text(
        encoding="utf-8"
    )
    # 定位 queue.Empty 心跳分支
    idx = src.find("except queue.Empty:")
    assert idx > 0, "未找到 SSE 心跳分支（except queue.Empty），路由结构可能已变"

    heartbeat_block = src[idx: idx + 1200]
    assert "sse_manager.touch(" in heartbeat_block, (
        "SSE 心跳分支必须调用 sse_manager.touch(client_id, client_queue)："
        "心跳是路由直接 yield 的、不经过 SSEClient.send()，不 touch 的话"
        "连接的心跳时间永远不刷新，5 分钟无业务事件就会被清理线程摘除"
    )
