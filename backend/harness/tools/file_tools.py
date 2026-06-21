# -*- coding: utf-8 -*-
"""
文件操作工具：read_file / write_file / list_files / delete_file
"""

from harness.tools.registry import ToolDefinition, ToolResult


def register_file_tools(registry):
    registry.register(ToolDefinition(
        name="read_file",
        description="读取工作区中的文件内容",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要读取的文件名（相对于工作区根目录）"}
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

    def read_file(self, filename: str) -> ToolResult:
        try:
            content = self.workspace.read(filename)
            return ToolResult(content=content)
        except Exception as e:
            return ToolResult(error=str(e))

    def write_file(self, filename: str, content: str) -> ToolResult:
        try:
            lines = content.count('\n') + 1
            self.workspace.write(filename, content)
            return ToolResult(
                content=f"已写入 {filename} ({lines} 行)",
                metadata={"filename": filename, "lines": lines}
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
