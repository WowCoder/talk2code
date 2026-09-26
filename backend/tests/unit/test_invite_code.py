# -*- coding: utf-8 -*-
"""邀请码服务与注册核销测试。

覆盖：
- 码格式（T2C- + Crockford Base32 12 位）与唯一性
- 注册必须带有效邀请码；错误文案不区分失败原因（防枚举）
- 并发/重复核销：同一码只能注册一个账号
- 发码状态机（issue_code）与状态约束
- 申请接口：字段校验 + 冷却幂等
"""
from datetime import datetime, timedelta

import pytest

from models import SessionLocal, User, InviteCode
from services.invite import (
    generate_code, normalize_code, consume_code, issue_code, find_pending_by_email,
)

_run = __import__("uuid").uuid4().hex[:8]  # 隔离共享测试库中的残留数据


# ---------------- 码生成 ----------------

def test_code_format():
    for _ in range(20):
        code = generate_code()
        assert code.startswith("T2C-")
        body = code[len("T2C-"):]
        assert len(body) == 14  # 12 字符 + 2 个分隔符
        assert body.count("-") == 2
        # Crockford Base32 不含 I/L/O/U（防手抄混淆）
        for ch in body.replace("-", ""):
            assert ch in "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def test_codes_unique():
    codes = {generate_code() for _ in range(50)}
    assert len(codes) == 50


def test_normalize_uppercases():
    assert normalize_code(" t2c-abcd-efgh-jkmn ") == "T2C-ABCD-EFGH-JKMN"


# ---------------- 注册核销 ----------------

def _mk_issued(code="ISSUECODEAAA1", days=7):
    db = SessionLocal()
    db.query(InviteCode).filter(InviteCode.code == code).delete()
    db.add(InviteCode(
        code=code, applicant_email="a@b.com", applicant_phone="13800000000",
        status="issued", expires_at=datetime.utcnow() + timedelta(days=days),
        delivery_status="skipped",
    ))
    db.commit()
    return code


def test_register_requires_invite_code(app_client):
    resp = app_client.post("/api/register", json={"username": "nocode_user", "password": "test123456"})
    assert resp.status_code == 400


def test_register_with_valid_code(app_client):
    code = _mk_issued("VALIDCODE0001")
    resp = app_client.post("/api/register", json={
        "username": f"invited_user_{_run}", "password": "test123456", "invite_code": code})
    assert resp.status_code == 201, resp.get_json()

    db = SessionLocal()
    inv = db.query(InviteCode).filter(InviteCode.code == code).first()
    assert inv.status == "used"
    assert inv.used_at is not None
    u = db.query(User).filter(User.username == f"invited_user_{_run}").first()
    assert u is not None
    assert inv.used_by_user_id == u.id


def test_register_rejects_reused_code(app_client):
    code = _mk_issued("REUSEDCODE001")
    r1 = app_client.post("/api/register", json={
        "username": f"first_user_{_run}", "password": "test123456", "invite_code": code})
    assert r1.status_code == 201
    r2 = app_client.post("/api/register", json={
        "username": f"second_user_{_run}", "password": "test123456", "invite_code": code})
    assert r2.status_code == 400


def test_register_rejects_unknown_and_expired_with_same_message(app_client):
    """防枚举：不存在 / 已过期 / 已用，文案完全一致。"""
    expired = _mk_issued("EXPIREDCODE001", days=-1)
    for code in ("NOTEXISTCODE1", expired):
        resp = app_client.post("/api/register", json={
            "username": f"u_{code}_{_run}", "password": "test123456", "invite_code": code})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "邀请码无效或已被使用"


def test_register_no_partial_user_on_bad_code(app_client):
    """码无效时不得留下半注册用户（事务回滚）。"""
    db = SessionLocal()
    assert db.query(User).filter(User.username == f"ghost_user_{_run}").first() is None
    resp = app_client.post("/api/register", json={
        "username": f"ghost_user_{_run}", "password": "test123456", "invite_code": "NOPE-NOPE-NOPE"})
    assert resp.status_code == 400
    assert db.query(User).filter(User.username == f"ghost_user_{_run}").first() is None


def test_consume_is_atomic_rowcount_guard():
    """同一码在同一事务两次核销，第二次必须失败（rowcount 防护）。"""
    from utils.db import transactional_db
    code = _mk_issued("ATOMICCODE001")
    with transactional_db() as db:
        assert consume_code(db, code, user_id=999) is True
        assert consume_code(db, code, user_id=998) is False


# ---------------- 发码状态机 ----------------

def test_issue_code_sets_expiry_and_status():
    db = SessionLocal()
    inv = InviteCode(applicant_email=f"sm-{_run}@x.com", applicant_phone="1", status="pending")
    db.add(inv)
    db.commit()
    db.refresh(inv)

    code = issue_code(db, inv)
    db.commit()
    assert inv.status == "issued"
    assert inv.code == code
    assert inv.expires_at is not None
    assert inv.expires_at > datetime.utcnow()
    assert inv.delivery_status == "pending"

    # 再发码应拒绝（只有 pending/rejected 可发）
    with pytest.raises(ValueError):
        issue_code(db, inv)


def test_issue_code_allows_rejected_reissue():
    """被拒的申请允许重新审批发放（撤回拒绝 = 二次机会）。"""
    db = SessionLocal()
    inv = InviteCode(applicant_email=f"re-{_run}@x.com", applicant_phone="1", status="rejected")
    db.add(inv)
    db.commit()
    db.refresh(inv)
    code = issue_code(db, inv)
    db.commit()
    assert inv.status == "issued" and inv.code == code


# ---------------- 申请冷却 ----------------

def test_find_pending_by_email_cooldown():
    db = SessionLocal()
    inv = InviteCode(applicant_email=f"cd-{_run}@x.com", applicant_phone="1", status="pending")
    db.add(inv)
    db.commit()

    assert find_pending_by_email(db, f"cd-{_run}@x.com", 24) is not None
    assert find_pending_by_email(db, f"cd-{_run}@x.com", 0) is None  # 0 = 不限制


# ---------------- 申请接口 ----------------

def test_invite_request_created(app_client):
    resp = app_client.post("/api/invite/requests", json={
        "email": f"new-{_run}@x.com", "phone": "13900000000", "note": "内测"})
    assert resp.status_code == 201
    assert "申请" in resp.get_json()["message"]


def test_invite_request_cooldown_idempotent(app_client):
    body = {"email": f"dup-{_run}@x.com", "phone": "13900000001", "note": ""}
    r1 = app_client.post("/api/invite/requests", json=body)
    r2 = app_client.post("/api/invite/requests", json=body)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.get_json().get("duplicate") is True
    db = SessionLocal()
    n = db.query(InviteCode).filter(InviteCode.applicant_email == f"dup-{_run}@x.com").count()
    assert n == 1


def test_invite_request_validates_fields(app_client):
    assert app_client.post("/api/invite/requests", json={
        "email": "bad-email", "phone": "1"}).status_code == 400
    assert app_client.post("/api/invite/requests", json={
        "email": f"ok-{_run}@x.com", "phone": ""}).status_code == 400
