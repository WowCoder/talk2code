# -*- coding: utf-8 -*-
"""市集数据模型测试（Ship 1 任务 1）。

不可回退约束：上架是 opt-in —— `market_visible` 必须默认 False。
现状所有站点都是 unlisted，用户的预期是"我只是在分享一个链接"，
默认搬进公共列表是对既有预期的背叛。
"""
import pytest
from models import SessionLocal, User
from models.models import PublishedSite, SiteLike, SiteVisitDedup
from utils.security import hash_password


@pytest.fixture
def db_user(app_client):
    """造一个独立用户，用例结束后连同其站点一起清掉（持久测试库会累积）。"""
    db = SessionLocal()
    u = db.query(User).filter(User.username == "mkt_model").first()
    if u is None:
        u = User(username="mkt_model", password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    uid = u.id
    db.close()
    yield uid
    db = SessionLocal()
    db.query(SiteVisitDedup).filter(
        SiteVisitDedup.site_id.in_(db.query(PublishedSite.id).filter(PublishedSite.user_id == uid))
    ).delete(synchronize_session=False)
    db.query(SiteLike).filter(
        SiteLike.site_id.in_(db.query(PublishedSite.id).filter(PublishedSite.user_id == uid))
    ).delete(synchronize_session=False)
    db.query(PublishedSite).filter(PublishedSite.user_id == uid).delete()
    db.commit()
    db.close()


def _new_site(db, user_id: int, slug: str) -> PublishedSite:
    s = PublishedSite(slug=slug, user_id=user_id, title="t")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_market_visible_defaults_false(db_user):
    db = SessionLocal()
    try:
        site = _new_site(db, db_user, "MKTVISDEFAULT000001")
        assert site.market_visible is False
        assert site.market_listed_at is None
    finally:
        db.close()


def test_badge_enabled_defaults_true(db_user):
    db = SessionLocal()
    try:
        site = _new_site(db, db_user, "MKTBADGEDEFAULT0001")
        assert site.badge_enabled is True
        assert site.author_note == ""
    finally:
        db.close()


def test_visit_dedup_unique_constraint(db_user):
    """同一 (site, fingerprint, day) 只能有一行 —— 唯一约束即去重实现。"""
    import datetime
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        site = _new_site(db, db_user, "MKTDEDUPUNIQ000001")
        day = datetime.date(2026, 9, 25)
        db.add(SiteVisitDedup(site_id=site.id, fingerprint="a" * 32, day=day))
        db.commit()
        db.add(SiteVisitDedup(site_id=site.id, fingerprint="a" * 32, day=day))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # 换一天可以再记（按天分桶，天然过期）
        db.add(SiteVisitDedup(site_id=site.id, fingerprint="a" * 32, day=datetime.date(2026, 9, 26)))
        db.commit()
    finally:
        db.close()


def test_site_like_unique_constraint(db_user):
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        site = _new_site(db, db_user, "MKTLIKEUNIQ0000001")
        db.add(SiteLike(site_id=site.id, user_id=db_user))
        db.commit()
        db.add(SiteLike(site_id=site.id, user_id=db_user))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.close()
