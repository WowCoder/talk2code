# -*- coding: utf-8 -*-
"""后台管理员鉴权与审批流测试。

覆盖越权防护的关键断言：
- 普通 user token 访问 /api/admin/* → 403（claim 不含 type=admin）
- 无 token → 401
- admin token 走 Authorization header，不与前台 cookie 登录态互相覆盖
- 审批动作的状态约束（重复审批 409、resend 仅未送达、revoke 仅 issued）
"""
from datetime import datetime, timedelta

import pytest

from models import SessionLocal, InviteCode
from models.models import AdminUser
from utils.security import hash_password

_run = __import__("uuid").uuid4().hex[:8]  # 隔离共享测试库中的残留数据


@pytest.fixture
def admin_token(app_client):
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "opadmin").delete()
    db.add(AdminUser(username="opadmin", password_hash=hash_password("adminpass9")))
    db.commit()
    resp = app_client.post("/api/admin/login", json={
        "username": "opadmin", "password": "adminpass9"})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- 登录 ----------------

def test_admin_login_success_and_bad_password(app_client, admin_token):
    assert admin_token  # fixture 里已成功登录

    resp = app_client.post("/api/admin/login", json={
        "username": "opadmin", "password": "wrong"})
    assert resp.status_code == 401


def test_admin_login_updates_last_login(app_client, admin_token):
    db = SessionLocal()
    a = db.query(AdminUser).filter(AdminUser.username == "opadmin").first()
    assert a.last_login_at is not None


# ---------------- 鉴权边界 ----------------

def test_admin_api_requires_token(app_client):
    assert app_client.get("/api/admin/invites").status_code == 401


def test_admin_api_rejects_user_token(app_client):
    """越权防护核心：前台用户 token 即使有效也进不了后台。"""
    from models import User
    db = SessionLocal()
    db.query(User).filter(User.username == f"plain_user_{_run}").delete()
    db.add(User(username=f"plain_user_{_run}", password_hash=hash_password("plain12345")))
    db.commit()

    app_client.post("/api/login", json={"username": f"plain_user_{_run}", "password": "plain12345"})
    resp = app_client.get("/api/admin/invites")
    assert resp.status_code == 403


def test_admin_api_accepts_admin_header_token(app_client, admin_token):
    resp = app_client.get("/api/admin/invites", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert "items" in resp.get_json()


def test_admin_and_frontend_sessions_coexist(app_client, admin_token):
    """管理员 token 走 header，前台 cookie 登录态不被覆盖。"""
    from models import User
    db = SessionLocal()
    db.query(User).filter(User.username == f"coexist_user_{_run}").delete()
    db.add(User(username=f"coexist_user_{_run}", password_hash=hash_password("co12345678")))
    db.commit()
    app_client.post("/api/login", json={"username": f"coexist_user_{_run}", "password": "co12345678"})

    # 前台登录态仍在
    assert app_client.get("/api/user/info").status_code == 200
    # 后台接口也通
    assert app_client.get("/api/admin/invites", headers=_auth(admin_token)).status_code == 200


# ---------------- 审批流 ----------------

def _mk_pending(email):
    db0 = SessionLocal()
    db0.query(InviteCode).filter(InviteCode.applicant_email == email).delete()
    db0.commit()

    db = SessionLocal()
    inv = InviteCode(applicant_email=email, applicant_phone="13812345678",
                     applicant_note="想用", status="pending")
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv.id


def test_approve_flow(app_client, admin_token):
    inv_id = _mk_pending(f"flow1-{_run}@x.com")
    resp = app_client.post(f"/api/admin/invites/{inv_id}/approve", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.get_json()["invite"]
    # SMTP_ENABLED=false（测试环境）→ skipped，但码已生成且在响应里可复制
    assert data["status"] == "issued"
    assert data["code"] and data["code"].startswith("T2C-")
    assert data["delivery_status"] == "skipped"


def test_approve_twice_conflict(app_client, admin_token):
    inv_id = _mk_pending(f"flow2-{_run}@x.com")
    r1 = app_client.post(f"/api/admin/invites/{inv_id}/approve", headers=_auth(admin_token))
    assert r1.status_code == 200
    r2 = app_client.post(f"/api/admin/invites/{inv_id}/approve", headers=_auth(admin_token))
    assert r2.status_code == 409


def test_reject_with_reason(app_client, admin_token):
    inv_id = _mk_pending(f"flow3-{_run}@x.com")
    resp = app_client.post(f"/api/admin/invites/{inv_id}/reject",
                           headers=_auth(admin_token), json={"reason": "信息不全"})
    assert resp.status_code == 200
    db = SessionLocal()
    inv = db.query(InviteCode).get(inv_id)
    assert inv.status == "rejected"
    assert inv.reject_reason == "信息不全"
    assert inv.decided_by is not None


def test_resend_and_revoke(app_client, admin_token):
    inv_id = _mk_pending(f"flow4-{_run}@x.com")
    app_client.post(f"/api/admin/invites/{inv_id}/approve", headers=_auth(admin_token))

    # skipped → 可重发
    resp = app_client.post(f"/api/admin/invites/{inv_id}/resend", headers=_auth(admin_token))
    assert resp.status_code == 200

    # revoke 仅对 issued 有效
    resp = app_client.post(f"/api/admin/invites/{inv_id}/revoke", headers=_auth(admin_token))
    assert resp.status_code == 200
    db = SessionLocal()
    assert db.query(InviteCode).get(inv_id).status == "revoked"

    # revoked 后不可再重发
    resp = app_client.post(f"/api/admin/invites/{inv_id}/resend", headers=_auth(admin_token))
    assert resp.status_code == 409


def test_invite_list_filter_and_pagination(app_client, admin_token):
    for i in range(3):
        _mk_pending(f"list{i}-{_run}@x.com")
    resp = app_client.get("/api/admin/invites?status=pending&page=1&page_size=2",
                          headers=_auth(admin_token))
    data = resp.get_json()
    assert data["page_size"] == 2
    assert len(data["items"]) <= 2
    assert data["total"] >= 3
    # pending 列表不携带码明文
    assert all(item["code"] is None for item in data["items"])
