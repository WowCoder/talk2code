# -*- coding: utf-8 -*-
from __future__ import annotations
"""
工具注册表 —— Agent 可用工具的单一注册源

提供 ToolHandler 抽象基类、@register_tool 装饰器和 ToolRegistry。
所有工具处理器继承 ToolHandler 并通过装饰器自注册。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Any, Optional


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable  # 实际执行函数
    permission: str = "read"  # read | write | execute
    max_retries: int = 1
    # 指向 ToolHandler 子类实例（装饰器注册时设置）
    tool_handler: Optional["ToolHandler"] = None


@dataclass
class ToolResult:
    """工具执行结果"""
    content: str = ""
    error: str = ""
    blocked: bool = False  # PRE_TOOL_USE 硬约束阻断（不算成功也不算失败，LLM 视为信息提示）
    metadata: dict = None

    @property
    def success(self) -> bool:
        return not self.error and not self.blocked

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# ==================== 全局注册表（供装饰器使用） ====================

_GLOBAL_REGISTRY: Optional["ToolRegistry"] = None


def get_global_registry() -> Optional["ToolRegistry"]:
    """获取全局 ToolRegistry 实例"""
    return _GLOBAL_REGISTRY


def set_global_registry(registry: "ToolRegistry"):
    """设置全局 ToolRegistry 实例（create_tool_registry 调用）"""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = registry


# ==================== ToolHandler 抽象基类 ====================

class ToolHandler(ABC):
    """工具处理器抽象基类

    所有工具处理器 MUST 继承此类并实现 execute 方法。
    """

    def __init__(self, workspace=None):
        self.workspace = workspace

    def set_workspace(self, workspace):
        """设置工作区引用（注册时 workspace 可能尚未初始化）"""
        self.workspace = workspace

    @abstractmethod
    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        """执行工具调用

        Args:
            args: 工具调用参数字典
            workspace: WorkspaceFS 实例（可选，优先使用实例属性）
            state: AgentState 实例（可选）

        Returns:
            ToolResult 执行结果
        """
        ...


# ==================== register_tool 装饰器 ====================

def register_tool(name: str, description: str = "", parameters: dict = None,
                  permission: str = "read", max_retries: int = 1):
    """装饰器：将 ToolHandler 子类自动注册到全局 ToolRegistry

    用法:
        @register_tool("my_tool", description="...", parameters={...}, permission="read")
        class MyToolHandler(ToolHandler):
            def execute(self, args, workspace=None, state=None) -> ToolResult:
                ...
    """
    def decorator(cls):
        # 验证是否继承 ToolHandler
        if not issubclass(cls, ToolHandler):
            raise TypeError(
                f"@register_tool 只能用于 ToolHandler 子类，"
                f"{cls.__name__} 未继承 ToolHandler"
            )

        # 存储注册元数据到类属性（延迟注册）
        cls._tool_name = name
        cls._tool_description = description
        cls._tool_parameters = parameters or {
            "type": "object", "properties": {}, "required": []
        }
        cls._tool_permission = permission
        cls._tool_max_retries = max_retries

        # 如果全局注册表已存在，立即注册
        global _GLOBAL_REGISTRY
        if _GLOBAL_REGISTRY is not None:
            _register_handler_to_registry(_GLOBAL_REGISTRY, cls, name, description,
                                          parameters or {}, permission, max_retries)

        return cls

    return decorator


def _register_handler_to_registry(registry: "ToolRegistry", cls, name: str,
                                   description: str, parameters: dict,
                                   permission: str, max_retries: int):
    """将 ToolHandler 子类注册到指定 ToolRegistry"""
    # 创建一个不绑定 workspace 的实例（workspace 后续通过 set_workspace 注入）
    instance = cls(workspace=None)

    # 创建 ToolDefinition，handler 委托给 instance.execute
    def _handler(**kwargs):
        return instance.execute(kwargs)

    tool_def = ToolDefinition(
        name=name,
        description=description or (cls.__doc__ or "").strip().split("\n")[0],
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        handler=_handler,
        permission=permission,
        max_retries=max_retries,
        tool_handler=instance,
    )
    registry.register(tool_def)


# ==================== ToolRegistry ====================

class ToolRegistry:
    """工具注册表 —— 所有 Agent 可用工具的单一注册源"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[ToolHandler]:
        """获取工具对应的 ToolHandler 实例（用于直接调用 execute）"""
        tool = self._tools.get(name)
        if tool and tool.tool_handler:
            return tool.tool_handler
        return None

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

    def set_workspace(self, workspace):
        """为所有已注册的 ToolHandler 实例设置 workspace 引用"""
        for tool_def in self._tools.values():
            if tool_def.tool_handler:
                tool_def.tool_handler.set_workspace(workspace)
                # 更新 handler 闭包以使用新的 workspace
                handler_instance = tool_def.tool_handler

                def make_handler(h):
                    def _handler(**kwargs):
                        return h.execute(kwargs)
                    return _handler

                tool_def.handler = make_handler(handler_instance)


def create_tool_registry() -> ToolRegistry:
    """创建并初始化默认的 ToolRegistry"""
    registry = ToolRegistry()

    # 设置全局注册表（供 @register_tool 装饰器使用）
    set_global_registry(registry)

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
