# -*- coding: utf-8 -*-
"""
LangGraph 工作流定义（v5: 简化 3 节点编排，QA 反馈作为对话注入）

图结构:

team_leader (需求分析 + 完整 Plan → 初始化 CompletionContract)
    ↓
[conditional: route_after_tl]
    ├── clarify → END
    └── → coder (统一编码节点，内部根据 complexity 选择策略)
           ↓
         verify (Fresh-Context Evaluator + 通用冒烟测试)
           ↓
[conditional: route_after_verify]
    ├── PASS → END
    ├── 冒烟确定性缺陷 → defect_repair (小上下文定向修复) → verify (最多 2 轮)
    └── 其他 NEEDS_WORK → QA 反馈注入 dialogue_history → coder → verify → END
                                      ↑ (最多 2 轮修复，同一 ToolCallLoop 上下文)
"""

from langgraph.graph import StateGraph, END

from harness.state.agent_state import AgentState
from harness.instructions.nodes import (
    team_leader_node, coder_node, verify_node, defect_repair_node,
)
from harness.observability.logger import get_logger

logger = get_logger(__name__)


# 小上下文定向修复最大轮数（独立于 coder 修复轮次：单次小 LLM 调用，成本低）
MAX_DEFECT_REPAIR_ROUNDS = 2


# ==================== 路由函数 ====================

def route_after_tl(state: AgentState) -> str:
    """TeamLeader 完成后的路由决策"""
    step = state.get("current_step", "")
    if step == "needs_clarification":
        return "clarify"

    # TL 失败时不应进入 coder 用空 plan 编码，直接结束由服务层决定重试
    if step == "team_leader_failed":
        return "done"

    # 其他所有情况 → coder 节点
    return "coder"


def _get_max_repair_rounds(state: AgentState) -> int:
    """根据复杂度计算最大修复轮次

    simple=0（无修复循环），standard=2（2轮修复后仍不通过则finished_with_issues）。
    每轮修复后 verify 会重新评估，PASS 则正常结束。
    """
    complexity = state.get("metadata", {}).get("complexity", "standard")
    rounds = {
        "simple": 0,
        "standard": 2,
    }
    return rounds.get(complexity, 2)


def route_after_verify(state: AgentState) -> str:
    """Verify 完成后的路由决策

    PASS → 结束
    冒烟确定性缺陷 → defect_repair（小上下文定向修复，成本低，优先消耗）
    其他 NEEDS_WORK → 重新进入 coder（QA 反馈已写入 dialogue_history）
    两条修复路径都有独立预算，任一耗尽后落到 coder 预算判断，全部耗尽则强制结束
    """
    if state.get("verify_passed", False):
        return "done"

    # 路径 1: 冒烟确定性缺陷 → 小上下文定向修复（不进 ToolCallLoop）
    smoke_defects = state.get("smoke_defects") or []
    defect_repair_count = state.get("metadata", {}).get("defect_repair_count", 0)
    if smoke_defects and defect_repair_count < MAX_DEFECT_REPAIR_ROUNDS:
        logger.info(
            f"[Graph] 检测到 {len(smoke_defects)} 个冒烟确定性缺陷 "
            f"(类型: {[d.get('type') for d in smoke_defects]})，"
            f"进入小上下文定向修复 (第 {defect_repair_count + 1}/{MAX_DEFECT_REPAIR_ROUNDS} 轮)"
        )
        return "defect_repair"

    repair_count = state.get("metadata", {}).get("repair_count", 0)
    max_rounds = _get_max_repair_rounds(state)
    if repair_count >= max_rounds:
        complexity = state.get("metadata", {}).get("complexity", "?")
        logger.warning(
            f"[Graph] 修复已达上限 {max_rounds} 轮 "
            f"(complexity={complexity}, repair_count={repair_count})，强制结束"
        )
        return "done"
    # 路径 2: NEEDS_WORK → 直接回到 coder（QA findings 已在 dialogue_history 中）
    # coder 在连续上下文中修复，不会丢失之前的编码记忆
    return "coder"


# ==================== 工作流创建 ====================

def create_workflow_v5() -> StateGraph:
    """
    v5: 简化 3 节点编排

    team_leader → coder → verify → (QA 反馈注入 → coder) → END

    与 v4 的关键区别:
    - 移除独立的 repair 节点，QA 反馈直接注入 dialogue_history
    - NEEDS_WORK 时直接回到 coder（连续上下文），而非经过 repair 重置
    - coder 拥有完整工具权限（不受 MAX_ITERATIONS=8 限制）
    - coder 可以自验证（run_preview）和自主决定修复策略
    """
    workflow = StateGraph(AgentState)

    # ---- 添加节点 ----
    workflow.add_node("team_leader", team_leader_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("defect_repair", defect_repair_node)
    workflow.add_node("verify", verify_node)

    # ---- 设置入口 ----
    workflow.set_entry_point("team_leader")

    # ---- 条件路由 ----

    # TeamLeader → clarify (END) / coder
    workflow.add_conditional_edges(
        "team_leader",
        route_after_tl,
        {
            "clarify": END,
            "done": END,
            "coder": "coder",
        }
    )

    # Coder → verify
    workflow.add_edge("coder", "verify")

    # Verify → done / defect_repair (冒烟确定性缺陷) / coder (QA 反馈修复)
    workflow.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "done": END,
            "coder": "coder",
            "defect_repair": "defect_repair",
        }
    )

    # DefectRepair 修复后直接回 verify 重新冒烟验证（不经过完整 coder）
    workflow.add_edge("defect_repair", "verify")

    app = workflow.compile()
    logger.info(
        "LangGraph 工作流 v5 已创建 "
        "(4 节点: team_leader → coder → verify ⇄ defect_repair, QA 反馈作为对话注入)"
    )
    return app


# ==================== 兼容旧接口 ====================

def create_workflow_v4() -> StateGraph:
    """v4 兼容接口（内部调用 v5）"""
    return create_workflow_v5()


def create_workflow_v3() -> StateGraph:
    """v3 兼容接口（内部调用 v5）"""
    return create_workflow_v5()


def create_workflow_v2() -> StateGraph:
    """v2 兼容接口（已废弃，内部调用 v5）"""
    return create_workflow_v5()


def create_workflow_post_plan() -> StateGraph:
    """
    v5 Post-Plan 子图：用户确认 Plan 后，从 coder 节点开始执行

    coder → verify → (QA 反馈注入 → coder) → END
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("coder", coder_node)
    workflow.add_node("defect_repair", defect_repair_node)
    workflow.add_node("verify", verify_node)

    workflow.set_entry_point("coder")

    workflow.add_edge("coder", "verify")

    workflow.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "done": END,
            "coder": "coder",
            "defect_repair": "defect_repair",
        }
    )

    workflow.add_edge("defect_repair", "verify")

    app = workflow.compile()
    logger.info("LangGraph 工作流 Post-Plan v5 已创建 (coder → verify ⇄ defect_repair, QA 反馈注入)")
    return app


def create_workflow() -> StateGraph:
    return create_workflow_v5()


_workflow_instance = None


def get_workflow() -> StateGraph:
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = create_workflow_v5()
    return _workflow_instance
