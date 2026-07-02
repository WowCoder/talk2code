# -*- coding: utf-8 -*-
"""
RoleOrchestrator —— 多角色编排引擎

根据复杂度自动编排角色执行序列：
- XS/S: 单角色 (FrontendEngineer)，等同当前行为
- M/L:  多角色串行 (PM → Architect → Engineer → QA)，含修复循环
"""

from typing import Optional

from harness.roles import Role, RoleResult, RoleRegistry
from harness.roles.definitions import create_role_registry, COMPLEXITY_ROUTE
from harness.instructions.role_executor import RoleExecutor
from harness.state.agent_state import AgentState
from harness.observability.logger import get_logger

logger = get_logger(__name__)


class RoleOrchestrator:
    """多角色编排引擎

    核心职责：
    1. 读取 complexity，查 COMPLEXITY_ROUTE 表确定角色序列
    2. 串行执行各角色（前序产出作为后续角色的上下文）
    3. QA 不通过时触发 Engineer → QA 修复循环
    4. XS/S 复杂度直接路由到 Engineer（不增加额外开销）
    """

    # QA → 修复 最大循环轮数
    MAX_QA_REPAIR_LOOPS = 2

    def __init__(self, workspace=None, sse_reporter=None,
                 tools=None, tool_loop_factory=None):
        self.workspace = workspace
        self.sse = sse_reporter
        self.tools = tools
        self.tool_loop_factory = tool_loop_factory  # 创建 ToolCallLoop 的工厂函数

        self.registry = create_role_registry()
        self.executor = RoleExecutor(
            workspace=workspace,
            sse_reporter=sse_reporter,
            tools=tools,
        )

    def execute(self, state: AgentState) -> AgentState:
        """
        执行多角色编排。

        Args:
            state: TeamLeader 完成后的 AgentState

        Returns:
            更新后的 AgentState
        """
        complexity = state.get("metadata", {}).get("complexity", "S")
        route = COMPLEXITY_ROUTE.get(complexity, ["FrontendEngineer"])

        logger.info(f"[Orchestrator] 复杂度={complexity}, 角色序列={route}")

        # 初始化角色追踪
        state.setdefault("role_history", [])
        state.setdefault("role_outputs", {})

        req_id = state["requirement_id"]

        # ---- XS/S: 单角色，省略 TeamLeader 决策 ----
        if complexity in ("XS", "S"):
            return self._run_simple_flow(state, route, complexity)

        # ---- M/L: 完整多角色流程，含 TeamLeader 决策 ----
        return self._run_full_flow(state, route, complexity, req_id)

    def _run_simple_flow(self, state, route, complexity):
        """XS/S 简化流程：跳过 TeamLeader/PM/Architect，直接 Engineer"""
        logger.info(f"[Orchestrator] 简化流程: 直接 FrontendEngineer")

        # 构建精简任务包
        requirement = state.get("requirement_content", "")
        plan = state.get("plan", {})

        if complexity == "XS":
            task = f"创建一个简单的页面：{requirement}。根据需求自由创建文件，不需要复杂结构。"
        else:
            task = f"创建以下前端应用：{requirement}。按标准结构创建 index.html + style.css + script.js。"

        if plan:
            features = plan.get("features", []) if isinstance(plan, dict) else []
            if features:
                task += f"\n功能要点：{', '.join(str(f) for f in features)}"

        # 执行 Engineer
        engineer_role = self.registry.get("FrontendEngineer")
        result = self.executor.execute(engineer_role, state, task_package=task)

        self._record_result(state, result)
        return state

    def _run_full_flow(self, state, route, complexity, req_id):
        """M/L 完整流程：TeamLeader → 角色序列 → QA 修复循环"""
        requirement = state.get("requirement_content", "")
        plan = state.get("plan", {})

        # 构建初始上下文（用户需求 + Plan）
        context = f"## 用户需求\n{requirement}"
        if plan:
            plan_text = self._format_plan(plan)
            context += f"\n\n## TeamLeader 分析结果\n{plan_text}"

        # ---- 串行执行角色序列 ----
        for role_name in route:
            role = self.registry.get(role_name)
            if not role:
                logger.warning(f"[Orchestrator] 未知角色: {role_name}，跳过")
                continue

            # 构建该角色的任务包
            task_package = self._build_task_package(
                role_name, state, context, complexity
            )

            # SSE 推送
            if self.sse:
                self.sse.dialogue(
                    req_id, "agent", "TeamLeader",
                    f"派发任务给 {role.display_name} ({role_name})",
                    "processing"
                )

            # 执行角色
            result = self.executor.execute(
                role, state,
                task_package=task_package,
                extra_context=self._get_role_context(state, role_name),
            )

            self._record_result(state, result)

            if not result.success and role_name == "FrontendEngineer":
                # Engineer 失败时保留错误但不中断
                state["error"] = result.error
                logger.warning(f"[Orchestrator] {role_name} 执行异常但继续: {result.error}")

            # QA 修复循环
            if role_name == "QAReviewer" and result.structured_output:
                qa_data = result.structured_output
                passed = qa_data.get("passed", True)
                rating = qa_data.get("overall_rating", 7)

                if not passed or rating < 6:
                    state = self._qa_repair_loop(
                        state, qa_data, req_id
                    )

        return state

    def _qa_repair_loop(self, state, qa_data, req_id):
        """QA → Engineer 修复循环"""
        for loop_i in range(self.MAX_QA_REPAIR_LOOPS):
            issues = qa_data.get("critical_issues", [])
            suggestions = qa_data.get("suggestions", [])

            if not issues:
                break

            logger.info(f"[Orchestrator] QA 修复循环 {loop_i + 1}: {len(issues)} 个问题")

            if self.sse:
                self.sse.dialogue(
                    req_id, "agent", "TeamLeader",
                    f"QA 发现 {len(issues)} 个问题，派回 FrontendEngineer 修复（第 {loop_i + 1} 轮）",
                    "processing"
                )

            # 构建修复任务包
            repair_task = "请修复以下 QA 审查发现的问题：\n"
            repair_task += "\n".join(f"- [{loop_i + 1}.{i+1}] {issue}"
                                    for i, issue in enumerate(issues))
            if suggestions:
                repair_task += "\n\n## 改进建议\n" + "\n".join(
                    f"- {s}" for s in suggestions
                )
            repair_task += "\n\n用 edit_file 局部修改，不要重写整个文件。修改完立即停止。"

            # 执行修复
            engineer_role = self.registry.get("FrontendEngineer")
            repair_result = self.executor.execute(
                engineer_role, state, task_package=repair_task,
            )
            self._record_result(state, repair_result)

            # 重新审查
            qa_role = self.registry.get("QAReviewer")
            qa_result = self.executor.execute(
                qa_role, state,
                task_package=f"重新审查修复后的代码，关注之前发现的问题是否已修复",
            )
            self._record_result(state, qa_result)

            qa_data = qa_result.structured_output or {}
            if qa_data.get("passed", True) and qa_data.get("overall_rating", 0) >= 6:
                logger.info(f"[Orchestrator] 修复循环 {loop_i + 1} 通过")
                break

        return state

    def _build_task_package(self, role_name: str, state: AgentState,
                            base_context: str, complexity: str) -> str:
        """为每个角色构建任务描述"""
        requirement = state.get("requirement_content", "")
        plan = state.get("plan", {})

        if role_name == "ProductManager":
            features = plan.get("features", []) if isinstance(plan, dict) else []
            feature_list = "\n".join(f"- {f}" for f in features) if features else requirement
            return (
                f"分析以下需求并生成 PRD 文档：\n\n"
                f"## 用户需求\n{requirement}\n\n"
                f"## 预分析的功能点\n{feature_list}\n\n"
                f"## 复杂度\n{complexity}\n\n"
                f"按照你的 PRD 模板输出完整的分析文档。"
            )

        elif role_name == "Architect":
            pm_output = state.get("role_outputs", {}).get("ProductManager", "")
            return (
                f"基于以下 PRD 设计前端架构：\n\n"
                f"## 用户需求\n{requirement}\n\n"
                f"## PRD\n{pm_output[:3000] if pm_output else '(PRD 待生成)'}\n\n"
                f"按照你的架构设计模板输出完整的技术方案。"
            )

        elif role_name == "FrontendEngineer":
            pm_output = state.get("role_outputs", {}).get("ProductManager", "")
            arch_output = state.get("role_outputs", {}).get("Architect", "")
            context = ""
            if pm_output:
                context += f"\n\n## PRD\n{pm_output[:2000]}"
            if arch_output:
                context += f"\n\n## 架构设计\n{arch_output[:2000]}"
            return (
                f"根据设计和需求创建代码文件：\n"
                f"## 用户需求\n{requirement}{context}\n\n"
                f"按照架构设计中的文件结构逐个创建文件。"
            )

        elif role_name == "QAReviewer":
            return (
                f"审查以下需求的代码实现质量：\n\n"
                f"## 用户需求\n{requirement}\n\n"
                f"检查代码是否完整实现需求，是否有安全和质量问题。"
            )

        return f"处理用户需求：{requirement}"

    def _get_role_context(self, state: AgentState, current_role: str) -> str:
        """获取前序角色的产出作为当前角色的上下文"""
        route = COMPLEXITY_ROUTE.get(
            state.get("metadata", {}).get("complexity", "S"),
            ["FrontendEngineer"]
        )

        # 找到当前角色在序列中的位置
        try:
            idx = route.index(current_role)
        except ValueError:
            return ""

        # 收集前序角色的产出
        role_outputs = state.get("role_outputs", {})
        context_parts = []

        for prev_role in route[:idx]:
            if prev_role in role_outputs:
                output = role_outputs[prev_role]
                # 截断过长内容
                if len(output) > 3000:
                    output = output[:3000] + "\n...(内容已截断)"
                context_parts.append(f"## {prev_role} 产出\n{output}")

        return "\n\n".join(context_parts) if context_parts else ""

    def _record_result(self, state: AgentState, result: RoleResult):
        """记录角色执行结果到 state"""
        history = state.get("role_history", []) or []
        history.append({
            "role_name": result.role_name,
            "success": result.success,
            "content": result.content[:500],
            "error": result.error,
        })
        state["role_history"] = history

        if result.success and result.content:
            outputs = state.get("role_outputs", {}) or {}
            outputs[result.role_name] = result.content
            state["role_outputs"] = outputs

    def _format_plan(self, plan: dict) -> str:
        """格式化 Plan 为可读文本"""
        if not isinstance(plan, dict):
            return str(plan)
        parts = []
        for key in ("features", "complexity", "tech_stack", "file_structure",
                     "data_model", "implementation_notes"):
            val = plan.get(key)
            if val:
                if isinstance(val, list):
                    parts.append(f"**{key}**: {', '.join(str(v) for v in val)}")
                elif isinstance(val, dict):
                    parts.append(f"**{key}**: {json.dumps(val, ensure_ascii=False)}")
                else:
                    parts.append(f"**{key}**: {val}")
        return "\n".join(parts)


import json
