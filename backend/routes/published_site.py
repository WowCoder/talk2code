# -*- coding: utf-8 -*-
"""已发布站点 Host 路由（plan B8）：按 Host 头把 <slug>.<APEX> 解析到静态产物。

用 before_request 短路：仅当请求 Host 是**合法 slug 形态**的子域时才接管，
否则 return None 让正常 /api 路由与主站 SPA 继续，避免与既有路由冲突
（尤其不能让主站的 www.<APEX> 被当成发布站点）。
安全红线（设计 §6）：
- 独立 apex（不承载主站 cookie）
- 每站点独占 origin（<slug>.apex 硬要求）
- unlisted → X-Robots-Tag: noindex
- 放开 same-origin：绝不回 CSP: sandbox（否则 localStorage 抛 SecurityError）
- 路径穿越 → 403
"""
import mimetypes
from datetime import date

from flask import Response, abort, request
from factory import app, logger
from config import settings

from models.models import PublishedSite
from utils.db import get_db
from services.publish.bundle import is_internal_path
from services.publish.decorate import market_url, render_badge, should_inject_badge
from services.publish.slug import is_valid_slug
from services.publish.store import LocalFSStore, StoreError


def _store() -> LocalFSStore:
    # 延迟构造：settings.PUBLISH_STORE_DIR 可能在测试期被改写
    return LocalFSStore()


@app.before_request
def _published_site_host_route():
    apex = settings.PUBLISH_APEX
    if not apex:
        return None
    # DNS 主机名大小写不敏感；slug 字母表为大写 Crockford Base32，
    # 故仅对 slug 部分统一大写后再校验 / 查表。
    host = request.host.split(':')[0].strip().lower()
    suffix = '.' + apex.lower()
    if not host.endswith(suffix):
        return None

    slug = host[:-len(suffix)].upper()
    if not is_valid_slug(slug):
        # 不是 slug 形态的子域（www / blog / api …）一律交回正常路由。
        # ⚠️ 这里**不能** abort(404)：主站自己也常挂在 www.<apex> 上，
        # 而 www.<apex> 同样以 .<apex> 结尾，硬 404 会在配置 PUBLISH_APEX
        # 之后把主站打成 404。slug 是 20 位 Crockford Base32，不会与
        # www 这类常规子域碰撞，因此按形态区分是安全的。
        return None

    filepath = request.path.lstrip('/') or 'index.html'
    if filepath.startswith('/') or '..' in filepath or filepath.startswith('~'):
        abort(403)
    # 平台内部文件（.task/ 等）不对公网提供。打包侧已过滤，这里再挡一次：
    # 修复前发布的产物目录里已经躺着 .task/TASK_STATE.md 等文件。
    if is_internal_path(filepath):
        abort(404)

    with get_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if not site or not site.current_hash:
            abort(404)
        content_hash = site.current_hash
        visibility = site.visibility
        market_visible = bool(site.market_visible)
        badge_enabled = bool(site.badge_enabled)
        site_pk = site.id

        # 访问计数：只在页面级请求（/ 或 index.html） +1，避免子资源
        # （css / js / img / favicon 等）请求污染数据 —— 否则加载一个页面
        # 会触发 5~6 次 +1，view_count 完全失去参考价值。
        # 顺带：同 session 内 commit，避免单独开 db 上下文的多余连接开销。
        if filepath in ('index.html', '', '/'):
            site.view_count = (site.view_count or 0) + 1
            # 市集热度用的「去重访客」也在这一刻记账：发布站是独立 origin，
            # 无法回调主站 API，去重只能由服务端在返回产物时完成。
            # 只在已上架时记 —— 未上架站点不参与热度排名，不必留账本。
            if market_visible:
                from services.market.visits import record_visit, visitor_fingerprint

                _day = date.today()
                record_visit(
                    db,
                    site_pk,
                    visitor_fingerprint(
                        request.remote_addr or '',
                        request.headers.get('User-Agent', ''),
                        _day.isoformat(),
                    ),
                    _day,
                )
            db.commit()

    try:
        data = _store().get(content_hash, filepath)
    except KeyError:
        abort(404)
    except StoreError:
        abort(403)

    # 来源 badge：只给「已上架」站点注入，且复验请求跳过（详见 decorate 模块注释）。
    # 未上架站点返回的字节必须与用户产物完全一致。
    if should_inject_badge(
        is_entry=filepath == 'index.html',
        market_visible=market_visible,
        badge_enabled=badge_enabled,
        is_verify_request=request.headers.get('X-T2C-Verify') == '1',
    ):
        _url = market_url()
        if _url:
            data = render_badge(data, url=_url, cache_key=f"{content_hash}:{int(badge_enabled)}")

    mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
    resp = Response(data, mimetype=mime)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    if visibility == 'unlisted':
        resp.headers['X-Robots-Tag'] = 'noindex'
    # 注意：刻意不设置 CSP: sandbox —— 已发布站点需 same-origin 才能用 localStorage
    return resp
