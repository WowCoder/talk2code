# -*- coding: utf-8 -*-
"""访客指纹与去重账本测试（Ship 1 任务 3）+ 热度算法（任务 4）。"""
import datetime

import pytest
from models import SessionLocal, User
from models.models import PublishedSite, SiteVisitDedup
from utils.security import hash_password

from services.market.heat import compute_heat, display_heat
from services.market.visits import record_visit, visitor_fingerprint


@pytest.fixture
def site(app_client):
    db = SessionLocal()
    u = db.query(User).filter(User.username == "mkt_visit").first()
    if u is None:
        u = User(username="mkt_visit", password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    s = PublishedSite(slug="MKTVISIT00000000001", user_id=u.id, title="t")
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    yield sid
    db = SessionLocal()
    db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == sid).delete()
    db.query(PublishedSite).filter(PublishedSite.id == sid).delete()
    db.commit()
    db.close()


def test_fingerprint_is_stable_and_opaque():
    a = visitor_fingerprint("1.2.3.4", "Mozilla/5.0", "2026-09-25")
    b = visitor_fingerprint("1.2.3.4", "Mozilla/5.0", "2026-09-25")
    c = visitor_fingerprint("1.2.3.5", "Mozilla/5.0", "2026-09-25")
    assert a == b
    assert a != c
    assert len(a) == 32
    # 明文不出现在指纹里（只存哈希，不存 IP / UA）
    assert "1.2.3.4" not in a
    assert "Mozilla" not in a


def test_record_visit_dedup_same_day(site):
    db = SessionLocal()
    try:
        day = datetime.date(2026, 9, 25)
        fp = visitor_fingerprint("1.2.3.4", "UA", day.isoformat())
        assert record_visit(db, site, fp, day) is True
        db.commit()
        assert record_visit(db, site, fp, day) is False
        db.commit()
        rows = db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == site).all()
        assert len(rows) == 1
        # 换一天算新的一次（按天分桶）
        next_day = datetime.date(2026, 9, 26)
        assert record_visit(db, site, fp, next_day) is True
        db.commit()
    finally:
        db.close()


def test_record_visit_savepoint_keeps_outer_transaction(site):
    """重复插入触发唯一约束时，只回滚 SAVEPOINT —— 外层同事务的其它写入要保住。

    这是 Host 路由的关键：view_count 自增和去重 INSERT 在同一个事务里，
    若整事务回滚，访问数会被一起吞掉。
    """
    db = SessionLocal()
    try:
        from models.models import SiteVisitDedup as _D
        day = datetime.date(2026, 9, 25)
        fp = visitor_fingerprint("9.9.9.9", "UA", day.isoformat())
        assert record_visit(db, site, fp, day) is True
        # 外键存在即证明外层事务未回滚（rollback 会连带丢弃上面的 INSERT）
        assert db.query(_D).filter(_D.site_id == site).count() == 1
        assert record_visit(db, site, fp, day) is False
        assert db.query(_D).filter(_D.site_id == site).count() == 1
        db.commit()
    finally:
        db.close()


# ---------------- 热度（任务 4） ----------------

def test_heat_weights_like_above_view():
    """同样 1 个信号，点赞的热度必须高于浏览。"""
    assert compute_heat(0, 1, 0, 0) > compute_heat(1, 0, 0, 0)


def test_heat_decays_over_time():
    now = compute_heat(10, 0, 0, 0)
    later = compute_heat(10, 0, 0, 24)
    assert later < now


def test_heat_scale_positive_for_fresh_site():
    assert compute_heat(1, 0, 0, 0) > 0
    assert display_heat(0.0) == 1


def test_heat_never_negative_hours():
    """上架时间异常（未来时间）不得产生负小时数导致分数放大。"""
    assert compute_heat(1, 0, 0, -5) == compute_heat(1, 0, 0, 0)
