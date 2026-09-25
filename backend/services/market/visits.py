# -*- coding: utf-8 -*-
"""市集访客去重（设计文档 §3.3 / §5.1）。

为什么不用 view_count 当热度
----------------------------
`view_count` 在每个 `index.html` 请求 +1，刷新一次就 +1 —— 拿它排序等于
邀请作者自己刷榜。所以热度用的是**去重访客**，`site_visit_dedup` 是它的账本。

为什么只存哈希
--------------
指纹 = sha256(salt | ip | ua | day)[:32]。IP 与 UA 只参与计算、不落库 ——
既减少敏感数据留存，也避免 UA 字符串膨胀账本。day 参与计算使账本按天分桶，
天然过期、可定期清理。
"""
import hashlib
from datetime import date
from typing import Optional

from config import settings
from models.models import SiteVisitDedup


def visitor_fingerprint(remote_addr: str, user_agent: str, day: str) -> str:
    """计算访客指纹。day 传 ISO 日期字符串（如 '2026-09-25'）。"""
    salt = settings.MARKET_VISIT_SALT or (settings.JWT_SECRET_KEY or "")[:16]
    raw = f"{salt}|{remote_addr}|{user_agent}|{day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def current_day(now=None) -> date:
    return (now or date.today())


def record_visit(db, site_id: int, fingerprint: str, day: Optional[date] = None) -> bool:
    """写入去重账本。已存在返回 False（不计入独立访客）。

    ⚠️ 必须用 SAVEPOINT（`begin_nested`）而不是 try/except + rollback：
    调用方（发布站 Host 路由）在同一个事务里还挂着 `view_count` 自增，
    整事务回滚会把访问数一起吞掉。SAVEPOINT 只回滚这一条 INSERT。
    """
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.add(SiteVisitDedup(
                site_id=site_id,
                fingerprint=fingerprint,
                day=day or current_day(),
            ))
        return True
    except IntegrityError:
        return False
