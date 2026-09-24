# -*- coding: utf-8 -*-
"""PublishService 测试（plan B6）：幂等语义 + 契约门禁 + on_published 解耦。

注：沙箱拦截 /tmp/pytest-of-*，故用 backend 下临时目录 + 独立 sqlite engine。
"""
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, PublishedBundle, PublishedSite
from services.publish.service import PublishService, PublishError
from services.publish.store import LocalFSStore


SAMPLE = [
    {"filename": "index.html", "content": "<html><body>hi</body></html>"},
    {"filename": "app.js", "content": "console.log(1)"},
]

# requirement.code_files 里混入的平台内部文件（工作区 .task/ 目录）
INTERNAL = [
    {"filename": ".task/TASK_STATE.md", "content": "# 内部状态"},
    {"filename": ".task/contract.json", "content": "{}"},
    {"filename": ".task/evaluator/result.json", "content": "{}"},
]


def _make_env():
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    store = LocalFSStore(d)
    engine = create_engine(f"sqlite:///{Path(d) / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def factory_cm():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return store, d, factory_cm


@pytest.fixture
def env():
    store, d, factory_cm = _make_env()
    yield store, factory_cm
    shutil.rmtree(d, ignore_errors=True)


def test_publish_creates_site_and_bundle(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    site = svc.publish(user_id=1, code_files=SAMPLE, requirement_id=10)
    assert site is not None
    assert len(site.slug) == 20
    assert site.version == 1
    assert site.current_hash
    # bundle 行已落库
    with factory() as db:
        assert db.query(PublishedBundle).get(site.current_hash) is not None
    # 磁盘文件已写
    assert store.exists(site.current_hash)
    assert store.get(site.current_hash, "index.html").decode() == SAMPLE[0]["content"]


def test_publish_idempotent_same_content(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    site1 = svc.publish(user_id=1, code_files=SAMPLE, requirement_id=11)
    calls = []
    site2 = svc.publish(
        user_id=1, code_files=SAMPLE, requirement_id=11,
        on_published=lambda s, h: calls.append((s, h)),
    )
    # 同一需求 → 同一 slug；内容不变 → version 不动；on_published 不触发
    assert site2.slug == site1.slug
    assert site2.version == 1
    assert calls == []


def test_publish_bumps_version_on_change(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    site1 = svc.publish(user_id=2, code_files=SAMPLE, requirement_id=20)
    changed = [dict(SAMPLE[1], content="console.log(2)")]
    site2 = svc.publish(user_id=2, code_files=changed, requirement_id=20)
    # slug 不变，version 递增
    assert site2.slug == site1.slug
    assert site2.version == 2
    assert site2.current_hash != site1.current_hash


def test_publish_on_published_called_only_on_new_content(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    calls = []
    svc.publish(user_id=3, code_files=SAMPLE, requirement_id=30,
                on_published=lambda s, h: calls.append((s, h)))
    assert len(calls) == 1
    # 再次发同样内容不触发
    svc.publish(user_id=3, code_files=SAMPLE, requirement_id=30,
                on_published=lambda s, h: calls.append((s, h)))
    assert len(calls) == 1


def test_publish_rejects_external_cdn(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    bad = [{"filename": "index.html",
            "content": '<script src="https://cdn.example.com/x.js"></script>'}]
    with pytest.raises(PublishError):
        svc.publish(user_id=4, code_files=bad, requirement_id=40)


def test_publish_no_files_raises(env):
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    with pytest.raises(PublishError):
        svc.publish(user_id=5, code_files=[], requirement_id=50)


def test_normalize_excludes_internal_paths(env):
    """点开头的路径段（.task/ 等）是平台内部文件，归一化阶段就剔除。"""
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    files = svc.normalize_code_files(SAMPLE + INTERNAL)
    assert set(files) == {"index.html", "app.js"}


def test_publish_does_not_expose_internal_files(env):
    """`unlisted` 只是 noindex 而不是保密：.task/ 内容绝不能进公网产物。"""
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    site = svc.publish(user_id=6, code_files=SAMPLE + INTERNAL, requirement_id=60)
    assert store.get(site.current_hash, "index.html")
    for rel in (".task/TASK_STATE.md", ".task/contract.json", ".task/evaluator/result.json"):
        with pytest.raises(KeyError):
            store.get(site.current_hash, rel)


def test_hash_ignores_internal_files(env):
    """内部文件不进产物 ⇒ 也不影响 content_hash（纯内部改动不产生新版本）。"""
    store, factory = env
    svc = PublishService(store=store, db_session_factory=factory)
    s1 = svc.publish(user_id=7, code_files=SAMPLE, requirement_id=70)
    changed_internal = INTERNAL + [{"filename": ".task/TASK_STATE.md", "content": "# 变了"}]
    s2 = svc.publish(user_id=7, code_files=SAMPLE + changed_internal, requirement_id=70)
    assert s2.current_hash == s1.current_hash
    assert s2.version == 1
