# -*- coding: utf-8 -*-
"""
LangGraph 工作流状态定义

LangChain 1.x 兼容验证:
- 使用 TypedDict 定义状态 (正确)
- 使用 Annotated[T, operator.add] 进行状态归约 (正确)
- 模式在 langgraph>=1.0.0 中稳定
"""

from typing import TypedDict, List, Annotated, Optional
import operator


class AgentState(TypedDict):
    """
    智能体工作流状态

    所有状态字段在节点执行时自动合并（使用 operator.add）
    """
    # 基础信息
    requirement_id: int
    requirement_content: str

    # 累积的智能体输出（自动追加）
    agent_outputs: Annotated[List[dict], operator.add]

    # 当前步骤（用于 SSE 推送）
    current_step: str

    # 最终代码文件
    code_files: Optional[List[dict]]

    # 错误信息
    error: Optional[str]

    # 对话历史（用于保存到数据库）
    dialogue_history: Annotated[List[dict], operator.add]

    # 元数据
    metadata: dict
