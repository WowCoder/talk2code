# -*- coding: utf-8 -*-
"""
Playwright headless 预览运行器

加载生成的 HTML，收集运行时错误。返回结构化报告：

{
  "available": True/False,     # 浏览器是否可用
  "url": str,
  "errors": [                  # 阻断性错误（应触发修复）
     {"type": "pageerror"|"console_error"|"request_failed", "message": str, ...}
  ],
  "logs": [str],               # console.log/info/warn（供调试，非阻断）
  "network": [{"url","status"}],
  "initialization": {          # 初始化检测（v2: 检测页面功能是否真正启动）
     "canvas_activity": True/False/None,  # None=无canvas, True=有像素变化, False=静态
     "animation_started": True/False,     # requestAnimationFrame 是否被调用
     "details": str,                      # 人类可读的检测结果
  },
}

设计要点：
- 同步 API + 短超时（默认 10s），避免单个坏页面卡死整个 agent loop
- 浏览器实例用完即关，无跨请求状态
- 缺失浏览器二进制时抛可识别异常，由调用方降级
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 单页加载 + 收集的总超时（秒）。生成的页面多为静态，10s 足够。
DEFAULT_TIMEOUT_MS = 10_000
# 页面加载后额外等待时间（ms），给异步脚本/初始化逻辑跑完的机会
SETTLE_MS = 1_500
# 会话整体预算的固定余量（秒）——叠加在 per-step 超时之上，测试可调小
_SESSION_BUDGET_SLACK_S = 20.0


from harness.tools.sandboxed_browser import (  # noqa: E402
    BrowserSessionTimeout,
    run_browser_session_isolated,
)


def run_preview_in_browser(
    html_path: Path,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    elem_checks: list[dict] | None = None,
) -> dict:
    """在全新线程里执行浏览器预览会话（req 154：复用线程二次启动挂死的根治）。

    超时/异常语义见 ``_run_preview_in_browser_session``；本包装只负责
    wall-clock 兜底——超时抛 BrowserSessionTimeout，由调用方（preview_tools
    的 except）降级为「预览验证跳过」。
    """
    budget = _watchdog_ms(timeout_ms, len(elem_checks or []) + 4) / 1000.0 + _SESSION_BUDGET_SLACK_S
    return run_browser_session_isolated(
        _run_preview_in_browser_session, budget, html_path,
        timeout_ms=timeout_ms, elem_checks=elem_checks,
    )


def _run_preview_in_browser_session(
    html_path: Path,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    elem_checks: list[dict] | None = None,
) -> dict:
    """
    在 headless 浏览器中加载 html_path，收集错误和元素存在性信息。

    Args:
        html_path: HTML 文件路径
        timeout_ms: 超时时间（毫秒）
        elem_checks: 可选，要检查的页面元素列表。
            每项格式: {"selector": "canvas", "label": "游戏画布", "required": True}
            required=True 的元素不存在时会作为功能缺陷记录到 defects 中。

    Raises:
        RuntimeError: 当 playwright 未安装或浏览器二进制缺失时（调用方应降级）
    """
    try:
        from harness.tools.sandboxed_browser import sandboxed_browser
    except ImportError as e:
        raise RuntimeError(f"playwright 未安装：{e}") from e

    url = html_path.resolve().as_uri()
    errors: list[dict] = []
    logs: list[str] = []
    network: list[dict] = []
    defects: list[dict] = []
    page_text = ""  # 页面文本内容（供 LLM 评估功能完整性）
    initialization = {  # 初始化检测结果
        "canvas_activity": None,
        "animation_started": False,
        "details": "",
    }

    try:
        # watchdog 必须覆盖 goto/settle/元素检查等全部合法等待（req 154）
        with sandboxed_browser(timeout_ms=_watchdog_ms(timeout_ms, len(elem_checks or []) + 4)) as browser:

            try:
                context = browser.new_context()
                page = context.new_page()
                # 声明为复验/预览请求：已发布站点的 Host 路由据此**跳过 badge 装饰**，
                # 让验收跑在用户作品的原稿上。badge 是平台装饰，不属于验收范围，
                # 且它浮在右下角可能遮挡 AC 脚本要点击的元素，造成假红。
                # 详见 services/publish/decorate.py
                page.set_extra_http_headers({"X-T2C-Verify": "1"})

                # ---- 注入 RAF 追踪脚本（在页面脚本执行前注入） ----
                # 用于检测 initGame / 游戏循环等是否真正启动了 requestAnimationFrame
                page.add_init_script("""
                    window.__talk2code_raf_called = false;
                    const _origRAF = window.requestAnimationFrame;
                    window.requestAnimationFrame = function(cb) {
                        window.__talk2code_raf_called = true;
                        return _origRAF.call(window, cb);
                    };
                """)

                # 收集器：把运行时信号塞进上面三个列表
                def _on_console(msg):
                    if msg.type == "error":
                        errors.append({
                            "type": "console_error",
                            "message": msg.text,
                            "location": _loc(msg.location),
                        })
                    else:
                        logs.append(f"[{msg.type}] {msg.text}")

                def _on_pageerror(err):
                    stack = (getattr(err, "stack", "") or "")[:1200]
                    errors.append({
                        "type": "pageerror",
                        "message": str(err),
                        "name": getattr(err, "name", ""),
                        "stack": stack,
                        "location": _parse_error_location(stack),
                    })

                def _on_request_failed(req):
                    failures = ("net::ERR_FAILED", "net::ERR_FILE_NOT_FOUND",
                                "net::ERR_CONNECTION_REFUSED")
                    if any(f in req.failure for f in failures):
                        errors.append({
                            "type": "request_failed",
                            "message": f"资源加载失败: {req.url} ({req.failure})",
                            "url": req.url,
                        })

                def _on_response(resp):
                    if resp.status >= 400:
                        network.append({"url": resp.url, "status": resp.status})

                page.on("console", _on_console)
                page.on("pageerror", _on_pageerror)
                page.on("requestfailed", _on_request_failed)
                page.on("response", _on_response)

                page.set_default_timeout(timeout_ms)
                # goto 用 domcontentloaded 而非 load：坏页面可能永不触发 load
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # 给异步初始化脚本一点时间跑完再采集
                try:
                    page.wait_for_timeout(SETTLE_MS)
                except Exception:
                    pass

                # ---- 元素存在性检查（功能验证的核心增强） ----
                if elem_checks:
                    for check in elem_checks:
                        selector = check.get("selector", "")
                        label = check.get("label", selector)
                        required = check.get("required", False)
                        try:
                            elem = page.query_selector(selector)
                            if elem:
                                logs.append(f"[element_check] ✅ {label} ({selector}) 存在")
                            else:
                                defect = {
                                    "type": "missing_element",
                                    "selector": selector,
                                    "label": label,
                                    "message": f"页面缺少关键元素: {label} ({selector})",
                                }
                                if required:
                                    defects.append(defect)
                                logs.append(f"[element_check] ❌ {label} ({selector}) 不存在")
                        except Exception as e:
                            logs.append(f"[element_check] ⚠️ {label} ({selector}) 检查异常: {e}")

                # ---- 提取页面可见文本（供 LLM 评估内容完整性） ----
                try:
                    page_text = page.inner_text("body")[:2000] if page.query_selector("body") else ""
                except Exception:
                    pass

                # ---- 初始化检测：canvas 像素变化 + RAF 调用 ----
                try:
                    # 检测 RAF 是否被调用
                    initialization["animation_started"] = page.evaluate(
                        "() => window.__talk2code_raf_called || false"
                    )

                    # 检测 canvas 是否有像素变化（游戏是否真正渲染）
                    canvases = page.query_selector_all("canvas")
                    if canvases:
                        canvas = canvases[0]
                        # 获取当前像素数据
                        initial_data = page.evaluate("""
                            (selector) => {
                                const c = document.querySelector(selector);
                                if (!c) return null;
                                const ctx = c.getContext('2d');
                                if (!ctx) return null;
                                return Array.from(ctx.getImageData(0, 0, c.width, c.height).data);
                            }
                        """, "canvas")
                        # 再等 2 秒让游戏循环跑几帧
                        page.wait_for_timeout(2000)
                        later_data = page.evaluate("""
                            (selector) => {
                                const c = document.querySelector(selector);
                                if (!c) return null;
                                const ctx = c.getContext('2d');
                                if (!ctx) return null;
                                return Array.from(ctx.getImageData(0, 0, c.width, c.height).data);
                            }
                        """, "canvas")
                        if initial_data and later_data and len(initial_data) == len(later_data):
                            changed = sum(1 for a, b in zip(initial_data, later_data) if a != b)
                            initialization["canvas_activity"] = changed > 50  # 至少 50 个像素变化
                            initialization["details"] = (
                                f"Canvas 像素变化: {changed} pixels"
                                + (" (有动画)" if changed > 50 else " (静态/未启动)")
                            )
                        else:
                            initialization["canvas_activity"] = False
                            initialization["details"] = "无法读取 canvas 像素数据"
                        logs.append(
                            f"[init_check] canvas_activity={initialization['canvas_activity']}, "
                            f"animation_started={initialization['animation_started']}"
                        )
                    else:
                        initialization["details"] = "无 canvas 元素"
                except Exception as e:
                    initialization["details"] = f"初始化检测异常: {e}"
                    logs.append(f"[init_check] 异常: {e}")
            finally:
                browser.close()
    except RuntimeError:
        raise  # 浏览器不可用，向上传播由调用方降级
    except Exception as e:
        # 其他意外错误也降级：不让验证工具搞崩 agent loop
        logger.warning("预览运行异常（降级为无结论）: %s", e)
        return {
            "available": True,
            "url": url,
            "errors": [],
            "logs": [],
            "network": [],
            "defects": [],
            "page_text": "",
            "skip_reason": f"运行异常: {e}",
        }

    return {
        "available": True,
        "url": url,
        "errors": errors,
        "logs": logs[:50],  # 限制体积
        "network": network[:50],
        "defects": defects,
        "page_text": page_text,
        "initialization": initialization,
    }


def _click_position(doc, selector: str, step: dict):
    """解析 click 步骤的落点（需求 199 事故：棋盘只能点中心）。

    Playwright 的 `click` 默认点元素**中心**。对 canvas / 棋盘 / 网格 / 地图这类
    「同一元素上多点交互」的实现，连续多条无落点的 click 会全部压在同一个像素上，
    只有第一条有效。req 199 实测：AC-1 要点两下切换两次回合，第二下落在已占用
    交叉点被判无效 → 断言「黑方回合」失败；AC-2 要连下 10 子成五，实际只落下
    1 子 → 获胜遮罩永不出现。两条都以 critical 产品缺陷呈现，但根因是脚本驱动缺陷。

    支持两种落点表达（坐标原点均为元素padding box 左上角，单位 CSS 像素）：
    - `at`: [rx, ry] —— 相对元素宽高的**比例**（0~1）。棋盘类 AC 用比例写坐标，
      不必知道棋盘的像素尺寸，如 [0.2, 0.4] = 横向 20%、纵向 40% 处。
    - `offset`: {"x": px, "y": px} —— 精确像素偏移，用于需要像素级对齐的场景。

    返回 Playwright 的 position dict；未指定落点则返回 None（走默认中心点击）。
    """
    at = step.get("at")
    off = step.get("offset")
    if not at and not off:
        return None
    try:
        el = doc.query_selector(selector)
        if el is None:
            return None
        box = el.bounding_box()
        if not box or not box.get("width") or not box.get("height"):
            return None
    except Exception:
        return None

    w = float(box["width"])
    h = float(box["height"])
    if at:
        try:
            rx, ry = float(at[0]), float(at[1])
        except Exception:
            return None
        # 夹进元素内部并留 1px 余量：正好压在边界上会被判定为 outside viewport
        x = min(max(w * rx, 1.0), max(w - 1.0, 1.0))
        y = min(max(h * ry, 1.0), max(h - 1.0, 1.0))
        return {"x": x, "y": y}
    try:
        return {"x": float(off.get("x", 0)), "y": float(off.get("y", 0))}
    except Exception:
        return None


def _resolve_selector(doc, selector: str):
    """选择器自适应解析（需求 126 事故：AC 脚本 `#start-btn` vs 产物 id `startBtn`）

    产品能跑但选择器因命名习惯差异（短横线 vs 驼峰、btn-vs-Button）失配时，
    首次直接 query 失败不立即返回——按级联策略定位真实可点击目标：

    1. 原样选择器
    2. 前缀/规范化变体：#add-btn → #addBtn / #add_btn / 去装饰后含 `add`
    3. 含文本/类的按钮兜底（用 JS 在页面内探测）
    返回可作用元素；全部失败返回 None。
    """
    try:
        if doc.query_selector(selector):
            return selector
    except Exception:
        pass

    candidates = []
    # 规范化的 hash 变体：去掉分隔符比较
    tag = selector.lstrip("#.").lower()
    stripped = tag.replace("-", "").replace("_", "")
    for cand in [f"#{tag}", f"#{tag.replace('-', '_')}", f"#{tag.replace('_', '-')}"]:
        candidates.append(cand)
    for cand in candidates:
        try:
            if doc.query_selector(cand):
                return cand
        except Exception:
            pass

    # 兜底：把选择器尾部当作语义关键词，在 id/文本中模糊匹配
    if tag:
        stem = max(stripped, key=len)
        try:
            found = doc.evaluate(
                """(keyword) => {
                    const kw = keyword;
                    const roots = document.querySelectorAll('button, a, [role=button], input, [id]');
                    for (const el of roots) {
                        const id = (el.id || '').toLowerCase();
                        const txt = (el.textContent || '').toLowerCase();
                        const cls = (el.className || '').toString().toLowerCase();
                        if (id.replace(/[-_]/g,'') === kw) return '#' + CSS.escape(el.id);
                        if (cls.replace(/[-_]/g,'').includes(kw)) return '#' + CSS.escape(el.id);
                        const k = kw.replace(/_/g, '');
                        if (txt.replace(/\\s/g,'').includes(k)) return '#' + CSS.escape(el.id);
                    }
                    return null;
                }""",
                stripped,
            )
            if found:
                return found
        except Exception:
            pass
    return None


# driver 被 watchdog 杀掉 / 浏览器崩溃后，会话里任何**新**命令都会在死管道上
# 永久挂起（实测：query_selector 挂死，playwright 对已死 transport 不快速失败）。
# 唯一安全的做法是识别这类错误并立即中止会话（req 154）。
_FATAL_TRANSPORT_RE = re.compile(
    r"connection closed|target closed|browser has been closed"
    r"|target page, context or browser has been closed|pipe closed|driver died",
    re.IGNORECASE,
)


def _is_fatal_transport_error(e: BaseException) -> bool:
    return bool(_FATAL_TRANSPORT_RE.search(str(e)))


def _watchdog_ms(timeout_ms: int, n_ops: int) -> int:
    """会话级 watchdog 预算：必须是**全部合法操作时长之和**的上界。

    req 154 教训：watchdog 只略大于单步超时时，会在「操作合法地等待自身超时」
    时误杀浏览器（driver 一死，会话内所有后续命令全部挂起，只能整段废弃）。
    旧实现里 close 在忙线程上阻塞、watchdog 形同虚设掩盖了这个问题；
    改为 kill 之后必须显式给足上界。
    """
    return int(timeout_ms * (n_ops + 3) + 5_000)


def _preview_allow_hosts(preview_url: str | None) -> tuple[str, ...]:
    """把 preview_url 的主机名加进沙箱出口白名单。

    默认白名单只豁免 ``127.0.0.1`` / ``localhost``——因为常规预览链路用数字 IP。
    但发布复验传进来的 preview_url 是 ``<slug>.<apex>`` 形态（例如
    ``http://xxxx.localhost:5001``、``https://xxxx.wowcoder.cn``），不在默认豁免内，
    会被 ``MAP * ~NOTFOUND`` + 黑洞代理拦掉 → 沙箱链路加载失败 → 静默回退直读
    本地文件。于是复验就丢掉了唯一有意义的信号：「线上 URL 真的能打开」。

    故凡带着 preview_url 进入沙箱的会话，都按 URL 动态放行其主机名。
    """
    if not preview_url:
        return ()
    host = urllib.parse.urlsplit(preview_url).hostname
    if not host or host in ("127.0.0.1", "localhost"):
        return ()
    return (host,)


def run_ac_checks(
    html_path: Path,
    ac_scripts: list[dict],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    preview_url: str = None,
    sse=None,
    requirement_id=None,
    dialogue_history: list | None = None,
) -> list[dict]:
    """在全新线程里执行 AC 验收会话（req 154：复用线程二次启动挂死的根治）。

    整体预算 = 会话 watchdog（全部合法操作时长上界，见 _watchdog_ms）+ 固定余量。
    超时不抛异常，而是把全部 AC 标记为 harness_errors（脚本驱动失败）——与
    session 内部的异常降级通道形状一致，verify 侧可照常触发缓存失效/重译逻辑。
    """
    _total_steps = sum(len(s.get("steps") or []) for s in ac_scripts) or len(ac_scripts)
    _watchdog = _watchdog_ms(timeout_ms, _total_steps)
    budget = _watchdog / 1000.0 + _SESSION_BUDGET_SLACK_S
    try:
        return run_browser_session_isolated(
            _run_ac_checks_session, budget, html_path,
            ac_scripts=ac_scripts, timeout_ms=timeout_ms, preview_url=preview_url,
            sse=sse, requirement_id=requirement_id,
            dialogue_history=dialogue_history,
        )
    except BrowserSessionTimeout as e:
        logger.warning(
            "[AC] 浏览器会话整体超时（%.0fs 预算），%d 条 AC 全部标记为驱动失败: %s",
            budget, len(ac_scripts), e,
        )
        return [
            {
                "ac_id": s.get("ac_id", "?"),
                "passed": False,
                "failures": [],
                "harness_errors": [f"浏览器会话超时: {e}"],
                "steps_executed": 0,
            }
            for s in ac_scripts
        ]


def _run_ac_checks_session(
    html_path: Path,
    ac_scripts: list[dict],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    preview_url: str = None,
    sse=None,
    requirement_id=None,
    dialogue_history: list | None = None,
) -> list[dict]:
    """
    执行验收条件（AC）的 Playwright 交互验证脚本。

    preview_url 提供时，通过与前端一致的沙箱 iframe 加载真实预览链路；
    否则直接打开工作区文件。

    每个 ac_script 包含：
    {
        "ac_id": "AC-1",
        "label": "用户可添加待办事项",
        "steps": [
            {"action": "type", "selector": "#input", "value": "测试"},
            {"action": "click", "selector": "#add-btn"},
            {"action": "wait", "ms": 500},
            {"action": "assert_exists", "selector": ".todo-item", "label": "列表有新项目"},
            {"action": "assert_text", "selector": ".todo-item", "contains": "测试"},
        ]
    }

    支持的 action 类型：
    - type:       在元素中输入文本
    - click:      点击元素
    - select:     下拉选择
    - press:      按键（如方向键 ArrowUp，canvas/键盘交互专用）
    - wait:       等待 ms 毫秒
    - assert_exists:    元素存在则通过
    - assert_visible:   元素可见则通过
    - assert_text:      元素文本包含指定内容
    - assert_count:     匹配元素数量 ≥ 预期
    - assert_value:     input 元素的 value 符合预期
    - assert_canvas_change: canvas 像素在 wait_ms 内发生变化
    - assert_dom_change:    观察范围内 DOM 在本次 AC 期间发生变化（DOM 实现的画面断言）

    Returns:
        [{"ac_id": "AC-1", "passed": True, "failures": [], "steps_executed": 5}, ...]
    """
    try:
        from harness.tools.sandboxed_browser import sandboxed_browser
    except ImportError:
        return [{"ac_id": s["ac_id"], "passed": False, "failures": [], "harness_errors": ["playwright 未安装"], "steps_executed": 0} for s in ac_scripts]

    url = html_path.resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)
    results = []

    try:
        # watchdog 必须覆盖所有 AC 全部步骤的合法等待（req 154：误杀后新命令全挂起）
        _ac_total_steps = sum(len(s.get("steps") or []) for s in ac_scripts) or len(ac_scripts)
        with sandboxed_browser(
            timeout_ms=_watchdog_ms(timeout_ms, _ac_total_steps),
            allow_hosts=_preview_allow_hosts(preview_url),
        ) as browser:

            try:
                context = browser.new_context()
                page = context.new_page()
                # 声明为复验/预览请求：已发布站点的 Host 路由据此**跳过 badge 装饰**，
                # 让验收跑在用户作品的原稿上。badge 是平台装饰，不属于验收范围，
                # 且它浮在右下角可能遮挡 AC 脚本要点击的元素，造成假红。
                # 详见 services/publish/decorate.py
                page.set_extra_http_headers({"X-T2C-Verify": "1"})
                page.set_default_timeout(timeout_ms)

                def _load(use_sandbox: bool):
                    """每个 AC 从干净状态加载（沙箱模式返回内层 frame）"""
                    if use_sandbox:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)  # 等待 iframe 资源拉取
                        frame = _resolve_preview_frame(page)
                        # req 151 复盘（致命）：沙箱 iframe 默认无焦点，frame.press 派发的
                        # 键盘事件不会到达内层 document 的 keydown 监听 → 游戏类 AC 全部
                        # 假红（「DOM 无变化 0 次」）。file:// 直读正常、沙箱 0 次，差异就在焦点。
                        # 必须显式抢焦点，否则方向键/Enter 类 AC 在沙箱链路下永远失败。
                        try:
                            frame.evaluate(
                                "() => { try { window.focus(); } catch (e) {} "
                                "try { document.body && document.body.focus(); } catch (e) {} }"
                            )
                        except Exception:
                            pass
                        return frame
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1000)  # 等待初始化
                    return page

                # ---- 前置探测：沙箱链路是否真的能加载出应用 ----
                # 不探测的话，链路一断（URL 不可达/相对路径/服务未起）所有 AC 都在
                # 空白页上执行，失败被归类为「脚本驱动失败」不计缺陷 → 静默假绿
                sandbox_on = sandbox
                degraded_reason = None
                if sandbox_on:
                    try:
                        degraded_reason = _frame_load_failure(_load(True))
                    except Exception as e:
                        degraded_reason = f"宿主页加载异常: {e}"
                    if degraded_reason:
                        sandbox_on = False
                        logger.warning(
                            "[AC] 沙箱预览链路不可用（%s），本轮回退直读工作区文件执行 AC。"
                            "请检查预览地址是否可达: %s",
                            degraded_reason, preview_url,
                        )

                browser_dead = False  # req 154：driver 被 watchdog 杀掉后，新命令会永久挂起
                for script in ac_scripts:
                    ac_id = script.get("ac_id", "?")
                    label = script.get("label", ac_id)
                    steps = script.get("steps", [])
                    failures = []          # 产品断言失败（真实缺陷信号）
                    harness_failures = []  # 脚本驱动失败（假阴性嫌疑，不计入产品缺陷）
                    not_applicable = []    # 前提不成立的断言（如页面无 canvas 却断言 canvas 变化）
                    steps_executed = 0
                    doc = None

                    # QA 验收聚合：一个 AC 只产生一条可展示记录（头部结论 + 内部步骤时间线），
                    # 逐步事件仅实时推送不落库。此前每步落一条，单需求实测产出 340 条
                    # qa_step 记录，前端逐条渲染成满屏 [AC-1] 行（需求 196）。
                    _ac_steps: list[dict] = []
                    _ac_start_ts = None
                    try:
                        from utils.sse import get_current_timestamp as _gct
                        _ac_start_ts = _gct()
                    except Exception:
                        _ac_start_ts = None
                    if sse is not None and requirement_id is not None:
                        try:
                            sse.qa_start(requirement_id, {
                                "ac_id": ac_id,
                                "label": label,
                                "status": "running",
                                "steps": [],
                                "start_ts": _ac_start_ts,
                            })
                        except Exception:
                            pass

                    try:
                        doc = _load(sandbox_on)
                        _load_fail = _frame_load_failure(doc)
                        if _load_fail:
                            # 页面本身打不开 = 真实缺陷（用户看到的就是白屏），
                            # 必须记进 failures 而不是当成脚本问题吞掉
                            failures.append(f"页面无法加载: {_load_fail}")

                        def _is_effectively_visible(sel: str) -> bool:
                            """「对用户真的可见」判定。

                            Playwright 的 is_visible 只认 display:none / visibility:hidden /
                            空 bounding box，**不认 opacity:0**。而前端遮罩层最常用的
                            隐藏写法恰恰是 `opacity:0; pointer-events:none`
                            （req 198：.overlay--hidden 正是这么写的）——
                            用户眼里按钮已经消失，Playwright 却认为它可见，
                            于是读到残留文案「开始游戏」判成 critical 缺陷。
                            这里沿祖先链把 opacity 也算进去。
                            """
                            try:
                                return bool(doc.evaluate("""
                                    (sel) => {
                                        const el = document.querySelector(sel);
                                        if (!el) return false;
                                        let e = el;
                                        while (e && e.nodeType === 1) {
                                            const cs = getComputedStyle(e);
                                            if (cs.display === 'none') return false;
                                            if (cs.visibility === 'hidden') return false;
                                            if (parseFloat(cs.opacity || '1') === 0) return false;
                                            e = e.parentElement;
                                        }
                                        const r = el.getBoundingClientRect();
                                        return r.width > 0 && r.height > 0;
                                    }
                                """, sel))
                            except Exception:
                                return True

                        def _canvas_sig():
                            return doc.evaluate("""
                                () => {
                                    const c = document.querySelector('canvas');
                                    if (!c) return null;
                                    const ctx = c.getContext('2d');
                                    if (!ctx) return null;
                                    const d = ctx.getImageData(0, 0, c.width, c.height).data;
                                    let h = 0;
                                    for (let i = 0; i < d.length; i += 97) h = (h * 31 + d[i]) | 0;
                                    return h;
                                }
                            """)

                        def _dom_mutation_count():
                            return doc.evaluate("() => window.__acMut || 0")

                        # DOM 观察器必须在触发动作之前安装：老旧脚本的形状是
                        # press → wait → assert_*，DOM 变化发生在 wait 期间，
                        # 若等到 assert 步骤再装观察器就已经错过了。
                        def _install_dom_observer(sel):
                            try:
                                eff = _resolve_selector(doc, sel) if sel else None
                                target = eff or sel or "body"
                                doc.evaluate("""
                                    (sel) => {
                                        window.__acMut = 0;
                                        if (window.__acOb) { try { window.__acOb.disconnect(); } catch (e) {} }
                                        const el = document.querySelector(sel) || document.body;
                                        window.__acOb = new MutationObserver(
                                            ms => { window.__acMut += ms.length; }
                                        );
                                        window.__acOb.observe(
                                            el, {childList: true, subtree: true,
                                                 attributes: true, characterData: true}
                                        );
                                    }
                                """, target)
                                return True
                            except Exception as obs_err:
                                harness_failures.append(f"DOM 观察器安装失败: {obs_err}")
                                return False

                        # 预扫描：取本条 AC 第一条 assert_dom_change 的观察范围并立即挂上
                        dom_obs_ready = False
                        for _s in steps:
                            if _s.get("action") == "assert_dom_change":
                                dom_obs_ready = _install_dom_observer(_s.get("selector", ""))
                                break

                        for step in steps:
                            action = step.get("action", "")
                            selector = step.get("selector", "")
                            steps_executed += 1
                            _f_before = len(failures)
                            _na_before = len(not_applicable)
                            _h_before = len(harness_failures)

                            try:
                                if action == "type":
                                    eff = _resolve_selector(doc, selector) or selector
                                    doc.fill(eff, step.get("value", ""))
                                elif action == "click":
                                    eff = _resolve_selector(doc, selector) or selector
                                    _pos = _click_position(doc, eff, step)
                                    if _pos is None:
                                        doc.click(eff)
                                    else:
                                        # 带落点：必须走 Locator.click(position=)，
                                        # Frame/Page.click(selector) 不支持指定坐标。
                                        doc.locator(eff).first.click(position=_pos)
                                elif action == "select":
                                    eff = _resolve_selector(doc, selector) or selector
                                    doc.select_option(eff, step.get("value", ""))
                                elif action == "press":
                                    key = step.get("key", "Enter")
                                    # 关键修正（需求 140）：沙箱 iframe 内，游戏 keydown 监听
                                    # 在内层 document 上。page.locator("body").press 只打外层宿主页，
                                    # 内层收不到 → assert_canvas_change 全部假阴性（"游戏能玩但 AC 失效"）。
                                    # 统一用 doc.press（沙箱模式 doc 即内层 frame）投到内层，
                                    # 按键冒泡到内层 document 监听即可触发。
                                    doc.press(selector or "body", key)
                                elif action == "wait":
                                    page.wait_for_timeout(step.get("ms", 500))
                                elif action == "assert_exists":
                                    eff = _resolve_selector(doc, selector)
                                    if not eff or not doc.query_selector(eff):
                                        failures.append(f"元素不存在: {step.get('label', selector)}")
                                elif action == "assert_visible":
                                    eff = _resolve_selector(doc, selector)
                                    if not eff or not doc.is_visible(eff):
                                        failures.append(f"元素不可见: {step.get('label', selector)}")
                                elif action == "assert_text":
                                    eff = _resolve_selector(doc, selector) or selector
                                    elem = doc.query_selector(eff)
                                    contains = step.get("contains", "")
                                    if elem is None:
                                        failures.append(
                                            f"元素不存在: {step.get('label', selector)}"
                                        )
                                    else:
                                        # 不可见的元素无从断言文案 —— 它的 inner_text
                                        # 是"上一次可见时的残留文本"，把它当成产品缺陷会
                                        # 造出大量假阳性。req 198：AC-1 断言 #btnStart
                                        # 点击后变成「暂停」，而实现是点开始后直接隐藏
                                        # 整个遮罩层（游戏确实启动了），读到的残留文本
                                        # 「开始游戏」被判成 critical 缺陷。
                                        # 元素可见性由 assert_visible 负责判定；
                                        # 这里只把它归到「断言前提不成立」，不计入产品缺陷。
                                        try:
                                            _vis = _is_effectively_visible(eff)
                                        except Exception:
                                            _vis = True
                                        if not _vis:
                                            not_applicable.append(
                                                f"文本断言不适用（{selector} 当前不可见，"
                                                f"无法断言其文案）: 期望包含 '{contains}'"
                                            )
                                        else:
                                            text = elem.inner_text()
                                            if contains not in text:
                                                failures.append(
                                                    f"文本不匹配: 期望包含 '{contains}', "
                                                    f"实际 '{text[:100]}'"
                                                )
                                elif action == "assert_count":
                                    eff = _resolve_selector(doc, selector)
                                    sel = eff or selector
                                    count = len(doc.query_selector_all(sel))
                                    expected = step.get("min_count", 1)
                                    if count < expected:
                                        failures.append(
                                            f"元素数量不足: {selector} 期望 ≥{expected}, 实际 {count}"
                                        )
                                elif action == "assert_value":
                                    eff = _resolve_selector(doc, selector) or selector
                                    value = doc.input_value(eff)
                                    expected = step.get("value", "")
                                    if value != expected:
                                        failures.append(
                                            f"值不匹配: {selector} 期望 '{expected}', 实际 '{value}'"
                                        )
                                elif action == "assert_canvas_change":
                                    s0 = _canvas_sig()
                                    page.wait_for_timeout(step.get("wait_ms", 2000))
                                    s1 = _canvas_sig()
                                    if s0 is None and s1 is None:
                                        # 页面没有 canvas（DOM/网格渲染实现）→ 断言前提不成立。
                                        # 记为「不适用」，走 unverified 通道：
                                        #   - 不计入 failures（它不是产品缺陷，实现可能完全正确）
                                        #   - 也不计 harness_errors（不是脚本驱动失败）
                                        # 保留 passed=False —— 「未验证」绝不等于「通过」。
                                        # req 147 复盘：混进 harness_errors 会让 DOM 游戏被永久判死，
                                        # 且缺陷清单里出现的全是「脚本错误」，修复环节拿不到有效信号。
                                        msg = (
                                            f"canvas 断言不适用（页面无 canvas 元素）: "
                                            f"{step.get('label', '')}"
                                        )
                                        not_applicable.append(msg)
                                    elif s0 == s1:
                                        failures.append(
                                            f"canvas 无变化: {step.get('label', '画面应随操作变化')}"
                                        )
                                elif action == "assert_dom_change":
                                    # DOM 实现的「画面变化」断言：统计本条 AC 开始到现在
                                    # 观察范围内的 DOM 变更次数。req 147 复盘：DOM 游戏的
                                    # 画面类 AC 原先被翻译成 assert_canvas_change，
                                    # 结果永远是「不适用」，拿不到任何可判定信号。
                                    if not dom_obs_ready:
                                        dom_obs_ready = _install_dom_observer(selector)
                                    page.wait_for_timeout(step.get("wait_ms", 1500))
                                    min_changes = step.get("min_changes", 1)
                                    n = _dom_mutation_count()
                                    if n < min_changes:
                                        failures.append(
                                            f"DOM 无变化: {step.get('label', '界面应随操作更新')}"
                                            f"（观察到变更 {n} 次，期望 ≥{min_changes}）"
                                        )
                                elif action == "screenshot":
                                    # 截图用于 LLM 诊断（不参与通过/失败判断）
                                    pass
                            except Exception as step_err:
                                # 操作类步骤抛异常 = 脚本无法驱动页面（选择器失配/超时），
                                # 与产品断言失败区分，避免假阴性压垮验收
                                harness_failures.append(f"步骤 [{action} {selector}]: {step_err}")
                                # req 154：driver 已死时后续命令会永久挂起，立即中止
                                if _is_fatal_transport_error(step_err):
                                    browser_dead = True
                                    break

                            # 逐步事件：收集进本 AC 的步骤汇总（无论是否绑定 SSE 都收集，
                            # 保证落库的 qa_result 始终完整）；绑定 SSE 时额外实时推送。
                            _f_added = failures[_f_before:]
                            _na_added = not_applicable[_na_before:]
                            _h_added = harness_failures[_h_before:]
                            if _h_added:
                                _status = "error"
                                _detail = _h_added[-1]
                            elif _f_added:
                                _status = "fail"
                                _detail = _f_added[-1]
                            elif _na_added:
                                _status = "na"
                                _detail = _na_added[-1]
                            else:
                                _status = "ok"
                                _detail = ""
                            _qa_step_payload = {
                                "ac_id": ac_id,
                                "action": action,
                                "selector": selector,
                                "value": step.get("value", ""),
                                "status": _status,
                                "detail": (_detail or "")[:300],
                            }
                            _ac_steps.append(_qa_step_payload)
                            if sse is not None and requirement_id is not None:
                                try:
                                    sse.qa_step(requirement_id, _qa_step_payload)
                                except Exception:
                                    pass

                    except Exception as ac_err:
                        harness_failures.append(f"AC 执行异常: {ac_err}")
                        if _is_fatal_transport_error(ac_err):
                            browser_dead = True

                    # ---- AC 结束：汇总成一条验收记录（实时推送 + 落库各一条） ----
                    _ac_passed = len(failures) == 0 and not harness_failures and not not_applicable
                    if harness_failures:
                        _ac_status = "error"
                    elif failures:
                        _ac_status = "fail"
                    elif not_applicable:
                        _ac_status = "na"
                    else:
                        _ac_status = "ok"
                    _ac_end_ts = None
                    try:
                        from utils.sse import get_current_timestamp as _gct2
                        _ac_end_ts = _gct2()
                    except Exception:
                        pass
                    _ac_payload = {
                        "ac_id": ac_id,
                        "label": label,
                        "status": _ac_status,
                        "passed": _ac_passed,
                        "steps": _ac_steps,
                        "start_ts": _ac_start_ts,
                        "end_ts": _ac_end_ts,
                        "summary": "; ".join(
                            (failures + harness_failures + not_applicable)[:3]
                        )[:300],
                    }
                    if sse is not None and requirement_id is not None:
                        try:
                            sse.qa_result(requirement_id, _ac_payload)
                        except Exception:
                            pass
                    # 落库：一个 AC 一条完整记录（含内部步骤），刷新后仍可恢复成同一张卡。
                    # 0 步不落卡：一个步骤都没跑起来，说明这条 AC 根本没被执行
                    # （浏览器提前退出 / 页面加载失败 / 事件错位），落进历史只会让刷新后
                    # 冒出「暂无步骤记录」的空卡，看着像质量工程师什么都没做。
                    # 前端收到 0 步的 qa_result 也会把那张卡撤掉，两侧保持一致。
                    if dialogue_history is not None and _ac_steps:
                        dialogue_history.append({
                            "role": "qa_result",
                            "name": "Catherine（质量工程师）",
                            "content": f"[{ac_id}] {label}",
                            "qa_result": _ac_payload,
                            "timestamp": _ac_end_ts,
                        })

                    # 「断言前提不成立」（N/A）不再混进 harness_errors：
                    # 它不是「脚本驱动失败」，而是「这条断言对当前实现根本不适用」。
                    # 单独走 unverified 通道，交给评估器裁量——既保留「未验证 ≠ 通过」，
                    # 又不再把「验不了」伪装成「验没过」去污染产品缺陷判定。
                    results.append({
                        "ac_id": ac_id,
                        "label": label,
                        # 脚本驱动失败（harness_errors）同样阻断 passed：选择器超时 / 元素点不动
                        # 意味着这条 AC 从未被真正验证过，此时判 passed=True 是假绿
                        #（req 145 的 AC-1/AC-4 就是带着 harness_errors 拿到 passed）。
                        # 与 nodes.py 快速通道 ac_all_passed 的语义保持一致。
                        "passed": len(failures) == 0 and not harness_failures and not not_applicable,
                        "failures": failures,
                        "harness_errors": harness_failures,
                        "not_applicable": not_applicable,
                        # True = 本条 AC 从未被真正验证（断言前提不成立），既非通过也非失败
                        "unverified": bool(not_applicable) and not failures and not harness_failures,
                        # True = 脚本没跑成 且 断言失败（P2.5）。此时 failures 是在「被半驱动坏的
                        # 页面」上产生的：点击超时没点成，后面的断言自然找不到元素。
                        # 这类失败是幽灵，直接喂给 repair 会让 coder 去修不存在的问题。
                        # 实测（tmp/analyze_ac_verification.py）：139 条 AC 中 45.3% 属于此类。
                        # 这里只做标记不丢信号，由 nodes.py 在提示词里降权。
                        "compromised": bool(failures) and bool(harness_failures),
                        "steps_executed": steps_executed,
                        # 沙箱链路降级时标注，便于区分「产品坏」与「验证环境坏」
                        "preview_degraded": degraded_reason,
                    })

                    if browser_dead:
                        # 剩余 AC 无法再执行：标记驱动失败并跳出（下一轮 _load 会挂死）
                        remaining = ac_scripts[len(results):]
                        for rest in remaining:
                            results.append({
                                "ac_id": rest.get("ac_id", "?"),
                                "label": rest.get("label", rest.get("ac_id", "?")),
                                "passed": False,
                                "failures": [],
                                "harness_errors": ["浏览器会话已中断（driver 被 watchdog 终止）"],
                                "steps_executed": 0,
                            })
                        logger.warning(
                            "[AC] 浏览器会话在执行中死亡（watchdog 终止/崩溃），剩余 %d 条 AC 标记为驱动失败",
                            len(remaining),
                        )
                        break

            finally:
                browser.close()
    except Exception as e:
        logger.warning("AC 验证运行异常: %s", e)
        return [{"ac_id": s["ac_id"], "passed": False, "failures": [], "harness_errors": [f"运行异常: {e}"], "steps_executed": 0} for s in ac_scripts]
    finally:
        if wrapper_tmp:
            try:
                import os as _os
                _os.unlink(wrapper_tmp)
            except OSError:
                pass

    return results


def _loc(loc) -> str:
    try:
        return f"{loc.url}:{loc.line_number}:{loc.column_number}"
    except Exception:
        return ""


def _parse_error_location(stack: str) -> str:
    """从 pageerror 的 stack 里提取「文件:行号」。

    req 189：裸 message（"Unexpected token ')'”）不带位置，coder 在 4 个 JS
    文件间盲猜 5 轮也没修掉。stack 首个 at 帧形如
    "at http://host/js/app.js:216:20"（file:// 直读同理），取末段文件名 + 行号
    即可把错误钉到具体位置。解析失败返回空串，绝不抛异常。
    """
    try:
        for _line in (stack or "").splitlines():
            _line = _line.strip()
            if _line.startswith("at ") and (".js" in _line or ".html" in _line):
                _ref = _line[3:].split(" ")[0]
                _parts = _ref.split(":")
                if len(_parts) >= 2:
                    _file = _parts[-3] if len(_parts) >= 3 else _parts[0]
                    return f"{_file.split('/')[-1]}:{_parts[-2]}"
                break
    except Exception:
        pass
    return ""


def capture_screenshot(html_path: Path, out_path: Path,
                       timeout_ms: int = 12_000, preview_url: str = None) -> str | None:
    """在全新线程里执行截图会话（req 154）。超时返回 None（与既有失败语义一致）。"""
    budget = _watchdog_ms(timeout_ms, 2) / 1000.0 + _SESSION_BUDGET_SLACK_S
    try:
        return run_browser_session_isolated(
            _capture_screenshot_session, budget, html_path, out_path,
            timeout_ms=timeout_ms, preview_url=preview_url,
        )
    except BrowserSessionTimeout as e:
        logger.warning("[Screenshot] 截图会话超时（跳过）: %s", e)
        return None


def _capture_screenshot_session(html_path: Path, out_path: Path,
                       timeout_ms: int = 12_000, preview_url: str = None) -> str | None:
    """对 index.html 截图（与用户一致的沙箱预览链路），保存为 PNG。

    用途：fast_pass 通道不再硬编码 ui_quality——截图落盘到
    .task/evaluator/screenshot.png，供用户查看与后续多模态评估使用。

    Returns:
        截图文件路径字符串；浏览器不可用等失败时返回 None（不抛异常）。
    """
    try:
        from harness.tools.sandboxed_browser import sandboxed_browser
    except ImportError:
        return None

    url = Path(html_path).resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)

    try:
        from playwright.sync_api import Error as PWError
        # watchdog 覆盖 goto/settle/截图的合法等待（req 154）
        with sandboxed_browser(
            timeout_ms=_watchdog_ms(timeout_ms, 2),
            allow_hosts=_preview_allow_hosts(preview_url),
        ) as browser:
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
                # 声明为复验/预览请求：已发布站点的 Host 路由据此**跳过 badge 装饰**，
                # 让验收跑在用户作品的原稿上。badge 是平台装饰，不属于验收范围，
                # 且它浮在右下角可能遮挡 AC 脚本要点击的元素，造成假红。
                # 详见 services/publish/decorate.py
                page.set_extra_http_headers({"X-T2C-Verify": "1"})
                page.set_default_timeout(timeout_ms)
                target = page
                if sandbox:
                    page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1500)
                    target = _resolve_preview_frame(page) or page
                    # 沙箱链路打不开就截到一张空白图，等于没留档 → 回退直读文件
                    fail = _frame_load_failure(target)
                    if fail:
                        logger.warning("[Screenshot] 沙箱预览不可用（%s），回退直读文件", fail)
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1200)
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1200)
                # 对内层 frame 截图时截整页宿主（包含 iframe 内容）
                out_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_path), full_page=False)
                return str(out_path)
            finally:
                browser.close()
    except PWError as e:
        logger.warning(f"[Screenshot] 浏览器不可用，跳过截图: {e}")
        return None
    except Exception as e:
        logger.warning(f"[Screenshot] 截图失败（跳过）: {e}")
        return None
    finally:
        if wrapper_tmp:
            try:
                import os as _os
                _os.unlink(wrapper_tmp)
            except OSError:
                pass


# ==================== 预览链路沙箱加载 ====================
# 与前端 PreviewFrame.vue 完全一致：sandbox="allow-scripts allow-forms"（无 allow-same-origin）
# 验证环境必须与用户真实预览环境同构，否则会出现"验证全过、用户看到死页面"

_SANDBOX_ATTRS = "allow-scripts allow-forms"


def _make_sandbox_wrapper_uri(preview_url: str) -> tuple[str, str]:
    """生成与前端预览 iframe 属性一致的宿主页，返回 (file:// URI, 临时文件路径)"""
    import tempfile
    wrapper = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0">'
        f'<iframe id="t2cframe" src="{preview_url}" '
        'style="width:100vw;height:100vh;border:0" '
        f'sandbox="{_SANDBOX_ATTRS}"></iframe>'
        '</body></html>'
    )
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8"
    )
    tmp.write(wrapper)
    tmp.close()
    return Path(tmp.name).resolve().as_uri(), tmp.name


