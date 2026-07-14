# -*- coding: utf-8 -*-
"""
文件操作工具：read_file / write_file / list_files / delete_file
"""

from harness.tools.registry import ToolDefinition, ToolResult


def register_file_tools(registry):
    registry.register(ToolDefinition(
        name="read_file",
        description=(
            "读取工作区中的文件内容。对于大文件（>300行），请使用 start_line/end_line 分页读取，"
            "避免一次性读取整个文件。读取后注意查看返回的 total_lines 元数据，"
            "如果文件被截断，用 start_line 定位到文件末尾查看 export/关键逻辑。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要读取的文件名（相对于工作区根目录）"},
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（1-based，可选）。用于分页读取大文件。不指定则从第1行开始。"
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（1-based，可选）。不指定则读到文件末尾。"
                },
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="read",
    ))

    registry.register(ToolDefinition(
        name="write_file",
        description="创建或覆盖工作区中的文件",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "文件名（相对于工作区根目录，支持子目录如 css/style.css）"},
                "content": {"type": "string", "description": "文件内容"}
            },
            "required": ["filename", "content"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="write",
    ))

    registry.register(ToolDefinition(
        name="list_files",
        description="列出工作区中的所有文件",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="read",
    ))

    registry.register(ToolDefinition(
        name="delete_file",
        description="删除工作区中的文件",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要删除的文件名"}
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: ToolResult(error="需要 workspace 上下文"),
        permission="write",
    ))


class FileToolHandler:
    """文件工具的实际处理器，绑定 WorkspaceFS 实例"""

    def __init__(self, workspace):
        self.workspace = workspace

    def read_file(self, filename: str, start_line: int = None, end_line: int = None) -> ToolResult:
        try:
            content = self.workspace.read(filename)
            lines = content.split('\n')
            total_lines = len(lines)

            if start_line is not None or end_line is not None:
                start = max(0, (start_line or 1) - 1)
                end = min(total_lines, end_line or total_lines)
                selected = lines[start:end]
                content = '\n'.join(selected)
                range_info = f" (行 {start + 1}-{end} / 共 {total_lines} 行)"
            else:
                range_info = f" (共 {total_lines} 行)"

            # 在内容前添加文件范围信息
            header = f"[文件: {filename}{range_info}]\n\n"
            return ToolResult(
                content=header + content,
                metadata={
                    "filename": filename,
                    "total_lines": total_lines,
                    "start_line": start_line or 1,
                    "end_line": end_line or total_lines,
                    "chars": len(content),
                }
            )
        except Exception as e:
            return ToolResult(error=str(e))

    def write_file(self, filename: str, content: str) -> ToolResult:
        try:
            lines = content.count('\n') + 1
            char_count = len(content)
            self.workspace.write(filename, content)
            # 只返回元数据，不返回文件内容
            # 避免 Agent 看到"截断"标记后陷入 read_file 验证死循环
            return ToolResult(
                content=f"已创建 {filename} ({lines} 行, {char_count} 字符)",
                metadata={"filename": filename, "lines": lines, "chars": char_count}
            )
        except Exception as e:
            return ToolResult(error=str(e))

    def list_files(self) -> ToolResult:
        try:
            files = self.workspace.list()
            return ToolResult(content="\n".join(files) if files else "(空目录)")
        except Exception as e:
            return ToolResult(error=str(e))

    def delete_file(self, filename: str) -> ToolResult:
        try:
            self.workspace.delete(filename)
            return ToolResult(content=f"已删除 {filename}")
        except Exception as e:
            return ToolResult(error=str(e))
