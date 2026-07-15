# -*- coding: utf-8 -*-
"""
AgentState —— 从 backend/agents/state.py 迁移并增强
"""

from enum import Enum
from typing import TypedDict, List, Optional, Annotated
from operator import add


class TaskType(str, Enum):
    """子任务类型枚举

    用于 TeamLeader 产出的 plan.tasks 中每个任务的 type 字段。
    - research: 信息收集（轻量 LLM 调用，无工具权限）
    - code: 编码实现（ToolCallLoop 完整流程）
    - review: 代码审查（对已完成文件做审查）
    """
    RESEARCH = "research"
    CODE = "code"
    REVIEW = "review"


class Subtask(TypedDict, total=False):
    """Agent 委派子任务定义

    TeamLeader plan.tasks 中每个任务项可携带 type 字段。
    未指定 type 时默认为 code（向后兼容）。
    """
    type: str           # TaskType 值，默认 "code"
    description: str    # 任务描述
    file: str           # 目标文件名（code/review 类型）
    exports: List[str]  # 对外导出
    imports: List[str]  # 依赖
    dependencies: List[str]  # 前置任务（文件名列表）


class AgentState(TypedDict, total=False):
    """智能体工作流状态（TeamLeader + FrontendEngineer 范式）"""

    # 基础信息
    requirement_id: int
    requirement_content: str
    user_id: int

    # TeamLeader 输出的结构化设计
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

    # 对话历史（使用 add reducer，节点返回的列表会追加而非替换）
    dialogue_history: Annotated[List[dict], add]

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

    # 意图分类结果 (quick/search/task/ambiguous)
    intent: Optional[str]

    # === 多角色协作字段（二期） ===
    # 角色执行历史 [{role_name, success, content, ...}]
    role_history: Optional[List[dict]]

    # 各角色产出 {role_name: output_text}
    role_outputs: Optional[dict]

    # === 任务分解字段（三期：逐文件编码） ===
    # 文件级任务列表（按依赖顺序）
    # [{file, description, exports, imports, dependencies}]
    tasks: Optional[List[dict]]

    # 文件间接口契约 {file_path: {exports: {name: signature}}}
    interfaces: Optional[dict]

    # 拓扑排序后的实施顺序
    implementation_order: Optional[List[str]]

    # 编码阶段收集的错误（用于后续文件的上下文注入）
    code_errors: Optional[List[str]]

    # QA 审查通过标记
    qa_passed: Optional[bool]

    # Tester SPEC 验收测试通过标记
    tester_passed: Optional[bool]

    # SummarizeCode 通过标记
    summarize_passed: Optional[bool]

    # Verify （Fresh-Context Evaluator）通过标记
    # 由 verify_node 设置，route_after_verify 和 _process_final_state 消费
    verify_passed: Optional[bool]

    # 修复轮次计数
    repair_count: Optional[int]
