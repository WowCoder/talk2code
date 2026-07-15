# -*- coding: utf-8 -*-
"""
增量编辑工具：edit_file

借鉴 Aider 的 search-replace 块协议（比 unified-diff 对 LLM 更友好，成功率更高）。

LLM 输出格式（edit 字段，支持单文件多个块）：

    <<<< SEARCH
    要替换的原始代码片段（必须精确匹配现有文件内容，含缩进）
    ====
    替换后的新代码
    >>>>

工具行为：
- 解析所有 SEARCH/REPLACE 块，逐个在文件中匹配
- 匹配前自动 normalize 缩进/行尾空白（容忍 LLM 常见的空白误差）
- 全部匹配成功才写入；任意一块匹配失败则整体不写，回灌文件实际内容让 LLM 校正
- 文件不存在 / 空文件 → 引导改用 write_file 创建
- REPLACE 为空 = 删除该片段
"""

from __future__ import annotations

import re

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)


# SEARCH/REPLACE 块正则：捕获每对 SEARCH...REPLACE
_BLOCK_RE = re.compile(
    r"<<<<\s*SEARCH\s*\n(.*?)\n====\s*\n(.*?)\n>>>>",
    re.DOTALL,
)


def parse_edit_blocks(edit: str) -> list[tuple[str, str]]:
    """解析 edit 字符串为 [(search, replace), ...]。格式非法抛 ValueError。"""
    blocks = _BLOCK_RE.findall(edit)
    if not blocks:
        raise ValueError(
            "edit 中未找到合法的 SEARCH/REPLACE 块。"
            "格式示例：\n<<<< SEARCH\n原始代码\n====\n新代码\n>>>>"
        )
    return blocks


def _normalize(text: str) -> str:
    """Normalize 行尾空白 + 统一缩进为空格，容忍 LLM 常见空白误差。"""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _normalize_whitespace(text: str) -> str:
    """激进空白规范化：折叠所有连续空白为单个空格，行首尾去空白。"""
    lines = []
    for line in text.split("\n"):
        collapsed = " ".join(line.split())
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def _normalize_indentation(text: str) -> str:
    """去除所有前导空白，只保留内容行。"""
    return "\n".join(
        line.lstrip()
        for line in text.split("\n")
    )


def _match(search: str, content: str) -> tuple[int, str]:
    """在 content 中匹配 search，返回 (occurrences, match_mode)。"""
    # Level 1: 原始精确匹配
    occurrences = content.count(search)
    if occurrences:
        return occurrences, "exact"

    # Level 2: 行尾空白规范化
    normalized_search = _normalize(search)
    normalized_content = _normalize(content)
    occurrences = normalized_content.count(normalized_search)
    if occurrences:
        return occurrences, "normalized"

    # Level 3: 激进空白折叠匹配
    ws_search = _normalize_whitespace(search)
    ws_content = _normalize_whitespace(content)
    occurrences = ws_content.count(ws_search)
    if occurrences:
        return occurrences, "whitespace"

    # Level 4: 缩进弹性匹配
    indent_search = _normalize_indentation(search)
    indent_content = _normalize_indentation(content)
    occurrences = indent_content.count(indent_search)
    if occurrences:
        return occurrences, "indentation"

    return 0, ""


# ==================== ToolHandler 子类 ====================

