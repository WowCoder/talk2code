# -*- coding: utf-8 -*-
"""创意市集 API（Ship 1）：公开列表 / 详情 + 点赞 + 上架开关。

设计文档 docs/design/marketplace.md §5。

鉴权分层：
- 列表与详情**公开**（免登录可逛）—— 与已发布站点免登录直开保持一致，
  也是"陌生人 → 注册"转化路径的入口。故必须挂 `rate_limit_market`。
- 点赞与上架开关需登录。他人资源一律 404（与既有 publish 路由同策略，
  避免响应差异泄露资源存在性）。
"""
from datetime import datetime, timedelta

from flask import jsonify, make_response, request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from factory import app, rate_limit_market
from models.models import (
    PublishedSite,
    SiteComment,
    SiteLike,
    SiteVisitDedup,
    User,
    UserFollow,
)
from utils.db import get_db, transactional_db

from services.market.comments import too_frequent, validate_body
from services.market.cover import delete_cover, get_cover, save_cover, validate_cover
from services.market.heat import compute_heat
from services.market.service import CATEGORIES, list_sites, normalize_category
from services.market.thumbs import ensure_thumb, get_thumb, thumb_url
from services.publish.slug import is_valid_slug
from services.publish.store import LocalFSStore, StoreError
from services.publish.urls import published_url


def _optional_user_id():
    """取当前登录用户；未登录 / token 无效 → None（游客）。"""
    try:
        verify_jwt_in_request(optional=True)
        ident = get_jwt_identity()
        return int(ident) if ident else None
    except Exception:
        return None


def _detail_payload(site, viewer_user_id=None) -> dict:
    """详情卡片。字段表与列表一致，另加作者 id（供关注入口使用）。"""
    with get_db() as db:
        uniq = db.query(func.count(func.distinct(SiteVisitDedup.fingerprint))).filter(
            SiteVisitDedup.site_id == site.id
        ).scalar() or 0
        likes = db.query(func.count(SiteLike.id)).filter(
            SiteLike.site_id == site.id
        ).scalar() or 0
        comments = db.query(func.count(SiteComment.id)).filter(
            SiteComment.site_id == site.id, SiteComment.is_deleted.is_(False)
        ).scalar() or 0
        liked = False
        following_author = False
        if viewer_user_id:
            liked = db.query(SiteLike.id).filter(
                SiteLike.site_id == site.id, SiteLike.user_id == viewer_user_id
            ).first() is not None
            following_author = db.query(UserFollow.id).filter(
                UserFollow.follower_id == viewer_user_id,
                UserFollow.followee_id == site.user_id,
            ).first() is not None
        author = db.query(User.username).filter(User.id == site.user_id).scalar() or ""
        followers = db.query(func.count(UserFollow.id)).filter(
            UserFollow.followee_id == site.user_id
        ).scalar() or 0

    base_at = site.market_listed_at or site.created_at
    hours = (datetime.utcnow() - base_at).total_seconds() / 3600.0 if base_at else 0.0
    return {
        "slug": site.slug,
        "title": (site.author_note or "").strip() or (site.title or "未命名作品"),
        "author": author,
        "author_id": site.user_id,
        "author_followers": followers,
        "following_author": following_author,
        "heat": compute_heat(uniq, likes, comments, hours),
        "like_count": likes,
        "category": site.category or "",
        "comment_count": comments,
        "listed_at": base_at.isoformat() if base_at else None,
        "url": published_url(site.slug),
        "thumb": thumb_url(site),
        "liked": liked,
    }


def _listed_site_or_404(db, slug: str):
    """已上架站点；不存在 / 未上架一律 None（对外统一 404）。"""
    if not is_valid_slug(slug):
        return None
    return db.query(PublishedSite).filter(
        PublishedSite.slug == slug,
        PublishedSite.market_visible.is_(True),
    ).first()


@app.route('/api/market/sites', methods=['GET'])
@rate_limit_market
def market_list():
    """市集列表（公开）。sort=hot|new，page/page_size 分页。"""
    sort = request.args.get('sort', 'hot')
    if sort not in ('hot', 'new'):
        sort = 'hot'
    data = list_sites(
        sort=sort,
        page=request.args.get('page', 1),
        page_size=request.args.get('page_size'),
        viewer_user_id=_optional_user_id(),
        category=request.args.get('category'),
        window=request.args.get('window'),
    )
    # 分类白名单随列表一起下发：前端 chips 不再自己维护一份枚举，
    # 避免「前端能选、后端拒收」的错位。
    data['categories'] = list(CATEGORIES)
    return jsonify(data), 200


