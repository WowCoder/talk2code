# -*- coding: utf-8 -*-
"""市集查询服务（设计文档 §5）。

两条硬边界：
1. **只出已上架站点** —— `market_visible` 是 opt-in，未上架的一律不进列表。
2. **响应不含 view_count / verify_status** —— 沿用既有「UI 暴露边界」约定：
   view_count 是可刷的数字，露出来等于邀请人刷；verify_status 常是平台自身
   复验异常，属于纯负向信号。两者都保留在后端供排障，不下发到市集卡片。
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from config import settings
from models.models import PublishedSite, SiteComment, SiteLike, SiteVisitDedup, User
from utils.db import get_db

from .heat import compute_heat
from .thumbs import thumb_url

# 分页上限：防止 page_size=100000 把整表拉出来
MAX_PAGE_SIZE = 50

CARD_FIELDS = (
    "slug", "title", "author", "heat", "like_count",
    "comment_count", "listed_at", "url", "liked",
)


def _clamp_page(page, page_size):
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or settings.MARKET_PAGE_SIZE)
    except (TypeError, ValueError):
        page_size = settings.MARKET_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    return page, page_size


# 分类白名单：前端 chips 与后端校验共用同一份，避免两边各写一套出现
# 「前端能选、后端拒收」的错位。
CATEGORIES = ("game", "tool", "admin", "landing", "other")


def normalize_category(raw) -> str:
    v = (raw or "").strip().lower()
    return v if v in CATEGORIES else ""


def list_sites(
    *,
    sort: str = "hot",
    page: int = 1,
    page_size: Optional[int] = None,
    viewer_user_id: Optional[int] = None,
    category: Optional[str] = None,
    window: Optional[str] = None,
) -> dict:
    """市集列表。公开调用（viewer_user_id 为空时按游客处理）。

    window='week' → 只统计最近 7 天上架的作品（周榜）；
    category → 按分类过滤，非法值一律当作「不过滤」而不是报错（公开端点的
    参数不该因为一个枚举值就把整页打成 400）。
    """
    page, page_size = _clamp_page(page, page_size)
    cat = normalize_category(category)

    with get_db() as db:
        q = db.query(PublishedSite).filter(PublishedSite.market_visible.is_(True))
        if cat:
            q = q.filter(PublishedSite.category == cat)
        if window == "week":
            q = q.filter(PublishedSite.market_listed_at >= datetime.utcnow() - timedelta(days=7))
        sites = q.all()
        if not sites:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

        site_ids = [s.id for s in sites]

        # 三个聚合各查一次，避免每行一次查询（N+1）
        uniq_map = dict(
            db.query(SiteVisitDedup.site_id, func.count(func.distinct(SiteVisitDedup.fingerprint)))
            .filter(SiteVisitDedup.site_id.in_(site_ids))
            .group_by(SiteVisitDedup.site_id)
            .all()
        )
        like_map = dict(
            db.query(SiteLike.site_id, func.count(SiteLike.id))
            .filter(SiteLike.site_id.in_(site_ids))
            .group_by(SiteLike.site_id)
            .all()
        )
        comment_map = dict(
            db.query(SiteComment.site_id, func.count(SiteComment.id))
            .filter(SiteComment.site_id.in_(site_ids), SiteComment.is_deleted.is_(False))
            .group_by(SiteComment.site_id)
            .all()
        )
        user_map = {
            u.id: u.username
            for u in db.query(User).filter(User.id.in_({s.user_id for s in sites})).all()
        }

        liked_ids = set()
        if viewer_user_id:
            liked_ids = {
                row[0] for row in db.query(SiteLike.site_id)
                .filter(SiteLike.user_id == viewer_user_id, SiteLike.site_id.in_(site_ids))
                .all()
            }

        now = datetime.utcnow()
        items = []
        for s in sites:
            base_at = s.market_listed_at or s.created_at
            hours = (now - base_at).total_seconds() / 3600.0 if base_at else 0.0
            items.append({
                "slug": s.slug,
                "title": (s.author_note or "").strip() or (s.title or "未命名作品"),
                "author": user_map.get(s.user_id, ""),
                "author_id": s.user_id,
                "heat": compute_heat(
                    uniq_map.get(s.id, 0),
                    like_map.get(s.id, 0),
                    comment_map.get(s.id, 0),
                    hours,
                ),
                "like_count": like_map.get(s.id, 0),
                "comment_count": comment_map.get(s.id, 0),
                "category": s.category or "",
                "listed_at": base_at.isoformat() if base_at else None,
                "url": _published_url(s.slug),
                "thumb": thumb_url(s),
                "liked": s.id in liked_ids,
            })

    if sort == "new":
        items.sort(key=lambda x: (x["listed_at"] or ""), reverse=True)
    else:
        items.sort(key=lambda x: x["heat"], reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_listed_site(slug: str) -> Optional[PublishedSite]:
    """取一个**已上架**站点。未上架 / 不存在一律返回 None（对外统一 404）。"""
    with get_db() as db:
        return db.query(PublishedSite).filter(
            PublishedSite.slug == slug,
            PublishedSite.market_visible.is_(True),
        ).first()


def _published_url(slug: str):
    # 唯一真值来源：链接只能由这里构造，禁止自行拼接
    from services.publish.urls import published_url
    return published_url(slug)
