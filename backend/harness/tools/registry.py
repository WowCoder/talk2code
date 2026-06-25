# -*- coding: utf-8 -*-
from __future__ import annotations
"""
工具注册表 —— Agent 可用工具的单一注册源
"""

from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable  # 实际执行函数
    permission: str = "read"  # read | write | execute
    max_retries: int = 1


@dataclass
class ToolResult:
    """工具执行结果"""
    content: str = ""
    error: str = ""
    metadata: dict = None

    @property
    def success(self) -> bool:
        return not self.error

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ToolRegistry:
    """工具注册表 —— 所有 Agent 可用工具的单一注册源"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools.get(name)

    def get_schemas(self) -> list[dict]:
        """生成 LLM function calling 格式的工具描述"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict) -> ToolResult:
        """执行工具调用，返回结果"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(error=f"未知工具: {name}")
        try:
            output = tool.handler(**arguments)
            if isinstance(output, ToolResult):
                return output
            return ToolResult(content=str(output) if output else "")
        except Exception as e:
            return ToolResult(error=str(e))

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_permission(self, name: str) -> str:
        tool = self._tools.get(name)
        return tool.permission if tool else "read"


def create_tool_registry() -> ToolRegistry:
    """创建并初始化默认的 ToolRegistry"""
    registry = ToolRegistry()

    from harness.tools.file_tools import register_file_tools
    from harness.tools.code_tools import register_code_tools
    from harness.tools.web_tools import register_web_tools
    from harness.tools.preview_tools import register_preview_tools
    from harness.tools.edit_tools import register_edit_tools

    register_file_tools(registry)
    register_code_tools(registry)
    register_web_tools(registry)
    register_preview_tools(registry)
    register_edit_tools(registry)

    return registry