@app.route('/api/market/thumbs/<slug>.png', methods=['GET'])
@rate_limit_market
def market_thumb(slug: str):
    """市集缩略图：**作者自定义封面优先**，其次才是首页自动截图。

    自动截图命中缓存直接返回；未命中则现场截一张（首访约数秒）。两者都拿不到
    一律 404 —— 前端回退到色块占位。缩略图是装饰，它的失败绝不能影响列表
    本身，所以这里既不重试也不报错页。
    """
    if not is_valid_slug(slug):
        return jsonify({'error': '站点不存在'}), 404

    with get_db() as db:
        # 按 slug 取站点（不限上架）：作者要在**上架前**预览自己传的封面，
        # 若只认已上架站点，未上架作品的封面预览会 404。
        site = db.query(PublishedSite).filter(PublishedSite.slug == slug).first()
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        owner_id = site.user_id
        listed = bool(site.market_visible)
        content_hash = site.current_hash

    # 未上架作品的封面仍需作者本人可见（上面那条预览路径），游客一律 404 ——
    # 否则「撤下市集」之后封面还能被公网直连取到，撤下就只是半撤。
    if not listed and _optional_user_id() != owner_id:
        return jsonify({'error': '站点不存在'}), 404

    # 封面是作者手动换的，不能吃长缓存：否则换完图要等一天才生效。
    cover_path, cover_mime = get_cover(slug)
    if cover_path:
        resp = make_response(cover_path.read_bytes())
        resp.headers['Content-Type'] = cover_mime
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    if not content_hash:
        return jsonify({'error': '站点不存在'}), 404

    cached = get_thumb(content_hash)
    if not cached:
        try:
            entry = LocalFSStore().get(content_hash, 'index.html')
        except (KeyError, StoreError):
            return jsonify({'error': '缩略图不可用'}), 404
        cached = ensure_thumb(content_hash, entry, published_url(slug))
        if not cached:
            return jsonify({'error': '缩略图不可用'}), 404

    resp = make_response(cached.read_bytes())
    resp.headers['Content-Type'] = 'image/png'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    # 按内容寻址命名，内容不变则永久有效
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@app.route('/api/market/sites/<slug>', methods=['GET'])
@rate_limit_market
def market_detail(slug: str):
    """市集详情（公开）。未上架 / 不存在 → 404。"""
    with get_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        return jsonify(_detail_payload(site, _optional_user_id())), 200


@app.route('/api/market/sites/<slug>/like', methods=['POST'])
@jwt_required()
def market_like(slug: str):
    """点赞。给自己的作品点赞 → 400（静默成功是假绿，必须显式报错）。"""
    user_id = int(get_jwt_identity())

    with transactional_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        if site.user_id == user_id:
            return jsonify({'error': '不能给自己的作品点赞'}), 400
        if db.query(SiteLike).filter_by(site_id=site.id, user_id=user_id).first() is None:
            try:
                with db.begin_nested():
                    db.add(SiteLike(site_id=site.id, user_id=user_id))
            except IntegrityError:
                pass  # 并发下已被别人插了，等效于已点赞
        site_id = site.id

    return jsonify({'ok': True, 'liked': True, 'like_count': _like_count(site_id)}), 200


@app.route('/api/market/sites/<slug>/like', methods=['DELETE'])
@jwt_required()
def market_unlike(slug: str):
    """取消点赞。幂等：本来没赞也返回 200。"""
    user_id = int(get_jwt_identity())

    with transactional_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        db.query(SiteLike).filter_by(site_id=site.id, user_id=user_id).delete(
            synchronize_session=False
        )
        site_id = site.id

    return jsonify({'ok': True, 'liked': False, 'like_count': _like_count(site_id)}), 200