def _prepare_sandbox(preview_url: str | None) -> tuple[bool, str | None, str | None]:
    """准备沙箱宿主页。

    宿主页是 file:// 临时文件，iframe src 必须是**绝对** URL，否则相对路径
    会解析成 `file:///api/pt/...` → chrome-error 空白页，所有后续检查都在
    空白页上执行（需求 140 假绿事故）。这里前置拦掉非绝对地址。

    Returns:
        (是否启用沙箱, 宿主页 file:// URI, 宿主页临时文件路径)
    """
    if not preview_url:
        return False, None, None
    if not preview_url.startswith(("http://", "https://")):
        logger.warning(
            "[Preview] preview_url 非绝对地址（%s），file:// 宿主页无法解析 "
            "—— 禁用沙箱模式，回退直读工作区文件",
            preview_url[:80],
        )
        return False, None, None
    uri, tmp = _make_sandbox_wrapper_uri(preview_url)
    return True, uri, tmp


def _frame_load_failure(doc) -> str | None:
    """检测 frame/page 是否真的加载出了内容。

    返回失败原因字符串，加载正常时返回 None。
    没有这道检查，chrome-error 空白页会让 CTA 查找返回空、检查项被静默
    跳过，最终 available=True / defects=0 的「假绿」。
    """
    try:
        url = doc.url or ""
    except Exception:
        url = ""
    if url.startswith("chrome-error://"):
        return f"页面加载失败 (url={url})"
    if url in ("", "about:blank"):
        return f"页面未导航 (url={url or '空'})"
    try:
        n = doc.evaluate("() => document.body ? document.body.children.length : -1")
    except Exception as e:
        return f"文档不可访问: {e}"
    if not isinstance(n, int) or n <= 0:
        return f"文档为空 (body 子元素数={n})"
    return None


