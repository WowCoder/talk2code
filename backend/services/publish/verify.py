# -*- coding: utf-8 -*-
"""发布后复验（Ship C，设计 §5.3 / G4）。

为什么必须：预览是 opaque origin（无存储），发布后的 apex 域名放开 same-origin
—— 两个环境不等价，会出现「预览全绿、上线拉胯」。故发布后必须对真实线上 URL
重跑验收，并把结果写回 published_sites(verified_at / verify_status)。

复验结论：
- ok         : smoke + AC + same-origin 全部通过
- degraded   : 任一失败 / 复验过程异常（安全默认：绝不谎报成功）
- unverified : 未配置 apex（Host 路由未启用），无法对线上 URL 复验

Chromium 实际跑在 Leon 本机 / CI；单测通过 monkeypatch _run_verification 验证
写库决策路径（ok / degraded / unverified），不依赖浏览器。
"""
import datetime
import tempfile
from pathlib import Path
from typing import Optional

from config import settings
from factory import logger
from models.models import PublishedSite
from utils.db import transactional_db

from services.publish.slug import is_valid_slug
from services.publish.store import LocalFSStore, StoreError


def _write_status(slug: str, status: str) -> None:
    """写回 verify_status + verified_at（事务内）。站点不存在则跳过。"""
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if site is None:
            return
        site.verify_status = status
        site.verified_at = datetime.datetime.utcnow()
        db.flush()


def _site_requirement_id(slug: str) -> Optional[int]:
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        return site.requirement_id if site else None


def decide_verify_status(slug: str, content_hash: str) -> Optional[str]:
    """计算复验结论（纯逻辑，可单测，不依赖站点行是否存在）。

    Returns:
        "ok" / "degraded" / "unverified"，或 None（非法 slug，不写库）。
    """
    if not is_valid_slug(slug):
        return None
    apex = settings.PUBLISH_APEX
    if not apex:
        return "unverified"  # Host 路由未启用，无法复验线上 URL
    url = f"https://{slug}.{apex}"
    requirement_id = _site_requirement_id(slug)
    try:
        ok = _run_verification(url, slug, requirement_id)
    except Exception as e:
        logger.warning(f"发布复验执行异常（判 degraded）: slug={slug} err={e}")
        ok = False
    return "ok" if ok else "degraded"


def trigger_publish_verify(slug: str, content_hash: str) -> None:
    """Ship C 入口：由 PublishService.publish(on_published=...) 调用。

    content_hash 当前未直接用于复验（站点以 slug 寻址），保留参数以对齐调用约定。
    """
    status = decide_verify_status(slug, content_hash)
    if status is None:
        logger.warning(f"发布复验收到非法 slug，跳过: {slug}")
        return
    _write_status(slug, status)


def _load_published_index(slug: str) -> Optional[Path]:
    """把已发布 index.html 落到临时文件，供 runner 作为 html_path 兜底。"""
    try:
        store = LocalFSStore()
        data = store.get(_current_hash(slug), "index.html")
    except (KeyError, StoreError):
        return None
    if data is None:
        return None
    fd = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    fd.write(data)
    fd.close()
    return Path(fd.name)


def _load_ac_scripts(requirement_id: Optional[int]) -> list:
    """尽力加载该需求的 AC 脚本缓存（.task/ac_scripts.json）。

    解析失败 / 不存在 → 返回空列表（跳过 AC，仅跑 smoke + same-origin）。
    """
    if not requirement_id:
        return []
    try:
        # 复用需求服务的 workspace 解析（与验收同源）
        from services.workspace_service import get_requirement_workspace
        ws = get_requirement_workspace(requirement_id)
        raw = ws.read(".task/ac_scripts.json") if ws.exists(".task/ac_scripts.json") else None
        if not raw:
            return []
        import json
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"发布复验加载 AC 脚本失败（跳过 AC）: req={requirement_id} err={e}")
        return []


def _run_verification(url: str, slug: str, requirement_id: Optional[int]) -> bool:
    """真实复验（需 Chromium）。返回 True=全通过。任何异常 → False（degraded）。

    拆分三路信号，与 C3「same-origin 专属用例」对应：
    1. run_universal_smoke(preview_url=url) —— 服务层没把产物搞坏
    2. run_ac_checks(preview_url=url)       —— 行为契约仍成立
    3. _check_same_origin(url)              —— 发布放开 same-origin 后 localStorage 可用
    """
    try:
        from harness.tools.preview_runner import (
            run_universal_smoke,
            run_ac_checks,
        )
        html_path = _load_published_index(slug)

        smoke = run_universal_smoke(html_path, preview_url=url)
        smoke_ok = bool(smoke.get("available"))

        ac_scripts = _load_ac_scripts(requirement_id)
        ac_ok = True
        if ac_scripts:
            results = run_ac_checks(html_path, ac_scripts, preview_url=url)
            ac_ok = all(
                r.get("passed") and not r.get("harness_errors")
                for r in results
            )

        same_origin_ok = _check_same_origin(url)
        return bool(smoke_ok and ac_ok and same_origin_ok)
    except Exception as e:
        logger.warning(f"发布复验执行异常（判 degraded）: url={url} err={e}")
        return False


def _check_same_origin(url: str) -> bool:
    """C3 专属：在放开 same-origin 的浏览器上下文里验证 localStorage 可写。

    预览环境因 iframe 无 allow-same-origin，localStorage 必抛 SecurityError；
    发布环境放开后必须可用，否则用户当成 bug。
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="load", timeout=15000)
                result = page.evaluate(
                    "try { localStorage.setItem('__t2c_probe', '1'); "
                    "localStorage.removeItem('__t2c_probe'); return true; } "
                    "catch(e) { return false; }"
                )
                return bool(result)
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"same-origin 复验失败（判 degraded）: url={url} err={e}")
        return False

