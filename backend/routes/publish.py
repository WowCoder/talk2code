# -*- coding: utf-8 -*-
"""一键发布 API（plan B7）：发布 / 取消发布 / 查询发布状态。

鉴权：所有接口 @jwt_required；他人 slug / requirement_id 一律返回 404
（与预览令牌同策略，避免响应差异泄露资源存在性）。
Ship C 复验通过 PublishService.publish(on_published=...) 解耦触发。
"""
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from config import settings
from factory import app, logger
from utils.db import get_db, transactional_db

from models.models import Requirement, PublishedSite
from services.market.cover import get_cover
from services.publish.service import PublishService, PublishError
from services.publish.slug import is_valid_slug
from services.publish.urls import published_host as _published_host
from services.publish.urls import published_url as _published_url


def _publish_warnings(host: str | None) -> list:
    """发布成功后仍存在的环境问题。

    PUBLISH_APEX 为空时 Host 路由整体关闭：产物已落盘、站点行已建立，
    但**没有任何可访问的公网地址**。此前这里静默返回 url=null，前端再自行
    拼一个 nip.io 兜底，点开必然落到主站 SPA（要登录 + 首页）——用户会
    当成「一键发布坏了」。所以必须显式告警，由前端当成阻断级提示。
    """
    if host:
        return []
    return [
        '未配置 PUBLISH_APEX：产物已保存，但当前环境没有可访问的发布域名，链接不可用'
    ]


def _site_payload(site) -> dict:
    """发布状态的对外表示（三个端点共用同一份字段表）。

    共用而非各写各的：前端只按一个形状解析；此前 POST /api/publish 少返回
    view_count / created_at / updated_at，于是刚发布完那一刻界面渲染出空时间
    与「0 次访问」（必须刷新页面、走 by-requirement 才对）—— 同一份数据在
    三个端点上漂移出的不一致，正是这种「看起来能用但数字不对」的来源。
    """
    host = _published_host(site.slug)
    return {
        'slug': site.slug,
        'version': site.version,
        'visibility': site.visibility,
        'runtime_tier': site.runtime_tier,
        'verify_status': site.verify_status,
        'current_hash': site.current_hash,
        'view_count': site.view_count,
        'title': site.title,
        'requirement_id': site.requirement_id,
        'published_host': host,
        'url': _published_url(site.slug),
        'warnings': _publish_warnings(host),
        'created_at': site.created_at.isoformat() if site.created_at else None,
        'updated_at': site.updated_at.isoformat() if site.updated_at else None,
        'verified_at': site.verified_at.isoformat() if site.verified_at else None,
        # 创意市集：上架状态与作者侧开关。这三个端点都是**作者本人**可见，
        # 把上架态并进同一份载荷，省掉发布面板再发一次请求。
        'listed': bool(site.market_visible),
        'author_note': site.author_note or '',
        'badge_enabled': bool(site.badge_enabled),
        'category': site.category or '',
        # 是否用了自定义封面（缩略图端点会优先出封面，这里只回一个布尔，
        # 作者面板靠它决定显示「移除封面」还是「上传封面」）
        'cover': get_cover(site.slug)[0] is not None,
    }


@app.route('/api/publish', methods=['POST'])
@jwt_required()
def publish_requirement():
    """发布某需求当前生成的代码为一个可分享 URL。

    body: {"requirement_id": 123, "visibility": "unlisted"}
    同一需求重复发布 → 同一 slug、version+1、内容不变则零写入。
    """
    current_user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    requirement_id = data.get('requirement_id')
    visibility = data.get('visibility', 'unlisted')
    if not requirement_id:
        return jsonify({'error': 'requirement_id 必填'}), 400

    with get_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == int(requirement_id),
            Requirement.user_id == current_user_id,
        ).first()
        if not requirement:
            return jsonify({'error': '需求不存在'}), 404
        if not requirement.code_files:
            return jsonify({'error': '该需求尚无生成代码'}), 400
        svc = PublishService()
        try:
            site = svc.publish(
                user_id=current_user_id,
                code_files=requirement.code_files,
                requirement_id=requirement.id,
                title=requirement.title or '',
                visibility=visibility,
                on_published=_trigger_publish_verify,
            )
        except PublishError as e:
            return jsonify({'error': str(e)}), 400

    return jsonify(_site_payload(site)), 200


