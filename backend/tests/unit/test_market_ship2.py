# -*- coding: utf-8 -*-
"""市集 Ship 2 测试：留言 + 关注 / 作者页。

约束：
- 留言不允许匿名（匿名区没有可追责主体）
- 软删而非物理删；只有留言作者或站点作者能删
- 不能关注自己（静默成功会让粉丝数变成自娱自乐的数字）
- 留言进入热度计算（权重 2）
"""
import datetime

import pytest
from models import SessionLocal, User
from models.models import PublishedSite, SiteComment, UserFollow
from utils.security import hash_password

from services.market.comments import too_frequent, validate_body
from services.market.heat import compute_heat

# ⚠️ 20 位 Crockford Base32，且**不含 I/L/O/U**（字母表排除易混淆字符）。
# 否则 Host 路由按形态判定会交回正常路由，请求落到主站 SPA，测试假绿。
SLUG = "MKTSP200000000000001"
OTHER = "MKTSP200000000000002"


def _ensure_user(db, name):
    u = db.query(User).filter(User.username == name).first()
    if u is None:
        u = User(username=name, password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u.id


@pytest.fixture
def env(app_client):
    from services.publish.slug import is_valid_slug
    assert is_valid_slug(SLUG) and is_valid_slug(OTHER), "测试 slug 必须合法"

    db = SessionLocal()
    owner = _ensure_user(db, "mkt_s2_owner")
    other = _ensure_user(db, "mkt_s2_other")
    db.query(SiteComment).delete()
    db.query(UserFollow).delete()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([SLUG, OTHER])).delete()
    db.commit()
    db.add(PublishedSite(
        slug=SLUG, user_id=owner, title="作品",
        market_visible=True, market_listed_at=datetime.datetime.utcnow(),
    ))
    db.add(PublishedSite(slug=OTHER, user_id=owner, title="未上架", market_visible=False))
    db.commit()
    db.close()

    yield {"owner": owner, "other": other, "slug": SLUG, "hidden": OTHER}

    db = SessionLocal()
    db.query(SiteComment).delete()
    db.query(UserFollow).delete()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([SLUG, OTHER])).delete()
    db.commit()
    db.close()


def _login(app_client, name):
    r = app_client.post("/api/login", json={"username": name, "password": "test123456"})
    assert r.status_code == 200


# ---------------- 校验 ----------------

def test_validate_body():
    assert validate_body("") is not None
    assert validate_body("   ") is not None
    assert validate_body("x" * 201) is not None
    assert validate_body("不错的作品") is None


def test_too_frequent_threshold():
    assert too_frequent(9) is False
    assert too_frequent(10) is True


# ---------------- 留言 ----------------

def test_comment_requires_auth(env, app_client):
    r = app_client.post(f"/api/market/sites/{env['slug']}/comments", json={"body": "hi"})
    assert r.status_code == 401


def test_comment_list_is_public(env, app_client):
    r = app_client.get(f"/api/market/sites/{env['slug']}/comments")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_add_and_list_comment(env, app_client):
    _login(app_client, "mkt_s2_other")
    r = app_client.post(f"/api/market/sites/{env['slug']}/comments", json={"body": "做得不错"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["comment_count"] == 1

    lst = app_client.get(f"/api/market/sites/{env['slug']}/comments").get_json()
    assert lst["total"] == 1
    assert lst["items"][0]["body"] == "做得不错"
    assert lst["items"][0]["author"] == "mkt_s2_other"
    assert lst["items"][0]["mine"] is True


def test_comment_unlisted_site_404(env, app_client):
    _login(app_client, "mkt_s2_other")
    assert app_client.post(
        f"/api/market/sites/{env['hidden']}/comments", json={"body": "x"}
    ).status_code == 404


def test_comment_soft_delete_by_author(env, app_client):
    _login(app_client, "mkt_s2_other")
    cid = app_client.post(
        f"/api/market/sites/{env['slug']}/comments", json={"body": "我来留个言"}
    ).get_json()["id"]
    r = app_client.delete(f"/api/market/sites/{env['slug']}/comments/{cid}")
    assert r.status_code == 200
    # 软删：行还在，但列表里看不见
    db = SessionLocal()
    try:
        assert db.get(SiteComment, cid).is_deleted is True
    finally:
        db.close()
    assert app_client.get(f"/api/market/sites/{env['slug']}/comments").get_json()["total"] == 0


def test_site_owner_can_delete_others_comment(env, app_client):
    _login(app_client, "mkt_s2_other")
    cid = app_client.post(
        f"/api/market/sites/{env['slug']}/comments", json={"body": "广告内容"}
    ).get_json()["id"]
    # 站点作者要能清理自己作品下的内容
    _login(app_client, "mkt_s2_owner")
    assert app_client.delete(f"/api/market/sites/{env['slug']}/comments/{cid}").status_code == 200


def test_third_party_cannot_delete(env, app_client):
    db = SessionLocal()
    third = _ensure_user(db, "mkt_s2_third")
    db.close()
    _login(app_client, "mkt_s2_other")
    cid = app_client.post(
        f"/api/market/sites/{env['slug']}/comments", json={"body": "留言"}
    ).get_json()["id"]
    _login(app_client, "mkt_s2_third")
    r = app_client.delete(f"/api/market/sites/{env['slug']}/comments/{cid}")
    assert r.status_code == 403


def test_comment_enters_heat(env, app_client):
    """留言权重 2：两条留言的热度增量应等于 4 个独立访客。"""
    assert compute_heat(0, 0, 2, 0) == compute_heat(4, 0, 0, 0)
    assert compute_heat(0, 0, 1, 0) == compute_heat(2, 0, 0, 0)


# ---------------- 关注 ----------------

def test_follow_requires_auth(env, app_client):
    assert app_client.post(f"/api/market/users/{env['owner']}/follow").status_code == 401


def test_follow_and_unfollow(env, app_client):
    _login(app_client, "mkt_s2_other")
    r = app_client.post(f"/api/market/users/{env['owner']}/follow")
    assert r.status_code == 200
    assert r.get_json()["followers"] == 1
    # 重复关注不翻倍
    assert app_client.post(f"/api/market/users/{env['owner']}/follow").get_json()["followers"] == 1

    r2 = app_client.delete(f"/api/market/users/{env['owner']}/follow")
    assert r2.get_json()["followers"] == 0


def test_cannot_follow_self(env, app_client):
    _login(app_client, "mkt_s2_owner")
    r = app_client.post(f"/api/market/users/{env['owner']}/follow")
    assert r.status_code == 400


def test_author_page(env, app_client):
    r = app_client.get(f"/api/market/users/{env['owner']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["username"] == "mkt_s2_owner"
    assert body["site_count"] == 1          # 只算已上架
    assert body["followed_by_me"] is False

    _login(app_client, "mkt_s2_other")
    app_client.post(f"/api/market/users/{env['owner']}/follow")
    body = app_client.get(f"/api/market/users/{env['owner']}").get_json()
    assert body["followed_by_me"] is True
    assert body["followers"] == 1
    assert body["following"] == 0


def test_author_page_unknown_404(env, app_client):
    assert app_client.get("/api/market/users/99999999").status_code == 404


def test_detail_exposes_follow_state(env, app_client):
    d = app_client.get(f"/api/market/sites/{env['slug']}").get_json()
    assert d["following_author"] is False
    assert d["author_followers"] == 0
    _login(app_client, "mkt_s2_other")
    app_client.post(f"/api/market/users/{env['owner']}/follow")
    d = app_client.get(f"/api/market/sites/{env['slug']}").get_json()
    assert d["following_author"] is True
    assert d["author_followers"] == 1
