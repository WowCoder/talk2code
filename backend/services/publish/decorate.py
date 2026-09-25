# -*- coding: utf-8 -*-
"""已发布站点的来源 badge（设计文档 §6）。

为什么是「服务时注入」
----------------------
落盘时注入会让装饰参与 `content_hash`，同一份作品「加 / 不加 badge」算出两个
hash，`version` 语义直接崩；而 `LocalFSStore.put_bundle` 是幂等的（目录存在即
零写入），作者事后改 `badge_enabled` 将**无法**重新装饰。服务时注入让磁盘产物
与 hash 恒等于用户原稿，开关还能即时生效。

为什么只给已上架站点注入
------------------------
badge 是「逛逛市集」的入口，没上架就不该有入口；顺带保证未上架站点对外返回的
字节与用户产物完全一致。

复验豁免
--------
复验带内部头 `X-T2C-Verify: 1` 时跳过装饰：① 复验验的是用户作品本身，平台装饰
不在验收范围；② badge 浮在右下角，可能遮挡 AC 脚本要点击的元素，造成假红。
"""
from typing import Optional

from config import settings

_CACHE: dict = {}
_CACHE_MAX = 200

BADGE_MARKER = "t2c-market-badge"

_BADGE_TEMPLATE = (
    '<div class="{m}">'
    '<a class="{m}-a" href="{url}" target="_blank" rel="noopener noreferrer">'
    '<span class="{m}-t">用 Talk2Code 做的</span>'
    '<span class="{m}-c">逛逛市集 &#8594;</span>'
    '</a></div>'
    '<style>.{m}{{position:fixed;right:14px;bottom:14px;z-index:2147483000;'
    'font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;'
    'pointer-events:none}}'
    '.{m}-a{{pointer-events:auto;display:flex;align-items:center;gap:6px;'
    'padding:6px 11px;border-radius:999px;background:#111827;color:#fff;'
    'text-decoration:none;font-size:12px;line-height:1;opacity:.72;'
    'box-shadow:0 2px 10px rgba(0,0,0,.18)}}'
    '.{m}-a:hover{{opacity:1}}'
    '.{m}-t{{opacity:.9}}'
    '.{m}-c{{font-weight:600}}</style>'
)


def market_url() -> Optional[str]:
    """主站市集地址。未配置出可访问地址时返回 None（此时不注入 badge）。"""
    explicit = (getattr(settings, "MARKET_SITE_URL", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    apex = (settings.PUBLISH_APEX or "").strip()
    if not apex:
        return None
    scheme = (settings.PUBLISH_URL_SCHEME or "https").strip()
    port = (settings.PUBLISH_URL_PORT or "").strip()
    return f"{scheme}://{apex}{':' + port if port else ''}/market"


def render_badge(html: bytes, *, url: str, cache_key: Optional[str] = None) -> bytes:
    """在入口 HTML 的 </body> 前插入来源 badge。任何异常一律返回原串。

    绝不因装饰导致站点打不开 —— 用户作品的可访问性优先级高于平台 CTA。
    """
    key = (cache_key, url)
    if cache_key:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    try:
        text = html.decode("utf-8", "replace")
        lower = text.lower()
        anchor = "</body>"
        pos = lower.rfind(anchor)
        if pos == -1:
            anchor = "</html>"
            pos = lower.rfind(anchor)
        if pos == -1:
            return html  # 没有可插入点，原样返回

        snippet = _BADGE_TEMPLATE.format(m=BADGE_MARKER, url=url)
        out = (text[:pos] + snippet + text[pos:]).encode("utf-8")
    except Exception:
        return html

    if cache_key:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def should_inject_badge(*, is_entry: bool, market_visible: bool,
                        badge_enabled: bool, is_verify_request: bool) -> bool:
    """是否注入 badge。四个条件缺一不可，集中在此避免调用方各写一份。"""
    return bool(
        is_entry
        and market_visible
        and badge_enabled
        and not is_verify_request
        and settings.PUBLISH_BADGE_ENABLED
    )
