# -*- coding: utf-8 -*-
"""创意市集路由测试（Ship 1 任务 6–9）。

覆盖不可回退约束：
- 未上架站点绝不在列表出现，详情 404
- 列表 / 详情公开可访问（免登录）
- 自赞 400（不静默成功）
- 响应不含 view_count / verify_status（既有 UI 暴露边界约定）
"""
from datetime import datetime, timedelta

import pytest
from models import SessionLocal, User
from models.models import PublishedSite, SiteLike, SiteVisitDedup
from utils.security import hash_password

from services.publish.slug import new_slug

SLUG_A = "MKTA0000000000000001"
SLUG_B = "MKTB0000000000000002"


def _ensure_user(db, name: str) -> int:
    u = db.query(User).filter(User.username == name).first()
    if u is None:
        u = User(username=name, password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u.id


def _mk_site(db, slug, user_id, **kw):
    s = PublishedSite(slug=slug, user_id=user_id, title=kw.pop("title", "t"), **kw)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def mkt(app_client):
    """两个用户 + 两个站点（一个已上架、一个未上架）。"""
    db = SessionLocal()
    uid_a = _ensure_user(db, "mkt_owner")
    uid_b = _ensure_user(db, "mkt_other")
    db.query(SiteVisitDedup).delete()
    db.query(SiteLike).delete()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([SLUG_A, SLUG_B])).delete()
    db.commit()
    listed = _mk_site(
        db, SLUG_A, uid_a,
        market_visible=True, market_listed_at=datetime.utcnow(), title="上架作品",
    )
    hidden = _mk_site(db, SLUG_B, uid_a, market_visible=False, title="未上架作品")
    db.close()

    yield {"owner": uid_a, "other": uid_b, "listed": SLUG_A, "hidden": SLUG_B,
           "listed_id": listed.id, "hidden_id": hidden.id}

    db = SessionLocal()
    db.query(SiteVisitDedup).delete()
    db.query(SiteLike).delete()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([SLUG_A, SLUG_B])).delete()
    db.commit()
    db.close()


def _login(app_client, name):
    r = app_client.post("/api/login", json={"username": name, "password": "test123456"})
    assert r.status_code == 200


# ---------------- 列表（任务 6） ----------------

def test_list_is_public(mkt, app_client):
    r = app_client.get("/api/market/sites")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == mkt["listed"]


def test_list_excludes_unlisted(mkt, app_client):
    """不可回退约束：未上架站点绝不出现在市集列表。"""
    body = app_client.get("/api/market/sites").get_json()
    assert mkt["hidden"] not in [i["slug"] for i in body["items"]]
    assert body["total"] == 1


def test_list_card_has_no_view_count_or_verify_status(mkt, app_client):
    """既有 UI 暴露边界约定：view_count 可刷，verify_status 是纯负向信号。"""
    item = app_client.get("/api/market/sites").get_json()["items"][0]
    assert "view_count" not in item
    assert "verify_status" not in item
    assert set(item.keys()) >= {"slug", "title", "author", "heat", "like_count", "url", "liked"}


def test_list_sort_new_uses_listed_at(mkt, app_client):
    db = SessionLocal()
    try:
        older = db.query(PublishedSite).filter_by(slug=mkt["listed"]).first()
        older.market_listed_at = datetime.utcnow() - timedelta(days=10)
        db.commit()
    finally:
        db.close()
    body = app_client.get("/api/market/sites?sort=new").get_json()
    assert body["items"][0]["slug"] == mkt["listed"]


# ---------------- 详情（任务 7） ----------------

def test_detail_public(mkt, app_client):
    r = app_client.get(f"/api/market/sites/{mkt['listed']}")
    assert r.status_code == 200
    assert r.get_json()["author"] == "mkt_owner"


def test_detail_unlisted_404(mkt, app_client):
    assert app_client.get(f"/api/market/sites/{mkt['hidden']}").status_code == 404


def test_detail_invalid_slug_404(mkt, app_client):
    assert app_client.get("/api/market/sites/nope").status_code == 404


# ---------------- 点赞（任务 8） ----------------

def test_like_requires_auth(mkt, app_client):
    r = app_client.post(f"/api/market/sites/{mkt['listed']}/like")
    assert r.status_code == 401


def test_self_like_returns_400(mkt, app_client):
    """静默成功是假绿 —— 必须显式报错。"""
    _login(app_client, "mkt_owner")
    r = app_client.post(f"/api/market/sites/{mkt['listed']}/like")
    assert r.status_code == 400
    assert "自己" in r.get_json()["error"]


def test_like_twice_counts_once(mkt, app_client):
    _login(app_client, "mkt_other")
    slug = mkt["listed"]
    r1 = app_client.post(f"/api/market/sites/{slug}/like")
    assert r1.status_code == 200
    app_client.post(f"/api/market/sites/{slug}/like")
    assert app_client.get(f"/api/market/sites/{slug}").get_json()["like_count"] == 1


def test_like_then_unlike(mkt, app_client):
    _login(app_client, "mkt_other")
    slug = mkt["listed"]
    app_client.post(f"/api/market/sites/{slug}/like")
    r = app_client.delete(f"/api/market/sites/{slug}/like")
    assert r.status_code == 200
    assert app_client.get(f"/api/market/sites/{slug}").get_json()["like_count"] == 0


def test_liked_flag_for_viewer(mkt, app_client):
    slug = mkt["listed"]
    assert app_client.get(f"/api/market/sites/{slug}").get_json()["liked"] is False
    _login(app_client, "mkt_other")
    app_client.post(f"/api/market/sites/{slug}/like")
    assert app_client.get(f"/api/market/sites/{slug}").get_json()["liked"] is True


def test_like_unlisted_site_404(mkt, app_client):
    _login(app_client, "mkt_other")
    assert app_client.post(f"/api/market/sites/{mkt['hidden']}/like").status_code == 404


# ---------------- 上架开关（任务 9） ----------------

def test_owner_can_list_and_unlist(mkt, app_client):
    _login(app_client, "mkt_owner")
    slug = mkt["hidden"]
    r = app_client.patch(f"/api/publish/{slug}/market", json={"listed": True, "author_note": "一句话"})
    assert r.status_code == 200
    assert r.get_json()["listed"] is True
    assert r.get_json()["author_note"] == "一句话"
    assert app_client.get("/api/market/sites").get_json()["total"] == 2

    r2 = app_client.patch(f"/api/publish/{slug}/market", json={"listed": False})
    assert r2.get_json()["listed"] is False
    assert r2.get_json()["listed_at"] is None   # 撤下要清空，再上架才重新计时
    assert app_client.get("/api/market/sites").get_json()["total"] == 1


def test_other_user_cannot_change_listing(mkt, app_client):
    _login(app_client, "mkt_other")
    r = app_client.patch(f"/api/publish/{mkt['listed']}/market", json={"listed": False})
    assert r.status_code == 404


def test_relisting_refreshes_listed_at(mkt, app_client):
    _login(app_client, "mkt_owner")
    slug = mkt["hidden"]
    app_client.patch(f"/api/publish/{slug}/market", json={"listed": True})
    first = app_client.get(f"/api/market/sites/{slug}").get_json()["listed_at"]
    app_client.patch(f"/api/publish/{slug}/market", json={"listed": False})
    app_client.patch(f"/api/publish/{slug}/market", json={"listed": True})
    second = app_client.get(f"/api/market/sites/{slug}").get_json()["listed_at"]
    assert second >= first
