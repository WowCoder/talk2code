# -*- coding: utf-8 -*-
"""
ToolCallLoop —— Agent ReAct 工具调用循环
从 agents/tool_loop.py 迁移到 harness/runtime.py
"""

import time
import json

from harness.state.agent_state import AgentState
from harness.tools.registry import ToolRegistry
from harness.tools.file_tools import FileToolHandler
from harness.tools.code_tools import CodeToolHandler
from harness.tools.preview_tools import PreviewToolHandler
from llm.client import get_client
from harness.observability.logger import get_logger

logger = get_logger(__name__)


class ToolCallLoop:
    """Agent 工具调用循环 —— ReAct 模式"""

    MAX_ITERATIONS = 15
    NO_PROGRESS_LIMIT = 5  # 连续无进展轮次限制
    # 真实运行验证（run_preview）失败后的最大修复轮次。
    # 与 hook 修复共用同一计数器，避免无限重试。
    MAX_REPAIR_ROUNDS = 2

    def __init__(self, workspace, git=None, tools: ToolRegistry = None,
                 hooks=None, tracer=None, cost_tracker=None, sse_reporter=None,
                 permission_manager=None, checkpoint=None):
        self.workspace = workspace
        self.git = git
        self.tools = tools
        self.hooks = hooks
        self.tracer = tracer
        self.cost_tracker = cost_tracker
        self.sse = sse_reporter
        self.permission_manager = permission_manager
        self.checkpoint = checkpoint

        # 创建工具处理器
        self._file_handler = FileToolHandler(workspace)
        self._code_handler = CodeToolHandler(workspace)
        self._preview_handler = PreviewToolHandler(workspace)

    def run(self, state: AgentState) -> AgentState:
        client = get_client()
        trace_id = state.get("metadata", {}).get("trace_id", "")

        for iteration in range(self.MAX_ITERATIONS):
            state["tool_call_count"] = iteration + 1

            # 调用 LLM with tools
            messages = self._build_messages(state)
            response = client.chat_with_tools(
                messages=messages,
                tools=self.tools.get_schemas() if self.tools else [],
                max_tokens=8000,
            )

            # 追踪 LLM 调用
            if self.tracer and trace_id:
                span = self.tracer.start_span(trace_id, f"tool_coder_iter_{iteration}")
                if response.usage and self.cost_tracker:
                    input_tokens, output_tokens = self.cost_tracker.extract_usage(
                        response.usage, client.provider
                    )
                    self.cost_tracker.record(trace_id, input_tokens, output_tokens, client.model)
                    span.metadata["tokens"] = input_tokens + output_tokens
                self.tracer.end_span(span)

            # 诊断日志（生产环境可关闭）
            from harness.observability.logger import get_logger
            _log = get_logger(__name__)
            tc_names = [tc.name for tc in response.tool_calls] if response.tool_calls else []
            _log.debug(f"[ToolLoop] 迭代 {iteration + 1}: tool_calls={tc_names}")

            # 无工具调用 → 任务完成
            if not response.tool_calls:
                state["current_step"] = "task_complete"
                state["dialogue_history"].append({
                    "role": "agent", "name": "Coder",
                    "content": response.content or "任务完成"
                })
                break

            # 发布 thinking
            if self.sse and response.content:
                self.sse.thinking(state["requirement_id"], response.content)

            # 执行所有工具调用
            for tc in response.tool_calls:
                result = self._execute_tool(state, tc)
                logger.info(f"[ToolLoop] 执行 {tc.name}: success={result.success} content={result.content[:100] if result.success else ''} error={result.error[:100] if not result.success else ''}")

                # 发布工具调用事件
                if self.sse:
                    self.sse.tool_call(state["requirement_id"], tc.name, tc.arguments)
                    self.sse.tool_result(
                        state["requirement_id"], tc.name,
                        result.success, result.content[:500] if result.success else "",
                        result.error[:500] if not result.success else "",
                    )
                    # write_file 成功后推送 code 事件，让前端实时更新代码面板
                    if tc.name == "write_file" and result.success:
                        self.sse.code(state["requirement_id"], [{
                            "filename": tc.arguments.get("filename", "unknown"),
                            "content": tc.arguments.get("content", "")
                        }])

                # 工具结果摘要（大文件内容截断，避免对话记录膨胀）
                tool_summary = result.content if result.success else result.error
                if tc.name in ("read_file", "write_file") and len(tool_summary) > 300:
                    tool_summary = tool_summary[:300] + "..."
                state["dialogue_history"].append({
                    "role": "tool_call",
                    "name": tc.name,
                    "content": tool_summary
                })

                # Git 自动 commit
                if self.git and tc.name == "write_file" and result.success:
                    filename = tc.arguments.get("filename", "unknown")
                    self.git.commit(f"[tool] write_file: {filename}")

            # 检查是否达到最大迭代
            if iteration >= self.MAX_ITERATIONS - 1:
                state["current_step"] = "max_iterations"
                break

            # 检查连续无进展
            if self._check_no_progress(state):
                state["current_step"] = "no_progress"
                break

            # 每 3 轮保存检查点，支持崩溃/重启后断点恢复
            if self.checkpoint and (iteration + 1) % 3 == 0:
                try:
                    self.checkpoint.save(
                        state["requirement_id"], "tool_coder", state
                    )
                except Exception as e:
                    logger.warning("保存检查点失败（不阻断）：%s", e)

        # 任务完成验证闭环：Hook 检查 + 真实运行验证（run_preview）
        # 失败时把错误回灌为修复 prompt，多轮收敛直到通过或达到上限
        repair_count = state.get("metadata", {}).get("repair_count", 0)
        if state["current_step"] == "task_complete" and repair_count < self.MAX_REPAIR_ROUNDS:
            failures = self._trigger_hooks(state) if self.hooks else []
            preview_errors = self._run_preview_validation(state)
            all_problems = failures + preview_errors
            if all_problems:
                state.setdefault("metadata", {})["repair_count"] = repair_count + 1
                repair_prompt = (
                    "代码已生成，但验证发现以下问题，请立即用 write_file 修复：\n"
                    + "\n".join(f"- {p}" for p in all_problems)
                )
                state["dialogue_history"].append({
                    "role": "user", "name": "System",
                    "content": repair_prompt
                })
                state["current_step"] = "repairing"
                state["no_progress_count"] = 0
                return self.run(state)

        # Git final commit
        if self.git:
            self.git.commit("[agent] task complete")

        return state

    def _execute_tool(self, state: AgentState, tool_call) -> "ToolResult":
        from harness.tools.registry import ToolResult

        # 权限检查
        if self.permission_manager:
            permission = self.tools.get_permission(tool_call.name)
            check = self.permission_manager.check(state["requirement_id"], tool_call.name, permission)
            if check == "needs_approval":
                if self.sse:
                    self.sse.permission_request(
                        state["requirement_id"], tool_call.name,
                        tool_call.arguments,
                        f"Agent 请求执行 {tool_call.name}"
                    )
                return ToolResult(error="需要用户审批")

        # 预处理 Hook
        if self.hooks:
            from harness.constraints.hooks import HookContext, HookPoint
            ctx = HookContext(
                requirement_id=state["requirement_id"],
                tool_name=tool_call.name,
                tool_args=tool_call.arguments,
                state=state,
            )
            if self.hooks.trigger(HookPoint.PRE_TOOL_USE, ctx):
                pass  # 预处理失败不阻断，只记录

        # 分发到对应处理器
        handler_map = {
            "read_file": lambda: self._file_handler.read_file(**tool_call.arguments),
            "write_file": lambda: self._file_handler.write_file(**tool_call.arguments),
            "list_files": lambda: self._file_handler.list_files(),
            "delete_file": lambda: self._file_handler.delete_file(**tool_call.arguments),
            "validate_html": lambda: self._code_handler.validate_html(**tool_call.arguments),
            "lint_css": lambda: self._code_handler.lint_css(**tool_call.arguments),
            "lint_js": lambda: self._code_handler.lint_js(**tool_call.arguments),
            "execute_code": lambda: self._code_handler.execute_code(**tool_call.arguments),
            "run_preview": lambda: self._preview_handler.run_preview(**tool_call.arguments),
        }

        handler = handler_map.get(tool_call.name)
        if handler:
            result = handler()
        else:
            result = self.tools.execute(tool_call.name, tool_call.arguments)

        # 后处理 Hook
        if self.hooks:
            from harness.constraints.hooks import HookContext, HookPoint
            ctx = HookContext(
                requirement_id=state["requirement_id"],
                tool_name=tool_call.name,
                tool_args=tool_call.arguments,
                tool_result=result.content if result.success else result.error,
                state=state,
            )
            failures = self.hooks.trigger(HookPoint.POST_TOOL_USE, ctx)
            if failures:
                state.setdefault("hook_failures", {})
                for f in failures:
                    hook_name = f.split(":")[0] if ":" in f else "unknown"
                    state["hook_failures"][hook_name] = state["hook_failures"].get(hook_name, 0) + 1
                    if self.sse:
                        self.sse.hook_check(state["requirement_id"], hook_name, False, f)

        return result

    def _trigger_hooks(self, state: AgentState) -> list:
        """触发 ON_TASK_COMPLETE Hook，返回失败列表"""
        from harness.constraints.hooks import HookContext, HookPoint
        ctx = HookContext(
            requirement_id=state["requirement_id"],
            state={
                "file_list": self.workspace.list(),
                "code_files": state.get("code_files", []),
            }
        )
        failures = self.hooks.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        if failures and self.sse:
            for f in failures:
                self.sse.hook_check(state["requirement_id"], "task_complete", False, f)
        return failures

    def _run_preview_validation(self, state: AgentState) -> list:
        """
        在 headless 浏览器中真实运行生成的页面，返回阻断性错误列表。

        这是把生成质量从「盲写」提升到「可见反馈」的关键闭环 ——
        让 agent 看到自己生成代码的运行结果并据此修复。

        返回 [] 表示通过或验证不可用（不阻断流程）。
        """
        existing = self.workspace.list()
        if "index.html" not in existing:
            return []  # 没有可预览的入口，跳过

        try:
            result = self._preview_handler.run_preview("index.html")
        except Exception as e:
            logger.warning("run_preview 异常（降级跳过）: %s", e)
            return []

        report = result.metadata if result and result.metadata else {}

        # SSE 推送验证结果（供前端展示）
        if self.sse:
            self.sse.preview(state["requirement_id"], report)

        # 浏览器不可用时降级，不阻断
        if not report.get("available", True):
            return []

        errors = report.get("errors", [])
        # 提取人类可读的错误摘要回灌给 LLM
        return [
            f"[{e.get('type', 'error')}] {e.get('message', '')}"
            for e in errors
            if e.get("message")
        ]

    def _build_messages(self, state: AgentState) -> list:
        """构建 LLM 消息列表"""
        messages = []

        # 系统提示词
        system_prompt = self._build_system_prompt(state)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # 对话历史
        for msg in state.get("dialogue_history", [])[-20:]:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            if role == "tool_call":
                tool_name = msg.get("name", "")
                tool_result = msg.get("result", "")
                messages.append({
                    "role": "assistant",
                    "content": f"[调用工具 {tool_name}]\n参数: {json.dumps(msg.get('content', {}), ensure_ascii=False)}"
                })
                messages.append({
                    "role": "user",
                    "content": f"[工具 {tool_name} 返回结果]\n{tool_result}"
                })
            elif role in ("user", "agent", "assistant"):
                messages.append({"role": "user" if role == "user" else "assistant", "content": content})

        return messages

    def _build_system_prompt(self, state: AgentState) -> str:
        """构建 Coder 系统提示词"""
        requirement = state.get("requirement_content", "")
        plan = state.get("plan")

        existing_files = self.workspace.list()
        existing_text = "\n".join(f"- {f}" for f in existing_files) if existing_files else "(空目录)"

        target_files = ["style.css", "script.js", "index.html"]
        missing = [f for f in target_files if f not in existing_files]
        missing_text = ", ".join(missing) if missing else "全部已创建"

        plan_section = ""
        if plan:
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if isinstance(plan, dict) else str(plan)
            plan_section = f"""## 实现计划（请严格遵循）
{plan_text}"""

        prompt = f"""你是一个资深前端工程师。请使用 write_file 工具创建所有缺失的文件。

## 用户需求
{requirement}

{plan_section}

## 当前已有文件
{existing_text}

## 尚未创建的文件（按顺序）
{missing_text}

## 要求
- 从上面"尚未创建"列表中选第一个文件，用 write_file 创建它
- **每次响应只创建一个文件**，不要在一次响应中同时创建多个文件
- 创建完成后在下一次响应中继续创建下一个
- 只创建"尚未创建"的文件，不要重复创建已有文件
- 全部文件创建完成后立即停止，告诉我"任务完成"
- 绝对不要调用 list_files 工具，直接看上面的已有文件列表

## 验证
- 全部文件创建完成后，系统会自动在无头浏览器中运行 index.html 验证 JS 是否报错；
  若报错会反馈给你，请据此用 write_file 修复并确保代码可真实运行。

## 代码规范
- index.html 引入 <script src="https://cdn.tailwindcss.com"></script>
- 数据用 localStorage 持久化
- 禁止: innerHTML, eval, document.write
- 代码完整可运行，不省略不写TODO
- 每个文件内容不少于100行"""
        return prompt

    def _check_no_progress(self, state: AgentState) -> bool:
        """检查连续无进展（前 3 轮豁免，给 LLM 足够的探索空间）"""
        if state.get("tool_call_count", 0) <= 3:
            return False
        current_files = set(self.workspace.list())
        last_files = set(state.get("last_file_list", []))
        state["last_file_list"] = list(current_files)
        if current_files == last_files:
            state["no_progress_count"] = state.get("no_progress_count", 0) + 1
        else:
            state["no_progress_count"] = 0
        return state.get("no_progress_count", 0) >= self.NO_PROGRESS_LIMIT
