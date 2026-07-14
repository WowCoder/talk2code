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
                                return Array.from(ctx.getImageData(0, 0, Math.min(c.width, 100), Math.min(c.height, 100)).data);
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
                                return Array.from(ctx.getImageData(0, 0, Math.min(c.width, 100), Math.min(c.height, 100)).data);
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


def _loc(loc) -> str:
    try:
        return f"{loc.url}:{loc.line_number}:{loc.column_number}"
    except Exception:
        return ""
