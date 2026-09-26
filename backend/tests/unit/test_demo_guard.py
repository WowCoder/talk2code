# -*- coding: utf-8 -*-
"""演示模式守卫测试。

覆盖核心安全约束：
- demo JWT 的写请求默认 403（demo_readonly）
- 白名单（login/logout/register/邀请码申请）放行
- 普通 JWT 不被守卫误伤
- GET 一律放行
- 新增未白名单写接口自动被拦（默认拒绝，防漏）
- 用密码登录演示帐号 = 完整权限（claim 里没有 demo）
"""
import pytest

from models import SessionLocal, User, InviteCode
from utils.security import hash_password
from datetime import datetime, timedelta

_run = __import__("uuid").uuid4().hex[:8]  # 每次运行唯一后缀，隔离共享测试库中的残留数据

# 模块导入期注册一个白名单外的「未来接口」：Flask 禁止应用处理过请求后再
# 注册路由，而同一 app 对象在其它测试里早已处理过请求。
from factory import app as _app  # noqa: E402


@_app.route("/api/future-feature", methods=["POST"])
def _future_feature():  # pragma: no cover - 演示守卫应在进入 handler 前拦截
    return {"ok": True}, 200


@pytest.fixture
def demo_user():
    """预置演示帐号（生产环境由 manage.py demo init 创建）。"""
    db = SessionLocal()
    u = db.query(User).filter(User.username == "demo").first()
    if u is None:
        u = User(username="demo", password_hash=hash_password("seed-demo-pw"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


def _mk_invite(code: str) -> None:
    """造一个可用邀请码（注册接口已要求邀请码必填）。"""
    db = SessionLocal()
    db.query(InviteCode).filter(InviteCode.code == code).delete()
    db.add(InviteCode(
        code=code,
        applicant_email="t@t.com",
        applicant_phone="13800000000",
        status="issued",
        expires_at=datetime.utcnow() + timedelta(days=1),
        delivery_status="skipped",
    ))
    db.commit()


def _register(client, username: str, code: str = "DEMOGUARDCODE1") -> None:
    _mk_invite(code)
    username = f"{username}_{_run}"
    db = SessionLocal()
    old = db.query(User).filter(User.username == username).first()
    if old:
        db.delete(old)
        db.commit()
    resp = client.post("/api/register", json={
        "username": username, "password": "test123456", "invite_code": code,
    })
    assert resp.status_code == 201, resp.get_json()


def _login(client, username: str, password: str = "test123456"):
    return client.post("/api/login", json={"username": username, "password": password})


def _enter_demo(client):
    resp = client.post("/api/demo/enter")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ---------------- 演示进入 ----------------

def test_demo_enter_returns_is_demo(app_client, demo_user):
    """进入演示模式后，/api/user/info 标记 is_demo。"""
    data = _enter_demo(app_client)
    assert data["user"]["is_demo"] is True

    info = app_client.get("/api/user/info").get_json()
    assert info["user"]["is_demo"] is True


def test_demo_enter_requires_init(app_client):
    """演示帐号未初始化时返回 404 + CLI 提示（不做隐式建号）。"""
    db = SessionLocal()
    db.query(User).filter(User.username == "demo").delete()
    db.commit()
    resp = app_client.post("/api/demo/enter")
    assert resp.status_code == 404
    assert "manage.py" in resp.get_json().get("hint", "")


def test_normal_user_is_not_demo(app_client, demo_user):
    _register(app_client, "notdemo_user")
    _login(app_client, "notdemo_user")
    info = app_client.get("/api/user/info").get_json()
    assert info["user"]["is_demo"] is False


def test_demo_login_with_password_has_write_rights(app_client, demo_user):
    """用密码登录演示帐号 = 完整权限（claim 里没有 demo=true）。"""
    db = SessionLocal()
    u = db.query(User).filter(User.username == "demo").first()
    u.password_hash = hash_password("demopass123")
    db.commit()

    resp = _login(app_client, "demo", "demopass123")
    assert resp.status_code == 200
    # 写请求不被拦（没有 demo claim）—— 用一个必失败的业务校验而非 403 来证明
    resp = app_client.post("/api/requirements", json={"content": "x"})
    assert resp.status_code != 403


# ---------------- 守卫：默认拒绝 ----------------

def test_demo_write_blocked_on_requirements(app_client, demo_user):
    _enter_demo(app_client)
    resp = app_client.post("/api/requirements", json={"content": "做一个贪吃蛇"})
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "demo_readonly"


def test_demo_market_like_blocked(app_client, demo_user):
    _enter_demo(app_client)
    resp = app_client.post("/api/market/sites/whatever/like")
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "demo_readonly"


def test_demo_new_write_endpoint_auto_denied(app_client, demo_user):
    """默认拒绝的核心验证：白名单外的新写接口，demo 立即被拦。

    路由必须在模块导入期注册 —— Flask 不允许应用处理过请求后再 add route，
    而同一个 app 对象在本进程的其它测试里早已处理过请求。
    """
    _enter_demo(app_client)
    resp = app_client.post("/api/future-feature")
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "demo_readonly"


# ---------------- 守卫：白名单 ----------------

def test_demo_can_logout(app_client, demo_user):
    _enter_demo(app_client)
    assert app_client.post("/api/logout").status_code == 200


def test_demo_can_register_and_invite_request(app_client, demo_user):
    """演示 → 注册的转化路径必须通：守卫不拦 register / 邀请码申请。"""
    _enter_demo(app_client)
    # 无码注册：业务层 400（非守卫 403）
    resp = app_client.post("/api/register", json={"username": "xx", "password": "yy123456"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] != "演示模式为只读，不可修改数据"

    resp = app_client.post("/api/invite/requests", json={
        "email": f"want-{_run}@in.com", "phone": "13800000001", "note": "想试试",
    })
    assert resp.status_code == 201


# ---------------- 守卫：不误伤 ----------------

def test_normal_user_write_not_blocked(app_client, demo_user):
    _register(app_client, "writer_user")
    _login(app_client, "writer_user")
    # 内容过短是业务校验（400），只要不是 403 就说明守卫没误伤
    resp = app_client.post("/api/requirements", json={"content": "x"})
    assert resp.status_code != 403


def test_anonymous_write_not_guarded(app_client):
    """无 token 时守卫放行，由路由层 @jwt_required 返回 401/422。"""
    resp = app_client.post("/api/requirements", json={"content": "x"})
    assert resp.status_code in (401, 422)


def test_demo_get_allowed(app_client, demo_user):
    _enter_demo(app_client)
    assert app_client.get("/api/requirements").status_code == 200
    assert app_client.get("/api/market/sites").status_code == 200


def test_admin_token_not_blocked_by_demo_guard(app_client, demo_user):
    """管理员 header token 与 demo cookie 并存时，守卫按 header token 判断放行。"""
    from models.models import AdminUser
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "guardadmin").delete()
    db.add(AdminUser(username="guardadmin", password_hash=hash_password("adminpass1")))
    db.commit()

    _enter_demo(app_client)
    resp = app_client.post("/api/admin/login", json={
        "username": "guardadmin", "password": "adminpass1"})
    token = resp.get_json()["token"]
    resp = app_client.get("/api/admin/invites", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
