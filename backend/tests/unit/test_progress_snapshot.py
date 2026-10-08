# -*- coding: utf-8 -*-
"""实时进度快照的守卫。

盯的是这一类事故：**刷新页面后进度凭空消失**。

进度原先只活在 SSE 推送里。SSE 管理器确实会给迟到客户端回放缓冲，但缓冲只有
最近 200 条消息，而一次生成会推送几百条事件（迭代步骤、QA 每一步、验收子步骤），
等用户刷新时最早那批 progress 早被挤出去了。于是「正在处理时刷新一下，阶段、
百分比、已完成文件数全没了」，而后端其实还在跑。

修法是每次推进度顺手写一行快照，进页面时读回来。这里钉住四件事：
1. 写入能读回，且一个需求只有一行（不累积历史）；
2. 阶段缺省（空串）不覆盖已有阶段 —— 否则指示器会闪回未知；
3. started_at 只在新一轮开始（reset）时重置 —— 否则「已等待」会被算成
   从上一轮就开始等；
4. 详情接口只在进行中把快照带出去 —— 终态需求不该再显示进度条。
"""
from datetime import datetime, timedelta

import pytest

from models import SessionLocal, User
from models.models import Requirement, RequirementProgress
from utils.security import hash_password

_run = __import__("uuid").uuid4().hex[:8]
_PASSWORD = "test123456"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _mk_user(db, uname):
    u = db.query(User).filter(User.username == uname).first()
    if u is None:
        u = User(username=uname, password_hash=hash_password(_PASSWORD))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


def _mk_req(db, user, status="processing"):
    r = Requirement(user_id=user.id, title=f"ps-{status}", content="c",
                    status=status)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_record_then_read_roundtrip():
    """写进去的进度必须能原样读回，且一个需求只有一行（不累积历史）。"""
    from harness.observability import progress_snapshot as ps

    db = SessionLocal()
    try:
        u = _mk_user(db, f"ps_{_run}")
        r = _mk_req(db, u)
        rid = r.id
    finally:
        db.close()

    ps.reset(rid)
    assert ps.read(rid) is None

    ps.record(rid, percent=20, message="正在编写代码", stage="coding")
    ps.record(rid, percent=45, message="已完成 index.html（1/3）", stage="coding")

    snap = ps.read(rid)
    assert snap is not None
    assert snap["percent"] == 45
    assert snap["message"] == "已完成 index.html（1/3）"
    assert snap["stage"] == "coding"

    db = SessionLocal()
    try:
        assert db.query(RequirementProgress).filter(
            RequirementProgress.requirement_id == rid).count() == 1
    finally:
        db.close()
    ps.reset(rid)


def test_empty_stage_keeps_previous_stage():
    """stage 传空串表示「沿用上一阶段」，不能把已有阶段清成未知。

    后端部分埋点不带 stage，写空会让阶段指示器闪回未知 —— 用户看到的是
    「明明在编码，指示器却跳回去了」。
    """
    from harness.observability import progress_snapshot as ps

    db = SessionLocal()
    try:
        r = _mk_req(db, _mk_user(db, f"ps_{_run}"))
        rid = r.id
    finally:
        db.close()

    ps.reset(rid)
    ps.record(rid, percent=30, message="正在验证", stage="verifying")
    ps.record(rid, percent=60, message="已完成 2 个验收项")  # 不带 stage
    snap = ps.read(rid)
    assert snap["stage"] == "verifying", "缺省阶段不该覆盖已有阶段"
    assert snap["percent"] == 60
    ps.reset(rid)


def test_reset_restarts_the_wait_clock():
    """新的一轮必须重新计时：started_at 在 reset 之后要晚于上一轮。

    不重置的话，续跑/重跑的「已等待」会从上一轮算起，界面上写着「已等待 3 小时」。
    """
    from harness.observability import progress_snapshot as ps

    db = SessionLocal()
    try:
        r = _mk_req(db, _mk_user(db, f"ps_{_run}"))
        rid = r.id
    finally:
        db.close()

    ps.reset(rid)
    ps.record(rid, percent=10, message="开始处理需求", stage="planning")
    first = ps.started_at(rid)
    assert first is not None

    ps.record(rid, percent=50, message="正在编写代码", stage="coding")
    assert ps.started_at(rid) == first, "同一轮内的后续进度不该重置计时起点"

    ps.reset(rid)
    assert ps.started_at(rid) is None
    ps.record(rid, percent=10, message="开始处理需求", stage="planning")
    assert ps.started_at(rid) > first, "新一轮必须重新计时"


def test_sse_reporter_persists_progress():
    """推进度必须顺手落库 —— 只推 SSE 的话刷新页面就丢了。"""

    class _FakeManager:
        def __init__(self):
            self.sent = []

        def broadcast(self, client_id, message):
            self.sent.append((client_id, message))
            return 1

    from harness.observability.sse_reporter import SSEReporter
    from harness.observability import progress_snapshot as ps

    db = SessionLocal()
    try:
        r = _mk_req(db, _mk_user(db, f"ps_{_run}"))
        rid = r.id
    finally:
        db.close()

    ps.reset(rid)
    reporter = SSEReporter(_FakeManager())
    reporter.progress(rid, 42, "已完成 app.js（2/3）", stage="coding")

    assert len(reporter.sse.sent) == 1, "落库失败不影响推送，但推送本身不能少"
    snap = ps.read(rid)
    assert snap and snap["percent"] == 42
    assert snap["message"] == "已完成 app.js（2/3）"
    ps.reset(rid)


def test_detail_api_returns_progress_only_while_running(app_client, db):
    """刷新恢复的唯一入口：详情接口只在进行中把进度带出去。"""
    from harness.observability import progress_snapshot as ps

    uname = f"ps_api_{_run}"
    u = _mk_user(db, uname)
    running = _mk_req(db, u, "processing")
    done = _mk_req(db, u, "finished")

    ps.reset(running.id)
    ps.record(running.id, percent=55, message="已完成 index.html（2/3）",
              stage="coding")

    # token 只通过 httpOnly cookie 下发（规避 XSS），测试客户端会自动带上
    resp = app_client.post("/api/login", json={
        "username": uname, "password": _PASSWORD})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    body = app_client.get(f"/api/requirements/{running.id}").get_json()
    assert body.get("progress"), "进行中的需求必须带回进度快照，否则刷新就丢"
    assert body["progress"]["percent"] == 55
    assert body["progress"]["stage"] == "coding"
    # 带时区：前端 new Date(naive) 会按本地时区解析，等待时长会凭空多一个偏移
    assert body["progress"]["started_at"].endswith("+00:00")

    done_body = app_client.get(f"/api/requirements/{done.id}").get_json()
    assert "progress" not in done_body, "终态需求不该再显示进度条"
    ps.reset(running.id)
