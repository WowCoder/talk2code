# -*- coding: utf-8 -*-
"""里程碑事件携带 LLM 输入输出的落库守卫。

背景：意图识别 / 澄清问题生成背后是一次真实 LLM 调用，但 record_event
只落 label + meta —— 后台点开只有 confidence 一个数字，「模型依据什么
文本、模型原话是什么」无从查起。补埋点之后这些事件携带 messages /
response 落库，与 llm_turn 走同一套渲染。这里锁定三件事：

1. TraceWriter.event 带 messages/response 时必须写 payload + message_refs；
2. 不带时维持旧行为（纯结论事件）—— 没有调用不得伪造空输入输出；
3. 详情接口对带 payload 的里程碑返回完整 messages 与 response，
   前端「Request / Response」签才有东西可渲染。
"""
import uuid

import pytest

from harness.observability.trace_writer import TraceWriter
from models import SessionLocal
from models.models import AdminUser, AgentEvent, AgentPayload, Requirement, User
from utils.security import hash_password


@pytest.fixture
def rid():
    db = SessionLocal()
    try:
        u = User(username=f"mp_{uuid.uuid4().hex[:8]}",
                 password_hash=hash_password("x12345678"))
        db.add(u)
        db.commit()
        db.refresh(u)
        r = Requirement(user_id=u.id, title="t", content="c", status="processing")
        db.add(r)
        db.commit()
        db.refresh(r)
        return r.id
    finally:
        db.close()


def _write_intent_with_trace(rid):
    """模拟 B2 之后的意图识别埋点：结论 + LLM 输入输出。"""
    db = SessionLocal()
    try:
        w = TraceWriter(db, requirement_id=rid)
        msgs = [
            {"role": "system", "content": "你是意图分类器"},
            {"role": "user", "content": "做一个贪吃蛇游戏"},
        ]
        resp = {"content": "TASK"}
        seq = w.event("intent", "意图识别 · task",
                      meta={"intent": "task", "confidence": 0.95},
                      messages=msgs, response=resp)
        return seq
    finally:
        db.close()


def test_event_with_trace_writes_payload_and_refs(rid):
    """携带输入输出的里程碑必须像 llm_turn 一样落 payload + message_refs。"""
    seq = _write_intent_with_trace(rid)
    assert seq > 0

    db = SessionLocal()
    try:
        ev = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == rid,
            AgentEvent.kind == "intent").one()
        assert ev.payload_id is not None
        assert ev.message_refs is not None and len(ev.message_refs) == 2

        p = db.query(AgentPayload).filter(
            AgentPayload.id == ev.payload_id).one()
        # response 原样落 payload：详情接口直接透传给前端
        assert (p.response or {}).get("content") == "TASK"
    finally:
        db.close()


def test_event_without_trace_stays_conclusion_only(rid):
    """不带输入输出的里程碑维持旧行为 —— 不伪造空 payload / 空 messages。"""
    db = SessionLocal()
    try:
        w = TraceWriter(db, requirement_id=rid)
        seq = w.event("memory", "长期记忆注入 · 1 条", meta={"injected": 1})
        assert seq > 0
        ev = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == rid,
            AgentEvent.kind == "memory").one()
        assert ev.payload_id is None
        assert ev.message_refs is None
    finally:
        db.close()


@pytest.fixture
def admin_token(app_client):
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "mpadmin").delete()
    db.add(AdminUser(username="mpadmin", password_hash=hash_password("mppass123")))
    db.commit()
    resp = app_client.post("/api/admin/login", json={
        "username": "mpadmin", "password": "mppass123"})
    return resp.get_json()["token"]


def test_detail_returns_milestone_trace(app_client, admin_token, rid):
    """详情接口必须把里程碑的输入输出还原出来 —— 前端分页签的取数来源。"""
    _write_intent_with_trace(rid)

    db = SessionLocal()
    try:
        ev = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == rid,
            AgentEvent.kind == "intent").one()
        eid = ev.id
    finally:
        db.close()

    resp = app_client.get(f"/api/admin/traces/events/{eid}",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    body = resp.get_json()
    # messages 按 message_refs 还原，正文来自 blob 表
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "做一个贪吃蛇游戏"
    assert (body["response"] or {}).get("content") == "TASK"