def _resolve_preview_frame(page):
    """定位沙箱 iframe 的内层 frame（非主 frame 的第一个）"""
    for fr in page.frames:
        if fr != page.main_frame:
            return fr
    return page.main_frame


def _install_frame_error_hook(frame):
    """在沙箱 iframe 内安装错误收集器（沙箱 frame 的错误不一定冒泡到 page 事件）"""
    try:
        frame.evaluate(
            "() => { window.__t2c_errs = [];"
            "window.addEventListener('error', e => window.__t2c_errs.push('js: ' + (e.message || '')));"
            "window.addEventListener('unhandledrejection', e =>"
            " window.__t2c_errs.push('promise: ' + (String(e.reason).slice(0, 120)))); }"
        )
    except Exception:
        pass


def _collect_frame_errors(frame, extra: list) -> list:
    errs = list(extra)
    try:
        inner = frame.evaluate("() => (window.__t2c_errs || [])")
        if isinstance(inner, list):
            errs.extend(inner)
    except Exception:
        pass
    return errs


# ==================== 层1 通用冒烟测试（品类无关） ====================

# 主交互入口的通用动词（覆盖 工具/游戏/表单/展示 各品类，中英兼顾）
_CTA_WORDS = (
    "开始 启动 试一试 试一下 立即体验 立即开始 播放 play start "
    "提交 登录 注册 计算 生成 添加 创建 保存 搜索 转换 下载 加载 继续 重试 换一个 随机 "
    "submit login sign register calculate generate add create save search convert try apply run"
).split()

