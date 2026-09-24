# -*- coding: utf-8 -*-
"""
TASK_STATE.md 维护工具：update_task_notes

对应设计文档 §4（L2 任务状态）：状态由 Agent 自写，harness 只校验小节名合法、
不解析语义。handler 仅负责按小节 merge 写文件，不判断内容对错。
"""

from harness.tools.registry import ToolDefinition, ToolResult, ToolHandler


# 已知小节（设计文档示例）：目标 / 决策与理由 / 文件状态 / 未决问题 / 下一步
KNOWN_SECTIONS = ("目标", "决策与理由", "文件状态", "未决问题", "下一步")


def _is_valid_section(section: str) -> bool:
    """harness 只校验小节名合法：非空即可（同名已知小节优先，自定义小节也允许）。"""
    return bool(section and section.strip())


def _merge_section(existing: str, section: str, content: str) -> str:
    """把指定小节 merge 进 TASK_STATE.md 文本，返回新全文。

    - 小节不存在 → 追加到末尾；
    - 小节已存在 → 整体覆盖该小节正文（到下一个 ## 小节或文件结尾）。
    """
    section = section.strip()
    content = (content or "").strip()
    lines = existing.split("\n") if existing else []

    start_idx = -1
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            name = ln[3:].strip()
            if name == section:
                start_idx = i
                break

    if start_idx == -1:
        out = list(lines)
        if out and out[-1].strip():
            out.append("")
        out.append(f"## {section}")
        if content:
            out.append(content)
        return "\n".join(out)

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    new_block = [f"## {section}", content] if content else [f"## {section}"]
    out = lines[:start_idx] + new_block + lines[end_idx:]
    return "\n".join(out)


class UpdateTaskNotesHandler(ToolHandler):
    """维护 .task/TASK_STATE.md 的任务状态笔记（按小节更新）"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        section = (args.get("section") or "").strip()
        content = args.get("content", "")
        if not _is_valid_section(section):
            return ToolResult(error="section 不能为空")

        path = ".task/TASK_STATE.md"
        try:
            existing_files = ws.list()
            existing = ws.read(path) if path in existing_files else ""
        except Exception:
            existing = ""

        try:
            new_content = _merge_section(existing, section, content)
            ws.write(path, new_content)
        except Exception as e:
            return ToolResult(error=f"写入 TASK_STATE.md 失败: {e}")

        return ToolResult(content=f"已更新 .task/TASK_STATE.md 的「{section}」小节。")


def register_task_state_tools(registry):
    """注册 TASK_STATE.md 维护工具到 ToolRegistry"""
    handler = UpdateTaskNotesHandler()
    registry.register(ToolDefinition(
        name="update_task_notes",
        description=(
            "维护 .task/TASK_STATE.md 的任务状态笔记。这是你（Agent）的第一职责：在信息产生的时刻"
            "记录目标 / 决策与理由 / 文件状态 / 未决问题 / 下一步，避免上下文窗口滚动后丢失进度。"
            "按小节整体覆盖更新；section 为小节名（如「目标」「决策与理由」「文件状态」"
            "「未决问题」「下一步」，也可自定义），content 为该小节的新内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "小节名，如：目标 / 决策与理由 / 文件状态 / 未决问题 / 下一步（也可自定义）",
                },
                "content": {
                    "type": "string",
                    "description": "该小节的新内容，整体覆盖原小节",
                },
            },
            "required": ["section", "content"],
        },
        handler=lambda **kwargs: handler.execute(kwargs),
        permission="write",
        tool_handler=handler,
    ))
