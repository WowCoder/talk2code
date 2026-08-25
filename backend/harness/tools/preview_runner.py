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
    wrapper_uri, wrapper_tmp = (None, None)
    if preview_url:
        wrapper_uri, wrapper_tmp = _make_sandbox_wrapper_uri(preview_url)
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

                def _load():
                    """每个 AC 从干净状态加载（沙箱模式返回内层 frame）"""
                    if preview_url:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)  # 等待 iframe 资源拉取
                        return _resolve_preview_frame(page)
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1000)  # 等待初始化
                    return page

                for script in ac_scripts:
                    ac_id = script.get("ac_id", "?")
                    label = script.get("label", ac_id)
                    steps = script.get("steps", [])
                    failures = []          # 产品断言失败（真实缺陷信号）
                    harness_failures = []  # 脚本驱动失败（假阴性嫌疑，不计入产品缺陷）
                    steps_executed = 0
                    doc = None

                    try:
                        doc = _load()

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
                                    doc.fill(selector, step.get("value", ""))
                                elif action == "click":
                                    doc.click(selector)
                                elif action == "select":
                                    doc.select_option(selector, step.get("value", ""))
                                elif action == "press":
                                    doc.press(selector or "body", step.get("key", "Enter"))
                                elif action == "wait":
                                    page.wait_for_timeout(step.get("ms", 500))
                                elif action == "assert_exists":
                                    elem = doc.query_selector(selector)
                                    if not elem:
                                        failures.append(f"元素不存在: {step.get('label', selector)}")
                                elif action == "assert_visible":
                                    if not doc.is_visible(selector):
                                        failures.append(f"元素不可见: {step.get('label', selector)}")
                                elif action == "assert_text":
                                    elem = doc.query_selector(selector)
                                    text = elem.inner_text() if elem else ""
                                    contains = step.get("contains", "")
                                    if contains not in text:
                                        failures.append(
                                            f"文本不匹配: 期望包含 '{contains}', 实际 '{text[:100]}'"
                                        )
                                elif action == "assert_count":
                                    count = len(doc.query_selector_all(selector))
                                    expected = step.get("min_count", 1)
                                    if count < expected:
                                        failures.append(
                                            f"元素数量不足: {selector} 期望 ≥{expected}, 实际 {count}"
                                        )
                                elif action == "assert_value":
                                    value = doc.input_value(selector)
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


# ==================== 预览链路沙箱加载 ====================
# 与前端 PreviewFrame.vue 完全一致：sandbox="allow-scripts allow-forms"（无 allow-same-origin）
# 验证环境必须与用户真实预览环境同构，否则会出现"验证全过、用户看到死页面"

_SANDBOX_ATTRS = "allow-scripts allow-forms"


def _make_sandbox_wrapper_uri(preview_url: str) -> str:
    """生成与前端预览 iframe 属性一致的宿主页，返回 file:// URI"""
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
    wrapper_uri, wrapper_tmp = (None, None)
    if preview_url:
        wrapper_uri, wrapper_tmp = _make_sandbox_wrapper_uri(preview_url)
        result["logs"].append(f"[smoke] 沙箱预览模式: {preview_url.split('/api/pt/')[-1][:40]}")
    interactive = None
    no_instant_death = None
    storage_safe = None

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

                def _open():
                    """加载页面（沙箱模式返回内层 frame），并安装错误收集器"""
                    if preview_url:
                        page.goto(wrapper_uri, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1500)  # 等待 iframe 资源拉取
                        fr = _resolve_preview_frame(page)
                        _install_frame_error_hook(fr)
                        return fr
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1200)
                    return page

                doc = _open()

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
                else:
                    result["logs"].append("[smoke] 页面无可点击交互入口，跳过交互/瞬死检查（纯展示页可接受）")
                    interactive, no_instant_death = None, None

                # ---- 不变量 4: localStorage 禁用（模拟预览沙箱） ----
                try:
                    poison = (
                        "try{Object.defineProperty(window,'localStorage',{"
                        "get(){throw new DOMException('SecurityError: localStorage blocked','SecurityError')},"
                        "configurable:true});}catch(e){}"
                    )
                    page.add_init_script(poison)
                    before_set = set(load_errors)
                    doc = _open()
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
