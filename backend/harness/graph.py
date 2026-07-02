# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义（从 agents/workflow.py 迁移到 harness/graph.py）
图结构: team_leader → END
ToolCallLoop 由 requirement_service 在 workflow 完成后显式调用
"""

from langgraph.graph import StateGraph, END

from harness.state.agent_state import AgentState
from harness.instructions.nodes import team_leader_node
from harness.observability.logger import get_logger

logger = get_logger(__name__)


def create_workflow_v2() -> StateGraph:
    """team_leader → END"""
    workflow = StateGraph(AgentState)
    workflow.add_node("team_leader", team_leader_node)
    workflow.set_entry_point("team_leader")
    workflow.add_edge("team_leader", END)
    app = workflow.compile()
    logger.info("LangGraph 工作流 v2 已创建 (team_leader → END, ToolCallLoop 在外部执行)")
    return app


def create_workflow() -> StateGraph:
    return create_workflow_v2()


_workflow_instance = None


def get_workflow() -> StateGraph:
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = create_workflow_v2()
    return _workflow_instance
