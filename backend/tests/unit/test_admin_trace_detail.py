# -*- coding: utf-8 -*-
"""事件详情接口的字段守卫。

为什么单列一个文件：`meta` 曾经在**接口层**被漏掉过一次——埋点把结论字段
（判定 / 得分 / 未达成 AC / 修复轮次）写进了库，详情接口没返回，
前端更是没渲染，「验收为什么没过」在后台界面上等于没有答案。
所以这里盯死两件事：

1. `meta` 必须原样出现在 `/api/admin/traces/events/<id>` 的响应里；
2. 里程碑事件的 `stage` / `turn_index` / `trace_id` 也要带出来，
   否则前端只能把第 3 轮的验收卡显示成第 0 轮。
"""
from datetime import datetime

import pytest

from models import SessionLocal, User
from models.models import AdminUser, AgentEvent, Requirement
from utils.security import hash_password

_run = __import__("uuid").uuid4().hex[:8]


@pytest.fixture
def admin_token(app_client):
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "trcadmin").delete()
    db.add(AdminUser(username="trcadmin", password_hash=hash_password("trcpass123")))
    db.commit()
    resp = app_client.post("/api/admin/login", json={
        "username": "trcadmin", "password": "trcpass123"})
    return resp.get_json()["token"]


def _seed_event(meta, *, kind="verify", stage="verifying", turn_index=2):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == f"trc_{_run}").first()
        if u is None:
            u = User(username=f"trc_{_run}", password_hash=hash_password("x12345678"))
            db.add(u)
            db.commit()
            db.refresh(u)
        r = Requirement(user_id=u.id, title="t", content="c", status="processing")
        db.add(r)
        db.commit()
        db.refresh(r)
        ev = AgentEvent(
            requirement_id=r.id, trace_id="a" * 32, seq=1,
            turn_index=turn_index, ts=datetime.utcnow(),
            kind=kind, stage=stage, label="验收未通过 · 未达成 AC 2 条",
            status="ok", meta=meta,
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        return ev.id
    finally:
        db.close()


def test_event_detail_returns_meta(app_client, admin_token):
    """meta 不能在接口层被丢掉——前端「要点」面板唯一的取数来源。"""
    meta = {"verdict": "NEEDS_WORK", "score": 62, "critical_count": 2,
            "failed_ac_ids": ["AC-1", "AC-3"], "defect_count": 0,
            "ac_total": 4, "fast_pass": False}
    eid = _seed_event(meta)
    resp = app_client.get(f"/api/admin/traces/events/{eid}",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["meta"] == meta
    # 未达成 AC 是排查的主线信息，单独钉一条，避免将来被「精简」掉
    assert data["meta"]["failed_ac_ids"] == ["AC-1", "AC-3"]


def test_event_detail_keeps_round_and_trace(app_client, admin_token):
    """轮次与 trace_id / call_id 要跟着出来 —— 少了它们，第 N 轮验收会被
    显示成第 0 轮，也没法从这条 LLM 事件跳到传输层原文。"""
    eid = _seed_event({"round": 3}, kind="repair", stage="repairing",
                      turn_index=3)
    resp = app_client.get(f"/api/admin/traces/events/{eid}",
                          headers={"Authorization": f"Bearer {admin_token}"})
    data = resp.get_json()
    assert data["kind"] == "repair"
    assert data["turn_index"] == 3
    assert data["stage"] == "repairing"
    assert data["trace_id"] == "a" * 32
    assert "call_id" in data
    assert data["requirement_id"] > 0
