# -*- coding: utf-8 -*-
"""badge 装饰 + Host 路由去重测试（Ship 1 任务 10–12）。

关键约束：
- 未上架站点返回的字节必须与用户产物**完全一致**（既有回归
  test_publish_and_serve_and_unpublish 断言过这一点，badge 只能给已上架站点注入）。
- 复验请求（X-T2C-Verify: 1）不带 badge。
- 找不到插入点时原样返回，绝不因装饰导致站点打不开。
"""
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest
from config import settings
from models import SessionLocal, User
from models.models import PublishedSite, SiteVisitDedup
from utils.security import hash_password

from services.publish.decorate import (
    BADGE_MARKER,
    market_url,
    render_badge,
    should_inject_badge,
)

# ⚠️ slug 必须是 20 位 Crockford Base32，且**不含 I/L/O/U**（字母表排除易混淆字符）。
# 否则 Host 路由按形态判定会直接交回正常路由（落到主站 SPA index.html），
# 测试会假绿成「返回了一个 index.html」而不自知。
SLUG = "MKTBADGE000000000001"
SLUG2 = "MKTBADGE000000000002"
HTML = b"<html><body><h1>hi</h1></body></html>"


@pytest.fixture
def site(app_client, monkeypatch):
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "decorate.test")
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "http")
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(Path(d) / "published"))

    from services.publish.store import LocalFSStore
    LocalFSStore().put_bundle("h" * 64, {"index.html": HTML})

    from services.publish.slug import is_valid_slug
    assert is_valid_slug(SLUG) and is_valid_slug(SLUG2), "测试 slug 必须合法，否则请求会落到主站 SPA"

    db = SessionLocal()
    u = db.query(User).filter(User.username == "mkt_deco").first()
    if u is None:
        u = User(username="mkt_deco", password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    for slug, visible in ((SLUG, True), (SLUG2, False)):
        if db.query(PublishedSite).filter_by(slug=slug).first() is None:
            db.add(PublishedSite(
                slug=slug, user_id=u.id, title="t", current_hash="h" * 64,
                market_visible=visible, badge_enabled=True,
            ))
    db.commit()
    db.close()

    yield
    db = SessionLocal()
    for slug in (SLUG, SLUG2):
        s = db.query(PublishedSite).filter_by(slug=slug).first()
        if s:
            db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == s.id).delete()
            db.delete(s)
    db.commit()
    db.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------- decorate 单元 ----------------

def test_render_badge_inserts_before_body():
    out = render_badge(HTML, url="http://x/market")
    assert BADGE_MARKER.encode() in out
    assert b"<h1>hi</h1>" in out
    assert out.index(BADGE_MARKER.encode()) < out.index(b"</body>")


def test_render_badge_no_anchor_returns_original():
    raw = b"<html><p>no closing tag"
    assert render_badge(raw, url="http://x/market") == raw


def test_render_badge_is_idempotent_under_cache():
    a = render_badge(HTML, url="http://x/market", cache_key="k1")
    b = render_badge(HTML, url="http://x/market", cache_key="k1")
    assert a == b
    # 不同开关下不应共用缓存
    assert render_badge(HTML, url="http://y/market", cache_key="k1") != a


def test_should_inject_matrix():
    base = dict(is_entry=True, market_visible=True, badge_enabled=True, is_verify_request=False)
    assert should_inject_badge(**base) is True
    assert should_inject_badge(**{**base, "market_visible": False}) is False
    assert should_inject_badge(**{**base, "badge_enabled": False}) is False
    assert should_inject_badge(**{**base, "is_verify_request": True}) is False
    assert should_inject_badge(**{**base, "is_entry": False}) is False


def test_market_url_uses_apex(monkeypatch):
    """apex 配好时应拼出 <scheme>://<apex>[:port]/market。

    必须显式 monkeypatch 配置，**不能读环境里的 settings**：CI / 全新检出没有
    `backend/.env`（.gitignore 第 2 行），PUBLISH_APEX 为空 → market_url() 返回
    None → 这里 `.endswith` 直接 AttributeError。本机因为 .env 里写了
    PUBLISH_APEX=localhost 而恒绿，是典型的「本机绿、CI 红」。
    """
    monkeypatch.setattr(settings, "MARKET_SITE_URL", "")
    monkeypatch.setattr(settings, "PUBLISH_APEX", "decorate.test")
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "http")
    monkeypatch.setattr(settings, "PUBLISH_URL_PORT", "5001")
    assert market_url() == "http://decorate.test:5001/market"


def test_market_url_none_when_unconfigured(monkeypatch):
    """未配置 apex 时返回 None，调用方据此跳过 badge 注入（不把站点打坏）。

    这条正是 CI 上的初始状态——补上它，配置缺失的行为才有断言兜着。
    """
    monkeypatch.setattr(settings, "MARKET_SITE_URL", "")
    monkeypatch.setattr(settings, "PUBLISH_APEX", "")
    assert market_url() is None


# ---------------- Host 路由集成 ----------------

def _get(app_client, slug, **extra):
    return app_client.get("/", headers={"Host": f"{slug}.decorate.test", **extra})


def test_listed_site_gets_badge(site, app_client):
    r = _get(app_client, SLUG)
    assert r.status_code == 200
    assert BADGE_MARKER.encode() in r.get_data()


def test_unlisted_site_bytes_unchanged(site, app_client):
    """未上架站点必须返回用户产物原稿 —— 字节级一致。"""
    r = _get(app_client, SLUG2)
    assert r.get_data() == HTML


def test_verify_request_skips_badge(site, app_client):
    r = _get(app_client, SLUG, **{"X-T2C-Verify": "1"})
    assert BADGE_MARKER.encode() not in r.get_data()
    assert r.get_data() == HTML


def test_host_route_dedup_counts_once(site, app_client):
    """同 UA 连续两次访问：view_count +2，独立访客只 +1。"""
    _get(app_client, SLUG)
    _get(app_client, SLUG)
    db = SessionLocal()
    try:
        s = db.query(PublishedSite).filter_by(slug=SLUG).first()
        assert s.view_count == 2
        assert db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == s.id).count() == 1
    finally:
        db.close()


def test_host_route_dedup_distinct_ua(site, app_client):
    _get(app_client, SLUG, **{"User-Agent": "UA-1"})
    _get(app_client, SLUG, **{"User-Agent": "UA-2"})
    db = SessionLocal()
    try:
        s = db.query(PublishedSite).filter_by(slug=SLUG).first()
        assert db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == s.id).count() == 2
    finally:
        db.close()


def test_unlisted_site_has_no_dedup_rows(site, app_client):
    """未上架站点不参与热度排名，不该留账本。"""
    _get(app_client, SLUG2)
    db = SessionLocal()
    try:
        s = db.query(PublishedSite).filter_by(slug=SLUG2).first()
        assert db.query(SiteVisitDedup).filter(SiteVisitDedup.site_id == s.id).count() == 0
    finally:
        db.close()
