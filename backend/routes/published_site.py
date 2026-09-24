# -*- coding: utf-8 -*-
"""已发布站点 Host 路由（plan B8）：按 Host 头把 <slug>.<APEX> 解析到静态产物。

用 before_request 短路：仅当请求 Host 命中 apex 时才接管，否则 return None
让正常 /api 路由继续，避免与既有路由冲突。
安全红线（设计 §6）：
- 独立 apex（不承载主站 cookie）
- 每站点独占 origin（<slug>.apex 硬要求）
- unlisted → X-Robots-Tag: noindex
- 放开 same-origin：绝不回 CSP: sandbox（否则 localStorage 抛 SecurityError）
- 路径穿越 → 403
"""
import mimetypes
from flask import Response, abort, request
from factory import app, logger
from config import settings

from models.models import PublishedSite
from utils.db import get_db
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
    if not slug or not is_valid_slug(slug):
        abort(404)

    filepath = request.path.lstrip('/') or 'index.html'
    if filepath.startswith('/') or '..' in filepath or filepath.startswith('~'):
        abort(403)

    with get_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if not site or not site.current_hash:
            abort(404)
        content_hash = site.current_hash
        visibility = site.visibility

    try:
        data = _store().get(content_hash, filepath)
    except KeyError:
        abort(404)
    except StoreError:
        abort(403)

    mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
    resp = Response(data, mimetype=mime)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    if visibility == 'unlisted':
        resp.headers['X-Robots-Tag'] = 'noindex'
    # 注意：刻意不设置 CSP: sandbox —— 已发布站点需 same-origin 才能用 localStorage
    return resp
