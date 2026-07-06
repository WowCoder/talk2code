# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义（三期：多节点编排）

图结构（按复杂度路由）：

team_leader (增强：任务分解 + 依赖排序)
    ↓
[conditional: complexity]
    ├── clarify → END
    ├── XS → simple_coder → END
    ├── S  → simple_coder → END
    └── M/L → pm → architect → file_by_file_coder → qa_reviewer
                  ↓
                  summarize → END
                  ↑ pass        ↓ fail
                  └── repair ───┘

ToolCallLoop 由各编码节点内部调用。
Harness 组件（tool_loop/role_executor 等）通过 harness_context 模块级缓存传递，
解决 LangGraph metadata 被节点整体替换导致组件丢失的问题。
"""

from langgraph.graph import StateGraph, END

from harness.state.agent_state import AgentState
from harness.instructions.nodes import (
    team_leader_node, tool_coder_node,
    pm_node, architect_node, qa_node, repair_node,
)
from harness.instructions.simple_coder import simple_coder_node
from harness.instructions.file_coder import file_by_file_coder_node
from harness.instructions.summarize import summarize_node
from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== 路由函数 ====================

def route_after_team_leader(state: AgentState) -> str:
    """TeamLeader 完成后的路由决策"""
    step = state.get("current_step", "")
    if step == "needs_clarification":
        return "clarify"

    complexity = state.get("metadata", {}).get("complexity", "S")
    complexity_lower = complexity.lower() if isinstance(complexity, str) else "s"

    if complexity_lower in ("xs", "s"):
        return complexity_lower
    elif complexity_lower in ("m", "l"):
        return "m"  # M/L 都走 pm → architect → file_coder
    else:
        logger.warning(f"[Graph] 未知复杂度: {complexity}，默认走 simple_coder")
        return "s"


def route_after_pm(state: AgentState) -> str:
    """PM 完成后的路由"""
    complexity = state.get("metadata", {}).get("complexity", "S")
    complexity_lower = complexity.lower() if isinstance(complexity, str) else "s"

    if complexity_lower == "s":
        return "simple_coder"  # S 复杂度跳过 Architect
    else:
        return "architect"  # M/L 继续到 Architect


def route_after_file_coder(state: AgentState) -> str:
    """逐文件编码后的路由"""
    step = state.get("current_step", "")
    if step == "coding_error" or state.get("error"):
        return "repair"
    return "qa"


def route_after_simple_coder(state: AgentState) -> str:
    """简单编码后的路由"""
    step = state.get("current_step", "")
    if step == "coding_error" or state.get("error"):
        return "repair"
    return "done"


def route_after_qa(state: AgentState) -> str:
    """QA 审查后的路由"""
    if state.get("qa_passed", True):
        return "summarize"
    else:
        repair_count = state.get("metadata", {}).get("repair_count", 0)
        if repair_count >= 3:
            logger.warning("[Graph] 修复已达上限，强制通过")
            return "summarize"
        return "repair"


def route_after_repair(state: AgentState) -> str:
    """修复后的路由"""
    repair_count = state.get("metadata", {}).get("repair_count", 0)
    if repair_count >= 3:
        logger.warning(f"[Graph] 修复已达 {repair_count} 轮，跳过 QA")
        return "summarize"
    return "qa"


def route_after_summarize(state: AgentState) -> str:
    """SummarizeCode 审查后的路由"""
    if state.get("summarize_passed", True):
        return "done"
    else:
        repair_count = state.get("metadata", {}).get("repair_count", 0)
        if repair_count >= 3:
            logger.warning("[Graph] 修复已达上限，强制结束")
            return "done"
        return "repair"


# ==================== 工作流创建 ====================

def create_workflow_v3() -> StateGraph:
    """
    三期：多节点编排图。

    team_leader → [conditional] → simple_coder / pm → architect → file_coder → qa → summarize → END
                                                                                ↑ fail          ↓ fail
                                                                                └── repair ─────┘
    """
    workflow = StateGraph(AgentState)

    # ---- 添加所有节点 ----
    workflow.add_node("team_leader", team_leader_node)
    workflow.add_node("pm", pm_node)
    workflow.add_node("architect", architect_node)
    workflow.add_node("simple_coder", simple_coder_node)
    workflow.add_node("file_by_file_coder", file_by_file_coder_node)
    workflow.add_node("qa_reviewer", qa_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("repair", repair_node)

    # ---- 设置入口 ----
    workflow.set_entry_point("team_leader")

    # ---- 条件路由 ----

    # TeamLeader → clarify / xs / s / m
    workflow.add_conditional_edges(
        "team_leader",
        route_after_team_leader,
        {
            "clarify": END,
            "xs": "simple_coder",
            "s": "simple_coder",
            "m": "pm",
            # "l" also goes to "m" path (see route_after_team_leader)
        }
    )

    # PM → simple_coder (S) / architect (M/L)
    workflow.add_conditional_edges(
        "pm",
        route_after_pm,
        {
            "simple_coder": "simple_coder",
            "architect": "architect",
        }
    )

    # Architect → file_by_file_coder (固定路径)
    workflow.add_edge("architect", "file_by_file_coder")

    # FileByFileCoder → qa / repair
    workflow.add_conditional_edges(
        "file_by_file_coder",
        route_after_file_coder,
        {
            "qa": "qa_reviewer",
            "repair": "repair",
        }
    )

    # SimpleCoder → done / repair
    workflow.add_conditional_edges(
        "simple_coder",
        route_after_simple_coder,
        {
            "done": END,
            "repair": "repair",
        }
    )

    # QA → summarize / repair
    workflow.add_conditional_edges(
        "qa_reviewer",
        route_after_qa,
        {
            "summarize": "summarize",
            "repair": "repair",
        }
    )

    # Repair → qa / summarize
    workflow.add_conditional_edges(
        "repair",
        route_after_repair,
        {
            "qa": "qa_reviewer",
            "summarize": "summarize",
        }
    )

    # Summarize → done / repair
    workflow.add_conditional_edges(
        "summarize",
        route_after_summarize,
        {
            "done": END,
            "repair": "repair",
        }
    )

    app = workflow.compile()
    logger.info("LangGraph 工作流 v3 已创建 (多节点编排: team_leader → [simple|multi-role] → summarize → END)")
    return app


# ==================== 兼容旧接口 ====================

def create_workflow_v2() -> StateGraph:
    """v2 兼容接口（已废弃，内部调用 v3）"""
    return create_workflow_v3()


def create_workflow() -> StateGraph:
    return create_workflow_v3()


_workflow_instance = None


def get_workflow() -> StateGraph:
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = create_workflow_v3()
    return _workflow_instance
