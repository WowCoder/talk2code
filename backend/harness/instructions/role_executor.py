# -*- coding: utf-8 -*-
"""
RoleExecutor —— 单一角色执行器

每个角色内部是 LLM 调用（可能带工具）：
- PM/Architect: 纯文本产出，简单 chat 调用
- FrontendEngineer: 完整 ReAct 工具循环
- QAReviewer: 带 read_file 的审查调用
"""

import json
import re
from typing import Optional

from harness.roles import Role, RoleResult
from harness.state.agent_state import AgentState
from llm.client import get_client
from harness.observability.logger import get_logger

logger = get_logger(__name__)


class RoleExecutor:
    """单一角色执行器 —— 封装不同角色的执行策略"""

    def __init__(self, workspace=None, sse_reporter=None, tools=None):
        self.workspace = workspace
        self.sse = sse_reporter
        self.tools = tools  # ToolRegistry 实例

    def execute(self, role: Role, state: AgentState,
                task_package: str = "", extra_context: str = "") -> RoleResult:
        """
        执行一个角色的任务。

        Args:
            role: 角色定义
            state: 当前 Agent 状态
            task_package: TeamLeader 派发的任务描述
            extra_context: 额外的上下文（前序角色的产出）

        Returns:
            RoleResult
        """
        logger.info(f"[RoleExecutor] 启动角色: {role.display_name} ({role.name})")

        # SSE 推送角色启动
        if self.sse:
            self.sse.progress(state["requirement_id"], 50,
                            f"{role.display_name} 工作中")

        try:
            if role.name == "FrontendEngineer":
                return self._execute_engineer(role, state, task_package, extra_context)
            elif role.name == "QAReviewer":
                return self._execute_qa(role, state, task_package, extra_context)
            else:
                return self._execute_text_role(role, state, task_package, extra_context)

        except Exception as e:
            logger.error(f"[RoleExecutor] {role.name} 执行异常: {e}")
            return RoleResult(
                role_name=role.name,
                success=False,
                error=str(e),
            )

    def _execute_text_role(self, role: Role, state: AgentState,
                           task_package: str, extra_context: str) -> RoleResult:
        """
        执行文本产出角色（PM / Architect）。

        这些角色只需一次 LLM 调用，产出结构化文本文档，
        不需要工具循环。
        """
        requirement = state.get("requirement_content", "")
        plan = state.get("plan", {})
        files = self.workspace.list() if self.workspace else []

        # 组装 prompt
        prompt_parts = [f"## 任务\n{task_package}"]

        if extra_context:
            prompt_parts.append(f"\n## 上下文\n{extra_context}")

        prompt_parts.append(f"\n## 用户原始需求\n{requirement}")

        if plan:
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
            prompt_parts.append(f"\n## 开发计划\n{plan_text}")

        if files:
            prompt_parts.append(f"\n## 工作区已有文件\n" + "\n".join(f"- {f}" for f in files))

        prompt = "\n".join(prompt_parts)

        # LLM 调用
        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt=role.system_prompt,
            use_memory=False,
            max_tokens=3000,
            timeout=60,
        )

        if response.is_error:
            return RoleResult(
                role_name=role.name,
                success=False,
                error=response.error or "未知错误",
            )

        content = response.content or ""

        # SSE 推送角色产出
        if self.sse:
            self.sse.dialogue(
                state["requirement_id"], "agent",
                role.display_name, f"## {role.display_name} 产出\n\n{content[:1500]}",
                "completed"
            )

        logger.info(f"[RoleExecutor] {role.name} 完成，产出 {len(content)} 字符")
        return RoleResult(
            role_name=role.name,
            success=True,
            content=content,
        )

    def _execute_engineer(self, role: Role, state: AgentState,
                          task_package: str, extra_context: str) -> RoleResult:
        """
        执行编码角色（FrontendEngineer）。

        这是唯一需要完整 ToolCallLoop 的角色。
        通过临时替换 system prompt 实现角色切换。
        """
        # 获取 ToolCallLoop 实例（由调用方注入到 state metadata）
        tool_loop = state.get("metadata", {}).get("_tool_loop")
        if not tool_loop:
            return RoleResult(
                role_name=role.name,
                success=False,
                error="ToolCallLoop 未注入到 state",
            )

        # 设置角色名称，让 ToolCallLoop 的 dialogue_history 使用正确的角色名
        state.setdefault("metadata", {})["coder_name"] = role.display_name
        state["metadata"]["thinking_name"] = role.display_name

        # 保存原始 system prompt builder
        original_builder = tool_loop._build_system_prompt

        # 注入角色专属 system prompt（含 PRD + 架构设计上下文）
        requirement = state.get("requirement_content", "")
        plan = state.get("plan", {})

        def _engineer_prompt(s):
            plan_section = ""
            if plan:
                plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if isinstance(plan, dict) else str(plan)
                plan_section = f"\n\n## 开发计划\n{plan_text}"

            context_section = ""
            if extra_context:
                context_section = f"\n\n## 上游角色产出\n{extra_context}"

            existing_files = self.workspace.list() if self.workspace else []
            existing_text = ""
            if existing_files:
                # 复用 ToolCallLoop 的文件摘要方法
                existing_text = tool_loop._build_file_summaries(existing_files)

            return f"""{role.system_prompt}

## 用户需求
{requirement}
{plan_section}
{context_section}

## 任务
{task_package}

## 当前已有文件及内容概要
{existing_text}

## 重要
- 按照架构设计中的文件结构逐个创建文件
- 每次响应只创建一个文件
- 全部完成后立即停止，告诉我"任务完成"
- 已有文件的概要已在上方列出，不要调用 list_files 或 read_file 查看已有文件"""

        try:
            tool_loop._build_system_prompt = _engineer_prompt
            final_state = tool_loop.run(state)

            # 恢复原始 builder
            tool_loop._build_system_prompt = original_builder

            # 将 tool_loop 产生的 dialogue 更新回 state
            state["dialogue_history"] = final_state.get("dialogue_history", [])
            state["code_files"] = final_state.get("code_files", [])
            state["current_step"] = final_state.get("current_step", "")

            return RoleResult(
                role_name=role.name,
                success=final_state.get("current_step") == "task_complete",
                content="代码生成完成",
                error=final_state.get("error", ""),
            )

        except Exception as e:
            tool_loop._build_system_prompt = original_builder
            raise e

    def _execute_qa(self, role: Role, state: AgentState,
                    task_package: str, extra_context: str) -> RoleResult:
        """
        执行审查角色（QAReviewer）。

        使用工具调用模式：先 read_file 查看代码，然后给出审查报告。
        """
        requirement = state.get("requirement_content", "")
        files = self.workspace.list() if self.workspace else []

        # 读取所有代码文件内容用于审查
        code_sections = []
        if self.workspace:
            for fname in files:
                try:
                    content = self.workspace.read(fname)
                    # 限制每个文件最多 400 行（避免 prompt 过长）
                    lines = content.split('\n')
                    if len(lines) > 400:
                        content = '\n'.join(lines[:400]) + f"\n... (共 {len(lines)} 行，仅显示前 400 行)"
                    code_sections.append(f"### {fname}\n```\n{content}\n```")
                except Exception:
                    code_sections.append(f"### {fname}\n(无法读取)")

        code_text = "\n\n".join(code_sections) if code_sections else "(无代码文件)"

        prompt = f"""## 审查任务
{task_package}

## 用户原始需求
{requirement}

## 当前代码文件
{code_text}

{extra_context if extra_context else ''}

请审查以上代码，按照你的输出格式给出评分和建议。"""

        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt=role.system_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=60,
        )

        if response.is_error:
            return RoleResult(
                role_name=role.name,
                success=False,
                error=response.error or "未知错误",
            )

        content = response.content or ""

        # 解析 JSON 审查报告
        review_data = None
        try:
            # 尝试直接解析
            review_data = json.loads(content)
        except json.JSONDecodeError:
            # 尝试从 ```json ... ``` 中提取
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if match:
                try:
                    review_data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            if not review_data:
                # 尝试从文本中提取 JSON 对象
                match = re.search(r'\{[\s\S]*\}', content)
                if match:
                    try:
                        review_data = json.loads(match.group())
                    except json.JSONDecodeError:
                        pass

        passed = review_data.get("passed", True) if review_data else True
        rating = review_data.get("overall_rating", 7) if review_data else 7

        # SSE 推送审查结果
        if self.sse:
            issues_text = ""
            if review_data:
                dims = review_data.get("dimensions", {})
                dims_text = " | ".join(f"{k}: {v}/10" for k, v in dims.items())
                issues = review_data.get("critical_issues", [])
                issues_text = "\n- " + "\n- ".join(issues) if issues else ""
                self.sse.dialogue(
                    state["requirement_id"], "agent",
                    role.display_name,
                    f"## 代码审查\n\n**评分**: {rating}/10\n**{dims_text}**{issues_text}",
                    "completed"
                )
            else:
                self.sse.dialogue(
                    state["requirement_id"], "agent",
                    role.display_name,
                    f"## 代码审查\n\n{content[:1000]}",
                    "completed"
                )

        logger.info(f"[RoleExecutor] QA 完成: rating={rating}, passed={passed}")

        return RoleResult(
            role_name=role.name,
            success=True,
            content=content,
            structured_output=review_data or {"overall_rating": rating, "passed": passed},
        )
