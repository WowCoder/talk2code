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

from harness.tools.registry import ToolDefinition, ToolResult


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
            return ToolResult(
                content=f"预览验证不可用（playwright 未安装）：{e}。可跳过此步。",
                metadata={"available": False, "errors": []},
            )

        try:
            if not self.workspace.exists(filename):
                return ToolResult(
                    error=f"文件不存在：{filename}",
                    metadata={"available": True, "errors": [{"type": "missing_file"}]},
                )
            report = run_preview_in_browser(self.workspace.path / filename)
            if report["errors"]:
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
            return ToolResult(
                content=f"预览验证跳过（浏览器不可用）：{e}",
                metadata={"available": False, "errors": [], "skip_reason": str(e)},
            )

    @staticmethod
    def _format_errors(report: dict) -> str:
        lines = [f"页面运行发现 {len(report['errors'])} 个错误："]
        for i, e in enumerate(report["errors"], 1):
            lines.append(f"  {i}. [{e['type']}] {e['message']}")
        return "\n".join(lines)

    @staticmethod
    def _format_success(report: dict) -> str:
        return (
            f"页面运行正常，无错误。console 消息 {len(report.get('logs', []))} 条，"
            f"网络请求 {len(report.get('network', []))} 个。"
        )
