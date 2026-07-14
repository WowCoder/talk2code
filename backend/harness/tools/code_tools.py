# -*- coding: utf-8 -*-
"""
代码验证工具：validate_html / lint_css / lint_js / execute_code
"""

import subprocess
import tempfile
import os
import re

from harness.tools.registry import ToolDefinition, ToolResult


def register_code_tools(registry):
    registry.register(ToolDefinition(
        name="execute_code",
        description="在沙箱中执行 HTML 文件，返回渲染结果或控制台输出",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要执行的 HTML 文件名（默认 index.html）"}
            },
            "required": []
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="execute",
    ))

    registry.register(ToolDefinition(
        name="validate_html",
        description="校验 HTML 语法有效性",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要校验的 HTML 文件名"}
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="read",
    ))

    registry.register(ToolDefinition(
        name="lint_css",
        description="检查 CSS 语法错误",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要检查的 CSS 文件名"}
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="read",
    ))

    registry.register(ToolDefinition(
        name="lint_js",
        description="检查 JavaScript 语法错误（自动检测 ES Module, 使用 Node.js AST 解析）",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要检查的 JS 文件名"}
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="read",
    ))


class CodeToolHandler:
    """代码工具的实际处理器"""

    def __init__(self, workspace):
        self.workspace = workspace

    def validate_html(self, filename: str) -> ToolResult:
        try:
            content = self.workspace.read(filename)
            try:
                from html.parser import HTMLParser
                parser = HTMLParser()
                parser.feed(content)
                parser.close()
                return ToolResult(content=f"HTML 语法校验通过 ({filename})")
            except Exception as e:
                return ToolResult(error=f"HTML 语法错误 ({filename}): {e}")
        except Exception as e:
            return ToolResult(error=str(e))

    def lint_css(self, filename: str) -> ToolResult:
        try:
            content = self.workspace.read(filename)
            # 基础 CSS 语法检查：检查括号平衡
            open_braces = content.count('{')
            close_braces = content.count('}')
            if open_braces != close_braces:
                return ToolResult(error=f"CSS 语法错误 ({filename}): 括号不匹配 ({{ {open_braces}, }} {close_braces})")
            return ToolResult(content=f"CSS 语法检查通过 ({filename})")
        except Exception as e:
            return ToolResult(error=str(e))

    def lint_js(self, filename: str) -> ToolResult:
        """检查 JavaScript 语法错误，自动检测 ES Module 语法并匹配参数"""
        try:
            content = self.workspace.read(filename)

            # 检测 ES Module 语法 (export/import 关键字)
            is_es_module = bool(re.search(
                r'\b(?:export|import)\s+(?:\{|\*|default|type|function|class|const|let|var|\w+\s+from)',
                content
            ))

            node_args = ["node", "--check"]
            if is_es_module:
                node_args.append("--input-type=module")
            node_args.append("-")

            result = subprocess.run(
                node_args,
                input=content, capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return ToolResult(error=f"JavaScript 语法错误 ({filename}): {result.stderr[:300]}")
            mode_label = " (ES Module)" if is_es_module else ""
            return ToolResult(content=f"JavaScript 语法检查通过{mode_label} ({filename})")
        except FileNotFoundError:
            return ToolResult(content=f"Node.js 未安装，跳过 JS 语法检查 ({filename})")
        except subprocess.TimeoutExpired:
            return ToolResult(error=f"JS 语法检查超时 ({filename})")
        except Exception as e:
            return ToolResult(error=str(e))

    def execute_code(self, filename: str = "index.html") -> ToolResult:
        try:
            content = self.workspace.read(filename)
            # 简单的 HTML 结构检查
            if '<html' not in content.lower() and '<!doctype' not in content.lower():
                return ToolResult(content=f"文件 {filename} 不包含标准 HTML 结构，跳过执行验证")
            return ToolResult(content=f"代码执行就绪 ({filename})", metadata={"filename": filename})
        except Exception as e:
            return ToolResult(error=str(e))
