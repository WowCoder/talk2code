# -*- coding: utf-8 -*-
"""
文件操作工具：read_file / write_file / list_files / delete_file

每个工具对应一个 ToolHandler 子类，支持通过 @register_tool 装饰器或
register_file_tools() 函数注册。
"""

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)


# ==================== ToolHandler 子类 ====================

class ReadFileHandler(ToolHandler):
    """读取工作区文件内容"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        try:
            content = ws.read(filename)
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

    # 保留旧方法名以兼容现有调用
    def read_file(self, filename: str, start_line: int = None, end_line: int = None) -> ToolResult:
        return self.execute({
            "filename": filename,
            "start_line": start_line,
            "end_line": end_line,
        })


class WriteFileHandler(ToolHandler):
    """创建或覆盖工作区文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        content = args.get("content", "")
        try:
            lines = content.count('\n') + 1
            char_count = len(content)
            ws.write(filename, content)
            return ToolResult(
                content=f"已创建 {filename} ({lines} 行, {char_count} 字符)",
                metadata={"filename": filename, "lines": lines, "chars": char_count}
            )
        except Exception as e:
            return ToolResult(error=str(e))

    def write_file(self, filename: str, content: str) -> ToolResult:
        return self.execute({"filename": filename, "content": content})


class ListFilesHandler(ToolHandler):
    """列出工作区所有文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        try:
            files = ws.list()
            return ToolResult(content="\n".join(files) if files else "(空目录)")
        except Exception as e:
            return ToolResult(error=str(e))

    def list_files(self) -> ToolResult:
        return self.execute({})


class DeleteFileHandler(ToolHandler):
    """删除工作区文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        try:
            ws.delete(filename)
            return ToolResult(content=f"已删除 {filename}")
        except Exception as e:
            return ToolResult(error=str(e))

    def delete_file(self, filename: str) -> ToolResult:
        return self.execute({"filename": filename})


# ==================== 兼容旧 FileToolHandler 类 ====================

class FileToolHandler:
    """向后兼容：聚合所有文件工具处理器（委托给子类实例）"""

    def __init__(self, workspace):
        self.workspace = workspace
        self._read = ReadFileHandler(workspace)
        self._write = WriteFileHandler(workspace)
        self._list = ListFilesHandler(workspace)
        self._delete = DeleteFileHandler(workspace)

    def read_file(self, filename: str, start_line: int = None, end_line: int = None) -> ToolResult:
        return self._read.read_file(filename, start_line, end_line)

    def write_file(self, filename: str, content: str) -> ToolResult:
        return self._write.write_file(filename, content)

    def list_files(self) -> ToolResult:
        return self._list.list_files()

    def delete_file(self, filename: str) -> ToolResult:
        return self._delete.delete_file(filename)


# ==================== 注册函数 ====================

def register_file_tools(registry):
    """注册文件操作工具到 ToolRegistry"""
    # 创建 handler 实例（workspace 稍后注入）
    read_handler = ReadFileHandler()
    write_handler = WriteFileHandler()
    list_handler = ListFilesHandler()
    delete_handler = DeleteFileHandler()

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
        handler=lambda **kwargs: read_handler.execute(kwargs),
        permission="read",
        tool_handler=read_handler,
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
        handler=lambda **kwargs: write_handler.execute(kwargs),
        permission="write",
        tool_handler=write_handler,
    ))

    registry.register(ToolDefinition(
        name="list_files",
        description="列出工作区中的所有文件",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        handler=lambda **kwargs: list_handler.execute(kwargs),
        permission="read",
        tool_handler=list_handler,
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
        handler=lambda **kwargs: delete_handler.execute(kwargs),
        permission="write",
        tool_handler=delete_handler,
    ))
