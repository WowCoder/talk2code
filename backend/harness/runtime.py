# -*- coding: utf-8 -*-
"""
ToolCallLoop —— Agent ReAct 工具调用循环
从 agents/tool_loop.py 迁移到 harness/runtime.py
"""

import time
import json
import re

from harness.state.agent_state import AgentState
from harness.tools.registry import ToolRegistry
from harness.tools.file_tools import FileToolHandler
from harness.tools.code_tools import CodeToolHandler
from harness.tools.preview_tools import PreviewToolHandler
from harness.tools.edit_tools import EditToolHandler
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
                 permission_manager=None, checkpoint=None, on_iteration=None):
        self.workspace = workspace
        self.git = git
        self.tools = tools
        self.hooks = hooks
        self.tracer = tracer
        self.cost_tracker = cost_tracker
        self.sse = sse_reporter
        self.permission_manager = permission_manager
        self.checkpoint = checkpoint
        self.on_iteration = on_iteration  # 可选回调，每轮迭代后调用以增量持久化

        # 创建工具处理器
        self._file_handler = FileToolHandler(workspace)
        self._code_handler = CodeToolHandler(workspace)
        self._preview_handler = PreviewToolHandler(workspace)
        self._edit_handler = EditToolHandler(workspace)

    def run(self, state: AgentState) -> AgentState:
        client = get_client()
        trace_id = state.get("metadata", {}).get("trace_id", "")

        # 每次进入 run() 重置运行时计数器，避免多轮调用（如修复循环）间状态污染
        state["no_progress_count"] = 0
        state["repeat_call_count"] = 0
        state["last_tool_signatures"] = set()
        state["tool_call_count"] = 0

        # 可配置的角色名称（多角色协作用，默认兼容旧行为）
        meta = state.get("metadata", {})
        coder_name = meta.get("coder_name", "Henry（开发）")
        thinking_name = meta.get("thinking_name", "Henry（开发）")

        # 根据复杂度调整迭代上限（M/L 多角色流程需要更多轮次覆盖文件创建和 QA 修复）
        complexity = state.get("metadata", {}).get("complexity", "S")
        effective_max_iterations = {
            "XS": 5,
            "S": self.MAX_ITERATIONS,
            "M": self.MAX_ITERATIONS + 5,  # M 也有多文件模块，需要更多轮次
            "L": self.MAX_ITERATIONS + 10,  # L 文件更多，QA 修复更复杂
        }.get(complexity, self.MAX_ITERATIONS)

        for iteration in range(effective_max_iterations):
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

            # 无工具调用 → 任务完成（初始生成流程需检查目标文件是否全部生成；
            # chat 模式只做增量修改，不要求补齐所有文件）
            if not response.tool_calls:
                is_chat = state.get("metadata", {}).get("is_chat", False)
                missing = [] if is_chat else self._check_missing_files(state)
                if missing:
                    state["dialogue_history"].append({
                        "role": "system", "name": "System",
                        "content": f"你还没有创建所有必需的文件，缺少：{', '.join(missing)}。"
                                 f"请继续用 write_file 创建剩余文件，不要停止。",
                        "hidden": True,
                    })
                    state["no_progress_count"] = 0
                    continue
                state["current_step"] = "task_complete"
                state["current_step"] = "task_complete"
                state["dialogue_history"].append({
                    "role": "agent", "name": coder_name,
                    "content": response.content or "任务完成"
                })
                break

            # 发布 thinking（优先用 reasoning_content，其次 content）
            thinking_text = response.reasoning_content or response.content
            if thinking_text:
                if self.sse:
                    self.sse.thinking(state["requirement_id"], thinking_text, thinking_name)
                # 存入对话历史，让前端刷新后仍可展示
                state["dialogue_history"].append({
                    "role": "thinking",
                    "name": thinking_name,
                    "content": thinking_text
                })

            # 保存 assistant 回复到对话历史（让下一轮 Agent 记得自己的规划，避免重复探索）
            if response.content and response.tool_calls:
                state["dialogue_history"].append({
                    "role": "assistant",
                    "name": coder_name,
                    "content": response.content[:1500]
                })

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
                    # write_file/edit_file 成功后推送 code 事件，让前端实时更新代码面板
                    if tc.name == "write_file" and result.success:
                        self.sse.code(state["requirement_id"], [{
                            "filename": tc.arguments.get("filename", "unknown"),
                            "content": tc.arguments.get("content", "")
                        }])
                    elif tc.name == "edit_file" and result.success:
                        # edit_file 推送修改后的完整文件内容（前端需最新全量）
                        edited_name = tc.arguments.get("filename", "unknown")
                        try:
                            edited_content = self.workspace.read(edited_name)
                        except Exception:
                            edited_content = ""
                        self.sse.code(state["requirement_id"], [{
                            "filename": edited_name,
                            "content": edited_content
                        }])

                # 工具结果摘要（超大文件截断，避免对话记录膨胀）
                # read_file 需要足够上下文让 LLM 构造 edit_file 的精确 SEARCH 块
                tool_summary = result.content if result.success else result.error
                is_chat = state.get("metadata", {}).get("is_chat", False)
                if tc.name == "read_file":
                    # Chat/编辑模式：需要完整文件内容构造精确 SEARCH 块
                    # 生成模式：8000 字符覆盖文件主要区域（配合文件摘要避免误判截断为损坏）
                    max_len = 16000 if is_chat else 8000
                elif tc.name in ("write_file", "edit_file"):
                    max_len = 8000  # 让 Agent 看到完整文件内容，避免写入后再 read_file 验证
                else:
                    max_len = 300
                if len(tool_summary) > max_len:
                    cut_hint = (
                        "\n... (文件较长已截断，文件本身完整未损坏。如需特定片段请指定行号范围读取)"
                        if is_chat else
                        "\n... (文件较长已截断，文件本身完整无损。文件摘要已在系统提示中，继续基于摘要工作，不要重复读取)"
                    )
                    tool_summary = tool_summary[:max_len] + cut_hint

                # 前端展示用简短摘要（不暴露大段文件内容）
                display_readable = self._tool_display_label(tc.name, tc.arguments, result)
                state["dialogue_history"].append({
                    "role": "tool_call",
                    "name": tc.name,
                    "content": tool_summary,
                    "arguments": tc.arguments,  # 保留原始参数，供前端详情展示
                    "readable": display_readable,  # 前端展示用简短标签
                })

                # Git 自动 commit
                if self.git and tc.name in ("write_file", "edit_file") and result.success:
                    filename = tc.arguments.get("filename", "unknown")
                    self.git.commit(f"[tool] {tc.name}: {filename}")

            # 检测重复工具调用（连续相同 tool+filename 视为卡住）
            current_signatures = set()
            for tc in (response.tool_calls or []):
                fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
                current_signatures.add(f"{tc.name}:{fname}")
            last_signatures = state.get("last_tool_signatures", set())
            if current_signatures and current_signatures == last_signatures:
                state["repeat_call_count"] = state.get("repeat_call_count", 0) + 1
            else:
                state["repeat_call_count"] = 0
            state["last_tool_signatures"] = current_signatures
            # 连续 3 轮相同工具调用 → 判定为卡住
            if state.get("repeat_call_count", 0) >= 3:
                logger.warning(
                    f"[ToolLoop] 连续 {state['repeat_call_count']} 轮重复调用相同工具: {current_signatures}，判定为无进展"
                )
                state["current_step"] = "no_progress"
                break

            # 检查是否达到最大迭代
            if iteration >= effective_max_iterations - 1:
                state["current_step"] = "max_iterations"
                break

            # 检查连续无进展
            if self._check_no_progress(state):
                state["current_step"] = "no_progress"
                break

            # 增量持久化：每轮迭代后回调，保存对话历史到数据库
            if self.on_iteration:
                try:
                    self.on_iteration(state)
                except Exception as e:
                    logger.warning(f"on_iteration 回调失败（不阻断）：{e}")

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
            # 修复循环中也运行 preview 验证（之前跳过，现在修复）
            preview_errors = self._run_preview_validation(state)
            all_problems = failures + preview_errors
            if all_problems:
                state.setdefault("metadata", {})["repair_count"] = repair_count + 1
                repair_prompt = (
                    "代码已生成，但验证发现以下问题，请立即修复（已有文件用 edit_file 局部修改，"
                    "不要重写整个文件）：\n"
                    + "\n".join(f"- {p}" for p in all_problems)
                )
                state["dialogue_history"].append({
                    "role": "system", "name": "System",
                    "content": repair_prompt,
                    "hidden": True,
                })
                state["current_step"] = "repairing"
                state["no_progress_count"] = 0
                return self.run(state)

        # Git final commit
        if self.git:
            self.git.commit("[agent] task complete")

        return state

    def _tool_display_label(self, tool_name: str, arguments: dict, result) -> str:
        """生成前端展示用的简短工具标签（不暴露大段文件内容）"""
        filename = arguments.get("filename", "")
        if tool_name == "read_file":
            lines = result.content.count('\n') + 1 if result.success and result.content else 0
            return f"📖 读取 {filename} ({lines} 行)"
        elif tool_name == "write_file":
            lines = result.content.count('\n') + 1 if result.success and result.content else 0
            return f"📝 创建 {filename} ({lines} 行)"
        elif tool_name == "edit_file":
            edits = arguments.get("edit", arguments.get("edits", ""))
            block_count = edits.count("<<<< SEARCH") if isinstance(edits, str) else 1
            return f"✏️ 编辑 {filename} ({block_count} 处修改)"
        elif tool_name == "list_files":
            files = (result.content or "").strip()
            count = len(files.split('\n')) if files else 0
            return f"📋 文件列表 ({count} 个文件)"
        elif tool_name == "delete_file":
            return f"🗑 删除 {filename}"
        elif tool_name in ("validate_html", "lint_css", "lint_js"):
            return f"🔍 检查 {filename}"
        elif tool_name == "execute_code":
            return f"▶ 运行代码验证"
        elif tool_name == "search_docs":
            return f"🔎 搜索: {arguments.get('query', '')}"
        elif tool_name == "fetch_cdn_library":
            return f"📦 CDN: {arguments.get('library', '')}"
        return f"🔧 {tool_name}"

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
            "edit_file": lambda: self._edit_handler.edit_file(**tool_call.arguments),
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
                state.setdefault("_recent_hook_failures", [])
                for f in failures:
                    hook_name = f.split(":")[0] if ":" in f else "unknown"
                    state["hook_failures"][hook_name] = state["hook_failures"].get(hook_name, 0) + 1
                    # 存入 _recent_hook_failures，下一轮 _build_messages 时注入 LLM 上下文
                    state["_recent_hook_failures"].append(f)
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

        # 对话历史：先过滤掉 thinking 消息（不发给 LLM，但会占用槽位），
        # 再截断最近 30 条有效消息（保留足够上下文避免重复 read_file）
        all_history = state.get("dialogue_history", [])
        relevant = [m for m in all_history if m.get("role") != "thinking"]
        for msg in relevant[-30:]:
            role = msg.get("role", "agent")
            content = str(msg.get("content", ""))
            if role == "tool_call":
                tool_name = msg.get("name", "")
                tool_result = msg.get("content", "")
                # 只保留工具结果，不伪造 assistant 消息（伪造的 "已执行工具 xx"
                # 会让 LLM 误以为已完成操作，导致跳过实际工具调用）
                messages.append({
                    "role": "user",
                    "content": f"[工具 {tool_name} 返回结果]\n{tool_result}"
                })
            elif role in ("user", "agent", "assistant"):
                messages.append({"role": "user" if role == "user" else "assistant", "content": content})

        # ---- 注入最近的 Hook 失败（让 LLM 看到验证错误并修复） ----
        recent_failures = state.get("_recent_hook_failures", [])
        if recent_failures:
            failure_text = (
                "## 最近验证失败（请立即修复这些问题）\n"
                + "\n".join(f"- {f}" for f in recent_failures[-5:])
            )
            messages.append({"role": "user", "content": failure_text})
            # 消费后保留一份在持久化字段中，但清空 _recent 避免重复注入
            state["_recent_hook_failures"] = []

        # ---- 分层上下文压缩（替换简单截断） ----
        from harness.instructions.compactor import ContextCompactor
        compactor = ContextCompactor(budget=56000)
        messages = compactor.maybe_compact(messages)

        return messages

    def _build_system_prompt(self, state: AgentState) -> str:
        """构建 Coder 系统提示词（含文件内容概要，避免 Agent 重复 read_file）

        根据复杂度 (XS/S/M/L) 切换提示词策略：
        - XS: 自由文件结构，跳过强制文件列表和验证
        - S:  当前默认行为（3 文件 + lint 验证）
        - M:  架构先导 + 子目录组织 + 完整验证
        - L:  多模块拆分 + 架构设计 + 多轮验证 + Repair
        """
        requirement = state.get("requirement_content", "")
        plan = state.get("plan")
        complexity = state.get("metadata", {}).get("complexity", "S")

        existing_files = self.workspace.list()
        existing_text = self._build_file_summaries(existing_files)

        plan_section = ""
        if plan:
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if isinstance(plan, dict) else str(plan)
            plan_section = f"""## 实现计划（请严格遵循）
{plan_text}"""

        # ---- 根据复杂度选择不同的提示词 ----

        if complexity == "XS":
            return self._build_xs_prompt(requirement, plan_section, existing_text, existing_files)

        elif complexity in ("M", "L"):
            return self._build_ml_prompt(requirement, plan_section, existing_text, existing_files, complexity)

        else:  # S (默认)
            return self._build_s_prompt(requirement, plan_section, existing_text, existing_files)

    def _build_xs_prompt(self, requirement: str, plan_section: str,
                         existing_text: str, existing_files: list) -> str:
        """XS 复杂度：自由文件结构，极简流程"""
        return f"""你是一个资深前端工程师。请使用 write_file 工具创建所需的文件。

## 用户需求
{requirement}

{plan_section}

## 当前已有文件及内容概要
{existing_text}

## 要求
- 根据需求自由创建文件，不强制要求 3 文件结构
- 简单需求可能只需要 1 个 HTML 文件即可
- 每次响应只创建一个文件
- 全部文件创建完成后立即停止，告诉我"任务完成"
- 不需要调用 list_files 或 read_file 查看已有文件（概要已在上方）
- **write_file 的返回结果已包含你刚写入的文件完整内容，不要再用 read_file 重新读取**

## 代码规范
- 使用 <script src="https://cdn.tailwindcss.com"></script> 引入 Tailwind CSS
- 需要持久化数据时用 localStorage
- 禁止: innerHTML, eval, document.write
- 代码完整可运行，不省略不写TODO"""

    def _build_s_prompt(self, requirement: str, plan_section: str,
                        existing_text: str, existing_files: list) -> str:
        """S 复杂度：当前默认行为（3 文件 + lint 验证）"""
        target_files = ["index.html", "style.css", "script.js"]
        missing = [f for f in target_files if f not in existing_files]
        missing_text = ", ".join(missing) if missing else "全部已创建"

        return f"""你是一个资深前端工程师。请使用 write_file 工具创建所有缺失的文件。

## 用户需求
{requirement}

{plan_section}

## 当前已有文件及内容概要
{existing_text}

## 尚未创建的文件（按顺序）
{missing_text}

## 要求
- 从上面"尚未创建"列表中选第一个文件，用 write_file 创建它
- **每次响应只创建一个文件**，不要在一次响应中同时创建多个文件
- 创建完成后在下一次响应中继续创建下一个
- 只创建"尚未创建"的文件，不要重复创建已有文件
- 全部文件创建完成后立即停止，告诉我"任务完成"
- **已有文件的概要已在上方列出，不要调用 list_files 或 read_file 查看已有文件**
- **write_file 的返回结果已包含你刚写入的文件完整内容，不要再用 read_file 重新读取刚写入的文件**

## 验证
- 全部文件创建完成后，系统会自动在无头浏览器中运行 index.html 验证 JS 是否报错；
  若报错会反馈给你，请据此用 write_file 修复并确保代码可真实运行。

## 代码规范
- index.html 引入 <script src="https://cdn.tailwindcss.com"></script>
- 数据用 localStorage 持久化
- 禁止: innerHTML, eval, document.write
- 代码完整可运行，不省略不写TODO
- 每个文件内容不少于100行"""

    def _build_ml_prompt(self, requirement: str, plan_section: str,
                         existing_text: str, existing_files: list,
                         complexity: str) -> str:
        """M/L 复杂度：架构先导 + 模块化 + 严格验证"""
        # 从 plan 中提取推荐的文件结构
        file_hint = ""
        plan_obj = json.loads(plan_section.split("\n", 1)[1]) if plan_section and "\n" in plan_section else {}
        if isinstance(plan_obj, dict):
            file_structure = plan_obj.get("file_structure", [])
            if file_structure:
                file_hint = "## 推荐文件结构\n" + "\n".join(f"- {f}" for f in file_structure)

        return f"""你是一个资深前端工程师和架构师。请按照架构设计创建高质量代码。

## 用户需求
{requirement}

{plan_section}

{file_hint}

## 当前已有文件及内容概要
{existing_text}

## 工作流程
1. 先创建入口文件 index.html（引入所有依赖）
2. 按模块逐层创建 CSS/JS 文件（使用子目录组织，如 css/、js/、components/）
3. 每个模块单一职责，文件间通过 import/export 或全局命名空间通信
4. 每创建 2-3 个文件后验证一次

## 要求
- **每次响应只创建一个文件**
- 按推荐文件结构创建，不使用构建工具
- 全部文件创建完成后用 validate_html / lint_css / lint_js 验证
- 验证完成后立即停止，告诉我"任务完成"
- **write_file 的返回结果已包含你刚写入的文件完整内容，不要再用 read_file 重新读取刚写入的文件**
- **read_file 截断不等于文件损坏——文件本身是完整的，不需要删除重写**

## 验证与修复
- 全部文件创建完成后系统会自动运行无头浏览器验证
- 报错会反馈给你，请据此用 edit_file 局部修复（不要重写整个文件）
- 最多修复 {self.MAX_REPAIR_ROUNDS + 1} 轮

## 代码规范
- index.html 引入 <script src="https://cdn.tailwindcss.com"></script>
- 数据持久化用 localStorage（5MB 内）或 IndexedDB（大量数据）
- 禁止: innerHTML, eval, document.write
- 代码完整可运行，不省略不写TODO
- 每个文件内容充实，组件拆分合理
- 复杂度 {complexity}：需要考虑可维护性和扩展性"""

    def _build_file_summaries(self, existing_files: list) -> str:
        """为已有文件生成内容概要，让 Agent 无需 read_file 就知道文件结构"""
        if not existing_files:
            return "(空目录)"

        lines = []
        for fname in existing_files:
            try:
                content = self.workspace.read(fname)
            except Exception:
                lines.append(f"- {fname}: (无法读取)")
                continue

            # 提取文件关键信息：前 30 行 + 尾部 10 行 + HTML/CSS/JS 结构特征
            all_lines = [l for l in content.split('\n')]
            content_lines = [l.strip() for l in all_lines if l.strip()]
            # 前 30 行（覆盖头部 + 主要结构）
            head_lines = content_lines[:30]
            # 尾部 10 行（覆盖底部 JS 逻辑、闭合标签）
            tail_lines = content_lines[-10:] if len(content_lines) > 30 else []

            # 提取结构性信息
            structural = []
            if fname.endswith('.html'):
                # 提取标题、主要容器、引入的文件
                for l in content_lines:
                    if '<title>' in l:
                        structural.append(l.strip()[:120])
                        break
                ids = set()
                for l in content_lines:
                    if 'id="' in l:
                        import re
                        ids.update(re.findall(r'id="([^"]+)"', l))
                    if "id='" in l:
                        ids.update(re.findall(r"id='([^']+)'", l))
                if ids:
                    structural.append(f"元素 id: {', '.join(sorted(ids)[:15])}")
                classes = set()
                for l in content_lines:
                    if 'class="' in l:
                        import re
                        classes.update(re.findall(r'class="([^"]+)"', l))
                    if "class='" in l:
                        classes.update(re.findall(r"class='([^']+)'", l))
                if classes:
                    structural.append(f"CSS class: {', '.join(sorted(classes)[:20])}")

            elif fname.endswith('.css'):
                # 提取选择器列表
                selectors = []
                for l in content_lines:
                    l = l.strip()
                    if l.endswith('{') and not l.startswith('@') and not l.startswith('/*'):
                        sel = l[:-1].strip()
                        if sel and len(sel) < 60:
                            selectors.append(sel)
                if selectors:
                    structural.append(f"选择器: {', '.join(selectors[:20])}")

            elif fname.endswith('.js'):
                # 提取函数名和 DOM 引用
                funcs = []
                import re
                for l in content_lines:
                    m = re.match(r'(?:async\s+)?function\s+(\w+)', l)
                    if m:
                        funcs.append(m.group(1))
                    m = re.match(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', l)
                    if m:
                        funcs.append(m.group(1))
                if funcs:
                    structural.append(f"函数: {', '.join(funcs[:15])}")
                # DOM 查询
                dom_refs = set()
                for l in content_lines:
                    for m in re.findall(r"(?:querySelector|getElementById|querySelectorAll)\(['\"]([^'\"]+)['\"]\)", l):
                        dom_refs.add(m)
                if dom_refs:
                    structural.append(f"DOM 引用: {', '.join(sorted(dom_refs)[:15])}")

            # 组装摘要：前 30 行 + 尾部 10 行 + 结构特征
            head_preview = ' | '.join(head_lines)[:400]
            parts = [f"- {fname} ({len(content_lines)} 行): {head_preview}"]
            if tail_lines:
                tail_preview = ' | '.join(tail_lines)[:200]
                parts.append(f"  ... 尾部: {tail_preview}")
            if structural:
                parts.append("  " + " | ".join(structural))
            lines.append('\n'.join(parts))

        return '\n'.join(lines)

    def _check_missing_files(self, state: AgentState) -> list[str]:
        """检查目标文件是否全部生成。返回缺失文件名列表。

        优先从架构设计的 file_structure 读取目标文件列表，
        避免硬编码与架构设计冲突（如 css/style.css vs style.css）。
        """
        complexity = state.get("metadata", {}).get("complexity", "S")
        existing = set(self.workspace.list())

        if complexity == "XS":
            if existing:
                return []
            return ["至少一个文件"]

        # 从 plan（TeamLeader/Architect 产出）中提取目标文件结构
        plan = state.get("plan")
        plan_files = []
        if isinstance(plan, dict):
            file_structure = plan.get("file_structure", [])
            if file_structure and isinstance(file_structure, list):
                plan_files = [f for f in file_structure if isinstance(f, str)]

        if plan_files:
            # 使用架构设计中的文件列表，支持子目录路径
            return sorted(f for f in plan_files if f not in existing)

        # Fallback: S 复杂度只需确认有入口文件即可
        if complexity == "S":
            # 检查是否有 index.html（可能在根目录或子目录）
            has_html = any(f.endswith("index.html") or f.endswith(".html") for f in existing)
            if has_html:
                return []
            return ["index.html"]

        # M/L: 回退为只检查入口文件存在
        has_html = any(f.endswith("index.html") for f in existing)
        missing = []
        if not has_html:
            missing.append("index.html")
        return missing

    def _check_no_progress(self, state: AgentState) -> bool:
        """检查连续无进展（前 3 轮豁免，给 LLM 足够的探索空间）"""
        if state.get("tool_call_count", 0) <= 3:
            return False
        current_files = set(self.workspace.list())
        last_files = set(state.get("last_file_list") or [])
        state["last_file_list"] = list(current_files)
        if current_files == last_files:
            state["no_progress_count"] = state.get("no_progress_count", 0) + 1
        else:
            state["no_progress_count"] = 0
        return state.get("no_progress_count", 0) >= self.NO_PROGRESS_LIMIT
