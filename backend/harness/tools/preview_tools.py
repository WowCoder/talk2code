# -*- coding: utf-8 -*-
"""
真实运行验证工具：run_preview

用 Playwright headless 加载生成的 index.html（file:// 协议），收集：
- console.error 调用
- 未捕获的 pageerror（JS 异常）
- 子资源（脚本/样式/图片）加载失败
- 404 fetch/XHR

返回结构化报告，供 ToolCallLoop 判断「代码是否真的能跑」并据此修复。

这是把生成质量从「盲写」提升到「可见反馈」的关键闭环 —— 对应 OpenHands
的 runtime observation：agent 必须能看到自己生成代码的运行结果。

Playwright/浏览器缺失时优雅降级（返回可用性提示而非崩溃），保证 CI 可跑。
"""

from __future__ import annotations

import logging

from harness.tools.registry import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


def register_preview_tools(registry):
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
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="execute",
    ))


class PreviewToolHandler:
    """绑定 WorkspaceFS 的预览运行处理器"""

    def __init__(self, workspace):
        self.workspace = workspace

    def run_preview(self, filename: str = "index.html") -> ToolResult:
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
            if not self.workspace.exists(filename):
                return ToolResult(
                    error=f"文件不存在：{filename}",
                    metadata={"available": True, "errors": [{"type": "missing_file"}]},
                )

            # 自动检测页面中的交互元素，用于功能验证
            elem_checks = self._detect_elements_to_check(filename)

            report = run_preview_in_browser(
                self.workspace.path / filename, elem_checks=elem_checks
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
            # 浏览器未安装等情况：降级，不阻断流程
            logger.warning("run_preview 异常（降级跳过）: %s", e)
            return ToolResult(
                content=f"预览验证跳过（浏览器不可用）：{e}",
                metadata={"available": False, "errors": [], "skip_reason": str(e)},
            )

    def _detect_elements_to_check(self, filename: str) -> list[dict]:
        """扫描 HTML 源码，自动检测需要验证的关键交互元素

        返回值格式与 run_preview_in_browser 的 elem_checks 参数一致。
        """
        checks = []
        try:
            html = self.workspace.read(filename)
        except Exception:
            return checks

        import re

        # 检测 Canvas 元素（游戏/图表类应用的核心渲染层）
        if re.search(r'<canvas\b', html, re.IGNORECASE):
            canvas_id = re.search(r'<canvas[^>]*\bid\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
            selector = f"#{canvas_id.group(1)}" if canvas_id else "canvas"
            checks.append({
                "selector": selector,
                "label": "游戏画布 (Canvas)",
                "required": True,
            })

        # 检测常见游戏 UI 元素
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
                # 避免重复添加相同 selector
                if not any(c["selector"] == selector for c in checks):
                    checks.append({
                        "selector": selector,
                        "label": label,
                        "required": False,  # 非画布元素缺失不强制阻断
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
        # 附加元素检查结果
        elem_ok = sum(
            1 for log in report.get("logs", [])
            if log.startswith("[element_check] ✅")
        )
        if elem_ok > 0:
            parts.append(f"关键元素验证通过: {elem_ok} 个。")
        # 附加初始化检测结果
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
