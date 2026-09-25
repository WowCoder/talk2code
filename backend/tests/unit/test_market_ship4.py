# -*- coding: utf-8 -*-
"""市集 Ship 4 测试：作者自定义封面。

约束（都是「默认态」的边界）：
- 默认缩略图就是首页截图；封面只是覆盖层，删掉就回到截图
- 只有作者本人能传 / 删；他人一律 404（不泄露资源存在性）
- 封面优先于截图，且不吃长缓存（作者随时可能换）
- 未上架站点的封面只对作者可见 —— 否则「撤下市集」之后图还能被公网直连取到
"""
import datetime
import io

import pytest
from models import SessionLocal, User
from models.models import PublishedSite
from utils.security import hash_password

from services.market.cover import delete_cover, get_cover, save_cover, validate_cover
from services.publish.slug import is_valid_slug

# 20 位 Crockford Base32，不含 I/L/O/U（见 ship3 测试注释）
_PREFIX = "MKTSP4"
_SUFFIX_LEN = 14
A = _PREFIX + "1".zfill(_SUFFIX_LEN)      # 已上架
B = _PREFIX + "2".zfill(_SUFFIX_LEN)      # 未上架

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff" + b"0" * 64


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
    assert all(is_valid_slug(s) for s in (A, B)), "测试 slug 必须合法"

    db = SessionLocal()
    uid = _ensure_user(db, "mkt_s4_owner")
    other = _ensure_user(db, "mkt_s4_other")
    db.query(PublishedSite).filter(PublishedSite.slug.in_([A, B])).delete()
    db.commit()
    now = datetime.datetime.utcnow()
    db.add(PublishedSite(slug=A, user_id=uid, title="上架作品", market_visible=True,
                         market_listed_at=now, current_hash="a" * 64))
    db.add(PublishedSite(slug=B, user_id=uid, title="未上架作品", market_visible=False,
                         current_hash="b" * 64))
    db.commit()
    db.close()

    yield {"uid": uid, "other": other, "a": A, "b": B}

    db = SessionLocal()
    db.query(PublishedSite).filter(PublishedSite.slug.in_([A, B])).delete()
    db.commit()
    db.close()


def _login(app_client, u="mkt_s4_owner"):
    assert app_client.post("/api/login", json={"username": u, "password": "test123456"}).status_code == 200


def _upload(app_client, slug, data=PNG, name="cover.png"):
    return app_client.post(
        f"/api/market/sites/{slug}/cover",
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


# ---------------- 校验 ----------------

def test_validate_cover():
    assert validate_cover(PNG) is None
    assert validate_cover(JPG) is None
    assert "图片" in validate_cover(b"")
    assert "PNG" in validate_cover(b"GIF89a" + b"0" * 10)          # 非图片魔数
    assert "2 MB" in validate_cover(b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024))


def test_save_and_delete_cover(tmp_path, monkeypatch):
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)

    assert get_cover(A) == (None, None)
    assert save_cover(A, PNG) == "image/png"
    assert get_cover(A)[1] == "image/png"

    # 换格式时不能两个扩展名各留一份，否则取到的还是旧图
    assert save_cover(A, JPG) == "image/jpeg"
    assert get_cover(A)[1] == "image/jpeg"
    assert not (tmp_path / f"{A}.png").exists()

    assert delete_cover(A) is True
    assert get_cover(A) == (None, None)
    assert delete_cover(A) is False      # 幂等


# ---------------- 上传 / 删除端点 ----------------

def test_owner_upload_and_thumb_serves_cover(env, app_client, tmp_path, monkeypatch):
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)

    _login(app_client)
    r = _upload(app_client, env["a"])
    assert r.status_code == 200 and r.get_json()["cover"] is True

    t = app_client.get(f"/api/market/thumbs/{env['a']}.png")
    assert t.status_code == 200
    assert t.data == PNG
    # 封面会被作者随时替换，不能吃长缓存
    assert "no-cache" in t.headers["Cache-Control"]

    jpg = _upload(app_client, env["a"], JPG, "cover.jpg")
    assert jpg.status_code == 200
    t = app_client.get(f"/api/market/thumbs/{env['a']}.png")
    assert t.headers["Content-Type"] == "image/jpeg"


def test_delete_cover_falls_back_to_screenshot(env, app_client, tmp_path, monkeypatch):
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)

    _login(app_client)
    _upload(app_client, env["a"])
    assert app_client.delete(f"/api/market/sites/{env['a']}/cover").status_code == 200

    # 没有封面、也没生成截图 → 404（前端回退色块）
    monkeypatch.setattr("routes.market.ensure_thumb", lambda *a, **k: None)
    assert app_client.get(f"/api/market/thumbs/{env['a']}.png").status_code == 404


def test_upload_requires_auth(env, app_client):
    assert _upload(app_client, env["a"]).status_code == 401


def test_other_user_cannot_upload(env, app_client):
    _login(app_client, "mkt_s4_other")
    assert _upload(app_client, env["a"]).status_code == 404


def test_bad_payload_rejected(env, app_client, tmp_path, monkeypatch):
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)
    _login(app_client)

    assert _upload(app_client, env["a"], b"not an image").status_code == 400
    assert app_client.post(f"/api/market/sites/{env['a']}/cover").status_code == 400
    assert get_cover(env["a"]) == (None, None)


def test_unlisted_cover_only_visible_to_author(env, app_client, tmp_path, monkeypatch):
    """撤下市集之后，封面不能被公网直连取到（只有作者还能预览）。"""
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)

    _login(app_client)
    assert _upload(app_client, env["b"]).status_code == 200
    assert app_client.get(f"/api/market/thumbs/{env['b']}.png").status_code == 200

    app_client.post("/api/logout")
    assert app_client.get(f"/api/market/thumbs/{env['b']}.png").status_code == 404


# ---------------- 发布面板载荷 ----------------

def test_publish_payload_exposes_cover_and_category(env, app_client, tmp_path, monkeypatch):
    """发布面板靠 cover 决定显示「移除封面」还是「上传封面」，靠 category 回填下拉。"""
    import services.market.cover as c
    monkeypatch.setattr(c, "cover_dir", lambda: tmp_path)

    _login(app_client)
    app_client.patch(f"/api/publish/{env['a']}/market", json={"category": "game"})

    p = app_client.get(f"/api/publish/{env['a']}/info").get_json()
    assert p["cover"] is False and p["category"] == "game"

    _upload(app_client, env["a"])
    assert app_client.get(f"/api/publish/{env['a']}/info").get_json()["cover"] is True
