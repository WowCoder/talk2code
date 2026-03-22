# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义
定义多智能体协同的状态图和执行流程
"""

from typing import TypedDict, List, Annotated, Optional
from langgraph.graph import StateGraph, END

from agents.state import AgentState
from agents.nodes import (
    researcher_node,
    product_manager_node,
    architect_node,
    engineer_node
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ==================== 条件分支函数 ====================

def should_proceed_to_engineer(state: AgentState) -> str:
    """
    判断是否进入工程师节点

    策略：
    - 如果架构师失败，可以选择重试或使用 fallback
    - 当前实现：总是继续，工程师有 fallback 机制
    """
    # 检查架构师是否成功
    architect_success = state.get('metadata', {}).get('architect_success', False)

    if architect_success:
        return "to_engineer"
    else:
        # 架构师失败，但仍然继续（工程师会使用 fallback）
        # 这里可以改为 "retry_architect" 实现自动重试
        return "to_engineer"


# ==================== 工作流创建 ====================

def create_workflow() -> StateGraph:
    """
    创建 LangGraph 工作流

    流程：研究员 → 产品经理 → 架构师 → 工程师 → END

    支持：
    - 状态自动累积
    - 条件分支
    - 错误恢复（通过 fallback）
    """
    # 创建图
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("product_manager", product_manager_node)
    workflow.add_node("architect", architect_node)
    workflow.add_node("engineer", engineer_node)

    # 设置入口点
    workflow.set_entry_point("researcher")

    # 添加边（顺序执行）
    workflow.add_edge("researcher", "product_manager")
    workflow.add_edge("product_manager", "architect")

    # 条件边（架构师 → 工程师）
    workflow.add_conditional_edges(
        "architect",
        should_proceed_to_engineer,
        {
            "to_engineer": "engineer",
            "retry_architect": "architect"  # 预留重试逻辑
        }
    )

    # 工程师结束后退出
    workflow.add_edge("engineer", END)

    # 编译工作流
    app = workflow.compile()

    logger.info("LangGraph 工作流已创建")
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
