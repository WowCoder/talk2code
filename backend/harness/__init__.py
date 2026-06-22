# -*- coding: utf-8 -*-
"""
Agent Harness 6 层架构实现

Layer 1 - Instructions:   动态上下文组装、上下文压缩、提示词模板、Craft/Skill 加载、智能体节点
Layer 2 - Tools:          工具注册表、文件操作、代码验证、Web 工具
Layer 3 - Environment:    权限管理、沙箱执行、会话隔离
Layer 4 - State:          WorkspaceFS、Git 版本化、长期记忆、断点恢复
Layer 5 - Constraints:    Hook 系统、代码质量检查、安全检查、Craft 强制执行
Layer 6 - Observability:  链路追踪、成本统计、SSE 事件管理、日志系统

Runtime:                  ToolCallLoop (harness.runtime) —— ReAct 工具调用循环
Graph:                    LangGraph 工作流定义 (harness.graph)
"""

from harness.runtime import ToolCallLoop
from harness.graph import create_workflow, create_workflow_v2, get_workflow
from harness.instructions.nodes import planner_node, tool_coder_node

__all__ = [
    'ToolCallLoop',
    'create_workflow',
    'create_workflow_v2',
    'get_workflow',
    'planner_node',
    'tool_coder_node',
    'create_harness',
]


def create_harness(requirement_id: int, user_id: int, db_session=None):
    """创建完整的 Harness 实例，初始化所有 6 层。

    Args:
        requirement_id: 需求 ID
        user_id: 用户 ID
        db_session: 可选的 SQLAlchemy session；注入后记忆/检查点/追踪将持久化到 DB，
                    不注入时退化为内存（保持向后兼容）。
    """
    from harness.state.workspace import WorkspaceFS
    from harness.state.versioning import GitVersioning
    from harness.state.checkpoint import CheckpointManager
    from harness.state.memory_store import MemoryStore
    from harness.tools.registry import create_tool_registry
    from harness.constraints.hooks import create_default_hook_manager
    from harness.environment.permissions import PermissionManager
    from harness.instructions.assembler import ContextAssembler
    from harness.instructions.compactor import ContextCompactor
    from harness.observability.tracer import Tracer
    from harness.observability.cost import CostTracker
    from llm.client import get_client

    workspace = WorkspaceFS(user_id, requirement_id)
    git = GitVersioning(workspace)
    tools = create_tool_registry()
    hooks = create_default_hook_manager()
    permissions = PermissionManager()
    checkpoint = CheckpointManager(db_session=db_session)
    memory = MemoryStore(db_session=db_session, llm_client=get_client())
    assembler = ContextAssembler(memory_store=memory)
    compactor = ContextCompactor()
    cost_tracker = CostTracker()
    tracer = Tracer(db_session=db_session, cost_tracker=cost_tracker)

    return {
        "workspace": workspace,
        "git": git,
        "tools": tools,
        "hooks": hooks,
        "permissions": permissions,
        "checkpoint": checkpoint,
        "memory": memory,
        "assembler": assembler,
        "compactor": compactor,
        "tracer": tracer,
        "cost_tracker": cost_tracker,
    }
