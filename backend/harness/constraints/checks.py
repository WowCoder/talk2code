# -*- coding: utf-8 -*-
"""
约束检查 Hook 集合 —— 合并自 craft_enforcer.py / security.py / quality.py

所有 Hook 遵循统一模式：
    def _xxx_hook(ctx: HookContext) -> Optional[str]
    返回 None = 通过，返回 str = 失败信息
"""

import re
from harness.constraints.hooks import HookContext


# ==================== Craft 规则检查 ====================

def _anti_ai_slop_hook(ctx: HookContext):
    """检查 AI 生成代码中的常见坏味道 (placeholder/TODO/占位文本)"""
    if not ctx.tool_args:
        return
    content = ctx.tool_args.get("content", "")
    if not content:
        return

    slop_patterns = [
        (r'lorem ipsum', '检测到 AI 坏味道: "lorem ipsum" 占位文本'),
        (r'TODO: implement', '检测到 AI 坏味道: 空洞的 TODO 注释'),
        (r'add your code here', '检测到 AI 坏味道: "add your code here" 占位标记'),
    ]
    for pattern, msg in slop_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return msg


# ==================== 安全检查 ====================

def _xss_hook(ctx: HookContext):
    """检查生成的代码中是否有 XSS 风险"""
    if ctx.tool_args:
        content = ctx.tool_args.get("content", "")
        if not content:
            return
        checks = [
            (r'innerHTML\s*=', '使用 innerHTML 存在 XSS 风险，建议使用 textContent 或 createElement'),
            (r'document\.write\(', '使用 document.write() 存在 XSS 风险'),
            (r'eval\(', '使用 eval() 存在安全风险'),
        ]
        for pattern, msg in checks:
            if re.search(pattern, content):
                return f"安全风险: {msg}"


# ==================== 代码质量检查 ====================

def _html_validity_hook(ctx: HookContext):
    """检查 HTML 语法有效性"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".html"):
            content = ctx.tool_args.get("content", "")
            try:
                from html.parser import HTMLParser
                parser = HTMLParser()
                parser.feed(content)
                parser.close()
            except Exception as e:
                return f"HTML 语法错误 ({filename}): {e}"


def _css_lint_hook(ctx: HookContext):
    """检查 CSS 括号平衡"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".css"):
            content = ctx.tool_args.get("content", "")
            open_braces = content.count('{')
            close_braces = content.count('}')
            if open_braces != close_braces:
                return f"CSS 括号不匹配 ({filename}): {{ {open_braces}, }} {close_braces}"


def _js_syntax_hook(ctx: HookContext):
    """检查 JS 语法"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".js"):
            content = ctx.tool_args.get("content", "")
            import subprocess
            try:
                result = subprocess.run(
                    ["node", "--check", "-"],
                    input=content, capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0:
                    return f"JavaScript 语法错误 ({filename}): {result.stderr[:300]}"
            except FileNotFoundError:
                pass  # Node.js 未安装，跳过
            except subprocess.TimeoutExpired:
                return f"JS 语法检查超时 ({filename})"


def _required_files_hook(ctx: HookContext):
    """任务完成时检查是否生成了 index.html"""
    file_list = ctx.state.get("file_list", [])
    if "index.html" not in file_list and not any(f.endswith("index.html") for f in file_list):
        return "缺少必需的 index.html 文件"


# ==================== 统一注册入口 ====================

def register_all_hooks(manager):
    """将所有约束检查 Hook 注册到 HookManager"""
    from harness.constraints.hooks import HookPoint

    # Craft 规则
    manager.register(HookPoint.POST_TOOL_USE, _anti_ai_slop_hook)

    # 安全
    manager.register(HookPoint.POST_TOOL_USE, _xss_hook)

    # 质量
    manager.register(HookPoint.POST_TOOL_USE, _html_validity_hook)
    manager.register(HookPoint.POST_TOOL_USE, _css_lint_hook)
    manager.register(HookPoint.POST_TOOL_USE, _js_syntax_hook)
    manager.register(HookPoint.ON_TASK_COMPLETE, _required_files_hook)

    # ---- 进度约束 Hook（硬阻断） ----
    from harness.constraints.progress_hooks import (
        block_unnecessary_read,
        block_premature_completion,
        track_write_success,
    )
    # PreToolUse: 在工具执行前阻断不合理行为
    manager.register(HookPoint.PRE_TOOL_USE, block_unnecessary_read)
    manager.register(HookPoint.PRE_TOOL_USE, block_premature_completion)
    # PostToolUse: write_file 成功后追踪写入、更新 contract
    manager.register(HookPoint.POST_TOOL_USE, track_write_success)
