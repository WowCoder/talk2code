# -*- coding: utf-8 -*-
"""
ToolCallLoop —— Agent ReAct 工具调用循环
从 agents/tool_loop.py 迁移到 harness/runtime.py
"""

import time
import json
import re
import hashlib

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
    # 读写比检测：窗口轮数 / 触发几次干预后强制终止。
    # 窗口必须明显小于迭代预算（min(文件数+3, 10) ≤ 10），否则终止分支永远跑不到。
    READ_HEAVY_WINDOW = 6
    READ_HEAVY_ABORT = 3
    # 同一文件被读取多少轮、且连续多少轮没有写入 → 判定反复回读
    REPEAT_READ_LIMIT = 4
    REPEAT_READ_NO_WRITE = 3
    # 写入后多少轮内回读该文件，追加"内容未变"提示
    READBACK_GUARD_ROUNDS = 5
    # 同一文件在一次任务内最多读取几次（超出直接跳过执行）。
    # 实测 req 183：index.html 被读 6 次、main.js 4 次，全是"确认式重读"，
    # 每次都要付一整轮 LLM 往返（2-45s），却零产出。软提示挡不住，只能硬限。
    READ_FILE_LIMIT_PER_TASK = 4
    # 「创建后免回读」：write_file 创建/整体重写的文件，正文已经在工具结果里，
    # 此后只要没人改过它，再 read_file 就是纯浪费一轮 LLM 往返 —— 直接跳过执行。
    # 需求 198 实测：css/style.css 创建后 3 轮里读了 2 次，js/game.js 亦然，
    # 每次白白付一整轮往返（弱模型尤其爱"先读确认"）。
    SKIP_READ_AFTER_CREATE = True
    # write_file 结果保留上限：创建时把完整正文留在上下文里，LLM 就不必再读一遍。
    # 此前统一截到 4000 字符（首尾预览），模型看不到中间部分，只能回读 → 死循环。
    WRITE_RESULT_MAX_LEN = 16000
    # 自修复保底：预算将尽且手上有确定性运行时错误时，一次性追加的轮数
    SELF_REPAIR_EXTRA_ITERATIONS = 3

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

        # LLM 最大生成 token 数：从配置读取，支持 reasoning 模型的额外思考开销
        from config import settings as _settings
        self._settings = _settings
        self._max_tokens = _settings.LLM_MAX_TOKENS

        # run 级复用的可观测性 writer（见 run/_run_impl）。None 表示未启用，
        # 此时埋点走每事件一个短 session 的降级路径。
        self._trace_writer = None

    def run(self, state: AgentState) -> AgentState:
        """整个 run 内复用一个 TraceWriter。

        为什么不在每个事件里单开 session：seq 是「读 MAX + 1」分配，每事件
        都要多一次 MAX 查询 + 一次 session 创建，一个需求上百个事件就是上百次
        额外 DB 往返，全压在生成主链路上。这里在入口建一个长 writer，
        在 finally 关闭；建不出来（DB 不可用 / 无 requirement_id）就退回
        原来的短 session 路径，埋点不会因此丢失。
        """
        req_id = (state.get("metadata", {}).get("requirement_id")
                  or state.get("requirement_id"))
        db = None
        if req_id:
            try:
                from models.models import SessionLocal
                from harness.observability.trace_writer import TraceWriter
                md = state.get("metadata") or {}
                db = SessionLocal()
                self._trace_writer = TraceWriter(
                    db, requirement_id=req_id,
                    trace_id=md.get("trace_id") or None,
                    turn_index=int(md.get("turn_index") or 0),
                )
            except Exception as e:
                logger.debug(f"[TraceWriter] run 级 writer 创建失败（降级为逐事件写入）: {e}")
                self._trace_writer = None
                db = None
        try:
            return self._run_impl(state)
        finally:
            self._trace_writer = None
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _run_impl(self, state: AgentState) -> AgentState:
        client = get_client()
        trace_id = state.get("metadata", {}).get("trace_id", "")
        # 对话轮次：初次生成为 0；多轮对话由 chat 路由按「已有最大轮次 + 1」写入。
        # 不在这里从 dialogue_history 派生 —— 那样既慢又脆弱（见 TraceWriter 注释）。
        turn_index = int(state.get("metadata", {}).get("turn_index") or 0)

        # 每次进入 run() 重置运行时计数器，避免多轮调用（如逐文件编码、修复循环）间状态污染
        state["no_progress_count"] = 0
        state["repeat_call_count"] = 0
        state["last_tool_signatures"] = set()
        state["tool_call_count"] = 0
        # 重置缺失文件检测计数器：逐文件编码模式下每个文件独立计数，
        # 避免上一个文件的计数器残留导致当前文件被误判为"连续缺失"而提前终止
        state["_missing_files_rounds"] = 0
        state["_same_missing_count"] = 0
        state.pop("_last_missing_files_key", None)
        # 死循环检测计数器同样必须重置：此前只重置了上面几个，
        # _read_heavy_count / _recent_core_sigs / _recent_has_write / _recent_writes
        # 会跨阶段（批量编码 → 修复轮 → 逐文件补全）残留，与本处
        # "避免多轮调用间状态污染"的意图自相矛盾。
        state["_read_heavy_count"] = 0
        state["_recent_core_sigs"] = []
        state["_recent_has_write"] = []
        state["_read_file_rounds"] = {}
        state["_consecutive_no_write"] = 0
        state["_recent_writes"] = {}
        # 「创建后免回读」登记表：filename → {hash, round, lines}。
        # 同样必须随阶段重置 —— 残留会让修复轮误跳过合法的首次读取。
        state["_known_content_files"] = {}
        state["_missing_reminder_key"] = ""
        state["_missing_reminder_count"] = 0

        # 可配置的角色名称（多角色协作用，默认兼容旧行为）
        meta = state.get("metadata", {})
        coder_name = meta.get("coder_name", DEV_NAME)
        thinking_name = meta.get("thinking_name", DEV_NAME)

        # 根据文件数动态计算迭代上限：文件数 + 3，上限 10
        # （原先文件数×2+3 过于宽松，贪吃蛇这类 6 文件项目可跑到 15 轮；
        #   收紧后 6 文件 → 9 轮，配合思考模式关闭，单轮成本已大幅下降）
        # simple 复杂度（单文件）使用固定 5 轮快速通道
        complexity = state.get("metadata", {}).get("complexity", "standard")
        plan_files = (state.get("implementation_order") or
                      (state.get("plan") or {}).get("file_structure", []) or
                      [])
        if complexity == "simple":
            effective_max_iterations = 5
        else:
            file_count = max(len(plan_files), 3)  # 至少按 3 个文件计算
            effective_max_iterations = min(file_count + 3, 10)

        # 实例级上限：chat/逐文件编码会覆盖 MAX_ITERATIONS 以收紧轮数
        effective_max_iterations = min(effective_max_iterations, self.MAX_ITERATIONS)

        # 用 while 循环（而非 for range）：允许 edit_file 失败后动态 +2 轮回退
        iteration = 0
        while iteration < effective_max_iterations:
            # 自修复保底（req 147）：预算将尽但 Agent 手上还压着一个确定性的运行时错误
            # （如 run_preview 报的 pageerror）→ 一次性追加若干轮让它把错误修掉。
            # 只追加一次，避免把"迭代上限"退化成"无上限"。
            if (
                effective_max_iterations - iteration <= 1
                and state.get("_deterministic_error_pending")
                and not state.get("_self_repair_extended")
            ):
                state["_self_repair_extended"] = True
                state["_deterministic_error_pending"] = False
                effective_max_iterations += self.SELF_REPAIR_EXTRA_ITERATIONS
                logger.info(
                    f"[ToolLoop] 检测到未修复的确定性运行时错误，自修复保底："
                    f"迭代预算 {effective_max_iterations - self.SELF_REPAIR_EXTRA_ITERATIONS} → "
                    f"{effective_max_iterations}"
                )
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

            # 追踪 LLM 调用：span 必须包住 chat_with_tools 才能记录真实耗时
            # （原先在调用返回后才 start_span，导致 duration 恒为 0，无法观测耗时）
            span = None
            if self.tracer and trace_id:
                span = self.tracer.start_span(trace_id, f"tool_coder_iter_{iteration}")

            # 调用 LLM with tools
            messages = self._build_messages(state)
            # 思考模式可按节点覆盖：coder 经 metadata["tool_thinking"]="disabled" 关闭
            # （实测写码大轮 reasoning tokens 占 completion 60-75%，req 156：
            #   10610 token 中 6877 为思考，是编码耗时的最大单因素）；
            # 其余节点不设置该键，维持默认 enabled。
            thinking_mode = meta.get("tool_thinking", "enabled")
            try:
                response = self._chat_with_breaker(
                    client, messages,
                    self.tools.get_schemas() if self.tools else [],
                    thinking_mode, iteration,
                )
            except Exception as e:
                # 熔断器打开或其他 LLM 不可用异常 → 立即终止
                error_msg = str(e)
                logger.error(
                    f"[ToolLoop] LLM 调用异常 (iter {iteration + 1}): {error_msg}"
                )
                if span:
                    self.tracer.end_span(span, status="error", error=error_msg)
                state["current_step"] = "llm_error"
                state["error"] = f"LLM 调用失败（熔断或不可用）: {error_msg}"
                break

            # ---- LLM 调用失败 → 立即终止，不在循环内死磕 ----
            if response.is_error:
                logger.error(
                    f"[ToolLoop] LLM 调用失败 (iter {iteration + 1}): "
                    f"{response.error or response.content[:200]}"
                )
                if span:
                    self.tracer.end_span(
                        span, status="error",
                        error=response.error or response.content[:200]
                    )
                state["current_step"] = "llm_error"
                state["error"] = f"LLM 调用失败: {response.error or response.content[:200]}"
                break

            # 记录 token 用量 + 结束 span（真实耗时 = start_span 到此刻）
            if span:
                if response.usage and self.cost_tracker:
                    input_tokens, output_tokens = self.cost_tracker.extract_usage(
                        response.usage, client.provider
                    )
                    self.cost_tracker.record(trace_id, input_tokens, output_tokens, client.model)
                    span.metadata["tokens"] = input_tokens + output_tokens
                self.tracer.end_span(span)

            # 统一埋点：文件明细（AGENT_EXEC_LOG=1）+ 运营后台索引。
            # 以前只写文件 —— 运营后台因此查不到任何实时数据，只能靠回填脚本补。
            try:
                from harness.observability.trace_writer import record_llm_turn
                from harness.observability.event_contract import STAGE_CODING
                record_llm_turn(
                    req_id, stage=STAGE_CODING,
                    iteration=iteration + 1, model=getattr(client, "model", None),
                    messages=messages,
                    tools=self.tools.get_schemas() if self.tools else [],
                    response=response,
                    thinking=thinking_mode,
                    latency_ms=(
                        round((span.end_time - span.start_time) * 1000, 1)
                        if span and span.end_time else None
                    ),
                    turn_index=turn_index, trace_id=trace_id,
                    writer=self._trace_writer,
                )
            except Exception as _e:
                logger.debug(f"[TraceWriter] llm_turn 记录失败（不阻断）: {_e}")

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
                    iteration += 1
                    continue
                # ---- 语法硬门禁：带语法错误不允许结束 coder 阶段 ----
                # 背景（req 200）：js/app.js 在第 5 轮就被 lint 报出
                # `SyntaxError: missing ) after argument list`，但 coder 带着它走完
                # 剩余轮次进了 verify —— verify 再用 54s 的 thinking 评估去"发现"
                # 这个 coder 阶段就已知的错误，defect_repair 又全部超时失败。
                # 一个本可在 coder 阶段 10 秒内修掉的语法错误，烧掉了后续 30 分钟。
                _syntax_errs = self._check_deliverable_syntax()
                if _syntax_errs:
                    _syn_rounds = state.get("_syntax_block_rounds", 0) + 1
                    state["_syntax_block_rounds"] = _syn_rounds
                    if _syn_rounds >= 3:
                        # 终止性保证：连续 3 轮仍修不好就放行，交给 verify /
                        # defect_repair 兜底，避免门禁本身把循环卡死到迭代上限
                        logger.warning(
                            f"[ToolLoop] 语法门禁连续 {_syn_rounds} 轮阻断仍未修复，"
                            f"放行完成: {_syntax_errs[:2]}"
                        )
                    else:
                        logger.warning(
                            f"[ToolLoop] 语法门禁阻断完成（第 {_syn_rounds} 轮）: {_syntax_errs}"
                        )
                        state["dialogue_history"].append({
                            "role": "system", "name": "System",
                            "content": (
                                "⚠️ 你的交付存在**机器可判定的硬伤，必须先修好才能结束**"
                                "（带病交付会让后续验收全部失败）：\n"
                                + "\n".join(f"- {e}" for e in _syntax_errs)
                                + "\n（语法错误用 edit_file 修复对应片段；引用了不存在的文件"
                                  "就补写该文件，或把引用改成你实际创建的文件名。）\n"
                                  "修好后再次声明完成，不要直接结束。"
                            ),
                            "hidden": True,
                            "preserve": True,
                        })
                        iteration += 1
                        continue

                state["current_step"] = "task_complete"
                # 完成硬约束接线：Agent 以"无 tool_calls"声明完成时，触发
                # PRE_TOOL_USE（约定信号 tool_name=None + current_step=task_complete），
                # 由 block_premature_completion 校验 CompletionContract。
                # 此前该 Hook 只挂在 _execute_tool 内部，真实完成路径完全绕过它。
                if self.hooks:
                    from harness.constraints.hooks import HookContext, HookPoint
                    # 注入 _workspace / file_list：完成时刻的契约校验
                    # （引用闭合 ENV-6、AC 预检）需要读取工作区真实状态
                    completion_ctx_state = dict(state)
                    completion_ctx_state["_workspace"] = self.workspace
                    completion_ctx_state["file_list"] = self.workspace.list()
                    ctx = HookContext(
                        requirement_id=state["requirement_id"],
                        tool_name=None,
                        state=completion_ctx_state,
                    )
                    contract_failures = self.hooks.trigger(HookPoint.PRE_TOOL_USE, ctx)
                    if contract_failures:
                        block_rounds = state.get("_contract_block_rounds", 0) + 1
                        state["_contract_block_rounds"] = block_rounds
                        failure_msg = "\n".join(contract_failures)
                        # 终止性保证：连续 3 轮阻断仍无法推进则放行并告警，
                        # 避免约束本身把循环卡死到迭代上限
                        if block_rounds >= 3:
                            logger.warning(
                                f"[ToolLoop] 完成约束连续 {block_rounds} 轮阻断仍未推进，"
                                f"放行完成。原因: {failure_msg[:200]}"
                            )
                        else:
                            state["dialogue_history"].append({
                                "role": "system", "name": "System",
                                "content": failure_msg,
                                "hidden": True,
                                "preserve": True,
                            })
                            logger.info(
                                f"[ToolLoop] 完成约束阻断声明完成 (第 {block_rounds} 轮): "
                                f"{failure_msg[:200]}"
                            )
                            state["current_step"] = "coding"
                            iteration += 1
                            continue
                    else:
                        state["_contract_block_rounds"] = 0

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

            # ---- 只有写入/编辑才算实质进展 ----
            # 此前把"有工具调用"等同于"有进展"，于是模型每轮发两个 read_file
            # 就能无限重置缺失文件计数器，系统再也不会催它补文件（需求 182 空转根因）。
            _round_has_write = any(
                _tc.name in ("write_file", "edit_file") for _tc in response.tool_calls
            )
            if _round_has_write:
                state["_missing_files_rounds"] = 0

            # 执行所有工具调用（收集到 batch_tools，统一发送迭代批量事件）
            batch_tools: list[ToolCallEvent] = []
            written_files: list[str] = []  # 本轮成功写入/编辑的文件（用于聚合 Git commit）
            _iter_started = False  # 本轮是否已发 iteration_start（首工具到达时惰性发送）
            # 轮次起止时间：落库后前端才能在轮次卡片上显示「起止时间」
            try:
                from utils.sse import get_current_timestamp as _gct_iter
                _iter_start_ts = _gct_iter()
            except Exception:
                _iter_start_ts = None
            for tc in response.tool_calls:
                # 动作级进度：在**执行前**推送，让用户看到"正在写 X"而不是转圈
                self._push_activity(state, tc.name, tc.arguments,
                                    iteration, effective_max_iterations)

                # 读取次数硬上限：确认式重读到第 N 次直接跳过，省掉一整轮往返
                _read_limited = False
                if tc.name == "read_file" and isinstance(tc.arguments, dict):
                    _rf = tc.arguments.get("filename", "") or ""
                    _counts = state.setdefault("_read_file_counts", {})
                    _counts[_rf] = _counts.get(_rf, 0) + 1
                    if _counts[_rf] > self.READ_FILE_LIMIT_PER_TASK:
                        _read_limited = True
                        from harness.tools.registry import ToolResult as _ToolResult
                        result = _ToolResult(
                            blocked=True,
                            content=(
                                f"[已跳过] {_rf} 在本次任务中已被读取 {_counts[_rf] - 1} 次，"
                                f"达到上限 {self.READ_FILE_LIMIT_PER_TASK} 次。内容就在上方对话历史里，"
                                f"请直接基于已有内容继续写代码，不要再读它确认。"
                                f"{self._file_preview_snippet(_rf)}"
                            ),
                        )
                        logger.info(f"[ToolLoop] read_file 次数上限: {_rf} 第 {_counts[_rf]} 次，跳过执行")

                # 创建后免回读：文件是你在本次任务里 write_file 创建/整体重写的，
                # 此后**没有任何人改过它**（磁盘内容与写入时逐字相同）——那完整正文
                # 就在上方 write_file 的结果里，再读一遍是纯粹浪费一轮往返。
                # 与次数上限、同内容去重互补：那两条拦的是"第 N 次重读"，这条拦的是
                # "创建后的第一次确认式回读"（需求 198 实测每文件至少省一轮）。
                # 同样只对整文件读取生效——分页读取是子集，模型可能在找特定行。
                if (
                    self.SKIP_READ_AFTER_CREATE
                    and not _read_limited
                    and tc.name == "read_file"
                    and isinstance(tc.arguments, dict)
                    and not tc.arguments.get("start_line")
                    and not tc.arguments.get("end_line")
                ):
                    _block = self._created_read_block(
                        state, tc.arguments.get("filename", "") or ""
                    )
                    if _block is not None:
                        _read_limited = True
                        result = _block

                # 同内容去重：文件自上次读取后**一个字节都没变**，再读一遍纯属浪费上下文。
                # 需求 196 实测：fix 循环里 js/game.js / js/app.js / index.html 被连读三轮，
                # 三次返回完全相同的 10135 字符（弱模型每轮重启"先读确认"计划）。
                # 次数上限拦不住（每段各读 4 次刚好在上限之下），所以按内容指纹拦截。
                # 只对"整文件读取"生效——带 start_line/end_line 的分页读取内容是子集，不可比。
                if (
                    not _read_limited
                    and tc.name == "read_file"
                    and isinstance(tc.arguments, dict)
                    and not tc.arguments.get("start_line")
                    and not tc.arguments.get("end_line")
                ):
                    _rf2 = tc.arguments.get("filename", "") or ""
                    if _rf2:
                        try:
                            import hashlib as _hashlib
                            _raw_now = self.workspace.read(_rf2)
                            _h_now = _hashlib.md5(
                                _raw_now.encode("utf-8", "ignore")
                            ).hexdigest()
                            _prev_hashes = state.setdefault("_read_file_hashes", {})
                            if _prev_hashes.get(_rf2) == _h_now:
                                _read_limited = True
                                from harness.tools.registry import ToolResult as _TR2
                                result = _TR2(
                                    blocked=True,
                                    content=(
                                        f"[已跳过] {_rf2} 自上次读取后内容完全没有变化，"
                                        f"你拿到的会是和上一轮逐字相同的结果（已在上方工具结果里）。"
                                        f"不要为确认而重读它 —— 直接基于已有内容继续；"
                                        f"需要改动就用 write_file / edit_file 写入。"
                                        # 同内容重读同理：直接把首尾贴出来，省掉下一次尝试
                                        f"{self._file_preview_snippet(_rf2)}"
                                    ),
                                )
                                logger.info(f"[ToolLoop] 同内容重读跳过: {_rf2}")
                            else:
                                _prev_hashes[_rf2] = _h_now
                        except Exception:
                            pass  # 读不到（文件不存在等）时放行，交给正常执行路径报错

                if not _read_limited:
                    result = self._execute_tool(state, tc)
                logger.info(f"[ToolLoop] 执行 {tc.name}: success={result.success} content={result.content[:100] if result.success else ''} error={result.error[:100] if not result.success else ''}")

                # 统一埋点：文件明细 + 运营后台索引
                try:
                    from harness.observability.trace_writer import record_tool_call
                    record_tool_call(
                        req_id, name=tc.name, arguments=tc.arguments,
                        result=result, iteration=iteration + 1,
                        turn_index=turn_index, trace_id=trace_id,
                        writer=self._trace_writer,
                    )
                except Exception as _e:
                    logger.debug(f"[TraceWriter] tool_call 记录失败（不阻断）: {_e}")

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

                if result.success and tc.name in ("write_file", "edit_file"):
                    fname = tc.arguments.get("filename", "unknown") if isinstance(tc.arguments, dict) else "unknown"
                    written_files.append(fname)

                # 实时推送单个工具操作（首工具到达时惰性创建轮次卡片，过程逐步累积）
                if self.sse:
                    if not _iter_started:
                        self.sse.iteration_start(state["requirement_id"], IterationBatchEvent(
                            iteration=iteration + 1,
                            coder_name=coder_name,
                            thinking_preview=(thinking_text or "")[:100],
                            agent_text=agent_text[:300] if agent_text else "",
                            tools=[],
                            content=f"第 {iteration + 1} 轮迭代",
                        ))
                        _iter_started = True
                    self.sse.iteration_append(state["requirement_id"], ToolCallEvent(
                        name=tc.name,
                        arguments=tc.arguments,
                        display_label=display_readable,
                        success=result.success,
                        blocked=result.blocked,
                    ))

                # 防回读：消费 _recent_writes（此前只有写入侧，全库无消费方 → 死字段）
                if tc.name == "read_file" and result.success:
                    self._annotate_readback(state, tc, result)
                    # 创建时塞进上下文的正文已被这次读取取代 → 卸载掉，别再占着 token。
                    # 只在"确实读到新内容"时卸载：读被跳过的（blocked）不能卸载，
                    # 否则模型手上唯一的正文副本会被抹掉。
                    self._offload_created_context(state, tc)

                # write_file 成功 → 这个文件的最新完整正文已在上下文里，记下指纹。
                # 之后只要磁盘内容没变，回读一律跳过（见上方"创建后免回读"）。
                if tc.name == "write_file" and result.success:
                    self._track_known_content(state, tc)

                # edit_file 成功 → 磁盘内容变了，模型手上的副本已过期，
                # 必须允许它重新 read_file 取准确片段（否则 SEARCH 匹配不上会反复失败）。
                if tc.name == "edit_file" and result.success:
                    self._untrack_known_content(state, tc)

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
                    max_len = 32000 if is_chat else 12000
                elif tc.name == "write_file":
                    # write_file: 创建/整体重写的结果就是「这份文件的权威副本」。
                    # 此前和 edit_file 一样只留 4000 字符，模型看不到中间部分，
                    # 只能再花一轮 read_file 回读（需求 198：创建后立刻回读同一文件）。
                    # 保留完整输出 = 省掉那一次回读，比省 token 更划算。
                    max_len = self.WRITE_RESULT_MAX_LEN
                elif tc.name == "edit_file":
                    max_len = 4000
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

            # ---- 轮次结束事件：通知前端固定累积卡片 ----
            # 单个工具已通过 iteration_append 实时推送，这里只需发结束信号。
            # iteration_batch 整轮一次性事件已废弃（不再发送），避免 SSE 缓冲堆积造成断线回放时
            # 「空转几秒后一次性刷出整轮」的体感问题（需求：Coder 轮次实时累积）。
            if self.sse and batch_tools:
                self.sse.iteration_end(
                    state["requirement_id"],
                    iteration + 1,
                )

            # 保存迭代批量消息到对话历史（页面刷新后恢复分组展示）
            if batch_tools:
                try:
                    from utils.sse import get_current_timestamp as _gct_iter2
                    _iter_end_ts = _gct_iter2()
                except Exception:
                    _iter_end_ts = None
                state["dialogue_history"].append({
                    "role": "iteration_batch",
                    "name": coder_name,
                    "content": f"第 {iteration + 1} 轮迭代 — {len(batch_tools)} 个操作",
                    "iteration": iteration + 1,
                    # 一轮=一条消息：思考与回复一并收进卡片内部渲染（前端折叠展示），
                    # 不再单独落 thinking/assistant 消息（那两条已是 hidden，仅供 LLM 上下文）
                    "thinking_preview": (thinking_text or "")[:2000],
                    "agent_text": agent_text or "",
                    "tools": [t.to_dict() for t in batch_tools],
                    "start_ts": _iter_start_ts,
                    "end_ts": _iter_end_ts,
                    "timestamp": _iter_end_ts,
                })

                # Git 自动 commit（聚合本轮全部成功写入，避免依赖循环残留变量
                # 导致"最后工具非写操作时不提交/消息只反映单文件"的问题）
                if self.git and written_files:
                    if len(written_files) <= 3:
                        commit_msg = f"[tool] {', '.join(written_files)}"
                    else:
                        commit_msg = f"[tool] 写入 {len(written_files)} 个文件: {', '.join(written_files[:3])} 等"
                    self.git.commit(commit_msg)

            # ===== 缺文件提醒：每轮投递 =====
            # 此前"还缺 X 文件"只挂在"本轮零 tool_calls"分支上，模型只要在空转中
            # 顺手发一个 read_file，就永远收不到这条指令（需求 182 空转 18 轮根因）。
            self._maybe_remind_missing_files(state, iteration)

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

            # 检测 1: 读写比异常 —— 最近若干轮几乎全是读、且没有写入
            # 收紧前：窗口 8 轮 + 终止阈值 4，而迭代预算 = min(文件数+3, 10)，
            # 检测最早第 8 轮才可能首次触发，终止分支数学上不可达（死代码）。
            # 需求 182 空转 18 轮只拿到干预提示，从未被终止。
            if len(recent_history) >= self.READ_HEAVY_WINDOW:
                window = recent_history[-self.READ_HEAVY_WINDOW:]
                read_only_rounds = sum(
                    1 for sigs in window
                    if sigs and all(s.startswith("read_file:") for s in sigs)
                )
                no_write_rounds = sum(
                    1 for w in recent_has_write[-self.READ_HEAVY_WINDOW:] if not w
                )
                if read_only_rounds >= 3 and no_write_rounds >= self.READ_HEAVY_WINDOW - 1:
                    state["_read_heavy_count"] = state.get("_read_heavy_count", 0) + 1
                    count = state["_read_heavy_count"]

                    if count >= self.READ_HEAVY_ABORT:
                        logger.warning(
                            f"[ToolLoop] 读写比异常经 {count} 次干预仍未写入，强制终止"
                        )
                        state["current_step"] = "no_progress"
                        state["error"] = "诊断死循环: 连续多轮只有读取、无写入操作"
                        break

                    read_files = set()
                    for sigs in recent_history[-3:]:
                        for s in sigs:
                            if s.startswith("read_file:"):
                                read_files.add(s.split(":", 1)[1])
                    intervention = (
                        f"你已连续读取同一批文件多轮，没有做任何代码修改。请选择下一步：\n"
                        f"1. 如果代码没问题 → 不要继续读文件，直接结束任务\n"
                        f"2. 如果发现具体问题 → 立即用 edit_file 修改，不要只读不改\n"
                        f"3. 不确定 → 用 run_preview 验证一次，根据结果执行 1 或 2\n"
                        f"已反复读取: {', '.join(read_files)}\n"
                        f"警告：继续只读不改将被系统判定为无进展并终止本轮编码。"
                    )
                    state["dialogue_history"].append({
                        "role": "system", "name": "System",
                        "content": intervention, "hidden": True,
                    })
                    logger.warning(
                        f"[ToolLoop] 读写比异常: 最近 {self.READ_HEAVY_WINDOW} 轮几乎无写操作，"
                        f"read_heavy_count={count}，注入干预提示"
                    )
                else:
                    if state.get("_read_heavy_count", 0) > 0:
                        state["_read_heavy_count"] = max(0, state["_read_heavy_count"] - 1)

            # 检测 2: 同一文件被反复读取（按文件累计轮次，不看签名集合）
            # 旧实现要求两轮的签名集合完全相等，模型只要每轮换一个搭配
            # （css+utils → css+contract → 单读 css）就能绕过，且"同一文件读 10 次"
            # 根本不算重复。改为按文件累计读取轮次判定。
            read_this_round = {
                s.split(":", 1)[1] for s in core_sigs if s.startswith("read_file:")
            }
            read_round_counts = state.setdefault("_read_file_rounds", {})
            for fname in read_this_round:
                if fname:
                    read_round_counts[fname] = read_round_counts.get(fname, 0) + 1
            state["_consecutive_no_write"] = (
                0 if has_write_or_edit else state.get("_consecutive_no_write", 0) + 1
            )
            if has_write_or_edit:
                read_round_counts.clear()

            hot_reads = [
                f for f, c in read_round_counts.items() if c >= self.REPEAT_READ_LIMIT
            ]
            if hot_reads and state["_consecutive_no_write"] >= self.REPEAT_READ_NO_WRITE:
                logger.warning(
                    f"[ToolLoop] 文件被反复读取且持续无写入，判定为无进展: "
                    f"{[(f, read_round_counts[f]) for f in hot_reads]}"
                )
                state["current_step"] = "no_progress"
                state["error"] = f"反复读取且未写入: {', '.join(hot_reads)}"
                break

            # 检测 3: 连续相同实质性签名（忽略 run_preview/execute_code/lint_js 等辅助工具扰动）
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
                    iteration += 1
                    continue
                # ---- 交付门禁保底：迭代耗尽也必须先过「确定性硬伤」检查 ----
                # 背景（t16 实测）：交付门禁此前只挂在「模型主动声明完成」这条路径上
                # （无 tool_calls → task_complete），而真实失败大多发生在「迭代耗尽」
                # 这条路：t16 连续 4 次运行全部在第 6 轮耗尽退出，其中一次已经写出了
                # index.html 却引用了从未创建的 js 文件 —— 门禁一次都没执行，坏代码
                # 被静默交付给 verify，再由 verify 花几十秒的 thinking 去「发现」它。
                # 这里只放行一次扩容（与上面的自修复保底同款纪律），避免把
                # 「迭代上限」退化成「无上限」。
                if not state.get("_gate_extended"):
                    # 两类确定性硬伤都要在耗尽前兜一次：
                    #   ① 必需文件根本没创建（t16 实测形态：索引页缺失 / 仍被引用）
                    #   ② 写了但语法错，或引用了不存在的资源
                    _final_errs = []
                    if not state.get("metadata", {}).get("is_chat", False):
                        _missing_final = self._check_missing_files(state)
                        if _missing_final:
                            _final_errs.append(
                                "必需文件还没创建：" + ", ".join(_missing_final)
                            )
                    _final_errs.extend(self._check_deliverable_syntax())
                    if _final_errs:
                        state["_gate_extended"] = True
                        effective_max_iterations += self.SELF_REPAIR_EXTRA_ITERATIONS
                        logger.warning(
                            f"[ToolLoop] 迭代耗尽但交付仍有确定性硬伤，扩容 "
                            f"{effective_max_iterations - self.SELF_REPAIR_EXTRA_ITERATIONS} → "
                            f"{effective_max_iterations} 轮: {_final_errs[:3]}"
                        )
                        state["dialogue_history"].append({
                            "role": "system", "name": "System",
                            "content": (
                                "⚠️ 迭代即将用完，但你的交付仍不完整，已为你追加若干轮。"
                                "请立刻补齐（不要再写新功能、不要再更新笔记）：\n"
                                + "\n".join(f"- {e}" for e in _final_errs)
                                + "\n（缺文件就 write_file 补上；语法错误用 edit_file 修复；"
                                  "引用了不存在的文件就补写该文件，"
                                  "或把引用改成你实际创建的文件名。）"
                            ),
                            "hidden": True,
                            "preserve": True,
                        })
                        iteration += 1
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
                    # 不阻断主流程，但必须显式暴露——静默降级会让断点恢复名存实亡
                    logger.error("保存检查点失败（不阻断，断点恢复将不可用）：%s", e)

            iteration += 1

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
            # 不带 ⛔ 前缀：前端操作列表会按 blocked 状态自带 ⛔ 图标，
            # 这里再带一个会渲染成「⛔ ⛔ 已跳过 …」
            return f"已跳过 {tool_name}: {result.content[:60]}…" if len(result.content) > 60 else f"已跳过 {tool_name}: {result.content}"
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

    # 超时错误特征：只有这类错误值得熔断重试，HTTP 4xx 之类重试必然复现
    _TIMEOUT_MARKERS = ("timed out", "timeout", "read timeout", "连接超时")

    def _is_timeout_error(self, text: str) -> bool:
        low = (text or "").lower()
        return any(m in low for m in self._TIMEOUT_MARKERS)

    def _turn_wall_plan(self) -> tuple[int, int]:
        """把「单轮墙钟上限」拆成 `(单次尝试超时, 最大重试次数)`。

        为什么要拆：`timeout` 约束的是**一次请求**，而用户感知的是**一轮**
        （含内部重试）。`LLM_TIMEOUT=300` + `LLM_MAX_RETRIES=2` 实际是 900s/轮，
        但这个乘积在代码里从没被写出来过 —— 它只活在"两个参数相乘"这个隐式
        关系里，所以 config 里那条"LLM_TIMEOUT 是绝对天花板"的注释才会一直
        和实现对不上。拆开之后，墙钟是一个能写进配置、能被测试断言的量。

        规则：
        - `LLM_TURN_MAX_WALL_S` 为 0 → 不设墙钟，原样返回（保持旧行为可选）
        - 单次尝试超时取 `min(LLM_TIMEOUT, 墙钟)`，`LLM_TIMEOUT` 仍是单次请求的硬上限
        - 重试次数取 `min(LLM_MAX_RETRIES, wall // per_call - 1)`，
          保证 `per_call × (retries + 1) ≤ wall`
        """
        per_call = int(getattr(self._settings, "LLM_TIMEOUT", 60) or 60)
        retries = int(getattr(self._settings, "LLM_MAX_RETRIES", 2) or 0)
        wall = int(getattr(self._settings, "LLM_TURN_MAX_WALL_S", 0) or 0)
        if wall <= 0:
            return per_call, retries
        per_call = max(1, min(per_call, wall))
        allowed = max(0, wall // per_call - 1)
        return per_call, min(retries, allowed)

    def _chat_with_breaker(self, client, messages: list, tools: list,
                           thinking_mode: str, iteration: int):
        """单轮 LLM 调用，带长尾熔断。

        实测依据（req 146-159 共 155 轮）：延迟 >60s 的轮次只占 20%，
        却吃掉 71% 的 LLM 总时间；而这些慢轮输出很短（中位 583 token），
        说明是空转 / 端点抖动，不是在生成长内容。与其干等 300s，
        不如在预算内断掉重来一次——多数情况下第二次就正常了。

        策略：
        1. 首试带预算且**关闭内部重试**（否则实际墙钟 = 预算 × (N+1)，熔断失效）；
        2. 仅当失败原因是超时才降级重试一次，用减半的 max_tokens；
        3. 重试同样带预算（熔断预算的 3 倍，下限 120s）且关闭内部重试，
           给真需要长输出的轮次留出余量，同时不把等待时间再放大一轮。

        配置 LLM_SLOW_TURN_TIMEOUT=0 可整体关闭。
        """
        budget = int(getattr(self._settings, "LLM_SLOW_TURN_TIMEOUT", 0) or 0)
        if budget <= 0:
            # 熔断关闭 ≠ 不设上限。这一支此前直接裸调：不传 timeout、不传
            # max_retries → 两个参数各自落回实例默认（LLM_TIMEOUT × LLM_MAX_RETRIES），
            # 单轮墙钟最坏 = 300 × (2+1) = 900s —— 而代码里**没有任何一处**写出过
            # 这个 900，它是"两个参数相乘"的隐式产物，调参时看不见。
            # 现在按显式的「单轮墙钟上限」反推这两个参数（见 _turn_wall_plan）。
            per_call, retries = self._turn_wall_plan()
            return client.chat_with_tools(
                messages=messages, tools=tools,
                max_tokens=self._max_tokens, thinking=thinking_mode,
                timeout=per_call, max_retries=retries,
            )

        first = client.chat_with_tools(
            messages=messages, tools=tools,
            max_tokens=self._max_tokens, thinking=thinking_mode,
            timeout=budget, max_retries=0,
        )
        if not first.is_error or not self._is_timeout_error(first.error or ""):
            return first

        # 降级重试必须同样显式约束 timeout 与 max_retries。
        # 这两个参数此前都没传：timeout 落到实例默认（LLM_TIMEOUT，本机 .env 为 300），
        # max_retries 落到 LLM_MAX_RETRIES（2）→ 单次"降级重试"最坏挂 3 × 300 = 900s。
        # req 162 正是这么等满 15 分钟的：45s 熔断本想快速止损，结果换来三次各 300s
        # 的慢失败，最后仍然判 failed，一个文件都没写出来。
        # 重试预算取熔断预算的 3 倍（下限 120s，不超过配置的单次上限），
        # 并再次关闭内部重试——否则墙钟时间会被乘回去，熔断等于没做。
        retry_timeout = min(
            max(budget * 3, 120),
            int(getattr(self._settings, "LLM_TIMEOUT", 300) or 300),
        )
        logger.warning(
            f"[ToolLoop] 单轮超过 {budget}s 触发熔断，以 {retry_timeout}s 预算降级重试 "
            f"(iter {iteration + 1})"
        )
        return client.chat_with_tools(
            messages=messages, tools=tools,
            max_tokens=max(self._max_tokens // 2, 4000), thinking=thinking_mode,
            timeout=retry_timeout, max_retries=0,
        )

    # 有「用户可感知语义」的工具：执行前推一条动作级进度，让前端说得出在做什么。
    # read_file / list_files 不推——它们高频且对用户无进展信息，只会刷屏。
    _ACTIVITY_TOOLS = frozenset({
        "write_file", "edit_file", "run_preview",
        "lint_js", "lint_css", "validate_html", "execute_code",
    })

    def _push_activity(self, state, tool_name: str, arguments: dict,
                       iteration: int, max_iterations: int) -> None:
        """推送动作级进度（编码期此前零推送，是「AI 正在处理…」的根因之一）。

        只描述**动作**（"正在创建 js/app.js"）而非角色名，前端直接展示。
        百分比按迭代预算线性映射：20=确认 Plan 开始编码，95=编码收尾。
        失败静默——进度推送永远不允许阻断主流程。
        """
        if not self.sse or tool_name not in self._ACTIVITY_TOOLS:
            return
        req_id = state.get("requirement_id")
        if not req_id:
            return
        try:
            args = arguments if isinstance(arguments, dict) else {}
            filename = args.get("filename", "")
            if tool_name == "write_file":
                text = f"正在创建 {filename or '文件'}"
            elif tool_name == "edit_file":
                text = f"正在修改 {filename or '文件'}"
            elif tool_name == "run_preview":
                text = "正在浏览器里试跑，检查报错"
            elif tool_name == "execute_code":
                text = "正在运行代码验证"
            else:
                text = f"正在检查 {filename or '文件'} 语法"
            # 百分比统一由 progress_plan 分配：编码带 20..75（按轮次右移），
            # 上界刻意低于验证带起点，否则验证一亮相进度条就回退
            # （此前这里是 `20 + 75*ratio`，能冲到 95，而验证从 80 重来）。
            from harness.observability import progress_plan as _pp
            percent = _pp.coding_percent(
                (iteration + 1) / max(max_iterations, 1),
                _pp.round_index(state),
            )
            self.sse.progress(req_id, percent, text, stage="coding")
        except Exception as e:
            logger.debug(f"[ToolLoop] 动作进度推送失败（不阻断）: {e}")

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

        # L2 staleness 跟踪：记录本轮文件变更 / notes 更新事件（harness 唯一介入点）。
        try:
            from harness.state.context_pipeline import note_task_activity
            note_task_activity(state, tool_call.name, result.success)
        except Exception:
            pass

        # 确定性运行时错误标记（req 147 复盘）：run_preview 抓到 pageerror 后，
        # 迭代预算 min(文件数+3,10) 恰好在这一轮耗尽，Agent 已经读文件准备修却被强行收尾。
        # 这里只做标记，由主循环决定是否追加预算（见 _self_repair_extension）。
        try:
            _err_text = "" if result.success else (result.error or "")
            _content_text = result.content or ""
            _blob = f"{_err_text}\n{_content_text}"
            if any(k in _blob for k in ("pageerror", "Uncaught", "运行时错误", "控制台错误")):
                state["_deterministic_error_pending"] = True
        except Exception:
            pass

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
                # 从 >= 2 降低到 >= 1：第 1 次 edit_file 失败即注入 write_file 回退提示，
                # 避免 Agent 在模糊匹配失败后持续尝试 edit_file 浪费迭代轮次。
                if state["_edit_fail_count"] >= 1:
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
        if self.tools is None:
            from harness.tools.registry import ToolResult
            return ToolResult(
                error=f"工具 '{tool_call.name}' 未注册且无工具注册表可用",
            )
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
        """构建 LLM 消息列表（v2：委托 ContextPipeline 做 L0/L3/L4/L5）。

        系统提示由 _build_system_prompt 产出（记忆注入在该方法外层包装，见
        requirement_service 的 _memory_aware_prompt，本方法不触碰其契约）。
        """
        # L2：run 开始播种 TASK_STATE.md（若缺失，从需求/spec 写入种子）；幂等、单次。
        self._ensure_task_state_seed(state)

        system_prompt = self._build_system_prompt(state)
        history = state.get("dialogue_history", [])
        hook_failures = state.get("_recent_hook_failures", [])
        requirement_content = state.get("requirement_content", "")

        # L2 staleness 检测：基于上一轮事件评估，达到阈值则注入提醒（harness 唯一介入点）。
        staleness_reminder = ""
        try:
            from harness.state.context_pipeline import evaluate_staleness
            staleness_reminder = evaluate_staleness(state)
        except Exception:
            staleness_reminder = ""

        # ---- 可变尾段拆分（前缀缓存治理，详见 docs/design/context-pipeline-v2.md）----
        # 实测（req 156）：provider 的前缀缓存对 msg[0] 是整条判定——即使变化只在
        # 末尾的「工作区文件索引 + TASK_STATE」段（公共前缀 82-100%），下一轮也
        # cached_tokens=0；msg[0] 字节完全相同才命中。因此把这两个每轮重建的段落
        # 从 system 消息整体搬到**末尾独立 user 消息**，保证 msg[0] 在整个 run 内
        # 字节稳定。分界标记 = coder_base.md 的「## 工作区文件索引」标题（模板内唯一）。
        TAIL_MARKER = "## 工作区文件索引"
        stable_prompt, var_tail = system_prompt, ""
        if TAIL_MARKER in system_prompt:
            marker_idx = system_prompt.index(TAIL_MARKER)
            stable_prompt = system_prompt[:marker_idx].rstrip() + "\n"
            var_tail = system_prompt[marker_idx:]

        # 预算从 56000 收紧到 24000：贪吃蛇实测 prompt_tokens 高达 20.9 万，
        # 主要来自每轮重发完整 plan + 文件摘要 + 最近 30 条工具结果。
        from harness.state.context_pipeline import ContextPipeline, _local_summary
        pipeline = ContextPipeline(
            budget=24000,
            llm_summary=_local_summary,
            ref_store=self._make_ref_store(),
        )
        # 估算口径对齐真实 messages：进 msg[0] 的是 stable_prompt（已剥离可变尾段），
        # 传完整 system_prompt 会把尾段重复计入 head，预算判断系统性偏高。
        # Chat 修改（第二轮对话）先做背景瘦身：第一轮的完整对话对"改点东西"是
        # 噪声 —— 只留原始需求 / 计划理解 / 验收结论 / 本轮诉求（详见
        # condense_chat_background）。只裁 prompt 视图，不落库。
        chat_bg = None
        if (state.get("metadata") or {}).get("is_chat"):
            from harness.state.context_pipeline import condense_chat_background
            history, chat_bg = condense_chat_background(history)
        history_msgs, stats = pipeline.build(
            head_content=stable_prompt or "",
            history=history,
            hook_failures=hook_failures,
            requirement_content=requirement_content,
        )

        messages = []
        if stable_prompt:
            messages.append({"role": "system", "content": stable_prompt})
        messages.extend(history_msgs)

        # 消费 hook 失败（pipeline 已注入，清空避免重复）
        if hook_failures:
            state["_recent_hook_failures"] = []

        # staleness 提醒追加为 user 消息（与 hook 失败同位置，下一轮 LLM 可见）
        if staleness_reminder:
            messages.append({"role": "user", "content": staleness_reminder})

        # 可变尾段固定挂在消息列表**最末**（离生成点最近）：每轮刷新的文件索引与
        # 任务状态，重建不落 dialogue_history，不会污染下一轮前缀。
        if var_tail:
            messages.append({
                "role": "user",
                "name": "System",
                "content": "[系统注入·每轮刷新，以下为最新工作区状态]\n\n" + var_tail,
            })

        # head_sha：稳定前缀的字节指纹，供跨轮比对缓存友好性（同 run 内不变 = 达标）
        head_sha = hashlib.sha256(stable_prompt.encode("utf-8")).hexdigest()[:8]

        logger.info(
            f"[ContextPipeline] head={stats['head_tokens']} head_sha={head_sha} "
            f"history={stats['history_tokens']} "
            # filtered = 每轮按设计剥离的 thinking / iteration_batch（日常治理）；
            # soft_masked = 超历史软预算的渐进遮蔽；后四项 = 超硬预算兜底。
            f"filtered={stats['filtered_count']}/{stats['filtered_tokens']}tok "
            f"soft_masked={stats.get('soft_masked', 0)} "
            f"masked_read={stats['masked_read']} "
            f"masked_nonfile={stats['masked_nonfile']} offloaded={stats['offloaded']} "
            f"dropped={stats['dropped']} compacted={stats['compacted']}"
            + (f" chat_bg=-{chat_bg['dropped']}/{chat_bg['dropped_tokens']}tok"
               if chat_bg else "")
            + f"{' stale=1' if staleness_reminder else ''}"
        )
        return messages

    def _read_task_state(self) -> str:
        """读取 .task/TASK_STATE.md 全文（不存在返回空串）。"""
        try:
            ws = getattr(self, "workspace", None)
            if ws is None or not hasattr(ws, "list"):
                return ""
            path = ".task/TASK_STATE.md"
            if path not in ws.list():
                return ""
            return ws.read(path)
        except Exception:
            return ""

    def _ensure_task_state_seed(self, state: dict):
        """run 开始播种 TASK_STATE.md（若缺失）：从需求 + spec 写入「目标/决策/文件状态」种子。

        幂等：仅当 .task/TASK_STATE.md 不存在时写入一次。agent 之后通过 update_task_notes
        维护它。设计文档 §3.A：spec 作种子，TASK_STATE.md 承载进度（spec 只读不写）。
        """
        try:
            ws = getattr(self, "workspace", None)
            if ws is None or not hasattr(ws, "list"):
                return
            path = ".task/TASK_STATE.md"
            if path in ws.list():
                return
            requirement = state.get("requirement_content", "") or ""
            plan = state.get("plan")
            seed = [
                "# .task/TASK_STATE.md（自动播种自需求/spec；Agent 用 update_task_notes 维护）",
                "## 目标",
                (requirement[:500] if requirement else "(未提供)"),
                "## 决策与理由",
            ]
            if isinstance(plan, dict):
                tasks = plan.get("tasks", [])
                if isinstance(tasks, list):
                    for t in tasks[:6]:
                        if isinstance(t, dict) and t.get("description"):
                            seed.append(f"- {str(t.get('file', ''))}: {str(t.get('description', ''))[:80]}")
            seed += ["## 文件状态", "## 未决问题", "## 下一步"]
            ws.write(path, "\n".join(seed) + "\n")
        except Exception as e:
            logger.debug(f"[TASK_STATE] 播种失败（不阻断）: {e}")

    def _make_ref_store(self):
        """非文件大结果落盘回调（写入 workspace 的 .task/refs/）。"""
        workspace = getattr(self, "workspace", None)
        if workspace is None or not hasattr(workspace, "write"):
            return None

        def _store(name: str, content: str):
            try:
                workspace.write(f".task/refs/{name}", content)
                return f".task/refs/{name}"
            except Exception:
                return None

        return _store

    def _get_craft_context(self, requirement: str = '') -> str:
        """渐进式加载 Skills，注入到编码 Prompt 中。

        使用 SkillLoader（基于 manifest.json）替代旧的 LLM 选择机制。
        同一任务只做一次匹配，后续轮次复用缓存。
        """
        try:
            if not hasattr(self, '_skill_cache'):
                self._skill_cache = {}
            cache_key = requirement[:200]  # 用需求前 200 字做缓存键
            if cache_key not in self._skill_cache:
                from harness.instructions.skill_loader import load_for_task
                self._skill_cache[cache_key] = load_for_task(requirement) if requirement else ''
            return self._skill_cache[cache_key]
        except Exception:
            return ''

    def _build_system_prompt(self, state: AgentState) -> str:
        """构建 Coder 系统提示词（稳定前缀 + 可变尾段）

        结构（§3.A）：模板骨架 / 需求 / 计划摘要 / 接口契约 = **稳定前缀**（run 内不变，
        命中 KV-cache）；工作区文件索引 + TASK_STATE.md = **可变尾段**。渲染时尾段仍
        拼在提示词文本最末，但 _build_messages 会按「## 工作区文件索引」标记把它拆出，
        作为**末尾独立 user 消息**下发——provider 对 msg[0] 整条判定缓存，尾段留在
        msg[0] 内（哪怕只在末尾变化）也会让缓存整条失效（req 156 实测）。
        文件索引给「一行结构摘要」（不含正文），正文按需 just-in-time read_file。

        根据复杂度切换提示词策略：
        - simple:  自由文件结构，极简流程，5 轮快速通过
        - standard: 架构先导 + 批量创建 + 完整验证
        """
        requirement = state.get("requirement_content", "")
        plan = state.get("plan")
        complexity = state.get("metadata", {}).get("complexity", "standard")

        existing_files = self.workspace.list()
        existing_text = self._build_file_summaries(existing_files)
        # L2 任务状态：把 .task/TASK_STATE.md 注入 head（可变尾段首段，每轮变）。
        # 它不在稳定前缀内，不破坏前缀缓存；agent 通过 update_task_notes 维护它。
        task_state = self._read_task_state()

        plan_section = ""
        first_round_section = ""
        if plan:
            # 稳定前缀**始终**只放计划摘要（run 内字节不变 → 命中 KV-cache）。
            plan_section = f"""## 实现计划（摘要）
{self._compact_plan_text(plan)}"""
            if state.get("tool_call_count", 1) <= 1:
                # 首轮的完整 plan + 批量分组提示放进**可变尾段**（模板最末的
                # {first_round_section}）。这样首轮仍能看到完整规格，而稳定前缀的
                # 字节序列在整个 run 内保持一致。
                # 此前首轮发完整 JSON、第 2 轮改摘要 → head 从 plan 段起整段 cache miss
                # （实测 head 5576 → 4384，且每次重入 coder 首轮都重演一次）。
                plan_full = json.dumps(plan, ensure_ascii=False, indent=2) if isinstance(plan, dict) else str(plan)
                batch_hint = self._generate_batch_hint(plan)
                first_round_section = f"""## 完整实现计划（首轮下发，请严格遵循）
{plan_full}

{batch_hint}"""

        if complexity == "simple":
            return self._build_simple_prompt(requirement, plan_section, existing_text, existing_files,
                                             task_state=task_state,
                                             first_round_section=first_round_section,
                                             plan=plan if isinstance(plan, dict) else None)
        else:
            # 跨文件 API 契约：从 plan.tasks[].exports 渲染，每轮都注入
            # （体积小且是硬约束，不参与第 2 轮起的 plan 摘要压缩）
            from harness.constraints.plan_validator import build_api_contracts_section
            api_contracts = build_api_contracts_section(plan if isinstance(plan, dict) else None)
            return self._build_standard_prompt(requirement, plan_section, existing_text, existing_files,
                                               first_round_section=first_round_section,
                                               api_contracts=api_contracts, task_state=task_state,
                                               plan=plan if isinstance(plan, dict) else None)

    def _compact_plan_text(self, plan: dict) -> str:
        """紧凑版计划：仅保留文件清单与任务要点，用于第 2 轮起的上下文瘦身"""
        if not isinstance(plan, dict):
            return str(plan)
        lines = []
        fs = plan.get("file_structure", [])
        if isinstance(fs, list) and fs:
            lines.append("目标文件: " + ", ".join(str(f) for f in fs))
        io = plan.get("implementation_order", [])
        if isinstance(io, list) and io:
            lines.append("实现顺序: " + ", ".join(str(f) for f in io))
        tasks = plan.get("tasks", [])
        if isinstance(tasks, list) and tasks:
            lines.append("任务要点:")
            for t in tasks[:10]:
                if isinstance(t, dict):
                    f = t.get("file", "")
                    d = str(t.get("description", ""))[:60]
                    if f:
                        lines.append(f"- {f}: {d}")
        return "\n".join(lines) if lines else "(计划已下发)"

    def _generate_batch_hint(self, plan: dict) -> str:
        """基于 Plan 的依赖关系，动态生成批量创建分组提示

        根据文件间的依赖关系，自动将文件分组为 3-4 个批次：
        - 第1组（基础层）: 无依赖的文件（CSS、常量、工具函数）
        - 第2组（核心层）: 依赖基础层的核心逻辑文件
        - 第3组（组装层）: 依赖核心层的应用入口和组装文件
        - 第4组（验证层）: 统一验证 + run_preview

        这样无论是什么应用（贪吃蛇、待办清单、计算器等），都能自动适配。
        """
        if not isinstance(plan, dict):
            return ""

        tasks = plan.get("tasks", [])
        implementation_order = plan.get("implementation_order", [])
        
        if not implementation_order:
            # 从 tasks 中提取文件列表
            implementation_order = [t.get("file", "") for t in tasks if isinstance(t, dict) and t.get("file")]
        
        if not implementation_order:
            return ""

        # 构建依赖映射
        dep_map = {}  # filename -> set of dependencies
        for t in tasks:
            if isinstance(t, dict):
                fname = t.get("file", "")
                deps = t.get("dependencies", []) or []
                dep_map[fname] = set(deps)

        # 按依赖层级分组
        groups = self._group_by_dependency_level(implementation_order, dep_map)
        
        if len(groups) <= 1:
            # 文件太少，不需要分组
            return ""

        # 生成分组提示文本
        lines = ["## 📦 批量创建分组（按依赖顺序）"]
        lines.append("请严格按照以下分组顺序，每轮创建一个分组的所有文件：")
        lines.append("")

        for i, group in enumerate(groups, 1):
            if i == len(groups):
                lines.append(f"**第 {i} 组（验证）**: 运行 validate_html / lint_css / lint_js → run_preview → 修复 → 完成")
            else:
                file_list = ", ".join(group)
                lines.append(f"**第 {i} 组（创建）**: {file_list}")
                lines.append(f"  → 本轮目标：一次性创建以上所有文件")
            lines.append("")

        lines.append("### 规则")
        lines.append("- 每组内的文件相互无依赖，可以并行创建")
        lines.append("- 必须完成前一组才能开始下一组")
        lines.append("- 同一组内的文件使用 write_file 一次性批量创建，不要分多轮")
        lines.append("- 最后一组（验证）必须确保 run_preview 通过")

        return "\n".join(lines)

    def _group_by_dependency_level(self, files: list, dep_map: dict) -> list:
        """按依赖层级分组

        将文件按依赖关系分层：
        - 第1层：无依赖的文件
        - 第2层：只依赖第1层的文件
        - 第3层：依赖第1-2层的文件
        ...

        然后合并为 3-4 个批次（均衡每组文件数）
        """
        if not files:
            return []

        # 计算每个文件的依赖层级
        levels = {}
        def _get_level(fname):
            if fname in levels:
                return levels[fname]
            deps = dep_map.get(fname, set())
            if not deps:
                levels[fname] = 0
                return 0
            max_dep_level = max(_get_level(d) for d in deps if d in files)
            levels[fname] = max_dep_level + 1
            return levels[fname]

        for f in files:
            _get_level(f)

        # 按层级分组
        level_groups = {}
        for f in files:
            lvl = levels.get(f, 0)
            if lvl not in level_groups:
                level_groups[lvl] = []
            level_groups[lvl].append(f)

        # 将层级组合并为 3-4 个批次（均衡分配）
        all_levels = sorted(level_groups.keys())
        if len(all_levels) <= 3:
            # 层级少，直接用层级分组
            return [level_groups[l] for l in all_levels] + [["验证"]]

        # 层级多，合并为 3 个创建批次 + 1 个验证批次
        batch_size = len(all_levels) // 3
        batches = []
        
        # 第1批：前 batch_size 个层级
        batch_1 = []
        for l in all_levels[:batch_size]:
            batch_1.extend(level_groups[l])
        batches.append(batch_1)

        # 第2批：中间 batch_size 个层级
        batch_2 = []
        for l in all_levels[batch_size:batch_size*2]:
            batch_2.extend(level_groups[l])
        batches.append(batch_2)

        # 第3批：剩余层级
        batch_3 = []
        for l in all_levels[batch_size*2:]:
            batch_3.extend(level_groups[l])
        batches.append(batch_3)

        # 第4批：验证
        batches.append(["验证"])

        return batches

    def _build_simple_prompt(self, requirement: str, plan_section: str,
                              existing_text: str, existing_files: list,
                              task_state: str = "", first_round_section: str = "",
                              plan: dict = None) -> str:
        """simple 复杂度：自由文件结构，极简流程，5 轮快速通道"""
        from harness.instructions.prompts import load_prompt, load_prompt_template
        from harness.constraints.environment_contract import render_environment_contract
        from harness.constraints.plan_validator import build_api_contracts_section
        craft_rules = self._get_craft_context(requirement)
        # API 契约此前只在 standard 分支注入，simple 恒为空串。
        # 但 simple 同样会跨文件调用（index.html + app.js 是常态），
        # 需求 124 的断层在这里照样能发生 —— 两个复杂度必须共用同一份契约。
        file_hint = ""
        if isinstance(plan, dict):
            file_structure = plan.get("file_structure", [])
            if file_structure:
                file_hint = "## 推荐文件结构\n" + "\n".join(f"- {f}" for f in file_structure)
        return load_prompt_template("coding/coder_base.md",
            requirement=requirement,
            plan_section=plan_section,
            api_contracts=build_api_contracts_section(plan if isinstance(plan, dict) else None),
            file_hint=file_hint,
            first_round_section=first_round_section,
            existing_text=existing_text,
            task_state=task_state,
            craft_rules=craft_rules,
            environment_contract=render_environment_contract(),
            mode_section=load_prompt("coding/coder_mode_simple.md"),
            max_repair_rounds=self._settings.CODER_MAX_REPAIR_ROUNDS,
        )

    def _build_standard_prompt(self, requirement: str, plan_section: str,
                                existing_text: str, existing_files: list,
                                first_round_section: str = "", api_contracts: str = "",
                                task_state: str = "", plan: dict = None) -> str:
        """standard 复杂度：架构先导 + 批量创建 + 完整的浏览器验证"""
        from harness.instructions.prompts import load_prompt, load_prompt_template
        from harness.constraints.environment_contract import render_environment_contract
        # 推荐文件结构直接取自 plan 对象。
        # 注：plan_section 现在**恒定**为紧凑摘要（非 JSON），不能再靠 json.loads 解析它，
        # 否则 file_hint 会永久为空 —— 这是「稳定前缀只用摘要」改造的连带修正项。
        file_hint = ""
        if isinstance(plan, dict):
            file_structure = plan.get("file_structure", [])
            if file_structure:
                file_hint = "## 推荐文件结构\n" + "\n".join(f"- {f}" for f in file_structure)
        craft_rules = self._get_craft_context(requirement)
        return load_prompt_template("coding/coder_base.md",
            requirement=requirement,
            plan_section=plan_section,
            api_contracts=api_contracts,
            file_hint=file_hint,
            first_round_section=first_round_section,
            existing_text=existing_text,
            task_state=task_state,
            craft_rules=craft_rules,
            environment_contract=render_environment_contract(),
            mode_section=load_prompt("coding/coder_mode_standard.md"),
            max_repair_rounds=self._settings.CODER_MAX_REPAIR_ROUNDS,
        )

    def _build_file_summaries(self, existing_files: list) -> str:
        """工作区文件索引：每文件一行「文件名 + 一行结构摘要」（不含正文）。

        对齐设计文档 §3.A L142：`文件名 + 一行结构摘要（不含内容）`，正文靠
        just-in-time 的 read_file。摘要**纯规则提取**（不调 LLM）——导出符号 / 函数名 /
        元素 id·class / CSS 选择器 / EXPORT 警告——给 Agent 一张「文件地图」。
        早期版本只回文件名（丢了「结构摘要」），实测诱发「写完立刻回读」：
        LLM 上下文里既没有正文（write 正文在 tool_call.arguments，被 _stage_history
        丢弃），也没有任何结构线索，只能 read_file 找回。索引体积小、可重建，放**可变尾段**。
        """
        if not existing_files:
            return "(空目录)"
        lines = []
        for fname in sorted(existing_files):
            try:
                content = self.workspace.read(fname)
            except Exception:
                lines.append(f"- {fname}: (无法读取)")
                continue
            summary = self._build_one_line_file_summary(fname, content)
            lines.append(f"- {fname}: {summary}" if summary else f"- {fname}")
        return "\n".join(lines)

    def _build_one_line_file_summary(self, fname: str, content: str) -> str:
        """规则提取的**单行**结构摘要（不含正文），供文件索引使用。

        仅保留可重建的「接口线索」，整体截断到 ~200 字符，避免索引膨胀。
        """
        if not content or not isinstance(content, str):
            return ""
        import re
        stripped = [l.strip() for l in content.split("\n") if l.strip()]
        n_lines = len(stripped)
        parts = []
        if fname.endswith(".html"):
            for l in stripped:
                m = re.search(r"<title>(.*?)</title>", l)
                if m:
                    parts.append(m.group(1).strip()[:40])
                    break
            ids = set()
            for l in stripped:
                ids.update(re.findall(r'id="([^"]+)"', l))
                ids.update(re.findall(r"id='([^']+)'", l))
            if ids:
                parts.append("元素 id: " + ", ".join(sorted(ids)[:10]))
        elif fname.endswith(".css"):
            selectors = []
            for l in stripped:
                if l.endswith("{") and not l.startswith("@") and not l.startswith("/*"):
                    sel = l[:-1].strip()
                    if sel and len(sel) < 50:
                        selectors.append(sel)
            if selectors:
                parts.append("选择器: " + ", ".join(selectors[:10]))
        elif fname.endswith(".js"):
            funcs = []
            for l in stripped:
                m = re.match(r"(?:async\s+)?function\s+(\w+)", l)
                if not m:
                    m = re.match(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", l)
                if m and m.group(1) not in funcs:
                    funcs.append(m.group(1))
            if funcs:
                parts.append("函数: " + ", ".join(funcs[:10]))
            dom_refs = set()
            for l in stripped:
                dom_refs.update(
                    re.findall(r"""(?:getElementById|querySelector(?:All)?)\(\s*["']([^"']+)["']""", l)
                )
            if dom_refs:
                parts.append("DOM: " + ", ".join(sorted(dom_refs)[:8]))
            if re.search(r"^\s*export\s", content, re.M):
                parts.append("含 export（与普通 <script> 冲突）")
        detail = "；".join(parts)
        head = f"({n_lines}行)"
        if detail:
            return f"{head} {detail}"[:200]
        return head

    def _pending_plan_files(self, state: AgentState) -> list[str]:
        """按实现计划比对文件系统，返回尚未创建的文件列表。

        与 _check_missing_files 的区别（两者不可互相替代）：
        - 只认文件系统真相 + implementation_order，不读 CompletionContract；
        - 不受 contract.clear() 死锁兜底影响——那个兜底会把"还缺文件"这件事
          一起抹掉，用它的结果做提醒会在第 3 轮之后彻底静音；
        - 逐文件补全模式下只关注当前目标文件。
        """
        if state.get("metadata", {}).get("is_chat", False):
            return []

        if state.get("_per_file_mode"):
            current_file = state.get("_current_target_file", "")
            if not current_file:
                return []
            existing = set(self.workspace.list())
            if current_file in existing:
                return []
            basename = current_file.split("/")[-1]
            if any(e.endswith(basename) for e in existing):
                return []
            return [current_file]

        impl_order = state.get("implementation_order") or []
        if not impl_order:
            plan = state.get("plan") or {}
            if isinstance(plan, dict):
                raw = plan.get("file_structure") or []
                impl_order = [
                    f if isinstance(f, str) else str(f.get("path", ""))
                    for f in raw
                ]
        impl_order = [f for f in impl_order if f]
        if not impl_order:
            return []

        existing = set(self.workspace.list())
        missing = []
        for f in impl_order:
            if f in existing:
                continue
            basename = f.split("/")[-1]
            if any(e.endswith(basename) for e in existing):
                continue
            missing.append(f)
        return missing

    def _maybe_remind_missing_files(self, state: AgentState, iteration: int):
        """每轮投递"还缺哪些文件"的提醒（同清单降频，避免刷屏淹没上下文）。"""
        missing = self._pending_plan_files(state)
        if not missing:
            state["_missing_reminder_key"] = ""
            state["_missing_reminder_count"] = 0
            return

        key = ",".join(sorted(missing))
        if key == state.get("_missing_reminder_key"):
            state["_missing_reminder_count"] = state.get("_missing_reminder_count", 0) + 1
        else:
            state["_missing_reminder_key"] = key
            state["_missing_reminder_count"] = 1

        count = state["_missing_reminder_count"]
        # 同一清单连续提醒 2 轮后改为隔轮提醒，避免重复句占据上下文
        if count > 2 and count % 2 == 0:
            return

        state["dialogue_history"].append({
            "role": "system", "name": "System",
            "content": (
                f"进度检查：按实现计划还差 {len(missing)} 个文件没有创建 —— "
                f"{', '.join(missing)}。\n"
                f"请立刻用 write_file 创建它们，不要再读取已有文件做确认："
                f"你写过的文件内容已在上面，class/id 契约以你自己写入的版本为准。"
            ),
            "hidden": True,
            "preserve": True,
        })
        logger.info(
            f"[ToolLoop] 第 {iteration + 1} 轮投递缺文件提醒: {missing}"
        )

    def _check_missing_files(self, state: AgentState) -> list[str]:
        """检查目标文件是否全部生成。返回缺失文件名列表。

        优先从 CompletionContract 读取（Default-FAIL 硬约束），
        其次从架构设计的 file_structure 读取目标文件列表，
        避免硬编码与架构设计冲突（如 css/style.css vs style.css）。

        关键修复：当 contract 报告缺失但文件系统中文件已存在时，
        以文件系统为准，自动修复 contract 状态，避免死循环。

        逐文件补全模式（_per_file_mode）：只检查当前目标文件是否已创建，
        不检查 contract 中的其他文件（它们由外层 targeted_recovery 逐个处理）。
        避免系统提示说"只创建一个文件"但 missing 检测说"还有 N 个文件缺失"的矛盾。
        """
        complexity = state.get("metadata", {}).get("complexity", "standard")
        existing = set(self.workspace.list())

        # 逐文件编码模式：只关注当前目标文件
        if state.get("_per_file_mode"):
            current_file = state.get("_current_target_file", "")
            if current_file and current_file not in existing:
                # 检查文件名尾部匹配（路径差异）
                basename = current_file.split("/")[-1]
                if not any(e.endswith(basename) for e in existing):
                    return [current_file]
            # 当前文件已创建（或无需检查）→ 任务完成
            return []

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

        # 无 plan 时的回退：只确认入口文件存在。
        # 注意：complexity 已被 team_leader_node 归一化为 'simple'/'standard'，
        # 历史遗留的 'S'/'M'/'L' 分支不可达，这里统一按"入口文件存在"兜底。
        has_html = any(f.endswith("index.html") or f.endswith(".html") for f in existing)
        return [] if has_html else ["index.html"]

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

            # 提取检查结论：语法错误/契约违规以 error 结果返回（success=False），
            # 属于要注入的发现而非工具故障；真正无法执行时 content 与 error 均为空
            lint_content = lint_result.error or lint_result.content or ""
            if not lint_content:
                return  # 工具未产出任何结论（如 Node 未安装），跳过
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

    def _annotate_readback(self, state: AgentState, tc, result):
        """防回读：对"刚被自己写过的文件"加回读提示。

        write_file/edit_file 的返回值已包含内容预览（前 80 行 + 后 10 行），
        写完立刻回读同一文件纯属浪费（需求 182：css/style.css 被回读 18 次）。
        这里不阻断读取（保留模型取用分页内容的能力），只明确告知内容未变。
        """
        fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
        if not fname:
            return
        write_round = (state.get("_recent_writes") or {}).get(fname)
        if write_round is None:
            return
        current_round = state.get("tool_call_count", 0)
        if current_round - write_round > self.READBACK_GUARD_ROUNDS:
            return
        note = (
            f"[提示] {fname} 是你在第 {write_round} 轮亲自写入的文件，此后没有被修改过，"
            f"内容与你写入时完全一致（write_file 的返回值已包含首尾预览）。"
            f"不要再为确认 class/id 反复读取它，直接以你写入的版本为准继续写其它文件。\n\n"
        )
        result.content = note + (result.content or "")
        logger.info(f"[ToolLoop] 防回读提示: {fname}（第 {write_round} 轮写入）")

    # ------------------------------------------------------------------
    # 创建文件后的「上下文托管」：追踪 → 免回读 → 卸载
    # ------------------------------------------------------------------

    @staticmethod
    def _content_hash(text: str) -> str:
        import hashlib
        return hashlib.md5((text or "").encode("utf-8", "ignore")).hexdigest()

    def _file_preview_snippet(self, filename: str, head: int = 15, tail: int = 15) -> str:
        """生成文件首尾各 N 行的带行号预览，用于「拦截重读」时让模型当场看到内容。

        为什么需要它：重读拦截保住了正确性，但拦截消息本身不给内容，模型只能靠
        记忆回溯"我到底写了什么"——对 flash 档模型这等于没给，于是它换个文件名或者
        过两轮又发起一次 read_file，空转轮次照烧迭代预算和延迟（需求 183 实测同一
        文件被读 6 次）。

        把首尾若干行直接贴进拦截消息，是成本最低的\"当场满足\"：模型想确认的通常就是
        类名 / 函数签名 / 结构骨架，首尾各 15 行基本够用；而中间正文它自己刚写过，
        不必再花一轮往返去取。

        Returns:
            形如 \"\\n1| ...\\n2| ...\" 的预览；文件读不到或为空时返回空字符串。
        """
        try:
            raw = self.workspace.read(filename)
        except Exception:
            return ""
        lines = (raw or "").splitlines()
        if not lines:
            return ""
        total = len(lines)
        # 文件足够短 → 全给（比截首尾更有用，且总量可控）
        if total <= head + tail:
            picked = [(i + 1, lines[i]) for i in range(total)]
        else:
            picked = [(i + 1, lines[i]) for i in range(head)]
            picked.append((0, f"… 省略中间 {total - head - tail} 行 …"))
            picked.extend(
                (i + 1, lines[i]) for i in range(total - tail, total)
            )
        body = "\n".join(
            f"{n:>4}| {txt}" if n else f"    | {txt}" for n, txt in picked
        )
        # 单行过长会撑爆上下文，按 200 字符截断（只看结构，不看长行细节）
        body = "\n".join(
            (ln[:200] + " …" if len(ln) > 200 else ln) for ln in body.split("\n")
        )
        return f"\n\n【{filename} 共 {total} 行，首尾预览】\n{body}"

    def _created_read_block(self, state: AgentState, filename: str):
        """创建后免回读判定：返回 ToolResult（blocked）表示拦截，None 表示放行。

        判据：文件在 `_known_content_files` 登记过，且磁盘当前内容与写入时逐字相同。
        任一不满足（没登记过 / 已被改动 / 读不到）都放行，交给正常执行路径。
        """
        if not filename:
            return None
        info = (state.get("_known_content_files") or {}).get(filename)
        if not info:
            return None
        try:
            disk = self.workspace.read(filename)
        except Exception:
            return None
        if self._content_hash(disk) != info.get("hash"):
            return None
        from harness.tools.registry import ToolResult
        logger.info(f"[ToolLoop] 创建后免回读跳过: {filename}")
        return ToolResult(
            blocked=True,
            content=(
                f"[已跳过] {filename} 是你在第 {info.get('round', '?')} 轮"
                f"用 write_file 写入的文件，此后没有被修改过，"
                f"内容与你写入时逐字相同（{info.get('lines', '?')} 行，"
                f"完整正文就在上方 write_file 的结果里）。"
                f"不要为确认 class/id 而重读它 —— 直接以你写入的版本为准继续；"
                f"需要改动就用 write_file / edit_file 写入。"
                # 附首尾预览：模型想确认的多半是类名/签名/结构骨架，直接给就不必再发一轮
                f"{self._file_preview_snippet(filename)}"
            ),
        )

    def _track_known_content(self, state: AgentState, tc):
        """write_file 成功后登记：该文件的完整正文此刻已在上下文里。

        登记内容是**磁盘上的最终内容**（而不是入参 content），这样"写入被截断/回滚"
        的情况不会被误登记成"模型已知完整正文"。
        """
        fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
        if not fname:
            return
        try:
            disk = self.workspace.read(fname)
        except Exception:
            return
        known = state.setdefault("_known_content_files", {})
        known[fname] = {
            "hash": self._content_hash(disk),
            "round": state.get("tool_call_count", 0),
            "lines": disk.count("\n") + 1,
        }
        logger.info(f"[ToolLoop] 登记已知正文: {fname}（{known[fname]['lines']} 行）")

    @staticmethod
    def _untrack_known_content(state: AgentState, tc):
        """edit_file 成功后撤销登记：内容已被局部改动，模型手上的副本过期了。

        必须撤销，否则模型想用 edit_file 的 SEARCH 精确匹配时会拿不到准确片段，
        又因为"内容未变被跳过"而陷入反复失败。
        """
        fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
        if not fname:
            return
        known = state.get("_known_content_files") or {}
        if known.pop(fname, None) is not None:
            logger.info(f"[ToolLoop] 撤销已知正文（已被 edit_file 修改）: {fname}")

    @staticmethod
    def _offload_created_context(state: AgentState, tc):
        """读取成功后卸载创建时的正文副本，避免两份相同内容长期占着上下文。

        「创建后免回读」被绕过只有一种合理情况：文件在写入之后又被改过
        （被 edit_file / 被其它工具），此时模型需要重新拿正文。既然新正文已经
        通过这次 read_file 进入上下文，创建时那份就变成纯冗余 —— 替换成一行占位说明。
        """
        fname = tc.arguments.get("filename", "") if isinstance(tc.arguments, dict) else ""
        if not fname:
            return
        known = state.get("_known_content_files") or {}
        if fname not in known:
            return
        known.pop(fname, None)
        history = state.get("dialogue_history") or []
        for entry in reversed(history):
            if entry.get("role") != "tool_call" or entry.get("name") != "write_file":
                continue
            args = entry.get("arguments") or {}
            if args.get("filename") != fname:
                continue
            entry["content"] = (
                f"[已卸载] {fname} 创建时的正文已从上下文移除 —— "
                f"它已被后续改动/读取取代，请以最近一次 read_file 的结果为准。"
            )
            logger.info(f"[ToolLoop] 卸载创建时正文: {fname}")
            return

    def _check_deliverable_syntax(self) -> list[str]:
        """检查交付文件的**确定性硬伤**，返回问题列表（空列表 = 无问题）。

        两类判据（都是机器可判定、不依赖模型审美判断）：
        1. 语法：与 lint_js / lint_css / _syntax_problem 对齐 ——
           .js → node --check、.css → 花括号平衡、.html → </html> 收尾、.json → 可解析
        2. 引用闭合：HTML 的 src/href、CSS 的 url()/@import 指向的本地文件必须存在
           （语法全对但引用了没创建的文件，预览会直接白屏）

        设计要点：
        - 只查交付文件（排除 .task/、.design/ 等元数据与模板目录）
        - 任一步异常一律视为"无问题"——**门禁自身故障绝不能阻断交付**
        """
        try:
            from harness.tools.file_tools import _syntax_problem
        except ImportError:
            return []
        try:
            files = [f for f in self.workspace.list() if self._is_deliverable(f)]
        except Exception:
            return []

        problems: list[str] = []
        for fname in files:
            if not fname.lower().endswith((".js", ".css", ".html", ".json")):
                continue
            try:
                content = self.workspace.read(fname)
            except Exception:
                continue
            try:
                problem = _syntax_problem(fname, content)
            except Exception:
                continue
            if problem:
                problems.append(f"{fname}: {problem}")

        # 引用闭合：语法正确但引用了不存在的文件，同样是确定性缺陷。
        # 单独 try 兜底：引用检查崩了也不能让语法检查的结果一起丢掉。
        try:
            problems.extend(self._check_broken_references())
        except Exception:
            pass
        return problems

    # 引用里这些前缀指向外部/内联资源，不属于工作区文件，一律跳过
    _REF_SKIP_PREFIX = (
        "http://", "https://", "//", "data:", "blob:", "mailto:",
        "tel:", "javascript:", "about:", "#",
    )

    def _check_broken_references(self) -> list[str]:
        """检查交付文件是否引用了不存在的本地资源（空列表 = 无问题）。

        背景（t16 实测）：模型写出 `index.html` 引用 `js/sort.js`，但只创建了
        `js/storage.js`。语法门禁全过（每个文件单独看都是合法 JS/HTML），
        预览却直接 `ERR_FILE_NOT_FOUND` 白屏 —— 从"能跑通"的角度看这是硬伤，
        而且是**机器可判定**的：解析出引用、判断文件在不在即可。

        与语法门禁合并返回，共用同一个阻断/放行逻辑（含 3 轮终止性保证）。

        设计要点：
        - 只查交付文件（.task/、.design/ 等元数据与模板目录不参与）
        - 解析 HTML 的 src/href、CSS 的 url() 与 @import
        - 跳过外部/内联引用（http、data:、#锚点…）
        - 跳过指向工作区外的相对路径（`..`）与目录引用（以 `/` 结尾）
        - 最多报 5 条，避免一次灌满上下文
        - 任一步异常一律返回"无问题"——门禁自身故障绝不能阻断交付
        """
        import posixpath
        import re as _re

        _html_ref = _re.compile(r'''(?:src|href)\s*=\s*["']([^"']*)["']''', _re.I)
        _css_url = _re.compile(r'''url\(\s*["']?([^"')]+?)["']?\s*\)''', _re.I)
        _css_import = _re.compile(r'''@import\s+(?:url\(\s*)?["']([^"']+)["']''', _re.I)

        try:
            files = [f for f in self.workspace.list() if self._is_deliverable(f)]
        except Exception:
            return []
        if not files:
            return []

        # 预建文件集与目录集：目录引用（如 <a href="css">）不算断链
        file_set = set(files)
        dir_set: set[str] = set()
        for f in files:
            d = posixpath.dirname(f)
            while d:
                dir_set.add(d)
                d = posixpath.dirname(d)

        problems: list[str] = []
        seen: set = set()
        for fname in files:
            low = fname.lower()
            if low.endswith((".html", ".htm")):
                kind = "html"
            elif low.endswith(".css"):
                kind = "css"
            else:
                continue
            try:
                content = self.workspace.read(fname)
            except Exception:
                continue
            # 门禁绝不能因内容形态异常而崩：非字符串（None / Mock / bytes）直接跳过。
            # 这里必须显式判类型而非 `or ""` —— Mock 是 truthy，会一路带进正则报 TypeError。
            if not isinstance(content, str) or not content:
                continue

            try:
                refs = _html_ref.findall(content) if kind == "html" else (
                    _css_url.findall(content) + _css_import.findall(content)
                )
            except Exception:
                continue

            for raw in refs:
                try:
                    ref = (raw or "").strip().strip("\"'")
                    if not ref or ref.lower().startswith(self._REF_SKIP_PREFIX):
                        continue
                    # 去掉查询串与锚点：style.css?v=2#top → style.css
                    ref = ref.split("#", 1)[0].split("?", 1)[0].strip()
                    if not ref or ref.endswith("/") or any(ch in ref for ch in "{}<>"):
                        continue
                    if ref.startswith("/"):
                        target = posixpath.normpath(ref.lstrip("/"))
                    else:
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(fname), ref)
                        )
                    if target.startswith(".."):
                        continue                      # 工作区外的路径不属交付范围
                    if target in file_set or target in dir_set:
                        continue
                    if (fname, ref) in seen:
                        continue
                    seen.add((fname, ref))
                    problems.append(f"{fname} 引用了不存在的资源：{ref}")
                    if len(problems) >= 5:
                        return problems
                except Exception:
                    continue
        return problems

    @staticmethod
    def _is_deliverable(path: str) -> bool:
        """是否交付文件（排除 .task/ 等元数据目录与隐藏文件）"""
        parts = path.split("/")
        if any(p.startswith(".") for p in parts[:-1]):
            return False
        return not parts[-1].startswith(".")

    def _deliverable_files(self) -> set:
        """工作区中的交付文件集合。

        无进展判定必须只看交付文件：workspace.list() 还包含 .task/** 元数据
        （contract.json、TASK_STATE.md、evaluator/result.json），它们被 hook、
        update_task_notes、verify 持续重写，会让"文件集合没变"这个判据永远
        不成立，no_progress_count 被反复重置（需求 182）。
        """
        return {f for f in self.workspace.list() if self._is_deliverable(f)}

    def _check_no_progress(self, state: AgentState) -> bool:
        """检查连续无进展（前 3 轮豁免，给 LLM 足够的探索空间）"""
        if state.get("tool_call_count", 0) <= 3:
            return False
        current_files = self._deliverable_files()
        last_files = set(state.get("last_file_list") or [])
        state["last_file_list"] = sorted(current_files)
        if current_files == last_files:
            state["no_progress_count"] = state.get("no_progress_count", 0) + 1
        else:
            state["no_progress_count"] = 0
        return state.get("no_progress_count", 0) >= self.NO_PROGRESS_LIMIT
