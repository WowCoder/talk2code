# -*- coding: utf-8 -*-
"""发布后复验（Ship C，设计 §5.3 / G4）。

为什么必须：预览是 opaque origin（无存储），发布后的 apex 域名放开 same-origin
—— 两个环境不等价，会出现「预览全绿、上线拉胯」。故发布后必须对真实线上 URL
重跑验收，并把结果写回 published_sites(verified_at / verify_status)。

复验结论：
- ok         : smoke + AC + same-origin 全部通过
- degraded   : 任一失败 / 复验过程异常（安全默认：绝不谎报成功）
- unverified : 未配置 apex（Host 路由未启用），无法对线上 URL 复验
- pending    : 刚发布，异步复验尚未写回（发布接口不再等复验）

两个易错前提，动这块代码前先读：
1. 复验的线上 URL 一律取 ``services.publish.urls.published_url()``，不要自行拼接
   ——协议与端口随环境变（本地是 ``http://<slug>.localhost:5001``）。
2. **未配 apex 时本模块几乎不执行**（``decide_verify_status`` 提前返回
   ``unverified``）。这意味着这里的缺陷在本地开发态会长期潜伏，只有配上
   ``PUBLISH_APEX`` 才集中暴露；改完务必用一次真实发布验证，光跑单测不够。

Chromium 实际跑在 Leon 本机 / CI；单测通过 monkeypatch _run_verification 验证
写库决策路径（ok / degraded / unverified），不依赖浏览器。
"""
import datetime
import tempfile
import threading
from pathlib import Path
from typing import Optional

from factory import logger
from models.models import PublishedSite
from utils.db import transactional_db

from services.publish.slug import is_valid_slug
from services.publish.store import LocalFSStore, StoreError
from services.publish.urls import published_url

# same-origin 探针预算：单步超时 15s，watchdog 给足「启动 + 导航 + evaluate」
# 的上界，外层隔离线程再留余量（同 preview_runner 的 watchdog 取值思路）。
SAME_ORIGIN_TIMEOUT_MS = 15_000
SAME_ORIGIN_BUDGET_S = SAME_ORIGIN_TIMEOUT_MS / 1000.0 + 10.0

# 同一 slug 同时只允许一个复验在跑。
# 复验要起一个 Chromium 会话（内存 + 秒级耗时），而「重新发布」可以连点：
# 不设闸门时并发会话会互相抢资源，且后写库的结果未必对应最新那次发布。
_verify_inflight: set = set()
_verify_lock = threading.Lock()


def _write_status(slug: str, status: str) -> None:
    """写回 verify_status + verified_at（事务内）。站点不存在则跳过。"""
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if site is None:
            return
        site.verify_status = status
        site.verified_at = datetime.datetime.utcnow()
        db.flush()


def decide_verify_status(slug: str, content_hash: str) -> Optional[str]:
    """计算复验结论（纯逻辑，可单测，不依赖站点行是否存在）。

    Returns:
        "ok" / "degraded" / "unverified"，或 None（非法 slug，不写库）。
    """
    if not is_valid_slug(slug):
        return None
    # 与前端展示、Host 路由共用同一份 URL（协议/端口来自配置）；为 None
    # 即 apex 未配置、Host 路由整体关闭，线上 URL 不存在 → 无从复验。
    url = published_url(slug)
    if not url:
        return "unverified"
    user_id, requirement_id = _site_owner(slug)
    try:
        ok = _run_verification(url, slug, user_id, requirement_id, content_hash)
    except Exception as e:
        logger.warning(f"发布复验执行异常（判 degraded）: slug={slug} err={e}")
        ok = False
    return "ok" if ok else "degraded"