@app.route('/api/publish/<slug>/market', methods=['PATCH'])
@jwt_required()
def update_market_listing(slug: str):
    """上架 / 撤下 + 一句话介绍 + badge 开关（仅作者本人）。

    撤下时清空 `market_listed_at`：再次上架应重新计时，否则热度的时间基准
    还挂在几个月前，新作品永远排不上来。
    """
    user_id = int(get_jwt_identity())
    if not is_valid_slug(slug):
        return jsonify({'error': '站点不存在'}), 404
    data = request.get_json(silent=True) or {}

    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if not site or site.user_id != user_id:
            return jsonify({'error': '站点不存在'}), 404

        if 'listed' in data:
            listed = bool(data['listed'])
            site.market_visible = listed
            site.market_listed_at = datetime.utcnow() if listed else None
        if 'author_note' in data:
            site.author_note = (data['author_note'] or '')[:120]
        if 'badge_enabled' in data:
            site.badge_enabled = bool(data['badge_enabled'])
        if 'category' in data:
            # 非法值静默归为「未分类」：这是作者的可选项，不值得为了一个枚举
            # 值让整个上架操作失败。
            site.category = normalize_category(data['category'])

        payload = {
            'slug': site.slug,
            'listed': bool(site.market_visible),
            'author_note': site.author_note or '',
            'badge_enabled': bool(site.badge_enabled),
            'category': site.category or '',
            'listed_at': site.market_listed_at.isoformat() if site.market_listed_at else None,
        }
    return jsonify(payload), 200


# ==================== 封面（Ship 4） ====================

def _own_site_or_404(db, slug: str, user_id: int):
    """自己的站点（不论是否上架）；他人资源一律 None（对外统一 404）。"""
    if not is_valid_slug(slug):
        return None
    site = db.query(PublishedSite).filter_by(slug=slug).first()
    return site if site and site.user_id == user_id else None


@app.route('/api/market/sites/<slug>/cover', methods=['POST'])
@jwt_required()
def market_upload_cover(slug: str):
    """上传封面（仅作者本人）。上架前后都能传：允许作者先备好图再上架。

    只认 PNG / JPEG 的魔数（不信 Content-Type），体积上限 2 MB。
    文件名一律用 slug，杜绝路径穿越与覆盖他人文件。
    """
    user_id = int(get_jwt_identity())
    file = request.files.get('file')
    if file is None:
        return jsonify({'error': '请选择一张图片'}), 400

    # 读进内存再校验：先落盘再校验会给「传 500MB 大文件」留出窗口。
    data = file.read()
    err = validate_cover(data)
    if err:
        return jsonify({'error': err}), 400

    with get_db() as db:
        if _own_site_or_404(db, slug, user_id) is None:
            return jsonify({'error': '站点不存在'}), 404

    if not save_cover(slug, data):
        return jsonify({'error': '只支持 PNG / JPEG 图片'}), 400
    return jsonify({'ok': True, 'cover': True}), 200


@app.route('/api/market/sites/<slug>/cover', methods=['DELETE'])
@jwt_required()
def market_delete_cover(slug: str):
    """移除封面（仅作者本人），回到首页自动截图。幂等。"""
    user_id = int(get_jwt_identity())
    with get_db() as db:
        if _own_site_or_404(db, slug, user_id) is None:
            return jsonify({'error': '站点不存在'}), 404
    delete_cover(slug)
    return jsonify({'ok': True, 'cover': False}), 200


def _like_count(site_id: int) -> int:
    with get_db() as db:
        return db.query(func.count(SiteLike.id)).filter(SiteLike.site_id == site_id).scalar() or 0


# ==================== 留言（Ship 2） ====================

