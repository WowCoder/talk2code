# -*- coding: utf-8 -*-
"""
代码验证工具：validate_html / lint_css / lint_js / execute_code

每个工具对应一个 ToolHandler 子类。
"""

import subprocess
import tempfile
import os
import re

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)


# ==================== ToolHandler 子类 ====================

class ValidateHtmlHandler(ToolHandler):
    """校验 HTML 语法有效性"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        try:
            content = ws.read(filename)
            from html.parser import HTMLParser
            parser = HTMLParser()
            parser.feed(content)
            parser.close()
            return ToolResult(content=f"HTML 语法校验通过 ({filename})")
        except Exception as e:
            return ToolResult(error=f"HTML 语法错误 ({filename}): {e}")

    def validate_html(self, filename: str) -> ToolResult:
        return self.execute({"filename": filename})


class LintCssHandler(ToolHandler):
    """检查 CSS 语法错误"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        try:
            content = ws.read(filename)
            open_braces = content.count('{')
            close_braces = content.count('}')
            if open_braces != close_braces:
                return ToolResult(error=f"CSS 语法错误 ({filename}): 括号不匹配 ({{ {open_braces}, }} {close_braces})")
            return ToolResult(content=f"CSS 语法检查通过 ({filename})")
        except Exception as e:
            return ToolResult(error=str(e))

    def lint_css(self, filename: str) -> ToolResult:
        return self.execute({"filename": filename})


class LintJsHandler(ToolHandler):
    """检查 JavaScript 语法错误（自动检测 ES Module）"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        try:
            content = ws.read(filename)
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
            if is_es_module:
                # 语法合法但 ES Module 无法在 file:// 预览环境加载（CORS 拦截），
                # 提示改用普通 <script> + IIFE/全局变量
                return ToolResult(content=(
                    f"⚠️ JavaScript 语法通过，但检测到 ES Module（import/export）。"
                    f"预览环境用 file:// 协议加载，ES Module 会被 CORS 拦截导致脚本不执行。"
                    f"请改用普通 <script> 标签 + IIFE (function(global){{...}})(window) + window.XXX 暴露接口 ({filename})"
                ))
            return ToolResult(content=f"JavaScript 语法检查通过 ({filename})")
        except FileNotFoundError:
            return ToolResult(content=f"Node.js 未安装，跳过 JS 语法检查 ({filename})")
        except subprocess.TimeoutExpired:
            return ToolResult(error=f"JS 语法检查超时 ({filename})")
        except Exception as e:
            return ToolResult(error=str(e))

    def lint_js(self, filename: str) -> ToolResult:
        return self.execute({"filename": filename})


class ExecuteCodeHandler(ToolHandler):
    """检查代码是否可执行（简单 HTML 结构检查）"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "index.html")
        try:
            content = ws.read(filename)
            if '<html' not in content.lower() and '<!doctype' not in content.lower():
                return ToolResult(content=f"文件 {filename} 不包含标准 HTML 结构，跳过执行验证")
            return ToolResult(content=f"代码执行就绪 ({filename})", metadata={"filename": filename})
        except Exception as e:
            return ToolResult(error=str(e))

    def execute_code(self, filename: str = "index.html") -> ToolResult:
        return self.execute({"filename": filename})


# ==================== 兼容旧 CodeToolHandler 类 ====================

class CodeToolHandler:
    """向后兼容：聚合所有代码工具处理器（委托给子类实例）"""

    def __init__(self, workspace):
        self.workspace = workspace
        self._validate_html = ValidateHtmlHandler(workspace)
        self._lint_css = LintCssHandler(workspace)
        self._lint_js = LintJsHandler(workspace)
        self._execute_code = ExecuteCodeHandler(workspace)

    def validate_html(self, filename: str) -> ToolResult:
        return self._validate_html.validate_html(filename)

    def lint_css(self, filename: str) -> ToolResult:
        return self._lint_css.lint_css(filename)

    def lint_js(self, filename: str) -> ToolResult:
        return self._lint_js.lint_js(filename)

    def execute_code(self, filename: str = "index.html") -> ToolResult:
        return self._execute_code.execute_code(filename)


# ==================== 注册函数 ====================

def register_code_tools(registry):
    validate_html_handler = ValidateHtmlHandler()
    lint_css_handler = LintCssHandler()
    lint_js_handler = LintJsHandler()
    execute_code_handler = ExecuteCodeHandler()

    registry.register(ToolDefinition(
        name="execute_code",
        description="检查目标 HTML 文件是否具备可渲染的入口结构（<html>/<!doctype>），并确认代码就绪；不做真实浏览器执行",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要执行的 HTML 文件名（默认 index.html）"}
            },
            "required": []
        },
        handler=lambda **kwargs: execute_code_handler.execute(kwargs),
        permission="execute",
        tool_handler=execute_code_handler,
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
        handler=lambda **kwargs: validate_html_handler.execute(kwargs),
        permission="read",
        tool_handler=validate_html_handler,
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
        handler=lambda **kwargs: lint_css_handler.execute(kwargs),
        permission="read",
        tool_handler=lint_css_handler,
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
        handler=lambda **kwargs: lint_js_handler.execute(kwargs),
        permission="read",
        tool_handler=lint_js_handler,
    ))