def trigger_publish_verify(slug: str, content_hash: str) -> None:
    """Ship C 同步入口：跑完复验并把结论写库后才返回。

    只在 ``PUBLISH_VERIFY_ASYNC=False``（测试）时被路由层调用；线上走
    :func:`schedule_publish_verify`，否则 Chromium 复验（15s 起）会卡住
    POST /api/publish 的请求线程。

    content_hash 当前未直接用于复验（站点以 slug 寻址），保留参数以对齐调用约定。
    """
    status = decide_verify_status(slug, content_hash)
    if status is None:
        logger.warning(f"发布复验收到非法 slug，跳过: {slug}")
        return
    _write_status(slug, status)


def schedule_publish_verify(slug: str, content_hash: str) -> bool:
    """异步入口：起一个守护线程跑复验，立即返回。

    为什么必须异步：复验要起 Chromium 跑 smoke + AC + same-origin 探针，
    单次 15s 起、带 AC 更久。同步跑在 POST /api/publish 的请求线程里会让
    发布会上限直接等于复验耗时——前端先超时报错、用户以为发布失败，而站点
    其实已经落盘可访问（「界面报错但站点已发布」，最难排查的一类状态）。
    异步化后发布接口立即返回，``verify_status`` 先落在 ``pending``，复验结束
    再写回 ``ok`` / ``degraded``（该字段不渲染给用户，供排障用）。

    Returns:
        True  = 已安排复验；False = 非法 slug 或该 slug 已有复验在跑。
    """
    if not is_valid_slug(slug):
        logger.warning(f"发布复验收到非法 slug，跳过: {slug}")
        return False
    with _verify_lock:
        if slug in _verify_inflight:
            return False
        _verify_inflight.add(slug)

    t = threading.Thread(
        target=_scheduled_verify,
        args=(slug, content_hash),
        name=f"publish-verify-{slug[:8]}",
        daemon=True,
    )
    t.start()
    return True


def _scheduled_verify(slug: str, content_hash: str) -> None:
    """守护线程主体：任何异常都不得逃逸，且必须把闸门释放掉。

    异常路径刻意**不**写 degraded，只记日志：写库本身可能就是失败原因，
    再写一次等于把「库不可用」变成第二个未捕获异常。验证状态留在
    ``pending`` 是诚实的（未知 ≠ 通过），不会谎报成功。
    """
    try:
        status = decide_verify_status(slug, content_hash)
        if status is not None:
            _write_status(slug, status)
    except Exception as e:
        logger.warning(f"发布复验后台执行异常（状态留 pending）: slug={slug} err={e}")
    finally:
        with _verify_lock:
            _verify_inflight.discard(slug)


def _current_hash_for_slug(slug: str) -> Optional[str]:
    """站点当前指向的 bundle hash（复验要落盘的那份 index.html）。"""
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        return site.current_hash if site else None


def _load_published_index(slug: str, content_hash: Optional[str] = None) -> Optional[Path]:
    """把已发布 index.html 落到临时文件，供 runner 作为 html_path 兜底。

    content_hash 由发布流程直接传入（发布事务已提交，即为最新值），拿不到时
    回退查库——历史上这里调用了一个根本不存在的 ``_current_hash()``，
    NameError 被 ``_run_verification`` 的 except 吞成 warning，表现为每次复验
    都判 degraded；本地未配 apex 时该分支根本不执行，所以一直没暴露。
    """
    content_hash = content_hash or _current_hash_for_slug(slug)
    if not content_hash:
        return None
    try:
        store = LocalFSStore()
        data = store.get(content_hash, "index.html")
    except (KeyError, StoreError):
        return None
    if data is None:
        return None
    fd = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    fd.write(data)
    fd.close()
    return Path(fd.name)


def _site_owner(slug: str) -> tuple[Optional[int], Optional[int]]:
    """站点属主 (user_id, requirement_id)——工作区路径按这两者分层。"""
    with transactional_db() as db:
        site = db.query(PublishedSite).filter_by(slug=slug).first()
        if site is None:
            return None, None
        return site.user_id, site.requirement_id