# 终止/失败态标记（只在"点击主交互后新出现"时才算缺陷）
_TERMINAL_MARKERS = (
    "游戏结束 game over 挑战失败 你输了 you lose "
    "出错了 发生错误 something went wrong error occurred "
    "加载失败 请求失败 提交失败 failed to load network error 崩溃"
).split()


def run_universal_smoke(html_path: Path, timeout_ms: int = 15_000, preview_url: str = None) -> dict:
    """在全新线程里执行通用冒烟会话（req 154）。

    超时返回 available=False 的空结果——与 verify 节点冒烟异常时的降级
    形状一致（「跳过」而非「通过」）。
    """
    budget = _watchdog_ms(timeout_ms, 4) / 1000.0 + _SESSION_BUDGET_SLACK_S
    try:
        return run_browser_session_isolated(
            _run_universal_smoke_session, budget, html_path,
            timeout_ms=timeout_ms, preview_url=preview_url,
        )
    except BrowserSessionTimeout as e:
        logger.warning("[Smoke] 通用冒烟会话超时（按不可用处理）: %s", e)
        return {"available": False, "defects": [], "checks": {},
                "logs": [f"浏览器会话超时: {e}"]}


def _run_universal_smoke_session(html_path: Path, timeout_ms: int = 15_000, preview_url: str = None) -> dict:
    """
    通用冒烟测试：与品类无关的确定性不变量，任何网页交付物一律适用。

    加载方式：
    - preview_url 提供时，通过与前端完全一致的沙箱 iframe（sandbox="allow-scripts
      allow-forms"）加载真实预览链路，保证"验证通过"等价于"用户可用"
    - 否则回退为直接打开工作区文件

    不变量：
    1. self_contained   — index.html 不硬依赖外部 CDN（script/样式表）
    2. interactive      — 主交互入口点击后页面有可观察变化（DOM 变动或 canvas 像素）
    3. no_instant_death — 主交互触发后 2.5s 内不新出现终止/失败态文案
    4. storage_safe     — localStorage 被禁用（预览沙箱约束）时页面不产生新 JS 错误

    Returns:
        {
          "available": bool,          # 浏览器/检查是否可用（False 时调用方应忽略结果）
          "checks": {name: bool},
          "defects": [{"type","severity","dimension","message","evidence","suggestion"}],
          "logs": [str],
        }
    """
    result = {"available": False, "checks": {}, "defects": [], "logs": []}

    import re as _re
    cdn_re = _re.compile(
        r'<(?:script[^>]+src|link[^>]+rel=["\']stylesheet["\'][^>]+href)=["\'](https?://[^"\']+)["\']',
        _re.IGNORECASE,
    )

    html_text = ""
    try:
        html_text = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        result["logs"].append(f"[smoke] 读取 HTML 失败: {e}")
        return result

    # ---- 不变量 1: 自包含（静态扫描，零成本） ----
    cdn_urls = sorted(set(cdn_re.findall(html_text)))
    if cdn_urls:
        result["defects"].append({
            "type": "cdn_dependency",
            "severity": "major",
            "dimension": "runtime",
            "message": f"页面硬依赖 {len(cdn_urls)} 个外部 CDN 资源，CDN 不可达时布局/功能会塌陷",
            "evidence": "; ".join(cdn_urls[:5]),
            "suggestion": "把 CDN 样式/脚本改为本地文件或内联（如 Tailwind → 原生 css/style.css），确保离线可完整运行",
        })
        result["checks"]["self_contained"] = False
    else:
        result["checks"]["self_contained"] = True

    # ---- 不变量 2/3/4: 需要浏览器 ----
    try:
        from harness.tools.sandboxed_browser import sandboxed_browser
    except ImportError:
        result["logs"].append("[smoke] playwright 未安装，跳过交互检查")
        return result

    url = Path(html_path).resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)
    if sandbox:
        result["logs"].append(f"[smoke] 沙箱预览模式: {preview_url.split('/api/pt/')[-1][:40]}")

    try:
        # watchdog 覆盖 goto/settle/CTA 探测/交互检查的合法等待（req 154）
        with sandboxed_browser(
            timeout_ms=_watchdog_ms(timeout_ms, 4),
            allow_hosts=_preview_allow_hosts(preview_url),
        ) as browser:

            try:
                context = browser.new_context()
                page = context.new_page()
                # 声明为复验/预览请求：已发布站点的 Host 路由据此**跳过 badge 装饰**，
                # 让验收跑在用户作品的原稿上。badge 是平台装饰，不属于验收范围，
                # 且它浮在右下角可能遮挡 AC 脚本要点击的元素，造成假红。
                # 详见 services/publish/decorate.py
                page.set_extra_http_headers({"X-T2C-Verify": "1"})
                page.set_default_timeout(timeout_ms)

                load_errors: list[str] = []

                def _err(msg):
                    load_errors.append(msg)

                page.on("pageerror", lambda e: _err(f"pageerror: {e}"))
                page.on("console", lambda m: _err(f"console_error: {m.text}") if m.type == "error" else None)
                page.on("dialog", lambda d: d.accept())

                def _open(use_sandbox: bool):
                    """加载页面（沙箱模式返回内层 frame），并安装错误收集器"""
                    if use_sandbox:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)  # 等待 iframe 资源拉取
                        fr = _resolve_preview_frame(page)
                        _install_frame_error_hook(fr)
                        return fr
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1200)
                    return page

                sandbox_on = sandbox
                doc = _open(sandbox_on)

                # ---- 不变量 0: 页面真的加载出内容了 ----
                # 这是所有后续检查的前提。缺了它，空白页会让 CTA 查找返回空、
                # interactive/no_instant_death 被静默跳过，最终 defects=0 假绿
                # （需求 140：iframe 落到 chrome-error，验证全绿但用户看到死页面）
                load_fail = _frame_load_failure(doc)
                if load_fail and sandbox_on:
                    result["logs"].append(f"[smoke] 沙箱预览不可用（{load_fail}），回退直读文件")
                    logger.warning("[smoke] 沙箱预览链路不可用（%s），回退直读文件: %s",
                                   load_fail, preview_url)
                    sandbox_on = False
                    doc = _open(False)
                    load_fail = _frame_load_failure(doc)
                result["checks"]["page_loads"] = not load_fail
                if load_fail:
                    result["defects"].append({
                        "type": "preview_unloadable",
                        "severity": "critical",
                        "dimension": "runtime",
                        "message": f"预览页面加载不出任何内容（{load_fail}），用户打开就是白屏",
                        "evidence": load_fail,
                        "suggestion": (
                            "确认 index.html 存在且 <body> 有实际内容；检查 css/js 引用路径"
                            "（相对路径、大小写、目录层级）是否与实际文件一致"
                        ),
                    })

                # ---- 找主交互入口（品类无关启发式） ----
                cta = None
                try:
                    candidates = doc.evaluate("""
                        () => {
                            const els = [...document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')];
                            return els.map((el, i) => {
                                const r = el.getBoundingClientRect();
                                const style = getComputedStyle(el);
                                const visible = r.width > 0 && r.height > 0
                                    && style.visibility !== 'hidden' && style.display !== 'none'
                                    && style.pointerEvents !== 'none';
                                return {i, tag: el.tagName, text: (el.innerText || el.value || '').trim().slice(0, 30), visible};
                            }).filter(x => x.visible && x.text);
                        }
                    """)
                    scored = []
                    for c in candidates:
                        low = c["text"].lower()
                        score = sum(2 for w in _CTA_WORDS if w in low) or (1 if c["tag"] in ("BUTTON", "INPUT") else 0)
                        if score > 0:
                            scored.append((score, c))
                    if scored:
                        scored.sort(key=lambda x: -x[0])
                        cta = scored[0][1]
                        result["logs"].append(f"[smoke] 主交互入口: <{cta['tag']}> '{cta['text']}'")
                    elif candidates:
                        cta = candidates[0]
                        result["logs"].append(f"[smoke] 未匹配动词，回退首个可见按钮: '{cta['text']}'")
                except Exception as e:
                    result["logs"].append(f"[smoke] CTA 查找异常: {e}")

                if cta:
                    # ---- 入口遮挡检测：Playwright 严格 actionability 会拒绝被遮罩覆盖的
                    # 点击（需求 129：结束遮罩 endOverlay 默认可见盖住 startBtn，玩家点不到开始）。
                    # smoke 用 JS 直点绕过遮挡会漏报，这里用 elementFromPoint 显式探测。 ----
                    blocked = None
                    try:
                        blocked = doc.evaluate("""
                            (idx) => {
                                const els = [...document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')];
                                const el = els[idx];
                                if (!el) return null;
                                const r = el.getBoundingClientRect();
                                if (r.width <= 0 || r.height <= 0) return null;
                                const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                                const top = document.elementFromPoint(cx, cy);
                                if (!top) return null;
                                if (top === el || el.contains(top) || top.contains(el)) return null;
                                const topTag = top.tagName;
                                const topText = (top.innerText || '').trim().slice(0, 24);
                                const topCls = (top.className || '').toString().slice(0, 40);
                                return { tag: topTag, text: topText, cls: topCls };
                            }
                        """, cta["i"])
                    except Exception:
                        pass
                    if blocked:
                        result["checks"]["interactive"] = False
                        result["defects"].append({
                            "type": "entry_blocked",
                            "severity": "critical",
                            "dimension": "functionality",
                            "message": (
                                f"主交互入口 '<{cta['tag']}> {cta['text']}' 被其他元素遮挡无法点击"
                                f"（中心点命中 <{blocked['tag']}> 文本='{blocked['text']}' class='{blocked['cls']}'）。"
                                "用户点不到主入口，等同于不可用"
                            ),
                            "evidence": f"elementFromPoint 命中遮挡元素 <{blocked['tag']}>",
                            "suggestion": (
                                "检查是否有遮罩/弹层默认可见且覆盖主入口：开始按钮在初始化前不得被"
                                "结束遮罩/欢迎遮罩/半透明层挡住。遮罩必须默认隐藏（display:none 或"
                                "hidden 类），仅游戏结束时显示；或让主入口 z-index 高于遮罩"
                            ),
                        })
                        result["logs"].append(
                            f"[smoke] 主入口 '<{cta['tag']}> {cta['text']}' 被遮挡: "
                            f"<{blocked['tag']}> text='{blocked['text']}' cls='{blocked['cls']}'"
                        )
                    else:
                        result["logs"].append(f"[smoke] 主入口 '<{cta['tag']}> {cta['text']}' 未被遮挡")

                    # ---- 安装观察器：DOM 变动计数 ----
                    doc.evaluate("""
                        () => {
                            window.__t2c_mutations = 0;
                            const ob = new MutationObserver(muts => { window.__t2c_mutations += muts.length; });
                            ob.observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
                        }
                    """)

                    def _canvas_sig():
                        return doc.evaluate("""
                            () => {
                                const c = document.querySelector('canvas');
                                if (!c) return null;
                                const ctx = c.getContext('2d');
                                if (!ctx) return null;
                                const d = ctx.getImageData(0, 0, c.width, c.height).data;
                                let h = 0;
                                for (let i = 0; i < d.length; i += 97) h = (h * 31 + d[i]) | 0;
                                return h;
                            }
                        """)

                    sig_before = _canvas_sig()
                    text_before = (doc.inner_text("body") or "").lower()

                    # 入口被遮挡时已判 interactive=False，跳过 JS 直点观察（避免覆盖判定）
                    if not blocked:
                        # ---- 先填就近的可见文本输入，再点击主交互入口 ----
                        # 待办/表单类应用的「添加」按钮在空输入时按设计就该无反应
                        # （合法防御，不是 bug），不填就 null 点击会判 no_interaction critical，
                        # 连修多轮都打偏。这里在点击前找页面上离 CTA 最近的可见 text input
                        # 填「test」—— 模拟真实用户的填表行为。
                        # search 类型刻意排除：fill 反而触发 list 过滤成空 → 点击后
                        # 即便添加成功整页变化也少 → 仍可能误判。
                        try:
                            filled = doc.evaluate("""
                                (buttonIdx) => {
                                    const buttons = [...document.querySelectorAll(
                                        'button, a, [role="button"], input[type="button"], input[type="submit"]')];
                                    const btn = buttons[buttonIdx];
                                    if (!btn) return null;
                                    const inputs = [...document.querySelectorAll(
                                        'input[type="text"], input[type="email"], input[type="url"], '
                                        + 'input[type="tel"], input:not([type])'
                                    )];
                                    const visible = inputs.filter(el => {
                                        const r = el.getBoundingClientRect();
                                        const s = getComputedStyle(el);
                                        return r.width > 0 && r.height > 0
                                            && s.visibility !== 'hidden' && s.display !== 'none'
                                            && !el.disabled && !el.readOnly;
                                    });
                                    if (!visible.length) return null;
                                    const bRect = btn.getBoundingClientRect();
                                    let best = null, bestDist = Infinity;
                                    for (const inp of visible) {
                                        const r = inp.getBoundingClientRect();
                                        const dx = (r.left + r.width/2) - (bRect.left + bRect.width/2);
                                        const dy = (r.top + r.height/2) - (bRect.top + bRect.height/2);
                                        const d = dx*dx + dy*dy;
                                        if (d < bestDist) { bestDist = d; best = inp; }
                                    }
                                    if (!best) return null;
                                    // 用原生 setter 绕开框架对 input.value 的劫持，
                                    // dispatch input+change 让 Vue v-model / React onChange 感知。
                                    const setter = Object.getOwnPropertyDescriptor(
                                        window.HTMLInputElement.prototype, 'value').set;
                                    setter.call(best, 'test');
                                    best.dispatchEvent(new Event('input', {bubbles: true}));
                                    best.dispatchEvent(new Event('change', {bubbles: true}));
                                    return best.outerHTML.slice(0, 200);
                                }
                            """, cta["i"])
                            if filled:
                                result["logs"].append(f"[smoke] 点击前先填就近文本输入: {filled}")
                        except Exception as e:
                            result["logs"].append(f"[smoke] 预填输入异常（继续原点击）: {e}")

                        # ---- 不变量 2+3: 点击主交互并观察 ----
                        try:
                            # cta["i"] 是 querySelectorAll 原始列表中的索引，直接按索引点击
                            doc.evaluate("""
                                (idx) => {
                                    const els = [...document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')];
                                    els[idx]?.click();
                                }
                            """, cta["i"])
                            page.wait_for_timeout(2500)
                        except Exception as e:
                            result["logs"].append(f"[smoke] 点击主交互异常: {e}")

                        mutations = doc.evaluate("() => window.__t2c_mutations || 0")
                        sig_after = _canvas_sig()
                        text_after = (doc.inner_text("body") or "").lower()

                        changed = mutations > 0 or sig_before != sig_after
                        result["checks"]["interactive"] = bool(changed)
                        if not changed:
                            result["defects"].append({
                                "type": "no_interaction",
                                "severity": "critical",
                                "dimension": "functionality",
                                "message": f"点击主交互入口 '{cta['text']}' 后 2.5s 内页面无任何可观察变化（DOM 与 canvas 均静止）",
                                "evidence": f"mutations={mutations}, canvas_changed={sig_before != sig_after}",
                                "suggestion": "检查事件绑定是否生效（元素选择器、脚本加载顺序、初始化调用）；主按钮必须驱动可见的状态变化",
                            })

                        new_terminal = [m for m in _TERMINAL_MARKERS if m not in text_before and m in text_after]
                        result["checks"]["no_instant_death"] = not new_terminal
                        if new_terminal:
                            result["defects"].append({
                                "type": "instant_death",
                                "severity": "major",
                                "dimension": "functionality",
                                "message": f"点击主交互 '{cta['text']}' 后立即进入终止/失败态（出现: {', '.join(new_terminal[:3])}），用户来不及操作",
                                "evidence": f"新出现的终止标记: {new_terminal}",
                                "suggestion": (
                                    "主流程启动必须有缓冲，二选一实现："
                                    "(a) 等待首次输入——点击开始后画面就绪但角色不移动，提示「按方向键开始」，"
                                    "首次方向键 keydown 才触发游戏循环；"
                                    "(b) 3-2-1 倒计时——倒计时结束才启动移动定时器。"
                                    "实现要点：把「启动移动」的调用从 start()/click 处理器中拆出，"
                                    "由首次 keydown 或 setTimeout(3000) 触发。注意：这不是样式问题，"
                                    "是流程问题，必须在 JS 逻辑中修改"
                                ),
                            })

                        # ---- 不变量 5: 键盘驱动型应用的输入响应 ----
                        # 需求 140 事故：点「开始游戏」后 renderReady() 画出蛇（interactive
                        # 判绿），但循环从未启动，按方向键永远不动。只看「点击后有变化」
                        # 抓不到，必须显式验证键盘输入后画面持续变化。
                        # 品类门禁：仅当页面有 canvas 且文案提示键盘操作时才检查。
                        _kbd_gate = False
                        try:
                            _kbd_gate = bool(doc.evaluate("""
                                () => {
                                    if (!document.querySelector('canvas')) return false;
                                    const t = ((document.body.innerText || '')).toLowerCase();
                                    return /方向键|方向鍵|箭头键|上下左右|wasd|arrow key|arrow keys|↑|←|→|↓/.test(t);
                                }
                            """))
                        except Exception:
                            _kbd_gate = False

                        if _kbd_gate:
                            # 关键修正（需求 140）：真实按键必须通过「内层 frame」投递。
                            # page.keyboard.press 只打外层宿主页，沙箱 iframe 的 document
                            # 收不到 keydown，循环永远起不来——这是 harness 焦点问题而非
                            # 产品缺陷。改用 doc.press("body", key)，按键会冒泡到内层
                            # document 的 keydown 监听。
                            # req 190 复盘：自动移动类游戏（贪吃蛇形态）在上方 2.5s 观察
                            # 窗口内可能已撞墙结束（开局居中、直行朝墙，实测 ~1.4s 即
                            # game over）。死局下按键合法地无效，旧逻辑据此把「游戏已
                            # 正常结束」误判成「游戏循环从未启动」。这里重按一次主入口
                            # （结束态下它正是「再来一局」；对其它应用是重复同一动作，
                            # 无观察层破坏），把键盘检查拉回"活着"的窗口内。
                            try:
                                doc.evaluate("""
                                    (idx) => {
                                        const els = [...document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')];
                                        els[idx]?.click();
                                    }
                                """, cta["i"])
                                page.wait_for_timeout(150)
                            except Exception:
                                pass
                            try:
                                _cv = doc.query_selector("canvas")
                                if _cv:
                                    _cv.click(timeout=3000)  # 先把焦点交给 canvas/内层 frame
                            except Exception:
                                pass
                            k0 = _canvas_sig()
                            for _k in ("ArrowRight", "ArrowDown"):
                                try:
                                    doc.press("body", _k)  # 帧级真实按键
                                except Exception:
                                    pass
                                page.wait_for_timeout(350)
                            page.wait_for_timeout(900)
                            k1 = _canvas_sig()
                            page.wait_for_timeout(700)
                            k2 = _canvas_sig()
                            real_alive = (k0 != k1) or (k1 != k2)

                            if real_alive:
                                alive = True
                                result["logs"].append(
                                    "[smoke] 真实按键（帧级投递）已驱动画面，键盘响应通过"
                                )
                            else:
                                # 兜底：真实帧投递可能因沙箱焦点限制失败，再用合成 keydown
                                # 直接派发到内层 document 确认循环逻辑本身是否可驱动。
                                # 合成能驱动 → 产品逻辑没问题，记为通过（标注 harness 限制）；
                                # 合成也不动 → 循环确实没启动，记真实缺陷。
                                try:
                                    doc.evaluate("""
                                        () => ['ArrowRight','ArrowDown'].forEach(k =>
                                            document.dispatchEvent(new KeyboardEvent('keydown', {
                                                key: k, code: k, bubbles: true,
                                                keyCode: k === 'ArrowRight' ? 39 : 40
                                            })))
                                    """)
                                except Exception:
                                    pass
                                page.wait_for_timeout(1800)
                                k3 = _canvas_sig()
                                if k3 != k2:
                                    alive = True
                                    result["logs"].append(
                                        "[smoke] 真实帧投递受限，但合成 keydown 已驱动画面，"
                                        "判定键盘响应通过（沙箱焦点限制，非产品缺陷）"
                                    )
                                else:
                                    alive = False
                                    result["logs"].append(
                                        "[smoke] 真实按键与合成事件均未驱动画面，循环确未启动"
                                    )

                            result["checks"]["keyboard_responsive"] = bool(alive)
                            if alive is False:
                                result["defects"].append({
                                    "type": "input_no_response",
                                    "severity": "critical",
                                    "dimension": "functionality",
                                    "message": (
                                        "点击开始并重按主入口后，方向键输入与画面自动演进"
                                        "在约 2.5s 采样窗口内均未使 canvas 变化 —— "
                                        "应用打开能看但无法通过输入交互"
                                    ),
                                    "evidence": f"canvas 签名连续三次采样一致: {k0} == {k1} == {k2}",
                                    "suggestion": (
                                        "先确认点击主入口后游戏循环真的启动："
                                        "(1) 是否存在独立的 startLoop() 且内部真的调用了 "
                                        "setInterval/requestAnimationFrame，且开始处理函数确实调用了它；"
                                        "(2) 方向键处理函数里是否有 "
                                        "`if (state === 'ready') { state = 'playing'; startLoop(); }`；"
                                        "(3) 不要只在 tick() 内部重设定时器——首次启动就没人调用 tick；"
                                        "(4) tick 内部是否有异常导致提前 return（可用 run_preview 看 console）。"
                                        "这是流程缺陷，必须改 JS 逻辑"
                                    ),
                                })

                        # 键盘驱动型应用：交互性以键盘响应为准。点击「开始」后进入 ready 态
                        # 画面静态（蛇已画好但不移动）属正常设计，不能用 no_interaction 误杀。
                        if _kbd_gate and result["checks"].get("keyboard_responsive") is True:
                            result["checks"]["interactive"] = True
                            result["defects"] = [
                                d for d in result["defects"]
                                if d.get("type") != "no_interaction"
                            ]
                            result["logs"].append(
                                "[smoke] 键盘驱动型应用：交互性由 keyboard_responsive 证明，"
                                "抑制 ready 态静态导致的 no_interaction 误报"
                            )
                else:
                    if load_fail:
                        result["logs"].append("[smoke] 页面未加载出内容，交互检查无法进行")
                    else:
                        result["logs"].append("[smoke] 页面无可点击交互入口，跳过交互/瞬死检查（纯展示页可接受）")

                # ---- 不变量 4: localStorage 禁用（模拟预览沙箱） ----
                # 页面本身都加载不出来时这项检查没有意义（空白页恒过 = 假绿）
                try:
                    if load_fail:
                        raise RuntimeError("页面未加载，跳过 storage 检查")
                    poison = (
                        "try{Object.defineProperty(window,'localStorage',{"
                        "get(){throw new DOMException('SecurityError: localStorage blocked','SecurityError')},"
                        "configurable:true});}catch(e){}"
                    )
                    page.add_init_script(poison)
                    before_set = set(load_errors)
                    doc = _open(sandbox_on)
                    page.wait_for_timeout(1200)
                    new_errors = [e for e in load_errors if e not in before_set]
                    new_errors = _collect_frame_errors(doc, new_errors)
                    storage_related = [e for e in new_errors if "localstorage" in e.lower() or "securityerror" in e.lower()]
                    result["checks"]["storage_safe"] = len(storage_related) == 0
                    if storage_related:
                        result["defects"].append({
                            "type": "storage_crash",
                            "severity": "major",
                            "dimension": "runtime",
                            "message": "localStorage 被禁用时页面产生 JS 错误（预览沙箱正是此环境，排行榜/存档功能会崩）",
                            "evidence": "; ".join(storage_related[:2]),
                            "suggestion": "所有 localStorage 访问包 try/catch，失败时降级为内存对象并保持页面可用",
                        })
                except Exception as e:
                    result["logs"].append(f"[smoke] storage 检查异常: {e}")
            finally:
                browser.close()
        result["available"] = True
    except Exception as e:
        logger.warning("[smoke] 通用冒烟异常（忽略）: %s", e)
        result["logs"].append(f"[smoke] 运行异常: {e}")
    finally:
        if wrapper_tmp:
            try:
                import os as _os
                _os.unlink(wrapper_tmp)
            except OSError:
                pass

    for name, ok in result["checks"].items():
        result["logs"].append(f"[smoke] {name}: {'✅' if ok else '❌'}")
    return result


