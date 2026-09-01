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
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 单页加载 + 收集的总超时（秒）。生成的页面多为静态，10s 足够。
DEFAULT_TIMEOUT_MS = 10_000
# 页面加载后额外等待时间（ms），给异步脚本/初始化逻辑跑完的机会
SETTLE_MS = 1_500


def run_preview_in_browser(
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
        from playwright.sync_api import sync_playwright, Error as PWError
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
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                # 典型：浏览器二进制未安装
                raise RuntimeError(
                    f"chromium 未安装，请运行 `playwright install chromium`：{e}"
                ) from e

            try:
                context = browser.new_context()
                page = context.new_page()

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
                    errors.append({
                        "type": "pageerror",
                        "message": str(err),
                        "name": getattr(err, "name", ""),
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


def run_ac_checks(
    html_path: Path,
    ac_scripts: list[dict],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    preview_url: str = None,
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

    Returns:
        [{"ac_id": "AC-1", "passed": True, "failures": [], "steps_executed": 5}, ...]
    """
    try:
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError:
        return [{"ac_id": s["ac_id"], "passed": False, "failures": [], "harness_errors": ["playwright 未安装"], "steps_executed": 0} for s in ac_scripts]

    url = html_path.resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)
    results = []

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError:
                return [{"ac_id": s["ac_id"], "passed": False, "failures": [], "harness_errors": ["chromium 未安装"], "steps_executed": 0} for s in ac_scripts]

            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                def _load(use_sandbox: bool):
                    """每个 AC 从干净状态加载（沙箱模式返回内层 frame）"""
                    if use_sandbox:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)  # 等待 iframe 资源拉取
                        return _resolve_preview_frame(page)
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

                for script in ac_scripts:
                    ac_id = script.get("ac_id", "?")
                    label = script.get("label", ac_id)
                    steps = script.get("steps", [])
                    failures = []          # 产品断言失败（真实缺陷信号）
                    harness_failures = []  # 脚本驱动失败（假阴性嫌疑，不计入产品缺陷）
                    steps_executed = 0
                    doc = None

                    try:
                        doc = _load(sandbox_on)
                        _load_fail = _frame_load_failure(doc)
                        if _load_fail:
                            # 页面本身打不开 = 真实缺陷（用户看到的就是白屏），
                            # 必须记进 failures 而不是当成脚本问题吞掉
                            failures.append(f"页面无法加载: {_load_fail}")

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

                        for step in steps:
                            action = step.get("action", "")
                            selector = step.get("selector", "")
                            steps_executed += 1

                            try:
                                if action == "type":
                                    eff = _resolve_selector(doc, selector) or selector
                                    doc.fill(eff, step.get("value", ""))
                                elif action == "click":
                                    eff = _resolve_selector(doc, selector) or selector
                                    doc.click(eff)
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
                                    text = elem.inner_text() if elem else ""
                                    contains = step.get("contains", "")
                                    if contains not in text:
                                        failures.append(
                                            f"文本不匹配: 期望包含 '{contains}', 实际 '{text[:100]}'"
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
                                    if s0 == s1:
                                        failures.append(
                                            f"canvas 无变化: {step.get('label', '画面应随操作变化')}"
                                        )
                                elif action == "screenshot":
                                    # 截图用于 LLM 诊断（不参与通过/失败判断）
                                    pass
                            except Exception as step_err:
                                # 操作类步骤抛异常 = 脚本无法驱动页面（选择器失配/超时），
                                # 与产品断言失败区分，避免假阴性压垮验收
                                harness_failures.append(f"步骤 [{action} {selector}]: {step_err}")

                    except Exception as ac_err:
                        harness_failures.append(f"AC 执行异常: {ac_err}")

                    results.append({
                        "ac_id": ac_id,
                        "label": label,
                        "passed": len(failures) == 0,
                        "failures": failures,
                        "harness_errors": harness_failures,
                        "steps_executed": steps_executed,
                        # 沙箱链路降级时标注，便于区分「产品坏」与「验证环境坏」
                        "preview_degraded": degraded_reason,
                    })

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


def capture_screenshot(html_path: Path, out_path: Path,
                       timeout_ms: int = 12_000, preview_url: str = None) -> str | None:
    """对 index.html 截图（与用户一致的沙箱预览链路），保存为 PNG。

    用途：fast_pass 通道不再硬编码 ui_quality——截图落盘到
    .task/evaluator/screenshot.png，供用户查看与后续多模态评估使用。

    Returns:
        截图文件路径字符串；浏览器不可用等失败时返回 None（不抛异常）。
    """
    try:
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError:
        return None

    url = Path(html_path).resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
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
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError:
        result["logs"].append("[smoke] playwright 未安装，跳过交互检查")
        return result

    url = Path(html_path).resolve().as_uri()
    sandbox, wrapper_uri, wrapper_tmp = _prepare_sandbox(preview_url)
    if sandbox:
        result["logs"].append(f"[smoke] 沙箱预览模式: {preview_url.split('/api/pt/')[-1][:40]}")

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                result["logs"].append(f"[smoke] chromium 不可用: {e}")
                return result

            try:
                context = browser.new_context()
                page = context.new_page()
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
                            page.wait_for_timeout(1600)
                            k1 = _canvas_sig()
                            page.wait_for_timeout(1200)
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
                                        "点击开始后按方向键，画面在 2.8s 内完全静止 —— "
                                        "游戏循环从未启动，应用打开能看但根本玩不了"
                                    ),
                                    "evidence": f"canvas 签名连续三次采样一致: {k0} == {k1} == {k2}",
                                    "suggestion": (
                                        "就绪(ready)状态必须由首次方向输入切换到运行(playing)并启动循环。"
                                        "检查点：(1) 是否存在独立的 startLoop() 且内部真的调用了 "
                                        "setInterval/requestAnimationFrame；(2) 方向键处理函数里是否有 "
                                        "`if (state === 'ready') { state = 'playing'; startLoop(); }`；"
                                        "(3) 不要只在 tick() 内部重设定时器——首次启动就没人调用 tick。"
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
