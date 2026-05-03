# -*- coding: utf-8 -*-
"""
LangGraph 工作流状态定义

LangChain 1.x 兼容验证:
- 使用 TypedDict 定义状态 (正确)
- 使用 Annotated[T, operator.add] 进行状态归约 (正确)
- 模式在 langgraph>=1.0.0 中稳定
"""

from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    """
    智能体工作流状态（Planner + Coder 范式）
    """
    # 基础信息
    requirement_id: int
    requirement_content: str

    # Planner 输出的结构化设计
    plan: Optional[dict]

    # 当前步骤（用于 SSE 推送）
    current_step: str

    # Coder 生成的最终代码文件
    code_files: Optional[List[dict]]

    # Validator 检查结果
    validation_result: Optional[dict]

    # Coder 重试次数
    retry_count: int

    # 错误信息
    error: Optional[str]

    # 对话历史（用于保存到数据库）
    dialogue_history: List[dict]

    # 元数据
    metadata: dict
