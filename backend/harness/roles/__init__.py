# -*- coding: utf-8 -*-
"""
角色定义 —— 多智能体协作体系

每个角色 = System Prompt + 工具子集 + 输出格式
同一 LLM 实例，通过切换 System Prompt + 上下文实现角色分化。
"""

from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class Role:
    """角色定义"""
    name: str                          # "TeamLeader" / "ProductManager" / ...
    display_name: str                  # 显示名 "Mike" / "Alice" / ...
    system_prompt: str                 # 角色专属系统提示词
    description: str                   # 简短描述
    tools: list = field(default_factory=list)      # 可用工具名称列表
    max_iterations: int = 5            # ReAct 最大迭代轮数
    output_type: str = "text"          # "text" | "json" | "files"


@dataclass
class RoleResult:
    """角色执行结果"""
    role_name: str
    success: bool
    content: str = ""                  # 文本产出
    structured_output: dict = None     # 结构化产出 (JSON)
    error: str = ""
    tokens_used: int = 0


class RoleRegistry:
    """角色注册表 —— 管理所有可用角色"""

    def __init__(self):
        self._roles: dict[str, Role] = {}

    def register(self, role: Role):
        self._roles[role.name] = role

    def get(self, name: str) -> Optional[Role]:
        return self._roles.get(name)

    def list_names(self) -> list[str]:
        return list(self._roles.keys())

    def get_tool_subset(self, role_name: str, all_tools: list) -> list:
        """获取角色可用的工具 schema 子集"""
        role = self._roles.get(role_name)
        if not role or not role.tools:
            return all_tools  # 未限制 → 全部工具
        # 过滤：只保留角色允许的工具
        return [t for t in all_tools
                if t.get("function", {}).get("name") in role.tools]
