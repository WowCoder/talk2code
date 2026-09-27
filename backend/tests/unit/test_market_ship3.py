# -*- coding: utf-8 -*-
"""市集 Ship 3 测试：分类筛选 + 周榜 + 缩略图端点。

约束：
- 非法分类 / 非法窗口一律当作「不过滤」，不把公开列表打成 400
- 缩略图按 content_hash 缓存；截不到是 404，前端回退色块，不影响列表
"""
import datetime

import pytest
from models import SessionLocal, User
from models.models import PublishedSite
from utils.security import hash_password

from services.market.service import CATEGORIES, normalize_category

# 注：缩略图用例统一在函数内 `import services.market.thumbs as t` 再 monkeypatch
# 缓存目录，故这里不再顶层导入 get_thumb / thumb_path（避免未使用导入）。

# ⚠️ 20 位 Crockford Base32，且**不含 I/L/O/U**（字母表排除易混淆字符）。
# 用「前缀 + 纯数字」的写法，避免再手滑写出 GAME/TOOL 这种含 O 的 slug。
_PREFIX = "MKTSP3"          # 6 位，字符集内
_SUFFIX_LEN = 14            # 6 + 14 = 20
GAME = _PREFIX + "1".zfill(_SUFFIX_LEN)
TOOL = _PREFIX + "2".zfill(_SUFFIX_LEN)
OLD = _PREFIX + "3".zfill(_SUFFIX_LEN)


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
    assert all(is_valid_slug(s) for s in (GAME, TOOL, OLD)), "测试 slug 必须合法"

    db = SessionLocal()
    uid = _ensure_user(db, "mkt_s3_owner")
    db.query(PublishedSite).filter(PublishedSite.slug.in_([GAME, TOOL, OLD])).delete()
    db.commit()
    now = datetime.datetime.utcnow()
    db.add(PublishedSite(slug=GAME, user_id=uid, title="游戏", market_visible=True,
                         market_listed_at=now, category="game", current_hash="h" * 64))
    db.add(PublishedSite(slug=TOOL, user_id=uid, title="工具", market_visible=True,
                         market_listed_at=now, category="tool", current_hash="g" * 64))
    db.add(PublishedSite(slug=OLD, user_id=uid, title="老作品", market_visible=True,
                         market_listed_at=now - datetime.timedelta(days=30), category="game"))
    db.commit()
    db.close()

    yield {"uid": uid, "game": GAME, "tool": TOOL, "old": OLD}

    db = SessionLocal()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([GAME, TOOL, OLD])).delete()
    db.commit()
    db.close()


def _login(app_client, u="mkt_s3_owner"):
    assert app_client.post("/api/login", json={"username": u, "password": "test123456"}).status_code == 200


# ---------------- 分类 ----------------

def test_normalize_category():
    assert normalize_category("game") == "game"
    assert normalize_category("GAME ") == "game"
    assert normalize_category("nope") == ""
    assert normalize_category(None) == ""
    for c in CATEGORIES:
        assert normalize_category(c) == c


def test_list_filter_by_category(env, app_client):
    r = app_client.get("/api/market/sites?category=game")
    assert r.status_code == 200
    slugs = [i["slug"] for i in r.get_json()["items"]]
    assert env["game"] in slugs and env["tool"] not in slugs

    r = app_client.get("/api/market/sites?category=tool").get_json()
    assert [i["slug"] for i in r["items"]] == [env["tool"]]


def test_illegal_category_is_ignored_not_400(env, app_client):
    r = app_client.get("/api/market/sites?category=%3Cscript%3E")
    assert r.status_code == 200
    assert r.get_json()["total"] == 3


def test_categories_exposed_for_frontend(env, app_client):
    assert app_client.get("/api/market/sites").get_json()["categories"] == list(CATEGORIES)


def test_owner_can_set_category(env, app_client):
    _login(app_client)
    r = app_client.patch(f"/api/publish/{env['tool']}/market", json={"category": "admin"})
    assert r.status_code == 200 and r.get_json()["category"] == "admin"
    # 非法值静默归为未分类，不因一个枚举值让整个上架操作失败
    r = app_client.patch(f"/api/publish/{env['tool']}/market", json={"category": "wat"})
    assert r.get_json()["category"] == ""


# ---------------- 周榜 ----------------

def test_week_window_excludes_old(env, app_client):
    r = app_client.get("/api/market/sites?window=week")
    slugs = [i["slug"] for i in r.get_json()["items"]]
    assert env["game"] in slugs and env["tool"] in slugs
    assert env["old"] not in slugs


def test_unknown_window_is_ignored(env, app_client):
    assert app_client.get("/api/market/sites?window=century").get_json()["total"] == 3


def test_week_and_category_compose(env, app_client):
    r = app_client.get("/api/market/sites?window=week&category=game").get_json()
    assert [i["slug"] for i in r["items"]] == [env["game"]]


# ---------------- 缩略图 ----------------

def test_thumb_404_when_not_generated(env, app_client, monkeypatch, tmp_path):
    """截不到是 404（前端回退色块），绝不能 500 或空响应。

    缓存目录指向 tmp_path：路由会先查 get_thumb 缓存，若指向真实
    published/_thumbs，一旦本机有上一次跑次的残留就会误命中 → 期望 404 得 200。
    """
    import services.market.thumbs as t
    monkeypatch.setattr(t, "thumb_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.market.ensure_thumb", lambda *a, **k: None)
    r = app_client.get(f"/api/market/thumbs/{env['game']}.png")
    assert r.status_code == 404


def test_thumb_serves_cached_png(env, app_client, tmp_path, monkeypatch):
    """已缓存的缩略图正常回图。

    必须把缓存目录指到 tmp_path，**绝不写真实 published/_thumbs**：
    该目录下留一个 <hash>.png 就会让下一次跑次里的 404 用例误命中，
    变成与代码无关的偶发失败（tmp_path 由 pytest 自动清理，无需 finally unlink）。
    """
    import services.market.thumbs as t
    monkeypatch.setattr(t, "thumb_dir", lambda: tmp_path)
    p = t.thumb_path("h" * 64)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    r = app_client.get(f"/api/market/thumbs/{env['game']}.png")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.data.startswith(b"\x89PNG")


def test_thumb_invalid_slug_404(env, app_client):
    assert app_client.get("/api/market/thumbs/nope.png").status_code == 404


def test_get_thumb_ignores_empty_file(tmp_path, monkeypatch):
    """0 字节的残留文件不能当成有效缓存（否则会永远返回一张坏图）。"""
    import services.market.thumbs as t
    monkeypatch.setattr(t, "thumb_dir", lambda: tmp_path)
    (tmp_path / "empty.png").write_bytes(b"")
    assert t.get_thumb("empty") is None
