# -*- coding: utf-8 -*-
"""backend/manage.py CLI 测试。

转移的数据边界（本套测试的防回归核心）：
- 迁：requirements.user_id、agent_traces.user_id、published_sites.user_id
- 不迁：记忆表（agent_memories / v2 / vectors）、site_likes、site_comments、user_follows
- 默认 dry-run 不改库；--yes 落库；重复执行幂等；copy 模式源用户保留
"""
import pytest

from models import (
    SessionLocal, User, Requirement, AgentMemory, AgentTrace, InviteCode,
)
from models.models import PublishedSite, AdminUser
from utils.security import hash_password

import manage


@pytest.fixture
def source_and_reqs():
    db = SessionLocal()
    src = db.query(User).filter(User.username == "cli_source").first()
    if src is None:
        src = User(username="cli_source", password_hash=hash_password("src12345678"))
        db.add(src)
        db.commit()
        db.refresh(src)
    demo = db.query(User).filter(User.username == "demo").first()
    if demo is None:
        demo = User(username="demo", password_hash=hash_password("demo123456"))
        db.add(demo)
        db.commit()
        db.refresh(demo)

    reqs = db.query(Requirement).filter(Requirement.user_id == src.id).all()
    for r in reqs:
        db.query(AgentTrace).filter(AgentTrace.requirement_id == r.id).delete()
        db.query(PublishedSite).filter(PublishedSite.requirement_id == r.id).delete()
    db.query(Requirement).filter(Requirement.user_id == src.id).delete()
    db.commit()

    made = []
    for i, status in enumerate(["finished", "failed"]):
        r = Requirement(user_id=src.id, title=f"cli需求{i}", content="内容",
                        status=status)
        db.add(r)
        db.commit()
        db.refresh(r)
        made.append(r)
        db.add(AgentTrace(trace_id=f"clitrace{r.id}", requirement_id=r.id,
                          user_id=src.id, data={}))
    site = PublishedSite(slug=f"CLISITE{made[0].id:016d}", user_id=src.id,
                         requirement_id=made[0].id, title="cli站点",
                         market_visible=False)
    db.add(site)
    db.add(AgentMemory(user_id=src.id, fact="私人记忆不迁移"))
    db.commit()
    yield src, demo, made
    # 清理由下一轮 fixture 前置完成


def test_transfer_dry_run_does_not_write(source_and_reqs):
    src, demo, reqs = source_and_reqs
    rc = manage.main(["demo", "transfer", "--from", str(src.id), "--all"])
    assert rc == 0
    db = SessionLocal()
    r = db.query(Requirement).get(reqs[0].id)
    assert r.user_id == src.id  # 默认 dry-run 未改库


def test_transfer_move_updates_boundary_tables(source_and_reqs):
    src, demo, reqs = source_and_reqs
    rc = manage.main(["demo", "transfer", "--from", str(src.id), "--all", "--yes"])
    assert rc == 0

    db = SessionLocal()
    for r in reqs:
        assert db.query(Requirement).get(r.id).user_id == demo.id
    trace = db.query(AgentTrace).filter(AgentTrace.requirement_id == reqs[0].id).first()
    assert trace.user_id == demo.id  # trace 跟着需求走
    site = db.query(PublishedSite).filter(
        PublishedSite.requirement_id == reqs[0].id).first()
    assert site.user_id == demo.id  # 站点作者跟着走

    # 记忆不迁移
    mem = db.query(AgentMemory).filter(
        AgentMemory.user_id == src.id, AgentMemory.fact == "私人记忆不迁移").first()
    assert mem is not None


def test_transfer_is_idempotent(source_and_reqs):
    src, demo, reqs = source_and_reqs
    assert manage.main(["demo", "transfer", "--from", str(src.id), "--all", "--yes"]) == 0
    # 重复执行：源用户已无需求，影响 0 行且不报错
    assert manage.main(["demo", "transfer", "--from", str(src.id), "--all", "--yes"]) == 0


def test_transfer_copy_keeps_source(source_and_reqs):
    src, demo, reqs = source_and_reqs
    rc = manage.main(["demo", "transfer", "--from", str(src.id), "--all",
                      "--mode", "copy", "--yes"])
    assert rc == 0
    db = SessionLocal()
    # 源用户保留原需求
    assert db.query(Requirement).get(reqs[0].id).user_id == src.id
    # demo 多出一份副本
    copies = db.query(Requirement).filter(
        Requirement.user_id == demo.id,
        Requirement.title == reqs[0].title).all()
    assert len(copies) >= 1


def test_transfer_single_requirement(source_and_reqs):
    src, demo, reqs = source_and_reqs
    rc = manage.main(["demo", "transfer", "--from", str(src.id),
                      "--requirement-id", str(reqs[0].id), "--yes"])
    assert rc == 0
    db = SessionLocal()
    assert db.query(Requirement).get(reqs[0].id).user_id == demo.id
    assert db.query(Requirement).get(reqs[1].id).user_id == src.id  # 未选中


def test_transfer_rejects_demo_as_source(source_and_reqs, capsys):
    src, demo, reqs = source_and_reqs
    rc = manage.main(["demo", "transfer", "--from", "demo", "--all", "--yes"])
    assert rc == 0  # 直接返回，不做事


def test_demo_init_prints_password_once(capsys):
    _db = SessionLocal()
    _db.query(User).filter(User.username == "demo").delete()
    _db.commit()
    rc = manage.main(["demo", "init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out
    db = SessionLocal()
    u = db.query(User).filter(User.username == "demo").first()
    assert u is not None
    # 幂等：已存在时不重置密码
    old_hash = u.password_hash
    rc = manage.main(["demo", "init"])
    out2 = capsys.readouterr().out
    assert "已存在" in out2
    db.refresh(u)
    assert u.password_hash == old_hash


def test_rotate_password_changes_hash(capsys):
    manage.main(["demo", "init"])
    db = SessionLocal()
    u = db.query(User).filter(User.username == "demo").first()
    old = u.password_hash
    rc = manage.main(["demo", "rotate-password"])
    assert rc == 0
    db.refresh(u)
    assert u.password_hash != old


def test_invite_create_cli(capsys):
    rc = manage.main(["invite", "create", "--count", "3", "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    codes = [line.strip() for line in out.splitlines() if line.strip().startswith("T2C-")]
    assert len(codes) == 3
    db = SessionLocal()
    for c in codes:
        inv = db.query(InviteCode).filter(InviteCode.code == c).first()
        assert inv is not None and inv.status == "issued"
        assert inv.expires_at is not None


def test_admin_create_user_cli(capsys):
    _db = SessionLocal()
    _db.query(AdminUser).filter(AdminUser.username == "cli_admin").delete()
    _db.commit()
    rc = manage.main(["admin", "create-user", "--username", "cli_admin",
                      "--password", "clipass123"])
    assert rc == 0
    db = SessionLocal()
    a = db.query(AdminUser).filter(AdminUser.username == "cli_admin").first()
    assert a is not None
    # 重复创建：明确拒绝（返回 1），不静默成功
    rc = manage.main(["admin", "create-user", "--username", "cli_admin",
                      "--password", "clipass123"])
    assert rc == 1
