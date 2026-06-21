# -*- coding: utf-8 -*-
"""
AgentState —— 从 backend/agents/state.py 迁移并增强
"""

from typing import TypedDict, List, Optional


class AgentState(TypedDict, total=False):
    """智能体工作流状态（Planner + ReAct Coder 范式）"""

    # 基础信息
    requirement_id: int
    requirement_content: str
    user_id: int

    # Planner 输出的结构化设计
    plan: Optional[dict]

    # 当前步骤
    current_step: str

    # Coder 生成的代码文件
    code_files: Optional[List[dict]]

    # 验证结果
    validation_result: Optional[dict]

    # 重试计数
    retry_count: int

    # 错误信息
    error: Optional[str]

    # 对话历史
    dialogue_history: List[dict]

    # 元数据
    metadata: dict

    # === 新增字段 ===
    # 工具调用计数器
    tool_call_count: int

    # 连续无进展计数器
    no_progress_count: int

    # 上次工作区文件列表（用于检测进展）
    last_file_list: Optional[List[str]]

    # Hook 检查失败次数追踪 {hook_name: fail_count}
    hook_failures: dict

    # 视觉风格偏好
    visual_style: Optional[str]