# ==================== 视觉证据提取（DOM + 计算样式） ====================

_EXTRACT_JS = r"""
() => {
  const res = {
    bg: '', fg: '', fontSizes: [], textColors: [], smallTargets: [],
    hasHover: false, hasFocus: false, domOutline: [], textLength: 0,
    cssVarCount: 0, inlineStyleCount: 0
  };
  const bodyCS = getComputedStyle(document.body);
  res.bg = bodyCS.backgroundColor;
  res.fg = bodyCS.color;

  const sizes = new Set(), colors = new Set();
  document.querySelectorAll('body *').forEach(el => {
    const s = getComputedStyle(el);
    const txt = (el.textContent || '').trim();
    // 只统计叶子节点上的文字，避免父容器字号重复计数
    if (txt && el.children.length === 0) {
      sizes.add(s.fontSize);
      colors.add(s.color);
    }
    if (el.getAttribute && el.getAttribute('style')) res.inlineStyleCount++;
  });
  res.fontSizes = Array.from(sizes).slice(0, 12);
  res.textColors = Array.from(colors).slice(0, 12);

  document.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44)) {
      res.smallTargets.push({
        tag: el.tagName.toLowerCase(),
        w: Math.round(r.width), h: Math.round(r.height),
        text: (el.textContent || '').trim().slice(0, 20)
      });
    }
  });
  res.smallTargets = res.smallTargets.slice(0, 8);

  try {
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules; } catch (e) { continue; }
      for (const rule of rules) {
        const sel = rule.selectorText || '';
        if (sel.includes(':hover')) res.hasHover = true;
        if (sel.includes(':focus') || sel.includes(':focus-visible')) res.hasFocus = true;
        if (rule.style) {
          for (let i = 0; i < rule.style.length; i++) {
            if (rule.style[i].startsWith('--')) { res.cssVarCount++; break; }
          }
        }
      }
    }
  } catch (e) {}

  const outline = [];
  document.querySelectorAll('body > *, body > * > *').forEach(el => {
    let cls = '';
    if (typeof el.className === 'string' && el.className.trim()) {
      cls = '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
    }
    outline.push(el.tagName.toLowerCase() + cls);
  });
  res.domOutline = outline.slice(0, 30);
  res.textLength = (document.body.innerText || '').length;
  return res;
}
"""


