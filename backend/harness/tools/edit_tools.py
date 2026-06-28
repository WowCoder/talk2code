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


def _normalize(text: str) -> str:
    """Normalize 行尾空白 + 统一缩进为空格，容忍 LLM 常见空白误差。

    - 移除每行尾部空白（空格/tab）
    - 不修改前导空白（保留缩进语义）
    - 不做全量 strip（首尾空行仍有意义）
    """
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _match(search: str, content: str) -> tuple[int, str]:
    """在 content 中匹配 search，返回 (occurrences, best_hint)。

    best_hint 用于匹配失败时回灌——它取自 content 中与 search 首行最接近的片段。
    """
    # 1) 原始精确匹配（优先）
    occurrences = content.count(search)
    if occurrences:
        return occurrences, ""

    # 2) Normalize 后匹配：容忍行尾空白差异
    normalized_search = _normalize(search)
    normalized_content = _normalize(content)
    occurrences = normalized_content.count(normalized_search)
    if occurrences:
        return occurrences, "normalized"

    return 0, ""


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

        # 逐块匹配；任一失败则整体不修改，返回上下文帮助 LLM 校正
        new_content = content
        for i, (search, replace) in enumerate(blocks, 1):
            occ, match_mode = _match(search, new_content)
            if occ == 0:
                return ToolResult(
                    error=self._no_match_message(filename, i, search, new_content),
                )
            if occ > 1:
                return ToolResult(
                    error=self._ambiguous_message(filename, i, search, occ),
                )
            if match_mode == "normalized":
                # normalize 匹配成功：在校准后的内容上执行替换，保证后续块的一致
                normalized_content = _normalize(new_content)
                normalized_search = _normalize(search)
                normalized_replace = _normalize(replace)
                new_content = normalized_content.replace(normalized_search, normalized_replace, 1)
            else:
                new_content = new_content.replace(search, replace, 1)

        # 写入
        try:
            self.workspace.write(filename, new_content)
        except Exception as e:
            return ToolResult(error=str(e))

        # 统计 diff
        old_lines = content.count("\n") + 1
        new_lines = new_content.count("\n") + 1
        match_tags = ["normalized"] if any(
            _match(s, content)[1] == "normalized" for s, _ in blocks
        ) else []
        return ToolResult(
            content=(
                f"已更新 {filename}：应用 {len(blocks)} 处修改 "
                f"({old_lines} → {new_lines} 行)"
                + (f"（通过空白归一匹配）" if match_tags else "")
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
        """匹配失败时回灌文件实际内容，让 LLM 看到真值后重新构造 SEARCH 块"""
        search_preview = search[:200] + ("..." if len(search) > 200 else "")

        # 用搜索块首行定位文件中的近似位置，回灌周围内容
        # 尝试层级：精确 substring > CSS 属性前缀（如 font-size:）> 首词
        first_line = search.split("\n", 1)[0].strip()
        context_snippet = ""
        match_ln = None
        if first_line:
            lines = content.split("\n")
            # 层级1：精确 substring 匹配
            for ln, line in enumerate(lines, 1):
                if first_line in line:
                    match_ln = ln
                    break
            # 层级2：CSS 属性前缀（font-size: / color: / margin: 等）
            if match_ln is None and ":" in first_line:
                prefix = first_line.split(":", 1)[0].strip()
                for ln, line in enumerate(lines, 1):
                    if line.strip().startswith(prefix):
                        match_ln = ln
                        break
            # 层级3：首词匹配
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

        if context_snippet:
            return (
                f"第 {block_idx} 个 SEARCH 块在 {filename} 中未找到精确匹配。\n"
                f"你的 SEARCH 块（前 200 字）：\n{search_preview}\n\n"
                f"文件 {filename} 从第 {max(1, match_ln - 2)} 行起的实际内容：\n{context_snippet}\n\n"
                f"⚠️ 请逐字对比 SEARCH 块与上面文件内容的差异。"
                f"不要用记忆/猜测的值——直接用上面显示的内容片段重新构造 SEARCH 块。"
            )
        else:
            return (
                f"第 {block_idx} 个 SEARCH 块在 {filename} 中未找到精确匹配。\n"
                f"你的 SEARCH 块（前 200 字）：\n{search_preview}\n\n"
                f"文件 {filename} 全文（{len(content)} 字）：\n{content[:1000]}\n\n"
                f"请用 read_file 重新读取文件，确保 SEARCH 片段逐字符匹配。"
            )

    @staticmethod
    def _ambiguous_message(filename: str, block_idx: int, search: str, occurrences: int) -> str:
        preview = search[:150] + ("..." if len(search) > 150 else "")
        return (
            f"第 {block_idx} 个 SEARCH 块在 {filename} 中匹配到 {occurrences} 处（需唯一）。"
            f"请在 SEARCH 片段中增加上下文行使其唯一。片段：\n{preview}"
        )
