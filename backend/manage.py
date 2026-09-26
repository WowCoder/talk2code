# -*- coding: utf-8 -*-
"""Talk2Code 运维命令行工具。

用法：
    python manage.py demo init
    python manage.py demo rotate-password
    python manage.py demo transfer --from alice --all --dry-run
    python manage.py invite create --count 10
    python manage.py admin create-user --username ops

用 argparse 而不是 click：requirements.txt 里没有 click，为一个运维脚本引入
新依赖不值得。
"""
import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 测试/离线环境下不强制要求真实密钥
os.environ.setdefault('JWT_SECRET_KEY', 'manage-cli-local')

from models import User, Requirement, AgentTrace, PublishedSite, InviteCode, AdminUser  # noqa: E402
from models import init_db  # noqa: E402
from utils.db import get_db, transactional_db  # noqa: E402
from utils.security import hash_password  # noqa: E402
from config import settings  # noqa: E402


def _generate_password() -> str:
    """演示/管理员初始密码。只在终端打印一次，库里只存 bcrypt 哈希。"""
    return secrets.token_urlsafe(20)


# ==================== demo ====================

def cmd_demo_init(args) -> int:
    init_db()
    with get_db() as db:
        existing = db.query(User).filter(User.username == settings.DEMO_USERNAME).first()

    if existing:
        print(f"演示帐号已存在：{settings.DEMO_USERNAME}（id={existing.id}）")
        print("如需重新生成密码：python manage.py demo rotate-password")
        return 0

    password = _generate_password()
    with transactional_db() as db:
        user = User(username=settings.DEMO_USERNAME, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        user_id = user.id

    print("演示帐号已创建")
    print(f"  用户名：{settings.DEMO_USERNAME}（id={user_id}）")
    print(f"  密码　：{password}")
    print()
    print("⚠️  该密码只显示这一次，请立即保存。用密码登录拥有写权限；")
    print("   登录页的「演示模式」入口无需密码，但会被后端强制只读。")
    return 0


def cmd_demo_rotate(args) -> int:
    init_db()
    with get_db() as db:
        user = db.query(User).filter(User.username == settings.DEMO_USERNAME).first()
        if not user:
            print(f"演示帐号不存在：{settings.DEMO_USERNAME}")
            print("先执行：python manage.py demo init")
            return 1

    password = _generate_password()
    with transactional_db() as db:
        db.query(User).filter(User.id == user.id).update(
            {'password_hash': hash_password(password)}
        )
    print(f"演示帐号密码已轮换：{settings.DEMO_USERNAME}")
    print(f"  新密码：{password}")
    return 0


def cmd_demo_transfer(args) -> int:
    """把某个用户的需求转移到演示帐号。

    数据边界（不是无脑 UPDATE 所有 user_id）：
      迁：requirements / agent_traces / published_sites（按 requirement_id 关联）
      不迁：记忆表（会污染演示帐号的记忆库，进而影响它后续生成质量）；
            site_likes / site_comments / user_follows（那些 user_id 是互动者不是作者）
    """
    init_db()

    with get_db() as db:
        if str(args.source).isdigit():
            src = db.query(User).filter(User.id == int(args.source)).first()
        else:
            src = db.query(User).filter(User.username == args.source).first()
        if not src:
            print(f"源用户不存在：{args.source}")
            return 1
        demo = db.query(User).filter(User.username == settings.DEMO_USERNAME).first()
        if not demo:
            print(f"演示帐号不存在：{settings.DEMO_USERNAME}")
            print("先执行：python manage.py demo init")
            return 1
        if src.id == demo.id:
            print("源用户就是演示帐号，无需转移")
            return 0
        src_id = src.id
        src_name = src.username
        demo_id = demo.id

        q = db.query(Requirement).filter(
            Requirement.user_id == src_id, Requirement.is_deleted.is_(False)
        )
        if args.requirement_id:
            q = q.filter(Requirement.id == args.requirement_id)
        reqs = q.all()
        req_ids = [r.id for r in reqs]

        sites = (
            db.query(PublishedSite)
            .filter(PublishedSite.user_id == src_id, PublishedSite.requirement_id.in_(req_ids))
            .all()
        ) if req_ids else []
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.user_id == src_id, AgentTrace.requirement_id.in_(req_ids))
            .count()
        ) if req_ids else 0

    if not req_ids:
        print(f"没有符合条件的需求（用户 {src_name}）")
        return 0

    print(f"源用户：{src_name}（id={src_id}）  →  演示帐号：{settings.DEMO_USERNAME}（id={demo_id}）")
    print(f"模式：{args.mode}")
    print(f"影响范围：需求 {len(req_ids)} 条 / 已发布站点 {len(sites)} 个 / 链路记录 {traces} 条")
    for s in sites:
        flag = '（已上架市集，转移后作者显示为演示帐号）' if s.market_visible else ''
        print(f"  - 站点 {s.slug}{flag}")

    if args.dry_run or not args.yes:
        print()
        print("这是 dry-run，未修改任何数据。确认后加 --yes 执行。")
        return 0

    try:
        with transactional_db() as db:
            if args.mode == 'copy':
                for rid in req_ids:
                    r = db.query(Requirement).filter(Requirement.id == rid).first()
                    if not r:
                        continue
                    db.add(Requirement(
                        user_id=demo_id,
                        title=r.title,
                        content=r.content,
                        status=r.status,
                        error_message=r.error_message,
                        dialogue_history=list(r.dialogue_history or []),
                        code_files=list(r.code_files or []),
                    ))
            else:
                db.query(Requirement).filter(
                    Requirement.id.in_(req_ids)
                ).update({'user_id': demo_id}, synchronize_session=False)
                db.query(AgentTrace).filter(
                    AgentTrace.user_id == src_id, AgentTrace.requirement_id.in_(req_ids)
                ).update({'user_id': demo_id}, synchronize_session=False)
                db.query(PublishedSite).filter(
                    PublishedSite.user_id == src_id, PublishedSite.requirement_id.in_(req_ids)
                ).update({'user_id': demo_id}, synchronize_session=False)
    except Exception as e:
        print(f"转移失败，已回滚：{e}")
        return 1

    print(f"转移完成（{args.mode}）")
    return 0