def _parse_rgb(value: str):
    """把 'rgb(r, g, b)' / 'rgba(r, g, b, a)' 解析为 (r, g, b)，失败返回 None。"""
    if not value or not isinstance(value, str):
        return None
    nums = ''.join(ch if (ch.isdigit() or ch in ',.() ') else ' ' for ch in value)
    parts = [p for p in nums.replace('(', ' ').replace(')', ' ').split(',') if p.strip()]
    try:
        if len(parts) >= 3:
            return tuple(max(0, min(255, int(float(p)))) for p in parts[:3])
    except (ValueError, TypeError):
        return None
    return None


def _contrast_ratio(fg: str, bg: str):
    """按 WCAG 计算前景/背景对比度，返回比值（失败返回 None）。

    这些值是**确定性**的（getComputedStyle 读出来的），比让模型看截图猜更准，
    而且能直接作为可判定缺陷进入修复循环。
    """
    f, b = _parse_rgb(fg), _parse_rgb(bg)
    if not f or not b:
        return None

    def lum(c):
        def ch(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])

    l1, l2 = lum(f), lum(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def _format_dom_css_summary(raw: dict) -> str:
    """把浏览器提取结果格式化为给 evaluator 看的紧凑文本（≤2K token）。"""
    if not raw:
        return ""
    lines = ["## 页面视觉结构（DOM + 计算样式，浏览器确定性取值，非猜测）"]

    bg, fg = raw.get("bg", ""), raw.get("fg", "")
    ratio = _contrast_ratio(fg, bg)
    lines.append(f"- 页面底色: {bg}；正文色: {fg}")
    if ratio is not None:
        verdict = "合格" if ratio >= 4.5 else ("偏低（大字可接受）" if ratio >= 3 else "不合格")
        lines.append(f"- 正文对比度: {ratio}:1 —— {verdict}（WCAG AA 正文需 ≥4.5:1）")

    sizes = raw.get("fontSizes") or []
    if sizes:
        px = sorted({int(float(s.replace('px', ''))) for s in sizes if 'px' in s})
        lines.append(f"- 字号档位: {px} px（共 {len(sizes)} 种；最小 {min(px) if px else '-'}px）")
        if px and min(px) < 12:
            lines.append(f"  ⚠️ 存在小于 12px 的字号（{min(px)}px），移动端可读性差")

    colors = raw.get("textColors") or []
    if colors:
        lines.append(f"- 文字颜色: {len(colors)} 种 —— {', '.join(colors[:8])}")

    outline = raw.get("domOutline") or []
    if outline:
        lines.append(f"- 页面结构: {' › '.join(outline[:18])}")

    lines.append(f"- 正文文本量: {raw.get('textLength', 0)} 字符")
    lines.append(
        f"- 交互反馈: hover 样式 {'有' if raw.get('hasHover') else '❌ 无'}"
        f"；focus 样式 {'有' if raw.get('hasFocus') else '❌ 无'}"
        f"；CSS 变量 {raw.get('cssVarCount', 0)} 处"
        f"；内联 style {raw.get('inlineStyleCount', 0)} 处"
    )
    small = raw.get("smallTargets") or []
    if small:
        shown = "; ".join(f"{t['tag']}({t['w']}×{t['h']})" for t in small[:5])
        lines.append(f"- ⚠️ 点击区偏小（<44px）: {len(small)} 个 —— {shown}")

    return "\n".join(lines)


def extract_dom_css_summary(html_path: Path, timeout_ms: int = 12_000,
                            preview_url: str = None,
                            return_raw: bool = False) -> "str | dict | None":
    """提取页面 DOM 结构 + 关键计算样式，作为 ui_quality 评估的视觉证据。

    为什么不用截图：颜色对比度、字号、间距、点击区这些从 getComputedStyle 读是
    **确定值**，从截图看是模型猜测。而且这些数值能直接变成可判定缺陷
    （对比度 <4.5:1、字号 <12px、点击区 <44px）进入修复循环，截图做不到。

    Args:
        return_raw: True 时返回原始提取字典（供 build_ui_lint_defects 判定硬违规），
                    False 时返回格式化文本（供 evaluator prompt 注入）。

    Returns:
        格式化文本 / 原始字典；浏览器不可用等失败时返回 None（不抛异常）。
    """
    def _session(html_path, timeout_ms, preview_url):
        try:
            from harness.tools.sandboxed_browser import sandboxed_browser
        except ImportError:
            return None
        url = Path(html_path).resolve().as_uri()
        sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)
        try:
            from playwright.sync_api import Error as PWError
            with sandboxed_browser(
                timeout_ms=_watchdog_ms(timeout_ms, 2),
                allow_hosts=_preview_allow_hosts(preview_url),
            ) as browser:
                try:
                    context = browser.new_context(viewport={"width": 1280, "height": 800})
                    page = context.new_page()
                    page.set_extra_http_headers({"X-T2C-Verify": "1"})
                    page.set_default_timeout(timeout_ms)
                    target = page
                    if sandbox:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)
                        target = _resolve_preview_frame(page) or page
                        if _frame_load_failure(target):
                            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            page.wait_for_timeout(1200)
                            target = page
                    else:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1200)
                    raw = target.evaluate(_EXTRACT_JS)
                    if not raw:
                        return None
                    return raw if return_raw else _format_dom_css_summary(raw)
                finally:
                    browser.close()
        except PWError as e:
            logger.warning(f"[DomCss] 浏览器不可用，跳过视觉提取: {e}")
            return None
        except Exception as e:
            logger.warning(f"[DomCss] 视觉提取失败（跳过）: {e}")
            return None
        finally:
            if wrapper_tmp:
                try:
                    import os as _os
                    _os.unlink(wrapper_tmp)
                except OSError:
                    pass

    budget = _watchdog_ms(timeout_ms, 2) / 1000.0 + _SESSION_BUDGET_SLACK_S
    try:
        return run_browser_session_isolated(
            _session, budget, html_path, timeout_ms=timeout_ms, preview_url=preview_url,
        )
    except BrowserSessionTimeout as e:
        logger.warning("[DomCss] 视觉提取会话超时（跳过）: %s", e)
        return None


