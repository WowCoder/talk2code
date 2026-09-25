# -*- coding: utf-8 -*-
"""市集缩略图（Ship 3）。

按需生成 + 按 content_hash 缓存：同一个版本的产物只截一次，重新发布（hash 变了）
会自动重截。生成失败（Chromium 不可用、超时）一律返回 None —— 前端回退到色块，
**绝不因为缩略图失败影响列表本身**。

为什么用 content_hash 而不是 slug 做缓存键：slug 指向的是"槽位"，内容变了 slug
不变；按 slug 缓存会让用户看到旧版本产品的截图。
"""
import threading
from pathlib import Path

from config import settings
from harness.observability.logger import get_logger

logger = get_logger(__name__)

THUMB_TIMEOUT_MS = 10_000

_lock = threading.Lock()
_inflight: set = set()


def thumb_dir() -> Path:
    d = Path(settings.PUBLISH_STORE_PATH) / "_thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumb_path(content_hash: str) -> Path:
    return thumb_dir() / f"{content_hash}.png"


def get_thumb(content_hash: str) -> Path | None:
    """已缓存则返回路径，否则 None。"""
    p = thumb_path(content_hash)
    return p if p.exists() and p.stat().st_size > 0 else None


def ensure_thumb(content_hash: str, entry_html: bytes, preview_url: str | None) -> Path | None:
    """确保缩略图存在；不存在则生成（同一 hash 并发只会有一个真正在截）。

    preview_url 为空（未配置发布域名）时退化为直读文件，资源可能加载不全，
    但仍比没有图好，故不直接放弃。
    """
    cached = get_thumb(content_hash)
    if cached:
        return cached

    with _lock:
        if content_hash in _inflight:
            return None  # 别人正在截，本次先回退色块
        cached = get_thumb(content_hash)
        if cached:
            return cached
        _inflight.add(content_hash)

    try:
        import tempfile

        from harness.tools.preview_runner import capture_screenshot

        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "index.html"
            html_path.write_bytes(entry_html)
            out = thumb_path(content_hash)
            res = capture_screenshot(
                html_path, out, timeout_ms=THUMB_TIMEOUT_MS, preview_url=preview_url
            )
            if not res:
                logger.info("市集缩略图生成失败（回退色块）: hash=%s", content_hash[:12])
                return None
            return get_thumb(content_hash)
    except Exception as e:
        logger.warning("市集缩略图异常（回退色块）: hash=%s err=%s", content_hash[:12], e)
        return None
    finally:
        with _lock:
            _inflight.discard(content_hash)


def thumb_url(site) -> str | None:
    """缩略图的对外地址：有自定义封面出封面，否则首页截图；都拿不到 None。

    **必须带版本参数**：截图分支吃 24h 长缓存，若 URL 恒定，作者传完封面
    浏览器仍会拿旧截图（端点改发 no-cache 救不了已经缓存的条目）。
    版本取封面文件的 mtime；没有封面退回 content_hash（内容寻址，天然稳定，
    换个版本截图 URL 也跟着换）。列表与详情必须共用这一个函数 —— 此前两处
    各拼各的，详情能刷出新封面而列表卡纹丝不动，正是「同一份数据两处口径」
    的老毛病。
    """
    from services.market.cover import get_cover  # 局部导入避免 market 包内环

    cover_path, _ = get_cover(site.slug)
    if cover_path is not None:
        version = int(cover_path.stat().st_mtime)
    elif site.current_hash:
        version = site.current_hash[:12]
    else:
        return None
    return f"/api/market/thumbs/{site.slug}.png?v={version}"
