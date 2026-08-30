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


# ==================== 路由函数 ====================

def _get_max_repair_rounds(state: AgentState) -> int:
    """coder 回环修复轮次上限（config 单一来源，prompt 文案同步注入）

    simple=0（无修复循环），standard=config.CODER_MAX_REPAIR_ROUNDS（默认 2）。
    每轮修复后 verify 会重新评估，PASS 则正常结束。
    """
    from config import settings
    complexity = state.get("metadata", {}).get("complexity", "standard")
    rounds = {
        "simple": 0,
        "standard": settings.CODER_MAX_REPAIR_ROUNDS,
    }
    return rounds.get(complexity, settings.CODER_MAX_REPAIR_ROUNDS)


def _get_max_defect_repair_rounds() -> int:
    """小上下文定向修复轮次上限（config 单一来源）"""
    from config import settings
    return settings.DEFECT_REPAIR_MAX_ROUNDS


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


def route_after_verify(state: AgentState) -> str:
    """Verify 完成后的路由决策（按缺陷类别路由，审查报告 Phase 3.2）

    PASS → 结束
    架构类缺陷（模块加载/CDN/文件缺失/入口断裂）→ coder 携根因卡片重构
      （defect_repair 被禁止新建/重构文件，结构上修不了架构问题）
    局部确定性缺陷 → defect_repair 小上下文定向修复（成本低，优先消耗）
    其他 NEEDS_WORK → 重新进入 coder（QA 反馈已写入 dialogue_history）
    各路径预算独立；全部耗尽后 done，由服务层交付门禁决定最终状态
    """
    if state.get("verify_passed", False):
        return "done"

    repair_count = state.get("metadata", {}).get("repair_count", 0)
    max_rounds = _get_max_repair_rounds(state)

    # 路径 0: 架构类缺陷 → 回 coder 重构（根因卡片已由 verify 注入对话）
    architectural_defects = state.get("architectural_defects") or []
    if architectural_defects:
        complexity = state.get("metadata", {}).get("complexity", "?")
        if repair_count < max_rounds:
            logger.info(
                f"[Graph] {len(architectural_defects)} 个架构类缺陷"
                f" ({[d.get('type') for d in architectural_defects]}) "
                f"→ 回 coder 重构 (第 {repair_count + 1}/{max_rounds} 轮, "
                f"complexity={complexity})"
            )
            return "coder"
        logger.warning(
            f"[Graph] 架构类缺陷未修复且 coder 预算耗尽 "
            f"(repair_count={repair_count}/{max_rounds})，交由交付门禁处理"
        )
        return "done"

    # 路径 1: 局部确定性缺陷 → 小上下文定向修复（不进 ToolCallLoop）
    smoke_defects = state.get("smoke_defects") or []
    defect_repair_count = state.get("metadata", {}).get("defect_repair_count", 0)
    max_defect_rounds = _get_max_defect_repair_rounds()
    if smoke_defects and defect_repair_count < max_defect_rounds:
        logger.info(
            f"[Graph] 检测到 {len(smoke_defects)} 个局部确定性缺陷 "
            f"(类型: {[d.get('type') for d in smoke_defects]})，"
            f"进入小上下文定向修复 (第 {defect_repair_count + 1}/{max_defect_rounds} 轮)"
        )
        return "defect_repair"

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

    # Coder → verify / 终止（LLM 故障、用户取消不进评估）
    workflow.add_conditional_edges(
        "coder",
        route_after_coder,
        {
            "verify": "verify",
            "abort": END,
        }
    )

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


def route_after_coder(state: AgentState) -> str:
    """coder 之后的路由

    - LLM 调用失败（llm_error）/ 用户取消 → 直接终止。
      此前 coder→verify 是无条件边，基础设施故障会导致 verify
      对空工作区跑"代码评估"，烧光修复预算后给出误导性结论。
    - 其余情况（正常完成 / 编码异常）→ verify 兜底校验
    """
    step = state.get("current_step", "")
    if step in ("llm_error", "cancelled"):
        return "abort"
    return "verify"


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

    workflow.add_conditional_edges(
        "coder",
        route_after_coder,
        {
            "verify": "verify",
            "abort": END,
        }
    )

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
