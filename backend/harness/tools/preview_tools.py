# -*- coding: utf-8 -*-
"""
真实运行验证工具：run_preview

用 Playwright headless 加载生成的 index.html（file:// 协议），收集：
- console.error 调用
- 未捕获的 pageerror（JS 异常）
- 子资源（脚本/样式/图片）加载失败
- 404 fetch/XHR

返回结构化报告，供 ToolCallLoop 判断「代码是否真的能跑」并据此修复。
"""

from __future__ import annotations

import logging

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)

logger = logging.getLogger(__name__)


# ==================== ToolHandler 子类 ====================

class RunPreviewHandler(ToolHandler):
    """在无头浏览器中真实运行 HTML 页面"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        return self.run_preview(args.get("filename", "index.html"))

    def run_preview(self, filename: str = "index.html") -> ToolResult:
        ws = self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")

        try:
            from harness.tools.preview_runner import run_preview_in_browser
        except ImportError as e:
            logger.warning(
                "run_preview 不可用（playwright 未安装）：%s。"
                "运行 `pip install playwright && playwright install chromium` 以启用真实浏览器验证。",
                e,
            )
            return ToolResult(
                content=f"预览验证不可用（playwright 未安装）：{e}。"
                        "运行 `pip install playwright && playwright install chromium` 以启用。",
                metadata={"available": False, "errors": []},
            )

        try:
            if not ws.exists(filename):
                return ToolResult(
                    error=f"文件不存在：{filename}",
                    metadata={"available": True, "errors": [{"type": "missing_file"}]},
                )

            elem_checks = self._detect_elements_to_check(filename)

            report = run_preview_in_browser(
                ws.path / filename, elem_checks=elem_checks
            )
            if report["errors"] or report.get("defects"):
                return ToolResult(
                    error=self._format_errors(report),
                    metadata=report,
                )
            return ToolResult(
                content=self._format_success(report),
                metadata=report,
            )
        except Exception as e:
            logger.warning("run_preview 异常（降级跳过）: %s", e)
            return ToolResult(
                content=f"预览验证跳过（浏览器不可用）：{e}",
                metadata={"available": False, "errors": [], "skip_reason": str(e)},
            )

    def _detect_elements_to_check(self, filename: str) -> list[dict]:
        """扫描 HTML 源码，自动检测需要验证的关键交互元素"""
        checks = []
        try:
            html = self.workspace.read(filename)
        except Exception:
            return checks

        import re

        if re.search(r'<canvas\b', html, re.IGNORECASE):
            canvas_id = re.search(r'<canvas[^>]*\bid\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
            selector = f"#{canvas_id.group(1)}" if canvas_id else "canvas"
            checks.append({
                "selector": selector,
                "label": "游戏画布 (Canvas)",
                "required": True,
            })

        ui_patterns = [
            (r'id\s*=\s*["\']score["\']', "#score", "分数显示"),
            (r'id\s*=\s*["\']gameOver["\']', "#gameOver", "游戏结束提示"),
            (r'id\s*=\s*["\']start["\']', "#start", "开始按钮"),
            (r'id\s*=\s*["\']restart["\']', "#restart", "重新开始按钮"),
            (r'class\s*=\s*["\'][^"\']*\bscore\b', ".score", "分数显示 (.score)"),
            (r'id\s*=\s*["\']highScore["\']', "#highScore", "最高分显示"),
            (r'id\s*=\s*["\']board["\']', "#board", "游戏面板"),
        ]
        for pattern, selector, label in ui_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                if not any(c["selector"] == selector for c in checks):
                    checks.append({
                        "selector": selector,
                        "label": label,
                        "required": False,
                    })

        return checks

    @staticmethod
    def _format_errors(report: dict) -> str:
        lines = []
        if report.get("errors"):
            lines.append(f"页面运行发现 {len(report['errors'])} 个错误：")
            for i, e in enumerate(report["errors"], 1):
                lines.append(f"  {i}. [{e['type']}] {e['message']}")
        if report.get("defects"):
            lines.append(f"页面功能缺陷 {len(report['defects'])} 个：")
            for i, d in enumerate(report["defects"], 1):
                lines.append(f"  {i}. [{d['type']}] {d['message']}")
        return "\n".join(lines) if lines else "页面运行正常。"

    @staticmethod
    def _format_success(report: dict) -> str:
        parts = [
            f"页面运行正常，无错误。console 消息 {len(report.get('logs', []))} 条，"
            f"网络请求 {len(report.get('network', []))} 个。"
        ]
        elem_ok = sum(
            1 for log in report.get("logs", [])
            if log.startswith("[element_check] ✅")
        )
        if elem_ok > 0:
            parts.append(f"关键元素验证通过: {elem_ok} 个。")
        init = report.get("initialization", {})
        if init:
            if init.get("canvas_activity") is False:
                parts.append("⚠️ Canvas 存在但无像素变化（游戏循环可能未启动）。")
            elif init.get("canvas_activity") is True:
                parts.append("✅ Canvas 有像素变化（动画/游戏正在运行）。")
            if init.get("animation_started") is False:
                parts.append("⚠️ requestAnimationFrame 未被调用（init/入口函数可能未执行）。")
            elif init.get("animation_started") is True:
                parts.append("✅ requestAnimationFrame 已调用（渲染循环已启动）。")
        return " ".join(parts)


# ==================== 兼容旧 PreviewToolHandler 类 ====================

class PreviewToolHandler(RunPreviewHandler):
    """向后兼容：PreviewToolHandler 现在是 RunPreviewHandler 的别名"""

    pass


# ==================== 注册函数 ====================

def register_preview_tools(registry):
    preview_handler = RunPreviewHandler()

    registry.register(ToolDefinition(
        name="run_preview",
        description=(
            "在无头浏览器中真实加载并运行生成的 HTML 页面，捕获 JS 运行时错误、"
            "console.error、资源加载失败。用于在交付前验证页面能否真正运行。"
            "返回结构化验证报告。建议每次生成/修改 index.html 后调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要运行的 HTML 文件名（默认 index.html）",
                },
            },
            "required": [],
        },
        handler=lambda **kwargs: preview_handler.execute(kwargs),
        permission="execute",
        tool_handler=preview_handler,
    ))
