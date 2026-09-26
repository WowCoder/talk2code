# -*- coding: utf-8 -*-
"""邀请码服务：生成 / 注册核销 / 申请冷却查询 / 发码。

设计依据：docs/design/demo-invite-admin.md §4。

关键约束：
- 码空间 Crockford Base32 12 位（去掉 I/L/O/U 防手抄混淆），约 60 bit 熵，
  不可暴力枚举。
- 核销必须原子：UPDATE ... WHERE status='issued' + rowcount 检查，
  不允许「先查再插」（并发下必然漏判，site_visit_dedup 踩过同一个坑）。
- 防枚举：所有失败原因（不存在/已用/过期）对外统一文案，由调用方输出。
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import update

from config import settings
from models import InviteCode

# Crockford Base32：0-9 + 大写字母，剔除 I/L/O/U
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_code() -> str:
    """生成 T2C-XXXX-XXXX-XXXX 形态的邀请码。"""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    prefix = settings.INVITE_CODE_PREFIX
    return f"{prefix}{body[:4]}-{body[4:8]}-{body[8:12]}"


def normalize_code(code: str) -> str:
    """统一大写 + 去首尾空白（用户可能小写/带空格粘贴）。"""
    return (code or "").strip().upper()


def consume_code(db, code: str, user_id: int) -> bool:
    """原子核销邀请码。必须在注册事务内调用。

    rowcount==1 才算核销成功；并发下同一码只有第一个事务能抢到。
    过期码 / 非 issued 状态 / 码不存在 → False。
    """
    now = datetime.utcnow()
    result = db.execute(
        update(InviteCode)
        .where(
            InviteCode.code == normalize_code(code),
            InviteCode.status == "issued",
            InviteCode.expires_at > now,
        )
        .values(status="used", used_at=now, used_by_user_id=user_id)
    )
    return result.rowcount == 1


def find_pending_by_email(db, email: str, cooldown_hours: int):
    """冷却期内同邮箱的 pending 申请（申请接口的幂等依据）。

    返回 None 表示可以新建。冷却时间为 0 表示不限制。
    只匹配 pending：已拒绝/已使用的申请不阻塞重新申请。
    """
    if cooldown_hours <= 0:
        return None
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
    return (
        db.query(InviteCode)
        .filter(
            InviteCode.applicant_email == email,
            InviteCode.status == "pending",
            InviteCode.created_at > cutoff,
        )
        .first()
    )


def issue_code(db, invite) -> str:
    """给已审批的申请发放码：pending/rejected → issued。

    只负责码与时效字段；decided_by / reject_reason 由路由层设置
    （路由层还要在事务提交后发信 —— 发信失败不回滚已发放的码）。
    """
    if invite.status not in ("pending", "rejected"):
        raise ValueError(f"当前状态（{invite.status}）不可发放邀请码")
    invite.status = "issued"
    invite.code = generate_code()
    invite.decided_at = datetime.utcnow()
    invite.expires_at = datetime.utcnow() + timedelta(days=settings.INVITE_CODE_TTL_DAYS)
    invite.delivery_status = "pending"
    return invite.code
