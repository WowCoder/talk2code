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
- 解析所有 SEARCH/REPLACE 块，逐个在文件中精确匹配
- 全部匹配成功才写入；任意一块匹配失败则整体不写，返回失败上下文让 LLM 校正
- 文件不存在 / 空文件 → 引导改用 write_file 创建
- REPLACE 为空 = 删除该片段

这是把"对话式迭代修改"从"整文件重写"提升到"局部 diff"的关键 ——
省 token、避免截断、避免改坏没动的地方。对应 Aider/Cline/OpenHands 的共同基础能力。
"""

from __future__ import annotations

import re

from harness.tools.registry import ToolDefinition, ToolResult


def register_edit_tools(registry):
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
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="write",
    ))


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


class EditToolHandler:
    """绑定 WorkspaceFS 的增量编辑处理器"""

    def __init__(self, workspace):
        self.workspace = workspace

    def edit_file(self, filename: str, edit: str) -> ToolResult:
        # 解析块
        try:
            blocks = parse_edit_blocks(edit)
        except ValueError as e:
            return ToolResult(error=str(e))

        # 文件必须已存在
        if not self.workspace.exists(filename):
            return ToolResult(
                error=f"文件不存在：{filename}。新建文件请用 write_file。"
            )

        try:
            content = self.workspace.read(filename)
        except Exception as e:
            return ToolResult(error=str(e))

        # 逐块精确匹配；任一失败则整体不修改，返回上下文
        new_content = content
        for i, (search, replace) in enumerate(blocks, 1):
            occurrences = new_content.count(search)
            if occurrences == 0:
                return ToolResult(
                    error=self._no_match_message(filename, i, search, new_content),
                )
            if occurrences > 1:
                return ToolResult(
                    error=self._ambiguous_message(filename, i, search, occurrences),
                )
            # 空替换 = 删除（含尾部换行清理）
            new_content = new_content.replace(search, replace)

        # 写入
        try:
            self.workspace.write(filename, new_content)
        except Exception as e:
            return ToolResult(error=str(e))

        # 统计 diff
        old_lines = content.count("\n") + 1
        new_lines = new_content.count("\n") + 1
        return ToolResult(
            content=(
                f"已更新 {filename}：应用 {len(blocks)} 处修改 "
                f"({old_lines} → {new_lines} 行)"
            ),
            metadata={
                "filename": filename,
                "blocks": len(blocks),
                "old_lines": old_lines,
                "new_lines": new_lines,
            },
        )

    @staticmethod
    def _no_match_message(filename: str, block_idx: int, search: str, content: str) -> str:
        """匹配失败时回灌文件上下文，帮助 LLM 校正 SEARCH 块"""
        # 找最接近的行号提示：搜索块首行
        first_line = search.split("\n", 1)[0].strip()
        near_line = None
        if first_line:
            for ln, line in enumerate(content.split("\n"), 1):
                if first_line in line:
                    near_line = ln
                    break
        loc = f"（可能在你提供的片段未精确匹配；首行近似出现在第 {near_line} 行）" if near_line else ""
        preview = search[:200] + ("..." if len(search) > 200 else "")
        return (
            f"第 {block_idx} 个 SEARCH 块在 {filename} 中未找到精确匹配{loc}。\n"
            f"你提供的 SEARCH 片段（前 200 字）：\n{preview}\n"
            f"请用 read_file 重新读取文件，确保 SEARCH 片段逐字符匹配（含缩进/空格/换行）。"
        )

    @staticmethod
    def _ambiguous_message(filename: str, block_idx: int, search: str, occurrences: int) -> str:
        preview = search[:150] + ("..." if len(search) > 150 else "")
        return (
            f"第 {block_idx} 个 SEARCH 块在 {filename} 中匹配到 {occurrences} 处（需唯一）。"
            f"请在 SEARCH 片段中增加上下文行使其唯一。片段：\n{preview}"
        )
