# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义
定义多智能体协同的状态图和执行流程

LangChain 1.x 兼容验证:
- 使用 from langgraph.graph import StateGraph, END (正确)
- 无弃用的 langchain.schema 导入 (正确)
- AgentState 使用 TypedDict + Annotated[operator.add] 模式 (正确)
"""

from typing import TypedDict, List, Annotated, Optional
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage

from agents.state import AgentState
from agents.nodes import planner_node, engineer_node
from utils.logger import get_logger

logger = get_logger(__name__)


# ==================== 条件分支函数 ====================

def should_retry_coder(state: AgentState) -> str:
    """
    判断是否重试工程师节点

    策略：
    - 如果工程师失败且重试次数未达上限，则重试
    - 否则退出
    """
    retry_count = state.get('retry_count', 0)
    engineer_success = state.get('metadata', {}).get('engineer_success', False)

    if not engineer_success and retry_count < 2:
        return "retry_coder"
    return "exit"


# ==================== 工作流创建 ====================

def create_workflow() -> StateGraph:
    """
    创建 LangGraph 工作流

    流程：Planner → Coder → END

    支持：
    - 状态自动累积
    - 条件分支（Coder 失败自动重试）
    - 错误恢复（通过 fallback）
    """
    # 创建图
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("planner", planner_node)
    workflow.add_node("coder", engineer_node)

    # 设置入口点
    workflow.set_entry_point("planner")

    # Planner → Coder
    workflow.add_edge("planner", "coder")

    # Coder 条件边
    workflow.add_conditional_edges(
        "coder",
        should_retry_coder,
        {
            "retry_coder": "coder",
            "exit": END
        }
    )

    # 编译工作流
    app = workflow.compile()

    logger.info("LangGraph 工作流已创建 (planner → coder)")
    return app


# ==================== 全局单例 ====================

# 延迟初始化，避免循环导入
_workflow_instance = None


def get_workflow() -> StateGraph:
    """获取工作流单例"""
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = create_workflow()
    return _workflow_instance
