# -*- coding: utf-8 -*-
from __future__ import annotations
"""
HookManager —— 生命周期事件管理

原则：成功静默，失败喧哗
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Optional


class HookPoint(Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_LLM_CALL = "pre_llm_call"
    POST_LLM_CALL = "post_llm_call"
    ON_ERROR = "on_error"
    ON_TASK_COMPLETE = "on_task_complete"


@dataclass
class HookContext:
    """Hook 执行上下文"""
    requirement_id: int
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None
    state: dict = field(default_factory=dict)


class HookManager:
    """
    Hook 管理器 —— 生命周期事件触发检查

    原则：成功静默，失败喧哗。
    Hook 检查通过时不返回任何信息，失败时才将错误信息塞回 Agent Loop。
    """

    def __init__(self):
        self._hooks: dict[HookPoint, list[callable]] = {
            hp: [] for hp in HookPoint
        }

    def register(self, point: HookPoint, hook: callable):
        """注册 Hook 函数"""
        self._hooks[point].append(hook)

    def trigger(self, point: HookPoint, ctx: HookContext) -> list[str]:
        """
        触发指定生命周期的所有 Hook

        Returns:
            失败信息列表（空列表表示全部通过）
        """
        failures = []
        for hook in self._hooks[point]:
            try:
                result = hook(ctx)
                if result:  # 非空 = 检查失败
                    failures.append(result)
            except Exception as e:
                failures.append(f"Hook [{hook.__name__}] 异常: {e}")
        return failures

    def get_hooks(self, point: HookPoint) -> list:
        return self._hooks.get(point, [])


def create_default_hook_manager() -> HookManager:
    """创建并初始化默认 HookManager"""
    from harness.constraints.checks import register_all_hooks

    manager = HookManager()
    register_all_hooks(manager)
    return manager
