# -*- coding: utf-8 -*-
"""
HarnessContext —— 请求级 harness 组件访问

解决 LangGraph 节点间组件传递问题：
metadata 在节点返回值中会被整体替换，导致 _tool_loop 等丢失，
因此通过本模块提供双路径访问（metadata + ContextVar）。

使用 contextvars.ContextVar 确保每个请求/线程拥有独立的上下文，
避免并发请求间的状态污染。
"""

import contextvars

# ContextVar 存储（每个异步/线程上下文独立）
_ctx_tool_loop: contextvars.ContextVar = contextvars.ContextVar('_tool_loop', default=None)
_ctx_role_executor: contextvars.ContextVar = contextvars.ContextVar('_role_executor', default=None)
_ctx_role_registry: contextvars.ContextVar = contextvars.ContextVar('_role_registry', default=None)
_ctx_workspace: contextvars.ContextVar = contextvars.ContextVar('_workspace', default=None)


def set_component(key: str, value):
    """设置单个组件（向后兼容接口）"""
    ctx_map = {
        "_tool_loop": _ctx_tool_loop,
        "_role_executor": _ctx_role_executor,
        "_role_registry": _ctx_role_registry,
        "_workspace": _ctx_workspace,
    }
    if key in ctx_map:
        ctx_map[key].set(value)


def get_component(key: str):
    """获取单个组件（向后兼容接口）"""
    ctx_map = {
        "_tool_loop": _ctx_tool_loop,
        "_role_executor": _ctx_role_executor,
        "_role_registry": _ctx_role_registry,
        "_workspace": _ctx_workspace,
    }
    ctx = ctx_map.get(key)
    return ctx.get() if ctx else None


def set_all(tool_loop=None, role_executor=None, role_registry=None, workspace=None):
    """批量设置组件"""
    if tool_loop is not None:
        _ctx_tool_loop.set(tool_loop)
    if role_executor is not None:
        _ctx_role_executor.set(role_executor)
    if role_registry is not None:
        _ctx_role_registry.set(role_registry)
    if workspace is not None:
        _ctx_workspace.set(workspace)


def get_tool_loop(state=None):
    """从 state metadata 或 ContextVar 获取 tool_loop"""
    if state:
        tl = state.get("metadata", {}).get("_tool_loop")
        if tl is not None:
            return tl
    return _ctx_tool_loop.get()


def get_role_executor(state=None):
    """从 state metadata 或 ContextVar 获取 role_executor"""
    if state:
        re_ = state.get("metadata", {}).get("_role_executor")
        if re_ is not None:
            return re_
    return _ctx_role_executor.get()


def get_role_registry(state=None):
    """从 state metadata 或 ContextVar 获取 role_registry"""
    if state:
        rr = state.get("metadata", {}).get("_role_registry")
        if rr is not None:
            return rr
    return _ctx_role_registry.get()


def get_workspace(state=None):
    """从 state metadata 或 ContextVar 获取 workspace"""
    if state:
        ws = state.get("metadata", {}).get("_workspace")
        if ws is not None:
            return ws
    return _ctx_workspace.get()


def clear_all():
    """清除当前上下文中的所有组件（用于请求结束后的清理）"""
    _ctx_tool_loop.set(None)
    _ctx_role_executor.set(None)
    _ctx_role_registry.set(None)
    _ctx_workspace.set(None)