# ==================== UI 硬违规 → 确定性缺陷 ====================

# 阈值刻意取「宽松下限」而非 WCAG AA 的推荐值，避免把正常设计判成缺陷：
#   · 对比度 3.0   —— AA 对大字号的下限；低于它连大字都不合格，属硬伤
#   · 点击区 24px  —— WCAG 2.2 AA 的最小目标尺寸（44px 是 AAA/触屏建议，不作硬门槛）
#   · 字号   10px  —— 低于 10px 在任何设备上都难以阅读
# 目的是让「明显不合格」能进修复循环，而不是把评审变成无休止的挑刺。
_UI_CONTRAST_HARD_MIN = 3.0
_UI_TARGET_HARD_MIN = 24
_UI_FONT_HARD_MIN = 10
_UI_LINT_MAX_DEFECTS = 2


def build_ui_lint_defects(raw) -> list:
    """把浏览器提取的确定性视觉数据转成可修复的缺陷条目。

    背景：预置成品模板（`.design/preset-*.css`）只是「建议」，即便模型完全没按模板
    走，此前也没有任何环节会发现 —— evaluator 的 ui_quality 是盲评，页面难看也不会
    阻塞交付，于是模板收益被削掉大半。这里把视觉硬伤变成**确定性缺陷**送入
    defect_repair，让「不好看」第一次具备了可判定、可修复的闭环。

    只挑三类硬伤，且最多 2 条（避免把修复预算耗在审美分歧上）。

    Args:
        raw: extract_dom_css_summary(..., return_raw=True) 的返回值。

    Returns:
        缺陷 dict 列表（可能为空）。任何异常都吞掉并返回空列表 —— 视觉 lint 属于
        增益功能，绝不允许它把验收流程搞崩。
    """
    if not isinstance(raw, dict):
        return []
    defects: list = []
    try:
        # ---- 1. 正文对比度低于硬下限 ----
        ratio = _contrast_ratio(raw.get("fg", ""), raw.get("bg", ""))
        if ratio is not None and ratio < _UI_CONTRAST_HARD_MIN:
            defects.append({
                "type": "ui_contrast",
                "severity": "major",
                "dimension": "ui_quality",
                "message": (
                    f"正文与背景对比度仅 {ratio}:1，低于可读下限 "
                    f"{_UI_CONTRAST_HARD_MIN}:1（WCAG AA）"
                ),
                "evidence": (
                    f"浏览器实测 getComputedStyle：正文色 {raw.get('fg')}，"
                    f"页面底色 {raw.get('bg')} → 对比度 {ratio}:1"
                ),
                "suggestion": (
                    "在 CSS 里调整正文色或页面底色，使对比度 ≥ 4.5:1；"
                    "若已套用 .design/ 下的成品模板，直接改用模板变量"
                    "（--text 配 --bg）即可满足。"
                ),
            })

        # ---- 2. 点击区小于最小可点尺寸 ----
        small = [
            t for t in (raw.get("smallTargets") or [])
            if int(t.get("w", 99)) < _UI_TARGET_HARD_MIN
            or int(t.get("h", 99)) < _UI_TARGET_HARD_MIN
        ]
        if small:
            shown = "; ".join(f"{t.get('tag')}({t.get('w')}×{t.get('h')})" for t in small[:4])
            defects.append({
                "type": "ui_target_size",
                "severity": "major",
                "dimension": "ui_quality",
                "message": (
                    f"{len(small)} 个可点击元素的尺寸小于 "
                    f"{_UI_TARGET_HARD_MIN}×{_UI_TARGET_HARD_MIN}px"
                ),
                "evidence": f"浏览器实测 boundingClientRect：{shown}",
                "suggestion": (
                    f"给按钮/链接设置 min-width / min-height ≥ {_UI_TARGET_HARD_MIN}px"
                    "（模板里的 .btn 已满足），图标按钮加 padding。"
                ),
            })

        # ---- 3. 字号过小 ----
        px = []
        for s in (raw.get("fontSizes") or []):
            try:
                px.append(int(float(str(s).replace("px", ""))))
            except (TypeError, ValueError):
                continue
        if px and min(px) < _UI_FONT_HARD_MIN:
            defects.append({
                "type": "ui_font_size",
                "severity": "major",
                "dimension": "ui_quality",
                "message": f"存在 {min(px)}px 的字号，低于可读下限 {_UI_FONT_HARD_MIN}px",
                "evidence": f"浏览器实测字号档位: {sorted(set(px))} px",
                "suggestion": (
                    "把过小字号提到模板的字号档位上（--fs-xs = 12px 为最小档），"
                    "正文用 --fs-md(16px)。"
                ),
            })
    except Exception as e:
        logger.warning(f"[UiLint] 视觉硬伤判定失败（跳过）: {e}")
        return []

    return defects[:_UI_LINT_MAX_DEFECTS]


def collect_ui_lint_defects(html_path: Path, timeout_ms: int = 12_000,
                            preview_url: str = None) -> list:
    """采集页面视觉硬伤（自成一次浏览器会话）。

    会话成本约 5~12s，换来的是「UI 不合格」第一次能进修复循环。
    任何失败都返回空列表 —— 视觉 lint 是增益功能，不允许中断验收。
    """
    try:
        raw = extract_dom_css_summary(
            html_path, timeout_ms=timeout_ms, preview_url=preview_url, return_raw=True,
        )
    except Exception as e:
        logger.warning(f"[UiLint] 采集失败（跳过）: {e}")
        return []
    defects = build_ui_lint_defects(raw)
    if defects:
        logger.info(
            f"[UiLint] 发现 {len(defects)} 个视觉硬伤: {[d['type'] for d in defects]}"
        )
    return defects