class EditFileHandler(ToolHandler):
    """增量编辑文件处理器"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        return self.edit_file(args.get("filename", ""), args.get("edit", ""))

    def edit_file(self, filename: str, edit: str) -> ToolResult:
        ws = self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")

        # 解析块
        try:
            blocks = parse_edit_blocks(edit)
        except ValueError as e:
            return ToolResult(error=str(e))

        # 文件必须已存在
        if not ws.exists(filename):
            return ToolResult(
                error=f"文件不存在：{filename}。新建文件请用 write_file。"
            )

        try:
            content = ws.read(filename)
        except Exception as e:
            return ToolResult(error=str(e))

        # 逐块匹配；任一失败则整体不修改
        new_content = content
        match_modes = set()
        for i, (search, replace) in enumerate(blocks, 1):
            occ, match_mode = _match(search, new_content)
            if occ == 0:
                return ToolResult(
                    error=self._no_match_message(filename, i, search, new_content, blocks),
                )
            if occ > 1:
                return ToolResult(
                    error=self._ambiguous_message(filename, i, search, occ),
                )
            match_modes.add(match_mode)

            if match_mode == "indentation":
                indent_content = _normalize_indentation(new_content)
                indent_search = _normalize_indentation(search)
                indent_replace = _normalize_indentation(replace)
                new_content = indent_content.replace(indent_search, indent_replace, 1)
            elif match_mode == "whitespace":
                ws_content = _normalize_whitespace(new_content)
                ws_search = _normalize_whitespace(search)
                ws_replace = _normalize_whitespace(replace)
                new_content = ws_content.replace(ws_search, ws_replace, 1)
            elif match_mode == "normalized":
                normalized_content = _normalize(new_content)
                normalized_search = _normalize(search)
                normalized_replace = _normalize(replace)
                new_content = normalized_content.replace(normalized_search, normalized_replace, 1)
            else:
                new_content = new_content.replace(search, replace, 1)

        # 写入
        try:
            ws.write(filename, new_content)
        except Exception as e:
            return ToolResult(error=str(e))

        # 统计 diff
        old_lines = content.count("\n") + 1
        new_lines = new_content.count("\n") + 1

        mode_labels = {
            "exact": "精确匹配",
            "normalized": "行尾空白归一",
            "whitespace": "空白折叠匹配",
            "indentation": "缩进弹性匹配",
        }
        mode_tag = ", ".join(
            mode_labels.get(m, m) for m in match_modes if m != "exact"
        )
        mode_hint = f"（通过{mode_tag}）" if mode_tag else ""

        modified_preview = new_content[:8000]
        if len(new_content) > 8000:
            modified_preview += (
                f"\n\n... (文件共 {len(new_content)} 字符，以上为前 8000 字符。"
                f"如需查看修改后的尾部，请用 read_file 的 start_line 参数)"
            )
        return ToolResult(
            content=(
                f"已更新 {filename}：应用 {len(blocks)} 处修改 "
                f"({old_lines} → {new_lines} 行){mode_hint}"
                + f"\n\n--- 修改后的完整文件 ---\n{modified_preview}"
            ),
            metadata={
                "filename": filename,
                "blocks": len(blocks),
                "old_lines": old_lines,
                "new_lines": new_lines,
                "match_modes": list(match_modes),
            },
        )

    @staticmethod
    def _no_match_message(filename: str, block_idx: int, search: str, content: str,
                          blocks: list = None) -> str:
        search_preview = search[:200] + ("..." if len(search) > 200 else "")
        total_blocks = len(blocks) if blocks else 1

        first_line = search.split("\n", 1)[0].strip()
        context_snippet = ""
        match_ln = None
        if first_line:
            lines = content.split("\n")
            for ln, line in enumerate(lines, 1):
                if first_line in line:
                    match_ln = ln
                    break
            if match_ln is None and ":" in first_line:
                prefix = first_line.split(":", 1)[0].strip()
                for ln, line in enumerate(lines, 1):
                    if line.strip().startswith(prefix):
                        match_ln = ln
                        break
            if match_ln is None:
                first_word = first_line.split()[0] if first_line.split() else ""
                if first_word:
                    for ln, line in enumerate(lines, 1):
                        if first_word in line:
                            match_ln = ln
                            break
            if match_ln:
                start = max(0, match_ln - 3)
                end = min(len(lines), match_ln + 5)
                context_snippet = "\n".join(
                    f"  {i}: {lines[i - 1]}" for i in range(start + 1, end + 1)
                )

        fallback_hint = (
            f"\n\n💡 提示：如果 edit_file 连续失败 2 次，请改用 write_file 重写整个文件。"
            f"使用 read_file 确认当前文件内容后再构造 SEARCH 块。"
        )

        if context_snippet:
            return (
                f"第 {block_idx}/{total_blocks} 个 SEARCH 块在 {filename} 中未找到精确匹配。\n"
                f"你的 SEARCH 块（前 200 字）：\n{search_preview}\n\n"
                f"文件 {filename} 从第 {max(1, match_ln - 2)} 行起的实际内容：\n{context_snippet}\n\n"
                f"⚠️ 请逐字对比 SEARCH 块与上面文件内容的差异。"
                f"不要用记忆/猜测的值——直接用上面显示的内容片段重新构造 SEARCH 块。"
                f"{fallback_hint}"
            )
        else:
            return (
                f"第 {block_idx}/{total_blocks} 个 SEARCH 块在 {filename} 中未找到精确匹配。\n"
                f"你的 SEARCH 块（前 200 字）：\n{search_preview}\n\n"
                f"文件 {filename} 全文（{len(content)} 字）：\n{content[:1000]}\n\n"
                f"请用 read_file 重新读取文件，确保 SEARCH 片段逐字符匹配。"
                f"{fallback_hint}"
            )

    @staticmethod
    def _ambiguous_message(filename: str, block_idx: int, search: str, occurrences: int) -> str:
        preview = search[:150] + ("..." if len(search) > 150 else "")
        return (
            f"第 {block_idx} 个 SEARCH 块在 {filename} 中匹配到 {occurrences} 处（需唯一）。"
            f"请在 SEARCH 片段中增加上下文行使其唯一。片段：\n{preview}"
        )


# ==================== 兼容旧 EditToolHandler 类 ====================

class EditToolHandler(EditFileHandler):
    """向后兼容：EditToolHandler 现在是 EditFileHandler 的别名"""

    pass


# ==================== 注册函数 ====================

def register_edit_tools(registry):
    edit_handler = EditFileHandler()

    registry.register(ToolDefinition(
        name="edit_file",
        description=(
            "对已存在的文件做局部修改（增量编辑），不要重写整个文件。"
            "提供 edit 参数，包含一个或多个 SEARCH/REPLACE 块：\n"
            "<<<< SEARCH\n原始代码片段（须精确匹配现有内容，含缩进）\n====\n新代码\n>>>>\n"
            "每个 SEARCH 块必须在文件中唯一且精确匹配。"
            "REPLACE 为空表示删除该片段。新建文件请改用 write_file。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要修改的文件名（相对于工作区根目录）",
                },
                "edit": {
                    "type": "string",
                    "description": "SEARCH/REPLACE 块（多个块连续排列即可）",
                },
            },
            "required": ["filename", "edit"],
        },
        handler=lambda **kwargs: edit_handler.execute(kwargs),
        permission="write",
        tool_handler=edit_handler,
    ))