@app.route('/api/publish/<slug>/unpublish', methods=['POST'])
@jwt_required()
def unpublish_site(slug: str):
    """取消发布：删除站点映射（bundle 产物保留以便后续去重）。"""
    current_user_id = int(get_jwt_identity())
    if not is_valid_slug(slug):
        return jsonify({'error': '非法 slug'}), 404
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if not site or site.user_id != current_user_id:
            return jsonify({'error': '站点不存在'}), 404
        db.delete(site)
    return jsonify({'ok': True, 'slug': slug}), 200


@app.route('/api/publish/by-requirement/<int:requirement_id>', methods=['GET'])
@jwt_required()
def publish_by_requirement(requirement_id: int):
    """按需求查询当前发布状态。

    用于详情页「发布」TAB 首屏加载：刷新页面后无需依赖前端内存，
    直接从 PublishedSite 表读取该需求最近一次发布记录。

    ⚠️ 故意设计为「永远 200」而非 404：避免 Chrome DevTools Network 面板
    对未发布需求显示一行红色 404 噪音（详情页打开即查，会一直跳出来）。
    安全性靠 body 内的 `published: bool` 判定 —— 未发布、req 不存在、
    req 不属于当前用户，三种情况 body 形态完全一致（`{published: false}`），
    response 形状无差异，外部观察者无法据此区分这三种场景（与 publish_info
    「404 一致」的同源策略，从 HTTP code 维度降级到 body 维度）。
    """
    current_user_id = int(get_jwt_identity())
    with get_db() as db:
        # 同一用户 × 同一需求理论上最多一条 PublishedSite 行；按 updated_at
        # 降序取最新作为权威来源（兜底历史脏数据）。
        site = db.query(PublishedSite).filter_by(
            requirement_id=requirement_id,
            user_id=current_user_id,
        ).order_by(PublishedSite.updated_at.desc()).first()
        if not site:
            return jsonify({'published': False}), 200
        payload = _site_payload(site)
        payload['published'] = True
        return jsonify(payload), 200


@app.route('/api/publish/<slug>/info', methods=['GET'])
@jwt_required()
def publish_info(slug: str):
    """查询发布状态（前端展示用）。"""
    current_user_id = int(get_jwt_identity())
    if not is_valid_slug(slug):
        return jsonify({'error': '非法 slug'}), 404
    with get_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if not site or site.user_id != current_user_id:
            return jsonify({'error': '站点不存在'}), 404
        return jsonify(_site_payload(site)), 200


def _trigger_publish_verify(slug: str, content_hash: str):
    """Ship C 复验触发（设计 §5.3）。

    解耦：发布主流程不依赖 Chromium 是否可用，也不等复验跑完。具体复验实现见
    services/publish/verify.py；若环境不可用，复验服务自身把站点标 degraded /
    留 pending，不在此处阻断发布。

    默认异步（``PUBLISH_VERIFY_ASYNC``）：复验要起 Chromium，单次 15s 起、
    带 AC 更久；同步跑在发布请求线程里会让 POST /api/publish 长时间不返回，
    前端超时报错而站点其实已发布。测试里置 False 走同步路径，便于断言。
    """
    try:
        from services.publish.verify import (
            schedule_publish_verify,
            trigger_publish_verify,
        )

        if settings.PUBLISH_VERIFY_ASYNC:
            schedule_publish_verify(slug, content_hash)
        else:
            trigger_publish_verify(slug, content_hash)
    except Exception as e:  # 复验失败不阻断发布
        logger.warning(f"发布后复验触发失败（已放行发布）: slug={slug} err={e}")
