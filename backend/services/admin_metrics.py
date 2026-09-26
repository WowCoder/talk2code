# -*- coding: utf-8 -*-
"""后台运营指标聚合。

口径约定（改这里之前先想清楚为什么）：

1. **需求热度必须复用 services/market/heat.py 的 compute_heat**，不另造公式。
   另造一个就会出现「后台说 87、前台显示 52」的信任崩塌 —— 同一个数字在两个
   地方不一致，用户会认为整个后台数据是假的。

2. **需求活跃度里的 JSON 长度在 Python 侧算**（len(dialogue_history)），不写
   `json_array_length` SQL —— 该函数在 SQLite 与 PG 上语法不同，为 Top 20 行
   数据引入方言分支不划算。

3. 这些是**业务指标**，只走 /api/admin/metrics（需鉴权）。公开的 /api/metrics
   保持纯技术指标不变 —— Prometheus 抓取端点通常无鉴权，往里塞用户数等于把
   运营数据公开。
"""
from datetime import datetime, timedelta

from sqlalchemy import func, distinct

from config import settings
from utils.db import get_db
from services.market.heat import compute_heat, display_heat

TOP_N = 20


def _since(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


def _count(db, model, **filters) -> int:
    q = db.query(func.count(model.id))
    for key, value in filters.items():
        q = q.filter(getattr(model, key) == value)
    return q.scalar() or 0


def collect_metrics() -> dict:
    """汇总后台看板所需的全部指标。"""
    from models import (
        User, Requirement, PublishedSite, SiteLike, SiteComment, SiteVisitDedup,
        InviteCode, AgentTrace,
    )

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = _since(7)

    with get_db() as db:
        # ---------- 用户 ----------
        total_users = (
            db.query(func.count(User.id))
            .filter(User.username != settings.DEMO_USERNAME)
            .scalar() or 0
        )
        new_users_today = (
            db.query(func.count(User.id))
            .filter(User.username != settings.DEMO_USERNAME, User.create_time >= today)
            .scalar() or 0
        )
        new_users_7d = (
            db.query(func.count(User.id))
            .filter(User.username != settings.DEMO_USERNAME, User.create_time >= week_ago)
            .scalar() or 0
        )
        active_users_7d = (
            db.query(func.count(distinct(Requirement.user_id)))
            .filter(Requirement.create_time >= week_ago)
            .scalar() or 0
        )

        # ---------- 需求 ----------
        req_base = db.query(Requirement).filter(Requirement.is_deleted.is_(False))
        total_reqs = req_base.count()
        new_reqs_today = req_base.filter(Requirement.create_time >= today).count()
        new_reqs_7d = req_base.filter(Requirement.create_time >= week_ago).count()

        status_rows = (
            db.query(Requirement.status, func.count(Requirement.id))
            .filter(Requirement.is_deleted.is_(False))
            .group_by(Requirement.status)
            .all()
        )
        status_dist = {row[0] or 'unknown': row[1] for row in status_rows}

        finished = status_dist.get('finished', 0)
        failed = status_dist.get('failed', 0)
        settled = finished + failed
        # 完成率的分母只算「已有结论」的需求 —— 把 processing/pending 算进分母
        # 会让「正在跑的任务越多，完成率越低」，指标就失去了意义
        completion_rate = round(finished / settled, 4) if settled else 0.0

        failed_reasons = (
            db.query(Requirement.error_message, func.count(Requirement.id))
            .filter(Requirement.is_deleted.is_(False), Requirement.status == 'failed')
            .group_by(Requirement.error_message)
            .order_by(func.count(Requirement.id).desc())
            .limit(5)
            .all()
        )
        top_failures = [
            {'reason': (row[0] or '未记录')[:200], 'count': row[1]}
            for row in failed_reasons
        ]

        # ---------- 发布与市集 ----------
        published_total = db.query(func.count(PublishedSite.id)).scalar() or 0
        listed_total = (
            db.query(func.count(PublishedSite.id))
            .filter(PublishedSite.market_visible.is_(True))
            .scalar() or 0
        )
        total_views = db.query(func.coalesce(func.sum(PublishedSite.view_count), 0)).scalar() or 0
        total_likes = db.query(func.count(SiteLike.id)).scalar() or 0
        total_comments = (
            db.query(func.count(SiteComment.id))
            .filter(SiteComment.is_deleted.is_(False))
            .scalar() or 0
        )

        # ---------- 需求热度榜 ----------
        listed_sites = (
            db.query(PublishedSite)
            .filter(PublishedSite.market_visible.is_(True))
            .all()
        )
        site_ids = [s.id for s in listed_sites]
        like_counts = {}
        comment_counts = {}
        visit_counts = {}
        if site_ids:
            for row in (
                db.query(SiteLike.site_id, func.count(SiteLike.id))
                .filter(SiteLike.site_id.in_(site_ids))
                .group_by(SiteLike.site_id)
                .all()
            ):
                like_counts[row[0]] = row[1]
            for row in (
                db.query(SiteComment.site_id, func.count(SiteComment.id))
                .filter(SiteComment.site_id.in_(site_ids), SiteComment.is_deleted.is_(False))
                .group_by(SiteComment.site_id)
                .all()
            ):
                comment_counts[row[0]] = row[1]
            for row in (
                db.query(SiteVisitDedup.site_id, func.count(distinct(SiteVisitDedup.fingerprint)))
                .filter(SiteVisitDedup.site_id.in_(site_ids))
                .group_by(SiteVisitDedup.site_id)
                .all()
            ):
                visit_counts[row[0]] = row[1]

        heat_rows = []
        for s in listed_sites:
            base = s.market_listed_at or s.created_at or now
            hours = max(0.0, (now - base).total_seconds() / 3600.0)
            heat = compute_heat(
                visit_counts.get(s.id, 0),
                like_counts.get(s.id, 0),
                comment_counts.get(s.id, 0),
                hours,
            )
            heat_rows.append({
                'requirement_id': s.requirement_id,
                'slug': s.slug,
                'title': s.title,
                'heat': display_heat(heat),
                'heat_raw': round(heat, 4),
                'views': visit_counts.get(s.id, 0),
                'likes': like_counts.get(s.id, 0),
                'comments': comment_counts.get(s.id, 0),
            })
        heat_rows.sort(key=lambda r: r['heat_raw'], reverse=True)
        top_heat = heat_rows[:TOP_N]

        # ---------- 邀请码 ----------
        invite_rows = (
            db.query(InviteCode.status, func.count(InviteCode.id))
            .group_by(InviteCode.status)
            .all()
        )
        invite_dist = {row[0]: row[1] for row in invite_rows}
        issued = invite_dist.get('issued', 0) + invite_dist.get('used', 0)
        used = invite_dist.get('used', 0)
        # 注册转化：发出来的码有多少真的变成了账号
        invite_conversion = round(used / issued, 4) if issued else 0.0

        # ---------- 技术侧（可观测性） ----------
        traces_7d = (
            db.query(func.count(AgentTrace.id))
            .filter(AgentTrace.created_at >= week_ago)
            .scalar() or 0
        )
        avg_duration = (
            db.query(func.coalesce(func.avg(AgentTrace.duration_ms), 0))
            .filter(AgentTrace.created_at >= week_ago)
            .scalar() or 0
        )
        total_cost = (
            db.query(func.coalesce(func.sum(AgentTrace.total_cost), 0))
            .filter(AgentTrace.created_at >= week_ago)
            .scalar() or 0
        )

    return {
        'users': {
            'total': total_users,
            'new_today': new_users_today,
            'new_7d': new_users_7d,
            'active_7d': active_users_7d,
        },
        'requirements': {
            'total': total_reqs,
            'new_today': new_reqs_today,
            'new_7d': new_reqs_7d,
            'status': status_dist,
            'completion_rate': completion_rate,
            'top_failures': top_failures,
        },
        'publish': {
            'published_total': published_total,
            'market_listed': listed_total,
            'total_views': int(total_views),
            'total_likes': total_likes,
            'total_comments': total_comments,
        },
        'invites': {
            'distribution': invite_dist,
            'pending': invite_dist.get('pending', 0),
            'issued': issued,
            'used': used,
            'conversion': invite_conversion,
        },
        'top_heat': top_heat,
        'observability': {
            'traces_7d': traces_7d,
            'avg_duration_ms': round(float(avg_duration or 0), 1),
            'cost_7d': round(float(total_cost or 0), 4),
        },
    }
