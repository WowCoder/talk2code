# -*- coding: utf-8 -*-
"""
PermissionManager —— 工具调用权限分级管理（单例模式）

权限模型：
Level 0 (只读): 自动放行
Level 1 (写入): 首次请求时一次性授权，后续自动放行
Level 2 (执行): 每次调用都需要用户审批
"""

import threading
from enum import Enum


class PermissionLevel(Enum):
    READ = 0
    WRITE = 1
    EXECUTE = 2


class PermissionResult:
    ALLOW = "allow"
    NEEDS_APPROVAL = "needs_approval"
    DENIED = "denied"


class PermissionManager:
    """
    工具调用权限管理器（单例）。

    确保 app.py 审批端点创建的实例和 ToolCallLoop 使用的是同一实例，
    用户的审批决定能正确生效。
    """

    _instance: 'PermissionManager | None' = None
    _lock = threading.Lock()

    def __new__(cls) -> 'PermissionManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._write_granted: dict[int, bool] = {}  # requirement_id → granted

    def check(self, requirement_id: int, tool_name: str, permission: str) -> str:
        level = self._get_level(permission)
        if level == PermissionLevel.READ:
            return PermissionResult.ALLOW
        if level == PermissionLevel.WRITE:
            if self._write_granted.get(requirement_id, False):
                return PermissionResult.ALLOW
            return PermissionResult.NEEDS_APPROVAL
        if level == PermissionLevel.EXECUTE:
            return PermissionResult.NEEDS_APPROVAL
        return PermissionResult.DENIED

    def grant(self, requirement_id: int, level: str):
        """用户授权后记录"""
        if level in ("write", "1"):
            self._write_granted[requirement_id] = True

    def revoke(self, requirement_id: int):
        """撤销授权"""
        self._write_granted.pop(requirement_id, None)

    def _get_level(self, permission: str) -> PermissionLevel:
        if permission == "read":
            return PermissionLevel.READ
        elif permission == "write":
            return PermissionLevel.WRITE
        elif permission == "execute":
            return PermissionLevel.EXECUTE
        return PermissionLevel.READ