def _load_ac_scripts(user_id: Optional[int], requirement_id: Optional[int]) -> list:
    """尽力加载该需求的 AC 脚本缓存（.task/ac_scripts.json）。

    缓存形状与验收同源：``{"ac_hash": ..., "scripts": [ ... ]}``——**是对象不是
    裸数组**，故取 ``scripts`` 键并校验类型。

    解析失败 / 不存在 → 返回空列表（跳过 AC，仅跑 smoke + same-origin）。
    """
    if not requirement_id or not user_id:
        return []
    try:
        # 与验收同源：工作区路径 = {base}/{user_id}/{requirement_id}
        from harness.state.workspace import WorkspaceFS
        ws = WorkspaceFS(user_id, requirement_id)
        raw = ws.read(".task/ac_scripts.json") if ws.exists(".task/ac_scripts.json") else None
        if not raw:
            return []
        import json
        cached = json.loads(raw)
        scripts = cached.get("scripts") if isinstance(cached, dict) else None
        return scripts if isinstance(scripts, list) else []
    except Exception as e:
        logger.warning(f"发布复验加载 AC 脚本失败（跳过 AC）: req={requirement_id} err={e}")
        return []


def _run_verification(
    url: str,
    slug: str,
    user_id: Optional[int],
    requirement_id: Optional[int],
    content_hash: Optional[str] = None,
) -> bool:
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
        html_path = _load_published_index(slug, content_hash)

        smoke = run_universal_smoke(html_path, preview_url=url)
        smoke_ok = bool(smoke.get("available"))

        ac_scripts = _load_ac_scripts(user_id, requirement_id)
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


def _same_origin_probe(url: str) -> bool:
    """探测会话本体（**必须整个在隔离线程里执行**，见 _check_same_origin）。"""
    import urllib.parse

    from harness.tools.sandboxed_browser import sandboxed_browser

    host = urllib.parse.urlsplit(url).hostname or ''
    with sandboxed_browser(
        allow_hosts=(host,) if host else (),
        timeout_ms=SAME_ORIGIN_TIMEOUT_MS,
    ) as browser:
        page = browser.new_page()
        # 同 preview_runner：声明为复验请求，跳过 badge 装饰（避免浮层干扰探针）
        page.set_extra_http_headers({"X-T2C-Verify": "1"})
        page.goto(url, wait_until="load", timeout=SAME_ORIGIN_TIMEOUT_MS)
        # 必须包成函数体：page.evaluate 收到裸语句串时按表达式 eval，
        # 顶层 return 会直接抛 "SyntaxError: Illegal return statement"
        # ——那样这条探针永远返回失败，复验恒判 degraded。
        return bool(page.evaluate(
            "() => { try { localStorage.setItem('__t2c_probe', '1'); "
            "localStorage.removeItem('__t2c_probe'); return true; } "
            "catch(e) { return false; } }"
        ))


def _check_same_origin(url: str) -> bool:
    """C3 专属：在放开 same-origin 的浏览器上下文里验证 localStorage 可写。

    预览环境因 iframe 无 allow-same-origin，localStorage 必抛 SecurityError；
    发布环境放开后必须可用，否则用户当成 bug。

    两个必须遵守的约束：
    - **隔离线程**：本函数由发布请求线程调用，而 Flask/WSGI 线程会被复用；
      Playwright sync API 在复用线程里第二次 ``sync_playwright().start()``
      会永久挂死（无 driver、无日志）。故整段探针走
      ``run_browser_session_isolated``。
    - **出口封锁下仍需可达**：探针打的是发布站点自身，故把自己的主机名
      加进 ``allow_hosts``；否则被 ``MAP * ~NOTFOUND`` + 黑洞代理拦掉，
      会得到一个假的 degraded。
    """
    try:
        from harness.tools.sandboxed_browser import run_browser_session_isolated
        return run_browser_session_isolated(
            _same_origin_probe, SAME_ORIGIN_BUDGET_S, url
        )
    except Exception as e:
        logger.warning(f"same-origin 复验失败（判 degraded）: url={url} err={e}")
        return False

