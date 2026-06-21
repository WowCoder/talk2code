# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义（从 agents/workflow.py 迁移到 harness/graph.py）
图结构: planner → END
ToolCallLoop 由 requirement_service 在 workflow 完成后显式调用
"""

from langgraph.graph import StateGraph, END

from harness.state.agent_state import AgentState
from harness.instructions.nodes import planner_node
from harness.observability.logger import get_logger

logger = get_logger(__name__)


def create_workflow_v2() -> StateGraph:
    """planner → END"""
    workflow = StateGraph(AgentState)
    workflow.add_node("planner", planner_node)
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", END)
    app = workflow.compile()
    logger.info("LangGraph 工作流 v2 已创建 (planner → END, ToolCallLoop 在外部执行)")
    return app


def create_workflow() -> StateGraph:
    return create_workflow_v2()


_workflow_instance = None


def get_workflow() -> StateGraph:
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = create_workflow_v2()
    return _workflow_instance
