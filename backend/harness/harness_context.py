# -*- coding: utf-8 -*-
"""
HarnessContext —— 模块级 harness 组件缓存

解决 LangGraph 节点间组件传递问题：
metadata 在节点返回值中会被整体替换，导致 _tool_loop 等丢失，
因此通过本模块提供双路径访问（metadata + 模块缓存），
避免循环导入。
"""

# 模块级缓存
_components = {
    "_tool_loop": None,
    "_role_executor": None,
    "_role_registry": None,
    "_workspace": None,
}


def set_component(key: str, value):
    """设置单个组件"""
    if key in _components:
        _components[key] = value


def get_component(key: str):
    """获取单个组件"""
    return _components.get(key)


def set_all(tool_loop=None, role_executor=None, role_registry=None, workspace=None):
    """批量设置组件"""
    if tool_loop is not None:
        _components["_tool_loop"] = tool_loop
    if role_executor is not None:
        _components["_role_executor"] = role_executor
    if role_registry is not None:
        _components["_role_registry"] = role_registry
    if workspace is not None:
        _components["_workspace"] = workspace


def get_tool_loop(state=None):
    """从 state metadata 或模块缓存获取 tool_loop"""
    if state:
        tl = state.get("metadata", {}).get("_tool_loop")
        if tl is not None:
            return tl
    return _components.get("_tool_loop")


def get_role_executor(state=None):
    """从 state metadata 或模块缓存获取 role_executor"""
    if state:
        re_ = state.get("metadata", {}).get("_role_executor")
        if re_ is not None:
            return re_
    return _components.get("_role_executor")


def get_role_registry(state=None):
    """从 state metadata 或模块缓存获取 role_registry"""
    if state:
        rr = state.get("metadata", {}).get("_role_registry")
        if rr is not None:
            return rr
    return _components.get("_role_registry")


def get_workspace(state=None):
    """从 state metadata 或模块缓存获取 workspace"""
    if state:
        ws = state.get("metadata", {}).get("_workspace")
        if ws is not None:
            return ws
    return _components.get("_workspace")
