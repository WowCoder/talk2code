# -*- coding: utf-8 -*-
"""运营指标接口测试。

口径断言：
- 完成率 = finished / (finished + failed)，在途（pending/processing）不计入分母
- 演示帐号不计入用户指标
- 热度榜与前台 compute_heat 数值一致（同一公式，不另造）
"""
from datetime import datetime, timedelta

import pytest

from sqlalchemy import func
from models import SessionLocal, User, Requirement
from models.models import AdminUser, PublishedSite, SiteVisitDedup, SiteLike
from services.market.heat import compute_heat, display_heat
from utils.security import hash_password

_run = __import__("uuid").uuid4().hex[:8]  # 隔离共享测试库中的残留数据


@pytest.fixture
def admin_token(app_client):
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "metadmin").delete()
    db.add(AdminUser(username="metadmin", password_hash=hash_password("metpass123")))
    db.commit()
    resp = app_client.post("/api/admin/login", json={
        "username": "metadmin", "password": "metpass123"})
    return resp.get_json()["token"]


def _mk_user(db, name):
    u = db.query(User).filter(User.username == name).first()
    if u is None:
        u = User(username=name, password_hash=hash_password("x12345678"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


def _mk_req(db, uid, status, title="t", **kw):
    r = Requirement(user_id=uid, title=title, content="c", status=status, **kw)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_metrics_shape_and_completion_rate(app_client, admin_token):
    db = SessionLocal()
    u = _mk_user(db, "metric_user1")
    # 清理该用户上一轮残留，完成率改用「前后增量」口径断言，
    # 不受库中其它测试遗留的 finished/failed 影响
    before = dict(
        db.query(Requirement.status, func.count(Requirement.id))
        .filter(Requirement.is_deleted.is_(False))
        .group_by(Requirement.status).all()
    )
    f0, fa0 = before.get("finished", 0), before.get("failed", 0)
    _mk_req(db, u.id, "finished")
    _mk_req(db, u.id, "failed")
    _mk_req(db, u.id, "processing")  # 在途，不计入完成率分母

    resp = app_client.get("/api/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.get_json()

    reqs = data["requirements"]
    assert reqs["status"]["finished"] == f0 + 1
    assert reqs["status"]["failed"] == fa0 + 1
    # 完成率只看「已有结论」的需求：(f0+1) / (f0+1 + fa0+1)。后端 round 到 4 位
    assert abs(reqs["completion_rate"] - (f0 + 1) / (f0 + fa0 + 2)) < 1e-4

    assert set(data.keys()) >= {"users", "requirements", "publish", "invites", "top_heat", "observability"}


def test_metrics_exclude_demo_user(app_client, admin_token):
    db = SessionLocal()
    demo = db.query(User).filter(User.username == "demo").first()
    if demo is None:
        demo = User(username="demo", password_hash=hash_password("demoxyz123"))
        db.add(demo)
        db.commit()

    resp = app_client.get("/api/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    data = resp.get_json()
    # 用户总数不含演示帐号
    db2 = SessionLocal()
    total_all = db2.query(User).count()
    assert data["users"]["total"] == total_all - 1


def test_top_heat_matches_market_formula(app_client, admin_token):
    db = SessionLocal()
    u = _mk_user(db, "heat_user1")
    req = _mk_req(db, u.id, "finished", title="热度榜作品")
    # 清理本用例（含历史轮次）留下的所有 HEAT 前缀站点 —— 本用例会创建
    # market_visible=True 的站点，若残留在共享测试库里会污染市集列表类
    # 测试的计数断言（test_market_routes 假设库里只有它自己造的站点）
    for (sid,) in db.query(PublishedSite.id).filter(
            PublishedSite.slug.like("HEAT%")).all():
        db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == sid).delete()
        db.query(SiteLike).filter(SiteLike.site_id == sid).delete()
    db.query(PublishedSite).filter(PublishedSite.slug.like("HEAT%")).delete()
    db.commit()

    site = PublishedSite(
        slug=f"HEAT{_run}0000000001", user_id=u.id, requirement_id=req.id,
        title="热度榜作品", market_visible=True,
        market_listed_at=datetime.utcnow() - timedelta(hours=10),
        view_count=5,
    )
    db.add(site)
    db.commit()
    db.refresh(site)

    uniq = 3
    for i in range(uniq):
        db.add(SiteVisitDedup(site_id=site.id, fingerprint=f"heatfp{site.id}{i}",
                              day=datetime.utcnow().date()))
    db.commit()

    likes = 2
    for i in range(likes):
        liker = _mk_user(db, f"heat_liker_{i}_{site.id}")
        db.add(SiteLike(site_id=site.id, user_id=liker.id))
    db.commit()

    try:
        resp = app_client.get("/api/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
        top = resp.get_json()["top_heat"]
        entry = next(e for e in top if e["slug"] == f"HEAT{_run}0000000001")

        assert entry["likes"] == 2
        assert entry["views"] == 3
        # 与前台同一公式（时间差存在毫秒级漂移，用 raw 值做小容差比较）
        expected = compute_heat(3, 2, 0, 10)
        assert abs(entry["heat_raw"] - expected) < 0.5
        assert entry["heat"] == display_heat(entry["heat_raw"])
    finally:
        # 用例结束即拆除，不留对市集列表测试可见的痕迹
        db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == site.id).delete()
        db.query(SiteLike).filter(SiteLike.site_id == site.id).delete()
        db.query(PublishedSite).filter(PublishedSite.id == site.id).delete()
        db.commit()


def test_metrics_requires_admin(app_client):
    assert app_client.get("/api/admin/metrics").status_code == 401