# ==================== invite ====================

def cmd_invite_create(args) -> int:
    """应急批量生成邀请码（不绑定申请人，供管理员手动分发）。"""
    from datetime import datetime, timedelta
    from services.invite import generate_code

    init_db()
    created = []
    try:
        with transactional_db() as db:
            for _ in range(args.count):
                code = generate_code()
                db.add(InviteCode(
                    code=code,
                    applicant_email=f'manual-{code[-6:].lower()}@local',
                    applicant_phone='',
                    applicant_note='后台批量生成',
                    status='issued',
                    delivery_status='skipped',
                    decided_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=args.days),
                ))
                created.append(code)
    except Exception as e:
        print(f"生成失败：{e}")
        return 1

    print(f"已生成 {len(created)} 个邀请码（{args.days} 天有效）：")
    for c in created:
        print(f"  {c}")
    return 0


# ==================== admin ====================

def cmd_admin_create_user(args) -> int:
    init_db()
    with get_db() as db:
        if db.query(AdminUser).filter(AdminUser.username == args.username).first():
            print(f"管理员已存在：{args.username}")
            return 1

    password = args.password or _generate_password()
    with transactional_db() as db:
        admin = AdminUser(username=args.username, password_hash=hash_password(password))
        db.add(admin)
        db.flush()
        admin_id = admin.id

    print(f"管理员已创建：{args.username}（id={admin_id}）")
    print(f"  密码：{password}")
    print(f"  后台入口：/admin/login（token 有效期 {settings.ADMIN_TOKEN_EXPIRES_HOURS} 小时）")
    return 0


# ==================== 入口 ====================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='manage.py', description='Talk2Code 运维命令行')
    sub = parser.add_subparsers(dest='group', required=True)

    demo = sub.add_parser('demo', help='演示帐号管理')
    demo_sub = demo.add_subparsers(dest='action', required=True)
    demo_sub.add_parser('init', help='创建演示帐号并打印初始密码').set_defaults(func=cmd_demo_init)
    demo_sub.add_parser('rotate-password', help='轮换演示帐号密码').set_defaults(func=cmd_demo_rotate)

    tr = demo_sub.add_parser('transfer', help='把需求转移到演示帐号')
    tr.add_argument('--from', dest='source', required=True, help='源用户 id 或用户名')
    tr.add_argument('--requirement-id', type=int, default=None, help='只转移指定需求')
    tr.add_argument('--all', action='store_true', help='转移该用户全部需求')
    tr.add_argument('--mode', choices=['move', 'copy'], default='move',
                    help='move=转移（默认）| copy=复制一份，源用户保留')
    tr.add_argument('--dry-run', action='store_true', help='只打印影响范围，不改数据')
    tr.add_argument('--yes', action='store_true', help='确认执行（默认 dry-run）')
    tr.set_defaults(func=cmd_demo_transfer)

    invite = sub.add_parser('invite', help='邀请码管理')
    invite_sub = invite.add_subparsers(dest='action', required=True)
    ic = invite_sub.add_parser('create', help='批量生成邀请码')
    ic.add_argument('--count', type=int, default=1)
    ic.add_argument('--days', type=int, default=7)
    ic.set_defaults(func=cmd_invite_create)

    adm = sub.add_parser('admin', help='后台管理员')
    adm_sub = adm.add_subparsers(dest='action', required=True)
    au = adm_sub.add_parser('create-user', help='创建后台管理员')
    au.add_argument('--username', required=True)
    au.add_argument('--password', default=None, help='不指定则随机生成')
    au.set_defaults(func=cmd_admin_create_user)

    return parser


def main(argv=None) -> int:
    # argv 参数供测试/编程式调用；默认走 sys.argv
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
