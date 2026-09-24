# -*- coding: utf-8 -*-
"""发布路由功能测试（plan B7 / B8）。

app_client 用 :memory: SQLite（单线程 SingletonThreadPool 共享连接），
故可在用例内跨请求共享 DB 状态。Ship C 复验用 monkeypatch 避开 Chromium。

覆盖：
- 未登录 401（B7 鉴权门禁）
- 登录后发布需求 → 200 + slug/version
- Host 路由按 <slug>.<apex> 返回静态产物 + noindex（B8）
- 查询 info / 取消发布 / 他人 slug 404（B7 归属校验）
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from config import settings
from models import Requirement, SessionLocal, User
from utils.security import hash_password

from services.publish.slug import new_slug
from services.publish import verify as verify_mod


SAMPLE_FILES = [
    {"filename": "index.html", "content": "<html><body>hi</body></html>"},
    {"filename": "app.js", "content": "console.log(1)"},
]


@pytest.fixture
def pub_env(app_client, auth_token, monkeypatch):
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "publish.test")
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(Path(d) / "published"))
    # 避开 Chromium：复验恒为真
    monkeypatch.setattr(verify_mod, "_run_verification", lambda *a, **k: True)

    # 取出登录用户并造一条需求
    db = SessionLocal()
    user = db.query(User).filter(User.username == "test_func").first()
    req = Requirement(
        user_id=user.id,
        title="demo",
        content="x",
        status="finished",
        code_files=SAMPLE_FILES,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    req_id = req.id
    db.close()

    yield app_client, req_id, d
    shutil.rmtree(d, ignore_errors=True)


def test_publish_requires_auth(app_client):
    r = app_client.post("/api/publish", json={"requirement_id": 1})
    assert r.status_code == 401


def test_publish_info_requires_auth(app_client):
    r = app_client.get(f"/api/publish/{new_slug()}/info")
    assert r.status_code == 401


def test_unpublish_requires_auth(app_client):
    r = app_client.post(f"/api/publish/{new_slug()}/unpublish")
    assert r.status_code == 401


def test_publish_and_serve_and_unpublish(pub_env):
    app_client, req_id, d = pub_env

    # 1) 发布
    r = app_client.post("/api/publish", json={"requirement_id": req_id})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    slug = body["slug"]
    assert len(slug) == 20
    assert body["version"] == 1
    assert body["verify_status"] == "ok"  # 复验被 mock 为通过

    # 2) Host 路由返回静态产物（B8）
    r2 = app_client.get("/", headers={"Host": f"{slug}.publish.test"})
    assert r2.status_code == 200, r2.status_code
    assert r2.get_data(as_text=True) == SAMPLE_FILES[0]["content"]
    assert r2.headers.get("X-Robots-Tag") == "noindex"
    assert r2.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" not in (r2.headers.get("Content-Security-Policy") or "")

    # 3) 查询 info
    r3 = app_client.get(f"/api/publish/{slug}/info")
    assert r3.status_code == 200
    assert r3.get_json()["version"] == 1

    # 4) 取消发布 → Host 路由 404，info 404
    r4 = app_client.post(f"/api/publish/{slug}/unpublish")
    assert r4.status_code == 200
    assert app_client.get("/", headers={"Host": f"{slug}.publish.test"}).status_code == 404
    assert app_client.get(f"/api/publish/{slug}/info").status_code == 404


def test_publish_unknown_requirement_404(pub_env):
    app_client, req_id, d = pub_env
    r = app_client.post("/api/publish", json={"requirement_id": 999999})
    assert r.status_code == 404


def test_publish_other_user_slug_404(pub_env, monkeypatch):
    app_client, req_id, d = pub_env
    # 先发布拿到 slug（属于 test_func）
    r = app_client.post("/api/publish", json={"requirement_id": req_id})
    slug = r.get_json()["slug"]

    # 造第二个用户，登录拿其 cookie，查询他人 slug → 404
    db = SessionLocal()
    if db.query(User).filter(User.username == "other_func").first() is None:
        db.add(User(username="other_func", password_hash=hash_password("test123456")))
        db.commit()
    db.close()
    app_client.post("/api/login", json={"username": "other_func", "password": "test123456"})
    r2 = app_client.get(f"/api/publish/{slug}/info")
    assert r2.status_code == 404
