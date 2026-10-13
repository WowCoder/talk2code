# -*- coding: utf-8 -*-
"""需求对外身份（slug）与名称测试。

不可回退约束：
- URL 里出现的是 slug，不是自增 id：id 会泄露平台累计需求数，还能被顺序枚举
- 按 slug 取详情必须校验归属（别人的 slug 看不到）
- 非法 / 不存在的 slug 一律 404，不报错、不回退到 id 语义
- 名称是短的（≤30 字），不是把用户输入原文截 100 字
"""
import pytest

from models import SessionLocal, User
from models.models import Requirement, _backfill_requirement_slugs
from services.publish.slug import is_valid_slug
from utils.security import hash_password

USER_A = "slug_owner"
USER_B = "slug_other"


def _ensure_user(db, name: str) -> int:
    u = db.query(User).filter(User.username == name).first()
    if u is None:
        u = User(username=name, password_hash=hash_password("test123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u.id


def _login(app_client, name):
    r = app_client.post("/api/login", json={"username": name, "password": "test123456"})
    assert r.status_code == 200, r.get_json()


@pytest.fixture(autouse=True)
def _no_background_pipeline(monkeypatch):
    """创建需求会真的把任务丢进队列、进而去打 LLM（测试环境必然 401）。

    本文件只关心 slug 与名称，跑流水线既慢又会打出外部请求。
    """
    from services.task_queue import task_queue
    monkeypatch.setattr(task_queue, "submit", lambda *a, **k: None)


@pytest.fixture
def users(app_client):
    db = SessionLocal()
    try:
        return {"a": _ensure_user(db, USER_A), "b": _ensure_user(db, USER_B)}
    finally:
        db.close()


def _create(app_client, content="帮我做一个可以记录每天喝水量的网页，要有图表统计，还要能设置提醒"):
    r = app_client.post("/api/requirements", json={"content": content})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["requirement"]


# ---------------- 创建：slug + 短名称 ----------------

def test_create_returns_valid_slug(users, app_client):
    _login(app_client, USER_A)
    req = _create(app_client)
    assert is_valid_slug(req["slug"])


def test_slug_is_not_the_autoincrement_id(users, app_client):
    """不可回退约束：slug 不能是 id 的字符串形式，否则等于什么都没改。"""
    _login(app_client, USER_A)
    req = _create(app_client)
    assert req["slug"] != str(req["id"])
    assert not req["slug"].isdigit()


def test_slugs_are_not_sequential(users, app_client):
    """两条需求的 slug 之间不存在可推算的关系。"""
    _login(app_client, USER_A)
    first = _create(app_client)
    second = _create(app_client)
    assert first["slug"] != second["slug"]
    # 相邻 id 的 slug 不共享前缀（顺序枚举必须失效）
    assert first["slug"][:8] != second["slug"][:8]


def test_create_title_is_short_not_raw_prefix(users, app_client):
    """名称要能一眼扫读，不是原文前 100 字。"""
    _login(app_client, USER_A)
    req = _create(app_client)
    assert len(req["title"]) <= 30
    assert not req["title"].startswith("帮我")
    assert "图表统计" not in req["title"]  # 补充说明不该进名称


def test_create_title_of_short_input(users, app_client):
    _login(app_client, USER_A)
    req = _create(app_client, content="贪吃蛇")
    assert req["title"] == "贪吃蛇"


# ---------------- 按 slug 取详情 ----------------

def test_detail_by_slug(users, app_client):
    _login(app_client, USER_A)
    created = _create(app_client)

    r = app_client.get(f"/api/requirements/s/{created['slug']}")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["requirement"]
    assert body["id"] == created["id"]
    assert body["slug"] == created["slug"]


def test_detail_by_slug_is_case_insensitive(users, app_client):
    """手敲 / 复制时大小写不该影响命中（Crockford Base32 的惯例）。"""
    _login(app_client, USER_A)
    created = _create(app_client)

    r = app_client.get(f"/api/requirements/s/{created['slug'].lower()}")
    assert r.status_code == 200, r.get_json()


def test_detail_invalid_slug_404(users, app_client):
    _login(app_client, USER_A)
    assert app_client.get("/api/requirements/s/not-a-real-slug").status_code == 404
    assert app_client.get("/api/requirements/s/123").status_code == 404


def test_detail_other_users_slug_404(users, app_client):
    """归属校验：拿到别人的 slug 也读不到内容。"""
    _login(app_client, USER_A)
    created = _create(app_client)

    _login(app_client, USER_B)
    assert app_client.get(f"/api/requirements/s/{created['slug']}").status_code == 404


def test_detail_by_id_still_works_for_legacy_links(users, app_client):
    """老链接 /detail/123 要能打开，前端才谈得上把它换成 slug 版。"""
    _login(app_client, USER_A)
    created = _create(app_client)

    r = app_client.get(f"/api/requirements/{created['id']}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["requirement"]["slug"] == created["slug"]


def test_list_returns_slug(users, app_client):
    _login(app_client, USER_A)
    created = _create(app_client)

    body = app_client.get("/api/requirements").get_json()
    slugs = [r["slug"] for r in body["requirements"]]
    assert created["slug"] in slugs


# ---------------- 存量数据回填 ----------------

def test_backfill_fills_null_slug(users, app_client):
    """ALTER 只加列不改写既有行：不回填的话历史需求在 URL 里拿不到 slug。"""
    db = SessionLocal()
    try:
        legacy = Requirement(
            user_id=users["a"], title="历史需求", content="历史需求", status="finished",
        )
        db.add(legacy)
        db.commit()
        db.refresh(legacy)
        assert legacy.slug is None

        _backfill_requirement_slugs()
        db.refresh(legacy)
        assert is_valid_slug(legacy.slug or "")

        _login(app_client, USER_A)
        r = app_client.get(f"/api/requirements/s/{legacy.slug}")
        assert r.status_code == 200, r.get_json()
    finally:
        db.query(Requirement).filter(Requirement.title == "历史需求").delete()
        db.commit()
        db.close()