@app.route('/api/market/sites/<slug>/comments', methods=['GET'])
@rate_limit_market
def market_comments(slug: str):
    """留言列表（公开），按时间倒序。软删的不返回。"""
    with get_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        rows = db.query(SiteComment).filter(
            SiteComment.site_id == site.id,
            SiteComment.is_deleted.is_(False),
        ).order_by(SiteComment.created_at.desc()).limit(100).all()
        user_ids = {r.user_id for r in rows}
        names = {u.id: u.username for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    items = [{
        'id': r.id,
        'author': names.get(r.user_id, ''),
        'author_id': r.user_id,
        'body': r.body,
        'created_at': r.created_at.isoformat() if r.created_at else None,
        'mine': False,
    } for r in rows]

    uid = _optional_user_id()
    if uid:
        for it in items:
            it['mine'] = it['author_id'] == uid
    return jsonify({'items': items, 'total': len(items)}), 200


@app.route('/api/market/sites/<slug>/comments', methods=['POST'])
@jwt_required()
def market_add_comment(slug: str):
    """发表留言。**不允许匿名** —— 匿名区没有可追责主体。"""
    user_id = int(get_jwt_identity())
    body = ((request.get_json(silent=True) or {}).get('body') or '')

    err = validate_body(body)
    if err:
        return jsonify({'error': err}), 400

    with transactional_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404

        # 频率：同一用户 60 秒内的自有留言条数（比按 IP 限流准，且不用额外存储）
        since = datetime.utcnow() - timedelta(seconds=60)
        recent = db.query(func.count(SiteComment.id)).filter(
            SiteComment.user_id == user_id, SiteComment.created_at >= since
        ).scalar() or 0
        if too_frequent(recent):
            return jsonify({'error': '留言太频繁了，稍后再试'}), 429

        c = SiteComment(site_id=site.id, user_id=user_id, body=body.strip())
        db.add(c)
        db.flush()
        cid = c.id

    with get_db() as db:
        c = db.get(SiteComment, cid)
        total = db.query(func.count(SiteComment.id)).filter(
            SiteComment.site_id == c.site_id, SiteComment.is_deleted.is_(False)
        ).scalar() or 0
        return jsonify({
            'ok': True,
            'id': cid,
            'comment_count': total,
            'created_at': c.created_at.isoformat() if c.created_at else None,
        }), 201


@app.route('/api/market/sites/<slug>/comments/<int:comment_id>', methods=['DELETE'])
@jwt_required()
def market_delete_comment(slug: str, comment_id: int):
    """删除留言：留言作者本人或**站点作者**可删（站点作者要能清理自己作品下的内容）。

    软删而非物理删：保留归属与时间戳，便于事后追溯。
    """
    user_id = int(get_jwt_identity())
    with transactional_db() as db:
        site = _listed_site_or_404(db, slug)
        if not site:
            return jsonify({'error': '站点不存在'}), 404
        c = db.query(SiteComment).filter(
            SiteComment.id == comment_id,
            SiteComment.site_id == site.id,
            SiteComment.is_deleted.is_(False),
        ).first()
        if not c:
            return jsonify({'error': '留言不存在'}), 404
        if c.user_id != user_id and site.user_id != user_id:
            return jsonify({'error': '没有权限删除这条留言'}), 403
        c.is_deleted = True

    return jsonify({'ok': True}), 200


# ==================== 关注 / 作者（Ship 2） ====================

@app.route('/api/market/users/<int:user_id>/follow', methods=['POST'])
@jwt_required()
def market_follow(user_id: int):
    """关注。不能关注自己 —— 静默成功会让"粉丝数"变成自娱自乐的数字。"""
    me = int(get_jwt_identity())
    if user_id == me:
        return jsonify({'error': '不能关注自己'}), 400

    with transactional_db() as db:
        if db.get(User, user_id) is None:
            return jsonify({'error': '用户不存在'}), 404
        if db.query(UserFollow).filter_by(follower_id=me, followee_id=user_id).first() is None:
            try:
                with db.begin_nested():
                    db.add(UserFollow(follower_id=me, followee_id=user_id))
            except IntegrityError:
                pass  # 并发下已关注，等效
    return jsonify({'ok': True, 'following': True, 'followers': _follower_count(user_id)}), 200


@app.route('/api/market/users/<int:user_id>/follow', methods=['DELETE'])
@jwt_required()
def market_unfollow(user_id: int):
    """取消关注。幂等。"""
    me = int(get_jwt_identity())
    with transactional_db() as db:
        db.query(UserFollow).filter_by(follower_id=me, followee_id=user_id).delete(
            synchronize_session=False
        )
    return jsonify({'ok': True, 'following': False, 'followers': _follower_count(user_id)}), 200


@app.route('/api/market/users/<int:user_id>', methods=['GET'])
@rate_limit_market
def market_author(user_id: int):
    """作者主页：公开作品数 + 粉丝数 + 关注数 + 我是否关注了他。"""
    uid = _optional_user_id()
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            return jsonify({'error': '用户不存在'}), 404
        site_count = db.query(func.count(PublishedSite.id)).filter(
            PublishedSite.user_id == user_id, PublishedSite.market_visible.is_(True)
        ).scalar() or 0
        followers = db.query(func.count(UserFollow.id)).filter(
            UserFollow.followee_id == user_id
        ).scalar() or 0
        following = db.query(func.count(UserFollow.id)).filter(
            UserFollow.follower_id == user_id
        ).scalar() or 0
        followed = False
        if uid:
            followed = db.query(UserFollow.id).filter(
                UserFollow.follower_id == uid, UserFollow.followee_id == user_id
            ).first() is not None

    return jsonify({
        'id': user_id,
        'username': user.username,
        'site_count': site_count,
        'followers': followers,
        'following': following,
        'followed_by_me': followed,
    }), 200


def _follower_count(user_id: int) -> int:
    with get_db() as db:
        return db.query(func.count(UserFollow.id)).filter(
            UserFollow.followee_id == user_id
        ).scalar() or 0
