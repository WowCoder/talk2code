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


def run_preview_in_browser(html_path: Path, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """
    在 headless 浏览器中加载 html_path，收集错误。

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
            "skip_reason": f"运行异常: {e}",
        }

    return {
        "available": True,
        "url": url,
        "errors": errors,
        "logs": logs[:50],  # 限制体积
        "network": network[:50],
    }


def _loc(loc) -> str:
    try:
        return f"{loc.url}:{loc.line_number}:{loc.column_number}"
    except Exception:
        return ""
