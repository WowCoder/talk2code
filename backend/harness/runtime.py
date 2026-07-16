# -*- coding: utf-8 -*-
"""
ToolCallLoop —— Agent ReAct 工具调用循环
从 agents/tool_loop.py 迁移到 harness/runtime.py
"""

import time
import json
import re

from harness.state.agent_state import AgentState
from harness.agent_names import DEV_NAME
from harness.tools.registry import ToolRegistry
from harness.tools.file_tools import FileToolHandler
from harness.tools.code_tools import CodeToolHandler
from harness.tools.preview_tools import PreviewToolHandler
from harness.tools.edit_tools import EditToolHandler
from harness.events import ToolCallEvent, IterationBatchEvent
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
                 checkpoint=None, on_iteration=None):
        self.workspace = workspace
        self.git = git
        self.tools = tools
        self.hooks = hooks
        self.tracer = tracer
        self.cost_tracker = cost_tracker
        self.sse = sse_reporter
        self.checkpoint = checkpoint
        self.on_iteration = on_iteration  # 可选回调，每轮迭代后调用以增量持久化

        # 创建工具处理器（已弃用 — 仅用于 _execute_tool_fallback 回退路径。
        # 新工具应通过 ToolHandler 子类 + ToolRegistry 注册，不使用此实例化方式。）
        self._file_handler = FileToolHandler(workspace)
        self._code_handler = CodeToolHandler(workspace)
        self._preview_handler = PreviewToolHandler(workspace)
        self._edit_handler = EditToolHandler(workspace)

        # 将 workspace 注入注册表中的 ToolHandler 实例
        if self.tools:
            self.tools.set_workspace(workspace)

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
        coder_name = meta.get("coder_name", DEV_NAME)
        thinking_name = meta.get("thinking_name", DEV_NAME)

        # 根据文件数动态计算迭代上限：文件数×2 + 3，上限 20
        # simple 复杂度（单文件）使用固定 5 轮快速通道
        complexity = state.get("metadata", {}).get("complexity", "standard")
        plan_files = (state.get("implementation_order") or
                      (state.get("plan") or {}).get("file_structure", []) or
                      [])
        if complexity == "simple":
            effective_max_iterations = 5
        else:
            file_count = max(len(plan_files), 3)  # 至少按 3 个文件计算
            effective_max_iterations = min(file_count * 2 + 3, 20)

        for iteration in range(effective_max_iterations):
            state["tool_call_count"] = iteration + 1

            # 检查取消信号
            req_id = state.get("metadata", {}).get("requirement_id") or state.get("requirement_id")
            if req_id:
                from services.requirement_service import RequirementService
                if RequirementService.is_cancelled(req_id):
                    logger.info(f"[ToolLoop] 需求 {req_id} 已被取消，终止执行")
                    state["current_step"] = "cancelled"
                    state["error"] = "操作已被用户取消"
                    break

            # 调用 LLM with tools
            messages = self._build_messages(state)
            response = client.chat_with_tools(
                messages=messages,
                tools=self.tools.get_schemas() if self.tools else [],
                max_tokens=8000,
            )

            # ---- LLM 调用失败 → 立即终止，不在循环内死磕 ----
            if response.is_error:
                logger.error(
                    f"[ToolLoop] LLM 调用失败 (iter {iteration + 1}): "
                    f"{response.error or response.content[:200]}"
                )
                state["current_step"] = "llm_error"
                state["error"] = f"LLM 调用失败: {response.error or response.content[:200]}"
                break

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
                    # 连续缺失文件计数器（不重置 no_progress_count，
                    # 让无进展检测也能并行工作）
                    missing_rounds = state.get("_missing_files_rounds", 0) + 1
                    state["_missing_files_rounds"] = missing_rounds
                    if missing_rounds >= 3:
                        logger.warning(
                            f"[ToolLoop] 连续 {missing_rounds} 轮报告文件缺失但无实质进展，"
                            f"强制终止。缺失文件: {missing}"
                        )
                        state["current_step"] = "no_progress"
                        break
                    state["dialogue_history"].append({
                        "role": "system", "name": "System",
                        "content": f"你还没有创建所有必需的文件，缺少：{', '.join(missing)}。"
                                 f"请继续用 write_file 创建剩余文件，不要停止。",
                        "hidden": True,
                        "preserve": True,
                    })
                    continue
                state["current_step"] = "task_complete"
                state["current_step"] = "task_complete"
                state["dialogue_history"].append({
                    "role": "agent", "name": coder_name,
                    "content": response.content or "任务完成"
                })
                break

            # 保存 thinking 到对话历史（仅 LLM 上下文，hidden 避免前端重复展示）
            thinking_text = response.reasoning_content or response.content
            if thinking_text:
                state["dialogue_history"].append({
                    "role": "thinking",
                    "name": thinking_name,
                    "content": thinking_text,
                    "hidden": True,
                })

            # 保存 assistant 回复到对话历史（仅 LLM 上下文，iteration_batch 已包含 agent_text 预览）
            agent_text = response.content[:1500] if response.content else ""
            if response.content and response.tool_calls:
                state["dialogue_history"].append({
                    "role": "assistant",
                    "name": coder_name,
                    "content": agent_text,
                    "hidden": True,
                })

            # ---- 有工具调用 = 有实质进展，重置缺失文件计数器 ----
            state["_missing_files_rounds"] = 0

            # 执行所有工具调用（收集到 batch_tools，统一发送迭代批量事件）
            batch_tools: list[ToolCallEvent] = []
            for tc in response.tool_calls:
                result = self._execute_tool(state, tc)
                logger.info(f"[ToolLoop] 执行 {tc.name}: success={result.success} content={result.content[:100] if result.success else ''} error={result.error[:100] if not result.success else ''}")

                # 生成前端展示用简短标签
                display_readable = self._tool_display_label(tc.name, tc.arguments, result)

                # 收集到批量列表（使用 Pydantic 模型替代松散 dict）
                batch_tools.append(ToolCallEvent(
                    name=tc.name,
                    arguments=tc.arguments,
                    display_label=display_readable,
                    success=result.success,
                    blocked=result.blocked,
                ))

                # 实时推送 code 事件（代码面板需要实时更新）
                if self.sse:
                    if tc.name == "write_file" and result.success:
                        self.sse.code(state["requirement_id"], [{
                            "filename": tc.arguments.get("filename", "unknown"),
                            "content": tc.arguments.get("content", "")
                        }])
                    elif tc.name == "edit_file" and result.success:
                        edited_name = tc.arguments.get("filename", "unknown")
                        try:
                            edited_content = self.workspace.read(edited_name)
                        except Exception:
                            edited_content = ""
                        self.sse.code(state["requirement_id"], [{
                            "filename": edited_name,
                            "content": edited_content
                        }])

                # 工具结果摘要（超大文件智能截断，保留首尾关键内容）
                # blocked 的工具 content 为阻断原因，success=False 但不应取 error（为空）
                tool_summary = result.content if (result.success or result.blocked) else result.error
                is_chat = state.get("metadata", {}).get("is_chat", False)
                if tc.name == "read_file":
                    # read_file: 保留完整内容，只对超大文件做首尾保留
                    max_len = 32000 if is_chat else 24000
                elif tc.name in ("write_file", "edit_file"):
                    max_len = 8000
                else:
                    max_len = 300
                if len(tool_summary) > max_len:
                    # 保留文件头部 + 尾部，让 Agent 看到关键结构（如 export 语句）
                    head_len = int(max_len * 0.7)
                    tail_len = int(max_len * 0.3)
                    head = tool_summary[:head_len]
                    tail = tool_summary[-tail_len:]
                    cut_hint = (
                        f"\n\n... (文件中间部分省略，共 {len(tool_summary)} 字符。"
                        f"如需查看特定行范围，请用 read_file 的 start_line/end_line 参数分页读取)"
                    )
                    tool_summary = head + cut_hint + "\n\n[文件末尾部分]\n" + tail

                # 存入对话历史（供 LLM 上下文，hidden 避免前端重复展示）
                state["dialogue_history"].append({
                    "role": "tool_call",
                    "name": tc.name,
                    "content": tool_summary,
                    "arguments": tc.arguments,
                    "readable": display_readable,
                    "hidden": True,
                })

            # ---- 推送迭代批量事件（替代逐个 tool_call/tool_result/thinking SSE） ----
            if self.sse and batch_tools:
                batch_event = IterationBatchEvent(
                    iteration=iteration + 1,
                    coder_name=coder_name,
                    thinking_preview=(thinking_text or "")[:100],
                    agent_text=agent_text[:300] if agent_text else "",
                    tools=batch_tools,
                    content=f"第 {iteration + 1} 轮迭代 — {len(batch_tools)} 个操作",
                )
                self.sse.iteration_batch(state["requirement_id"], batch_event)

            # 保存迭代批量消息到对话历史（页面刷新后恢复分组展示）
            if batch_tools:
                state["dialogue_history"].append({
                    "role": "iteration_batch",
                    "name": coder_name,
                    "content": f"第 {iteration + 1} 轮迭代 — {len(batch_tools)} 个操作",
                    "iteration": iteration + 1,
                    "thinking_preview": (thinking_text or "")[:100],
                    "agent_text": agent_text[:300] if agent_text else "",
                    "tools": [t.to_dict() for t in batch_tools],
                })

                # Git 自动 commit
                if self.git and tc.name in ("write_file", "edit_file") and result.success:
                    filename = tc.arguments.get("filename", "unknown")
                    self.git.commit(f"[tool] {tc.name}: {filename}")

            # ===== 增强死循环检测：核心操作签名累积 + 读写比 =====
            # 提取当前轮的核心操作签名（tool_name:filename，忽略行范围等参数差异）
            core_sigs = set()
            has_write_or_edit = False
            for tc in (response.tool_calls or []):
                fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
                sig = f"{tc.name}:{fname}"
                core_sigs.add(sig)
                if tc.name in ("write_file", "edit_file"):
                    has_write_or_edit = True

            # 追踪最近 N 轮的核心操作历史（跨轮累积对比）
            recent_history = state.setdefault("_recent_core_sigs", [])
            recent_has_write = state.setdefault("_recent_has_write", [])
            recent_history.append(core_sigs)
            recent_has_write.append(has_write_or_edit)
            HISTORY_WINDOW = 10
            if len(recent_history) > HISTORY_WINDOW:
                recent_history.pop(0)
                recent_has_write.pop(0)

            # 检测 1: 读写比异常 —— 最近 8 轮中纯 read_file 占比过高且无写操作
            if len(recent_history) >= 8:
                read_only_rounds = sum(
                    1 for i, sigs in enumerate(recent_history[-8:])
                    if sigs and all(s.startswith("read_file:") for s in sigs)
                )
                no_write_rounds = sum(1 for w in recent_has_write[-8:] if not w)
                if read_only_rounds >= 3 and no_write_rounds >= 8:
                    state["_read_heavy_count"] = state.get("_read_heavy_count", 0) + 1
                    if state["_read_heavy_count"] >= 2:
                        read_files = set()
                        for sigs in recent_history[-3:]:
                            for s in sigs:
                                if s.startswith("read_file:"):
                                    read_files.add(s.split(":", 1)[1])
                        intervention = (
                            f"你已连续读取同一批文件 {state['_read_heavy_count'] * 4} 轮，"
                            f"没有做任何代码修改。请选择下一步：\n"
                            f"1. 如果代码没问题 → 不要继续读文件，直接结束任务\n"
                            f"2. 如果发现具体问题 → 立即用 edit_file 修改，不要只读不改\n"
                            f"3. 不确定 → 用 run_preview 验证一次，根据结果执行 1 或 2\n"
                            f"已反复读取: {', '.join(read_files)}"
                        )
                        state["dialogue_history"].append({
                            "role": "system", "name": "System",
                            "content": intervention, "hidden": True,
                        })
                        logger.warning(
                            f"[ToolLoop] 读写比异常: 最近 8 轮无写操作，"
                            f"read_heavy_count={state['_read_heavy_count']}，注入干预提示"
                        )
                        if state["_read_heavy_count"] >= 4:
                            logger.warning("读写比异常持续多轮干预，强制终止")
                            state["current_step"] = "no_progress"
                            state["error"] = "诊断死循环: 连续多轮只有读取、无写入操作"
                            break
                else:
                    if state.get("_read_heavy_count", 0) > 0:
                        state["_read_heavy_count"] = max(0, state["_read_heavy_count"] - 1)

            # 检测 2: 连续相同实质性签名（忽略 run_preview/execute_code/lint_js 等辅助工具扰动）
            last_signatures = state.get("last_tool_signatures", set())
            substantive_tools = {"read_file", "write_file", "edit_file"}
            substantive_now = {s for s in core_sigs if s.split(":")[0] in substantive_tools}
            substantive_last = {s for s in last_signatures if s.split(":")[0] in substantive_tools}
            if substantive_now and substantive_now == substantive_last:
                state["repeat_call_count"] = state.get("repeat_call_count", 0) + 1
            else:
                state["repeat_call_count"] = 0
            state["last_tool_signatures"] = core_sigs
            # 连续 4 轮相同实质性调用且无写操作 → 卡住
            if state.get("repeat_call_count", 0) >= 4 and not has_write_or_edit:
                logger.warning(
                    f"[ToolLoop] 连续 {state['repeat_call_count']} 轮相同实质性调用"
                    f" {substantive_now}，判定为无进展"
                )
                state["current_step"] = "no_progress"
                state["error"] = f"连续重复: {', '.join(substantive_now)}"
                break

            # 检查是否达到最大迭代（edit_file 失败后自动 +2 轮用于 write_file 回退）
            if iteration >= effective_max_iterations - 1:
                if state.get("metadata", {}).get("_needs_write_fallback"):
                    # 给 write_file 回退预留额外 2 轮
                    effective_max_iterations += 2
                    state["metadata"]["_needs_write_fallback"] = False
                    logger.info(
                        f"[ToolLoop] edit_file 失败后扩展迭代上限至 {effective_max_iterations}"
                    )
                    # 继续循环（不 break），让 LLM 用 write_file 重写
                    continue
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

        # 任务完成后运行 Hook 检查 + 预览验证，将问题注入上下文
        # 修复由 graph 层 verify→coder 循环统一处理，不再在 ToolCallLoop 内部递归
        if state["current_step"] == "task_complete":
            failures = self._trigger_hooks(state) if self.hooks else []
            preview_errors = self._run_preview_validation(state)
            all_problems = failures + preview_errors
            if all_problems:
                state["dialogue_history"].append({
                    "role": "system", "name": "System",
                    "content": (
                        "代码已生成，验证发现以下问题（将在质量评估后统一修复）：\n"
                        + "\n".join(f"- {p}" for p in all_problems)
                    ),
                    "hidden": True,
                    "preserve": True,
                })

        # Git final commit
        if self.git:
            self.git.commit("[agent] task complete")

        return state

    def _tool_display_label(self, tool_name: str, arguments: dict, result) -> str:
        """生成前端展示用的简短工具标签（不暴露大段文件内容）"""
        if result.blocked:
            return f"⛔ 已跳过 {tool_name}: {result.content[:60]}…" if len(result.content) > 60 else f"⛔ 已跳过 {tool_name}: {result.content}"
        filename = arguments.get("filename", "")
        if tool_name == "read_file":
            lines = result.content.count('\n') + 1 if result.success and result.content else 0
            total = result.metadata.get("total_lines", lines) if result.success and result.metadata else lines
            start = result.metadata.get("start_line", 1) if result.success and result.metadata else 1
            end = result.metadata.get("end_line", lines) if result.success and result.metadata else lines
            return f"📖 读取 {filename} (行 {start}-{end} / 共 {total} 行)"
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

        # 预处理 Hook
        if self.hooks:
            from harness.constraints.hooks import HookContext, HookPoint
            ctx = HookContext(
                requirement_id=state["requirement_id"],
                tool_name=tool_call.name,
                tool_args=tool_call.arguments,
                state=state,
            )
            pre_failures = self.hooks.trigger(HookPoint.PRE_TOOL_USE, ctx)
            if pre_failures:
                # 将阻断信息注入 LLM 上下文（下一轮 system prompt 会注入），
                # 返回 success=True + content=阻断原因：
                # - LLM 视之为"工具返回的信息"而非"工具失败"，不会尝试重试同一工具
                # - 同时注入一条隐藏 system 消息，供下一轮 LLM 调用时显式提示
                failure_msg = "\n".join(pre_failures)
                state.setdefault("_recent_hook_failures", []).append(failure_msg)
                state["dialogue_history"].append({
                    "role": "system", "name": "System",
                    "content": f"[约束提醒] {failure_msg}",
                    "hidden": True,
                })
                logger.info(f"[ToolLoop] PRE_TOOL_USE 跳过（非失败）: {failure_msg[:200]}")
                return ToolResult(content=failure_msg, blocked=True)

        # 分发到对应处理器：优先通过注册表获取 ToolHandler 实例
        handler = None
        if self.tools:
            handler = self.tools.get_handler(tool_call.name)

        if handler:
            # 通过 ToolHandler.execute() 统一接口调用
            result = handler.execute(tool_call.arguments)
        else:
            # 回退：兼容旧的硬编码 handler_map（逐步废弃）
            result = self._execute_tool_fallback(state, tool_call)

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

        # ---- edit_file 失败追踪：自动注入 write_file 回退引导 ----
        if tool_call.name == "edit_file":
            if result.success:
                state["_edit_fail_count"] = 0
                self._update_contract_on_edit(state, tool_call.arguments)
                # edit_file 后也运行增量 lint
                self._auto_lint_after_write(state, tool_call.arguments)
            else:
                state["_edit_fail_count"] = state.get("_edit_fail_count", 0) + 1
                filename = tool_call.arguments.get("filename", "unknown")
                if state["_edit_fail_count"] >= 2:
                    intervention = (
                        f"edit_file 对 {filename} 已连续失败 {state['_edit_fail_count']} 次。\n"
                        f"请立即改用 write_file 重写整个文件：\n"
                        f"1. 先用 read_file 读取 {filename} 的完整内容\n"
                        f"2. 修改需要改的部分\n"
                        f"3. 用 write_file 写入修改后的完整文件\n"
                        f"不要继续尝试 edit_file！"
                    )
                    state["dialogue_history"].append({
                        "role": "system", "name": "System",
                        "content": intervention, "hidden": True,
                    })
                    state.setdefault("metadata", {})["_needs_write_fallback"] = True
                    logger.warning(
                        f"[ToolLoop] edit_file 对 {filename} 失败 {state['_edit_fail_count']} 次，"
                        f"注入 write_file 回退引导"
                    )

        # ---- write_file 成功后更新 CompletionContract + 重置计数 ----
        if tool_call.name == "write_file" and result.success:
            state["_edit_fail_count"] = 0
            self._update_contract_on_write(state, tool_call.arguments)
            # 增量更新文件摘要缓存（避免下次 _build_file_summaries 全量重建）
            self._update_file_summary_cache(tool_call.arguments)
            # 推送任务状态更新到前端（全复杂度通用）
            if self.sse:
                try:
                    self.sse.task_update(
                        state["requirement_id"],
                        tool_call.arguments.get("filename", ""),
                        "completed"
                    )
                except Exception:
                    pass
            # ---- 增量质量信号：写文件后自动运行语法检查 ----
            self._auto_lint_after_write(state, tool_call.arguments)

        return result

    def _execute_tool_fallback(self, state: AgentState, tool_call) -> "ToolResult":
        """回退：硬编码 handler_map（逐步废弃，新增工具应使用 ToolHandler + 注册表）"""
        from harness.tools.registry import ToolResult

        handler_map = {
            "read_file": lambda: self._file_handler.read_file(
                filename=tool_call.arguments.get("filename"),
                start_line=tool_call.arguments.get("start_line"),
                end_line=tool_call.arguments.get("end_line"),
            ),
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
            return handler()
        return self.tools.execute(tool_call.name, tool_call.arguments)

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

    def _get_craft_context(self, requirement: str = '') -> tuple:
        """渐进式加载 Skills，注入到编码 Prompt 中。

        使用 SkillLoader（基于 manifest.json）替代旧的 LLM 选择机制。
        同一任务只做一次匹配，后续轮次复用缓存。

        Returns:
            (rules_text: str, _unused: str)
        """
        try:
            if not hasattr(self, '_skill_cache'):
                self._skill_cache = {}
            cache_key = requirement[:200]  # 用需求前 200 字做缓存键
            if cache_key not in self._skill_cache:
                from harness.instructions.skill_loader import load_for_task
                self._skill_cache[cache_key] = load_for_task(requirement) if requirement else ''
            return self._skill_cache[cache_key], ''
        except Exception:
            return '', ''

    def _build_system_prompt(self, state: AgentState) -> str:
        """构建 Coder 系统提示词（含文件内容概要，避免 Agent 重复 read_file）

        根据复杂度切换提示词策略：
        - simple:  自由文件结构，极简流程，5 轮快速通过
        - standard: 架构先导 + 批量创建 + 完整验证
        """
        requirement = state.get("requirement_content", "")
        plan = state.get("plan")
        complexity = state.get("metadata", {}).get("complexity", "standard")

        existing_files = self.workspace.list()
        existing_text = self._build_file_summaries(existing_files)

        plan_section = ""
        if plan:
            plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if isinstance(plan, dict) else str(plan)
            plan_section = f"""## 实现计划（请严格遵循）
{plan_text}"""

        if complexity == "simple":
            return self._build_simple_prompt(requirement, plan_section, existing_text, existing_files)
        else:
            return self._build_standard_prompt(requirement, plan_section, existing_text, existing_files)

    def _build_simple_prompt(self, requirement: str, plan_section: str,
                              existing_text: str, existing_files: list) -> str:
        """simple 复杂度：自由文件结构，极简流程，5 轮快速通道"""
        from harness.instructions.prompts import load_prompt_template
        craft_rules, skill_instructions = self._get_craft_context(requirement)
        return load_prompt_template("coding/coder_xs.md",
            requirement=requirement,
            plan_section=plan_section,
            existing_text=existing_text,
            craft_rules=craft_rules,
            skill_instructions=skill_instructions,
        )

    def _build_standard_prompt(self, requirement: str, plan_section: str,
                                existing_text: str, existing_files: list) -> str:
        """standard 复杂度：架构先导 + 批量创建 + 完整的浏览��验证"""
        from harness.instructions.prompts import load_prompt_template
        # 从 plan 中提取推荐的文件结构
        file_hint = ""
        plan_obj = json.loads(plan_section.split("\n", 1)[1]) if plan_section and "\n" in plan_section else {}
        if isinstance(plan_obj, dict):
            file_structure = plan_obj.get("file_structure", [])
            if file_structure:
                file_hint = "## 推荐文件结构\n" + "\n".join(f"- {f}" for f in file_structure)
        craft_rules, skill_instructions = self._get_craft_context(requirement)
        return load_prompt_template("coding/coder_ml.md",
            requirement=requirement,
            plan_section=plan_section,
            file_hint=file_hint,
            existing_text=existing_text,
            max_repair_rounds=1,
            complexity="standard",
            craft_rules=craft_rules,
            skill_instructions=skill_instructions,
        )

    def _build_file_summaries(self, existing_files: list) -> str:
        """为已有文件生成内容概要，让 Agent 无需 read_file 就知道文件结构

        优先使用增量缓存（_summary_cache），缓存未命中时才读取文件构建摘要。
        """
        if not existing_files:
            return "(空目录)"

        cache = getattr(self, '_summary_cache', {})
        lines = []
        for fname in existing_files:
            # 优先使用缓存（write_file 后增量更新）
            if fname in cache:
                lines.append(cache[fname])
                continue

            try:
                content = self.workspace.read(fname)
            except Exception:
                lines.append(f"- {fname}: (无法读取)")
                continue

            summary = self._build_single_file_summary(fname, content)
            if summary:
                cache[fname] = summary  # 加入缓存
                lines.append(summary)
            else:
                lines.append(f"- {fname}: (空文件)")

        return '\n'.join(lines)

    def _check_missing_files(self, state: AgentState) -> list[str]:
        """检查目标文件是否全部生成。返回缺失文件名列表。

        优先从 CompletionContract 读取（Default-FAIL 硬约束），
        其次从架构设计的 file_structure 读取目标文件列表，
        避免硬编码与架构设计冲突（如 css/style.css vs style.css）。

        关键修复：当 contract 报告缺失但文件系统中文件已存在时，
        以文件系统为准，自动修复 contract 状态，避免死循环。
        """
        complexity = state.get("metadata", {}).get("complexity", "standard")
        existing = set(self.workspace.list())
        if complexity == "simple":
            if existing:
                return []
            return ["至少一个文件"]

        # 优先从 CompletionContract 获取（硬约束检查清单）
        contract = state.get("_completion_contract")
        if contract and contract.exists():
            pending = contract.pending_files()
            if pending:
                # ---- 文件系统兜底：contract 说缺失但文件实际存在 → 自动修复 ----
                actually_missing = []
                auto_fixed = []
                for f in pending:
                    if f in existing:
                        # 文件实际存在但 contract 未追踪 → 自动标记为已创建
                        try:
                            contract.mark_created(f)
                            auto_fixed.append(f)
                        except Exception:
                            pass
                    else:
                        # 也检查文件名尾部匹配（处理路径差异如 css/style.css vs style.css）
                        basename = f.split("/")[-1]
                        matching = [e for e in existing if e.endswith(basename)]
                        if matching:
                            try:
                                contract.mark_created(f)
                                auto_fixed.append(f)
                            except Exception:
                                pass
                        else:
                            actually_missing.append(f)

                if auto_fixed:
                    logger.info(
                        f"[ToolLoop] Contract 自动修复: {len(auto_fixed)} 个文件 "
                        f"({', '.join(auto_fixed[:5])}) 在文件系统中已存在，已标记为 created"
                    )

                if actually_missing:
                    # ---- 死锁检测：同一批文件连续多轮被报告缺失 ----
                    missing_key = ",".join(sorted(actually_missing))
                    prev_key = state.get("_last_missing_files_key", "")
                    if missing_key == prev_key:
                        state["_same_missing_count"] = state.get("_same_missing_count", 0) + 1
                    else:
                        state["_same_missing_count"] = 1
                    state["_last_missing_files_key"] = missing_key

                    if state["_same_missing_count"] >= 3:
                        logger.warning(
                            f"[ToolLoop] 死锁检测: 相同文件列表已连续报告 "
                            f"{state['_same_missing_count']} 轮缺失但未修复 "
                            f"({actually_missing})。以文件系统为准，清除 contract 阻塞。"
                        )
                        try:
                            contract.clear()
                        except Exception:
                            pass
                        state["_same_missing_count"] = 0
                        state.pop("_last_missing_files_key", None)
                        return []

                    return actually_missing
                return []
            return []

        # 从 plan（TeamLeader/Architect 产出）中提取目标文件结构
        plan = state.get("plan")
        plan_files = []
        if isinstance(plan, dict):
            file_structure = plan.get("file_structure", [])
            if file_structure and isinstance(file_structure, list):
                plan_files = [f for f in file_structure if isinstance(f, str)]

        if plan_files:
            # 使用架构设计中的文件列表，支持子目录路径
            missing = []
            for f in plan_files:
                # 精确匹配或尾部文件名匹配
                if f not in existing:
                    basename = f.split("/")[-1]
                    if not any(e.endswith(basename) for e in existing):
                        missing.append(f)
            return sorted(missing)

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

    def _update_file_summary_cache(self, arguments: dict):
        """write_file 成功后增量更新单个文件的摘要缓存"""
        filename = arguments.get("filename", "")
        content = arguments.get("content", "")
        if not filename:
            return

        if not hasattr(self, '_summary_cache'):
            self._summary_cache = {}

        # 生成单文件摘要
        summary = self._build_single_file_summary(filename, content)
        if summary:
            self._summary_cache[filename] = summary

    def _auto_lint_after_write(self, state: AgentState, arguments: dict):
        """write_file/edit_file 成功后自动运行语法检查，增量注入质量信号

        在 LLM 下一轮迭代中自然看到 lint 反馈，无需 Agent 手动调用 lint 工具。
        只在错误数 <= 5 时注入（过多错误会产生噪声）。
        """
        filename = arguments.get("filename", "")
        if not filename:
            return

        # 根据文件类型选择对应的 lint 方法
        try:
            if filename.endswith('.html'):
                lint_result = self._code_handler.validate_html(filename=filename)
            elif filename.endswith('.css'):
                lint_result = self._code_handler.lint_css(filename=filename)
            elif filename.endswith('.js'):
                lint_result = self._code_handler.lint_js(filename=filename)
            else:
                return  # 不支持的文件类型，跳过

            if not lint_result.success:
                return  # lint 工具本身失败，静默跳过

            # 从 lint 结果中提取错误信息
            lint_content = lint_result.content or ""
            if "通过" in lint_content or "pass" in lint_content.lower():
                return  # 无错误，跳过

            # 解析错误行数，过多时跳过（避免噪声）
            error_lines = [l for l in lint_content.split('\n') if l.strip() and '✗' in l or '❌' in l or 'Error' in l or 'error' in l]
            if len(error_lines) > 5:
                logger.debug(f"[AutoLint] {filename}: {len(error_lines)} 个问题，过多，跳过注入")
                return

            # 注入 lint 结果到下一轮 LLM 上下文
            lint_feedback = (
                f"## 🔍 自动语法检查: {filename}\n"
                f"```\n{lint_content[:1500]}\n```\n"
                f"请在下一轮编码中修复以上问题。"
            )
            state.setdefault("dialogue_history", []).append({
                "role": "system",
                "name": "AutoLint",
                "content": lint_feedback,
                "hidden": True,
                "preserve": True,
            })
            logger.info(f"[AutoLint] {filename}: 检测到 {len(error_lines)} 个问题，已注入反馈")

            # 推送 lint 结果到前端
            if self.sse:
                self.sse.hook_check(
                    state["requirement_id"],
                    f"auto_lint:{filename}",
                    len(error_lines) == 0,
                    lint_content[:500],
                )
        except Exception as e:
            logger.debug(f"[AutoLint] {filename} lint 异常: {e}")

    def _build_single_file_summary(self, fname: str, content: str) -> str:
        """生成单个文件的摘要（提取自 _build_file_summaries）"""
        if not content or not isinstance(content, str):
            return f"- {fname}: (无法读取)"

        import re
        all_lines = [l for l in content.split('\n')]
        content_lines = [l.strip() for l in all_lines if l.strip()]
        head_lines = content_lines[:30]
        tail_lines = content_lines[-10:] if len(content_lines) > 30 else []

        structural = []
        if fname.endswith('.html'):
            for l in content_lines:
                if '<title>' in l:
                    structural.append(l.strip()[:120])
                    break
            ids = set()
            for l in content_lines:
                if 'id="' in l:
                    ids.update(re.findall(r'id="([^"]+)"', l))
                if "id='" in l:
                    ids.update(re.findall(r"id='([^']+)'", l))
            if ids:
                structural.append(f"元素 id: {', '.join(sorted(ids)[:15])}")
            classes = set()
            for l in content_lines:
                if 'class="' in l:
                    classes.update(re.findall(r'class="([^"]+)"', l))
                if "class='" in l:
                    classes.update(re.findall(r"class='([^']+)'", l))
            if classes:
                structural.append(f"CSS class: {', '.join(sorted(classes)[:20])}")
        elif fname.endswith('.css'):
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
            funcs = []
            for l in content_lines:
                m = re.match(r'(?:async\s+)?function\s+(\w+)', l)
                if m:
                    funcs.append(m.group(1))
                m = re.match(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', l)
                if m:
                    funcs.append(m.group(1))
            if funcs:
                structural.append(f"函数: {', '.join(funcs[:15])}")
            dom_refs = set()
            for l in content_lines:
                for m in re.findall(r"(?:querySelector|getElementById|querySelectorAll)\(['\"]([^'\"]+)['\"]\)", l):
                    dom_refs.add(m)
            if dom_refs:
                structural.append(f"DOM 引用: {', '.join(sorted(dom_refs)[:15])}")
            # 检测 export 语句（ES Module 语法）——关键：与 <script> 标签加载方式冲突
            exports = []
            for l in content_lines:
                m = re.match(r'export\s+(?:default\s+)?(?:class|function|const|let|var|\{|\*)', l)
                if m:
                    exports.append(l.strip()[:80])
            if exports:
                structural.append(f"⚠️ EXPORT 语句: {', '.join(exports[:5])} —— 如 index.html 使用普通 <script> 加载，将导致 SyntaxError！")

        head_preview = ' | '.join(head_lines)[:400]
        parts = [f"- {fname} ({len(content_lines)} 行): {head_preview}"]
        if tail_lines:
            tail_preview = ' | '.join(tail_lines)[:200]
            parts.append(f"  ... 尾部: {tail_preview}")
        if structural:
            parts.append("  " + " | ".join(structural))
        return '\n'.join(parts)

    def _update_contract_on_write(self, state: AgentState, arguments: dict):
        """write_file 成功后自动更新 CompletionContract（冗余保障）

        progress_hooks.track_write_success 已通过 POST_TOOL_USE Hook 处理，
        此方法作为额外冗余，确保即使 Hook 失效也能追踪文件创建。

        关键修复：不再依赖 contract.exists() 前置条件。如果 contract 不存在
        或文件不在 contract 中，自动初始化/扩展 contract 并标记 created。
        """
        filename = arguments.get("filename", "")
        if not filename:
            return

        # 1. 追踪最近写入（用于防回读）
        current_round = state.get("tool_call_count", 0)
        state.setdefault("_recent_writes", {})[filename] = current_round

        # 2. 更新 CompletionContract（强制确保追踪）
        from harness.constraints.completion_contract import CompletionContract
        contract = state.get("_completion_contract")
        if contract is None:
            contract = CompletionContract(self.workspace)
            state["_completion_contract"] = contract

        # 如果 contract 文件不存在，从 plan 的 implementation_order 初始化
        if not contract.exists():
            impl_order = state.get("implementation_order", [])
            if impl_order:
                contract.initialize(impl_order)
                logger.info(f"[Contract] write_file 触发 contract 初始化: {len(impl_order)} 个文件")

        # 标记 created —— 如果文件不在 contract 中（动态新增），自动添加
        if not contract.mark_created(filename):
            # 文件不在 contract 中，追加进去
            contract.add_file(filename, created=True)
            logger.info(f"[Contract] 追加动态文件: {filename}")

    def _update_contract_on_edit(self, state: AgentState, arguments: dict):
        """edit_file 成功后标记文件为已验证

        修复阶段 DEV 优先使用 edit_file 而非 write_file，
        因此需要单独追踪 edit_file 来更新 contract 状态。
        """
        filename = arguments.get("filename", "")
        if not filename:
            return

        # 追踪最近修改（用于防回读）
        current_round = state.get("tool_call_count", 0)
        state.setdefault("_recent_writes", {})[filename] = current_round

        # 更新 CompletionContract: 标记文件为已验证
        from harness.constraints.completion_contract import CompletionContract
        contract = state.get("_completion_contract")
        if contract is None:
            contract = CompletionContract(self.workspace)
            state["_completion_contract"] = contract

        # 如果 contract 不存在，尝试初始化
        if not contract.exists():
            impl_order = state.get("implementation_order", [])
            if impl_order:
                contract.initialize(impl_order)

        # 如果文件不在 contract 中，动态添加
        if not contract.is_created(filename):
            if not contract.mark_created(filename):
                contract.add_file(filename, created=True)
        contract.mark_validated(filename)

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
