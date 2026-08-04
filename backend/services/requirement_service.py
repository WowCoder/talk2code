# -*- coding: utf-8 -*-
"""
需求管理服务（6 层架构集成版）
封装 AI 多智能体协同处理需求的业务逻辑
"""

import json
import threading
from typing import Optional

from models import SessionLocal, Requirement
from harness.observability.logger import get_logger
from utils.sse import SSEMessage, get_current_timestamp
from services.sse_manager import sse_manager
from harness.graph import get_workflow, create_workflow_post_plan
from harness.harness_context import set_all as set_harness_components
from harness.state.agent_state import AgentState
from harness.state.workspace import WorkspaceFS
from harness.state.versioning import GitVersioning
from harness.state.checkpoint import CheckpointManager
from harness.tools.registry import create_tool_registry
from harness.tools.file_tools import FileToolHandler
from harness.tools.code_tools import CodeToolHandler
from harness.constraints.hooks import create_default_hook_manager

from harness.observability.tracer import Tracer
from harness.observability.cost import CostTracker
from harness.observability.sse_reporter import SSEReporter
from harness.runtime import ToolCallLoop
from harness.agent_names import TL_NAME, DEV_NAME, QA_NAME
from harness.instructions.intent_router import IntentRouter, IntentType
from harness.state.memory import MemoryManager
from llm.client import get_client

logger = get_logger(__name__)

# 全局记忆管理器（持久化到 agent_memories_v2 表，跨进程重启保留）
_memory_manager: Optional[MemoryManager] = None


def _get_memory_manager() -> MemoryManager:
    """懒初始化全局 MemoryManager（首次调用时加载模型）"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(llm_client=get_client())
    return _memory_manager


class RequirementService:
    """需求管理服务（集成 harness 6 层）"""

    # 取消信号：全局字典，key=requirement_id, value=threading.Event
    _cancel_events: dict = {}
    _cancel_lock = threading.Lock()

    # 已推送 SPEC 的需求 ID 集合（防止并发请求重复推送）
    _spec_pushed_ids: set = set()

    @classmethod
    def get_cancel_event(cls, requirement_id: int) -> threading.Event:
        """获取或创建需求对应的取消信号"""
        with cls._cancel_lock:
            if requirement_id not in cls._cancel_events:
                cls._cancel_events[requirement_id] = threading.Event()
            return cls._cancel_events[requirement_id]

    @classmethod
    def signal_cancel(cls, requirement_id: int):
        """发送取消信号"""
        with cls._cancel_lock:
            event = cls._cancel_events.get(requirement_id)
            if event:
                event.set()
                logger.info(f"[Cancel] 需求 {requirement_id} 取消信号已发送")

    @classmethod
    def clear_cancel(cls, requirement_id: int):
        """清除取消信号"""
        with cls._cancel_lock:
            cls._cancel_events.pop(requirement_id, None)

    @staticmethod
    def is_cancelled(requirement_id: int) -> bool:
        """检查需求是否已被取消"""
        with RequirementService._cancel_lock:
            event = RequirementService._cancel_events.get(requirement_id)
            return event.is_set() if event else False

    def __init__(self):
        self.workflow = get_workflow()
        self._progress_map = {
            'team_leader': 20,
            'coder': 70,
            'verify': 90,
            'repair': 85,
        }

    @staticmethod
    def _mark_requirement_failed(requirement, reason: str, final_state: dict = None):
        """统一标记需求失败，确保 error_message 必填"""
        requirement.status = 'failed'
        error_parts = [reason]
        if final_state:
            detail = final_state.get('error', '')
            if detail:
                error_parts.append(str(detail)[:200])
            tc_count = final_state.get('tool_call_count', 0)
            if tc_count:
                error_parts.append(f"共 {tc_count} 轮迭代")
            np_count = final_state.get('no_progress_count', 0)
            if np_count:
                error_parts.append(f"连续 {np_count} 轮无进展")
        requirement.error_message = " — ".join(error_parts)

    def process_requirement(self, requirement_id: int) -> bool:
        """处理需求：执行 LangGraph 多智能体协同流程"""
        db = SessionLocal()
        try:
            requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
            if not requirement:
                logger.error(f"需求不存在：{requirement_id}")
                return False

            if requirement.status in ['finished', 'processing']:
                logger.info(f"需求 {requirement_id} 状态为 {requirement.status}，跳过")
                return False

            requirement.status = 'processing'
            db.commit()
            logger.info(f"需求 {requirement_id} 开始处理")

            # ===== 意图路由：非 TASK 请求快速返回，不进入完整工作流 =====
            if '[用户补充说明]' not in requirement.content:
                router = IntentRouter()
                intent_result = router.classify(requirement.content)
                logger.info(f"需求 {requirement_id} 意图分类: {intent_result.intent.value}")

                if intent_result.intent == IntentType.QUICK:
                    return self._handle_quick_answer(db, requirement, requirement_id, intent_result)
                elif intent_result.intent == IntentType.SEARCH:
                    return self._handle_search_answer(db, requirement, requirement_id, intent_result)
                elif intent_result.intent == IntentType.AMBIGUOUS:
                    return self._handle_ambiguous_direct(db, requirement, requirement_id)

            # 初始化 harness 各层
            workspace = WorkspaceFS(requirement.user_id, requirement_id)
            workspace.init(requirement.code_files)
            git = GitVersioning(workspace)
            tools = create_tool_registry()
            hooks = create_default_hook_manager()
            # 注入 db_session：记忆/检查点/追踪跨重启持久化
            checkpoint = CheckpointManager(db_session=db)
            cost_tracker = CostTracker()
            tracer = Tracer(db_session=db, cost_tracker=cost_tracker)

            # SSE reporter
            sse = SSEReporter(sse_manager)

            # 经验注入：将历史成功经验注入 ToolCallLoop 的 System Prompt
            # 通过包装 _build_system_prompt 方法实现（在原始 prompt 后追加 few-shot 示例）
            _original_builder = None  # 延迟绑定

            # ToolCallLoop
            # 增量持久化回调：每轮迭代后将对话历史保存到数据库
            def _persist_dialogue(state):
                try:
                    dialogue = state.get('dialogue_history', [])
                    if dialogue:
                        requirement.dialogue_history = dialogue
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(requirement, 'dialogue_history')
                        db.commit()
                except Exception as e:
                    logger.warning(f"增量持久化对话失败（不阻断）：{e}")

            tool_loop = ToolCallLoop(
                workspace=workspace,
                git=git,
                tools=tools,
                hooks=hooks,
                tracer=tracer,
                cost_tracker=cost_tracker,
                sse_reporter=sse,
                checkpoint=checkpoint,
                on_iteration=_persist_dialogue,
            )

            # 记忆注入：将历史成功经验作为 few-shot 示例追加到 System Prompt
            _original_builder = tool_loop._build_system_prompt
            _req_content = requirement.content
            _mgr = _get_memory_manager()

            def _memory_aware_prompt(state):
                base = _original_builder(state)
                return _mgr.before_task(_req_content, base)

            tool_loop._build_system_prompt = _memory_aware_prompt

            # 构建初始状态
            initial_state: AgentState = {
                'requirement_id': requirement_id,
                'requirement_content': requirement.content,
                'user_id': requirement.user_id,
                'plan': None,
                'current_step': 'starting',
                'code_files': requirement.code_files or [],
                'validation_result': None,
                'retry_count': 0,
                'error': None,
                'dialogue_history': requirement.dialogue_history or [],
                'metadata': {},
                'tool_call_count': 0,
                'no_progress_count': 0,
                'last_file_list': workspace.list(),
                'hook_failures': {},
                'visual_style': '',
                'intent': 'task',  # 进入此流程的均为 TASK
                # 三期新增字段
                'tasks': [],
                'interfaces': {},
                'implementation_order': [],
                'code_errors': [],
                'qa_passed': True,
                'tester_passed': True,
                'summarize_passed': True,
                'repair_count': 0,
                'role_history': [],
                'role_outputs': {},
            }

            # 检查断点恢复
            resumed_state = checkpoint.resume(requirement_id)
            if resumed_state:
                logger.info(f"从断点恢复需求 {requirement_id}")
                initial_state = {**initial_state, **resumed_state}

            # 开始链路追踪
            trace = tracer.start_trace(requirement_id, requirement.user_id)
            initial_state['metadata']['trace_id'] = trace.trace_id

            sse.progress(requirement_id, 0, '开始处理需求')

            # 将 harness 组件注入 state metadata + 模块级缓存（双路径）
            # metadata 可能被 LangGraph 节点整体替换，模块级缓存作为兜底
            initial_state["metadata"]["_tool_loop"] = tool_loop
            initial_state["metadata"]["_workspace"] = workspace

            # 设置模块级缓存
            set_harness_components(
                tool_loop=tool_loop,
                workspace=workspace,
            )

            # 执行 LangGraph 工作流（三期：多节点编排图）
            # 图内已包含所有路由逻辑：team_leader → [conditional] → coder → qa → summarize → END
            final_state = self._execute_workflow_with_stream(requirement_id, initial_state)

            if final_state is None:
                return False

            # 检查澄清
            if final_state.get('current_step') == 'needs_clarification':
                dialogue = final_state.get('dialogue_history', [])
                if dialogue:
                    requirement.dialogue_history = dialogue
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(requirement, 'dialogue_history')
                requirement.status = 'pending'
                db.commit()
                return True

            # TL 完成（成功或失败），暂停等待用户确认 Plan
            if final_state.get('current_step') in ('presenting_plan', 'team_leader_failed'):
                dialogue = final_state.get('dialogue_history', [])
                if dialogue:
                    requirement.dialogue_history = dialogue
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(requirement, 'dialogue_history')
                requirement.status = 'planning'
                db.commit()
                logger.info(f"[Service] 需求 {requirement_id} 进入 planning 状态，等待用户确认")
                return True

            # 处理最终状态
            return self._process_final_state(db, requirement, requirement_id, final_state,
                                             workspace, git, tracer, sse)

        except Exception as e:
            logger.error(f"处理需求时发生异常：{e}", exc_info=True)
            try:
                self._mark_requirement_failed(requirement, f"处理异常: {str(e)[:200]}")
                db.commit()
            except:
                pass
            return False

        finally:
            db.close()

    def _execute_workflow_with_stream(self, requirement_id: int, initial_state: AgentState,
                                       workflow=None) -> Optional[AgentState]:
        """流式执行 LangGraph 工作流（三期：多节点编排）

        Args:
            requirement_id: 需求 ID
            initial_state: 初始状态
            workflow: 可选，指定的工作流实例。默认使用 self.workflow (完整 v4 图)
        """
        final_state = None
        last_dialogue_count = len(initial_state.get('dialogue_history', []) or [])
        last_code_count = 0

        wf = workflow if workflow is not None else self.workflow

        for event in wf.stream(initial_state, stream_mode='values'):
            final_state = event

            # 检查取消信号
            if RequirementService.is_cancelled(requirement_id):
                logger.info(f"[Workflow] 需求 {requirement_id} 已被取消，终止工作流")
                final_state['current_step'] = 'cancelled'
                final_state['error'] = '操作已被用户取消'
                cancel_msg = SSEMessage.format_event('cancelled', {
                    'message': '操作已被用户取消',
                    'requirement_id': requirement_id,
                })
                sse_manager.broadcast(str(requirement_id), cancel_msg)
                break

            # ★ 先推送增量对话消息（在 early-break 之前），
            # 确保 TL 分析消息 / 澄清追问等在 SSE 实时流中可见，
            # 避免实时视图与持久化顺序不一致
            _SKIP_DIALOGUE_ROLES = {'thinking', 'tool_call', 'tool_result', 'hook_check'}
            dialogues = final_state.get('dialogue_history', []) or []
            for dialogue in dialogues[last_dialogue_count:]:
                role = dialogue.get('role', 'agent')
                if role in _SKIP_DIALOGUE_ROLES:
                    continue
                if dialogue.get('hidden'):
                    continue
                self._send_dialogue(requirement_id, dialogue.get('name', TL_NAME),
                                    dialogue.get('content', ''),
                                    role)
            last_dialogue_count = len(dialogues)

            code_files = final_state.get('code_files', []) or []
            for file_data in code_files[last_code_count:]:
                self._send_code(requirement_id, file_data.get('filename', 'unknown.txt'), file_data.get('content', ''))
            last_code_count = len(code_files)

            if final_state.get('current_step') == 'needs_clarification':
                # 优先从 metadata 获取 question_form，fallback 到 dialogue_history
                question_form = final_state.get('metadata', {}).get('question_form', {})
                if not question_form:
                    # 从 dialogue_history 中提取（持久化恢复场景），跳过已提交的表单
                    for msg in (final_state.get('dialogue_history') or []):
                        if msg.get('question_form') and not msg['question_form'].get('submitted'):
                            question_form = msg['question_form']
                            break
                if question_form:
                    self._send_question_form(requirement_id, question_form)
                break

            current_step = final_state.get('current_step', '')

            # TL 完成后推送 SPEC 和任务清单到前端，暂停等待用户确认
            # 处理 team_leader_done（成功）和 team_leader_failed（失败但有部分 plan）
            tl_completed = current_step in ('team_leader_done', 'team_leader_failed')
            if tl_completed and requirement_id not in self.__class__._spec_pushed_ids:
                self.__class__._spec_pushed_ids.add(requirement_id)
                plan = final_state.get('plan') or {}
                if isinstance(plan, dict) and plan:  # 有有效 plan 数据时才推送
                    logger.info(f"[SSE] 推送 SPEC 和 task_list for requirement {requirement_id}")
                    spec_msg = SSEMessage.format_event('spec', {
                        'title': (final_state.get('requirement_content') or '')[:60],
                        'features': plan.get('features', []),
                        'acceptance_criteria': plan.get('acceptance_criteria', []),
                        'file_structure': plan.get('file_structure', []),
                        'tech_stack': plan.get('tech_stack', {}),
                        'data_model': plan.get('data_model', ''),
                        'complexity': plan.get('complexity', 'S'),
                        'implementation_notes': plan.get('implementation_notes', ''),
                    })
                    sse_manager.broadcast(str(requirement_id), spec_msg)
                    impl_order = plan.get('implementation_order', [])
                    tasks = plan.get('tasks', [])
                    if impl_order:
                        task_items = []
                        for f in impl_order:
                            task_info = None
                            for t in tasks:
                                if t.get('file') == f:
                                    task_info = t
                                    break
                            task_items.append({
                                'file': f,
                                'description': task_info.get('description', f) if task_info else f,
                                'status': 'pending',
                            })
                        task_msg = SSEMessage.format_event('task_list', {
                            'tasks': task_items
                        })
                        sse_manager.broadcast(str(requirement_id), task_msg)
                else:
                    # TL 失败且无有效 plan，推送错误信息
                    logger.warning(f"[SSE] TL 失败无有效 plan for requirement {requirement_id}")
                    error_msg = SSEMessage.format_event('spec', {
                        'title': (final_state.get('requirement_content') or '')[:60],
                        'features': [],
                        'acceptance_criteria': [],
                        'file_structure': [],
                        'tech_stack': {},
                        'data_model': '',
                        'complexity': 'S',
                        'implementation_notes': f"分析失败: {final_state.get('error', '未知错误')}",
                    })
                    sse_manager.broadcast(str(requirement_id), error_msg)

                # 保存检查点，暂停等待用户确认
                tool_loop = final_state.get('metadata', {}).get('_tool_loop')
                if tool_loop and tool_loop.checkpoint:
                    final_state['current_step'] = 'presenting_plan'
                    tool_loop.checkpoint.save(requirement_id, 'team_leader', final_state)
                    logger.info(f"[SSE] 暂停工作流, 等待用户确认 plan for requirement {requirement_id}")
                break  # 暂停 stream, 等待用户确认后继续

            # 多节点进度映射
            node_name = self._detect_node_name(current_step)
            if node_name:
                progress = self._progress_map.get(node_name, 0)
                display_name = {
                    'team_leader': TL_NAME,
                    'coder': DEV_NAME,
                    'verify': QA_NAME,
                    'repair': DEV_NAME,
                }.get(node_name, node_name)
                self._send_progress(requirement_id, display_name, progress)

            # 错误不会立即中断（让图走到 END），除非是严重错误
            if final_state.get('error') and 'ToolCallLoop 未注入' in str(final_state.get('error', '')):
                logger.error(f"工作流执行严重错误：{final_state['error']}")
                break

        return final_state

    def confirm_plan(self, requirement_id: int, feedback: str = "") -> bool:
        """
        用户确认 Plan 后，从 coder 节点继续执行工作流。

        Args:
            requirement_id: 需求 ID
            feedback: 用户反馈/修改意见（可选，为空表示直接确认）

        Returns:
            成功 True，失败 False
        """
        from harness.runtime import ToolCallLoop
        from harness.tools.registry import create_tool_registry
        from harness.constraints.hooks import create_default_hook_manager
        from harness.observability.tracer import Tracer
        from harness.observability.cost import CostTracker
        from sqlalchemy.orm.attributes import flag_modified

        db = SessionLocal()
        try:
            requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
            if not requirement:
                logger.error(f"需求不存在：{requirement_id}")
                return False

            if requirement.status != 'planning':
                logger.warning(f"需求 {requirement_id} 状态为 {requirement.status}，非 planning，跳过")
                return False

            # 如果有用户反馈，追加到需求内容中
            if feedback:
                enriched = f"{requirement.content}\n\n[用户反馈]\n{feedback}"
                requirement.content = enriched

                # 追加 plan_feedback 标记的消息到对话历史
                dialogue_list = list(requirement.dialogue_history or [])
                dialogue_list.append({
                    'role': 'user', 'name': '用户',
                    'content': f"对 Plan 的反馈：{feedback}",
                    'plan_feedback': True,
                })
                requirement.dialogue_history = dialogue_list
                flag_modified(requirement, 'dialogue_history')

                # 清除旧检查点，重置状态为 pending，重新走完整工作流（含 TL 分析）
                checkpoint_mgr = CheckpointManager(db_session=db)
                checkpoint_mgr.clear(requirement_id)
                requirement.status = 'pending'
                db.commit()
                logger.info(f"[ConfirmPlan] 需求 {requirement_id} 有反馈，重置状态重新走完整工作流")

                # 异步重新执行完整工作流（在新的 RequirementService 实例上，避免状态污染）
                import threading
                new_service = RequirementService()
                thread = threading.Thread(
                    target=new_service.process_requirement,
                    args=(requirement_id,),
                    daemon=False,
                )
                thread.start()
                return True

            requirement.status = 'processing'
            db.commit()
            logger.info(f"[ConfirmPlan] 需求 {requirement_id} 开始 Post-Plan 编码流程")

            # ---- 初始化 harness 组件 ----
            workspace = WorkspaceFS(requirement.user_id, requirement_id)
            workspace.init(requirement.code_files)
            git = GitVersioning(workspace)
            tools = create_tool_registry()
            hooks = create_default_hook_manager()
            checkpoint = CheckpointManager(db_session=db)
            cost_tracker = CostTracker()
            tracer = Tracer(db_session=db, cost_tracker=cost_tracker)
            sse = SSEReporter(sse_manager)

            def _persist_dialogue(state):
                try:
                    dialogue = state.get('dialogue_history', [])
                    if dialogue:
                        requirement.dialogue_history = dialogue
                        flag_modified(requirement, 'dialogue_history')
                        db.commit()
                except Exception as e:
                    logger.warning(f"增量持久化对话失败（不阻断）：{e}")

            tool_loop = ToolCallLoop(
                workspace=workspace, git=git, tools=tools, hooks=hooks,
                tracer=tracer, cost_tracker=cost_tracker, sse_reporter=sse,
                checkpoint=checkpoint, on_iteration=_persist_dialogue,
            )

            # 记忆注入
            _original_builder = tool_loop._build_system_prompt
            _req_content = requirement.content
            _mgr = _get_memory_manager()

            def _memory_aware_prompt(state):
                base = _original_builder(state)
                return _mgr.before_task(_req_content, base)

            tool_loop._build_system_prompt = _memory_aware_prompt

            # ---- 从检查点恢复 TL 后的状态 ----
            resumed_state = checkpoint.resume(requirement_id)
            if not resumed_state:
                logger.error(f"[ConfirmPlan] 找不到检查点 for requirement {requirement_id}")
                requirement.status = 'failed'
                requirement.error_message = "找不到检查点，无法恢复状态"
                db.commit()
                return False

            logger.info(f"[ConfirmPlan] 从检查点恢复状态: node={resumed_state.get('current_step', '?')}")

            # 构建初始状态（合并检查点 + harness 注入）
            # dialogue_history 以 DB 为准：包含确认 Plan 时追加的 plan_confirmed 消息，
            # 避免被检查点中 TL 完成时的旧对话覆盖
            initial_state: AgentState = {
                **resumed_state,
                'current_step': 'starting',  # 重置，让 post-plan 图正常流转
                'dialogue_history': list(requirement.dialogue_history or []),
            }

            # 注入 harness 组件
            initial_state["metadata"]["_tool_loop"] = tool_loop
            initial_state["metadata"]["_workspace"] = workspace
            set_harness_components(tool_loop=tool_loop, workspace=workspace)

            # 开始链路追踪
            trace = tracer.start_trace(requirement_id, requirement.user_id)
            initial_state['metadata']['trace_id'] = trace.trace_id

            sse.progress(requirement_id, 20, '用户已确认 Plan，开始编码')

            # ---- 执行 Post-Plan 工作流（coder → verify → repair）----
            post_plan_workflow = create_workflow_post_plan()
            final_state = self._execute_workflow_with_stream(
                requirement_id, initial_state, post_plan_workflow
            )

            if final_state is None:
                return False

            # 处理最终状态
            return self._process_final_state(db, requirement, requirement_id, final_state,
                                             workspace, git, tracer, sse)

        except Exception as e:
            logger.error(f"[ConfirmPlan] 异常：{e}", exc_info=True)
            try:
                requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
                if requirement:
                    requirement.status = 'failed'
                    requirement.error_message = f"确认计划异常: {str(e)[:200]}"
                    db.commit()
            except:
                pass
            return False

        finally:
            db.close()

    @staticmethod
    def _detect_node_name(current_step: str) -> str:
        """根据 current_step 检测当前节点名称"""
        step_to_node = {
            'team_leader_done': 'team_leader',
            'team_leader_failed': 'team_leader',
            'generating': 'coder',
            'coding_done': 'coder',
            'coding_error': 'coder',
            'llm_error': 'coder',
            'verify_done': 'verify',
            'repair_done': 'repair',
            'repair_error': 'repair',
            'task_complete': 'coder',
        }
        return step_to_node.get(current_step, '')

    def _process_final_state(self, db, requirement, requirement_id, final_state, workspace, git, tracer, sse) -> bool:
        """处理最终状态（三期：兼容多节点工作流）"""
        try:
            current_step = final_state.get('current_step', '')

            # 成功状态列表
            success_steps = ('task_complete', 'coding_done', 'verify_done', 'repair_done')

            # 安全网：如果 current_step 明确表示任务完成，忽略可能残留的 error
            if current_step in success_steps:
                if final_state.get('error'):
                    logger.info(f"需求 {requirement_id} current_step={current_step}，忽略残留 error: {final_state['error']}")
                    final_state['error'] = None

            if final_state.get('error'):
                self._mark_requirement_failed(
                    requirement,
                    f"执行错误: {final_state['error'][:200]}",
                    final_state
                )
                db.commit()
                # 失败时也保存 trace（供诊断）
                self._save_trace_on_failure(final_state, requirement_id, tracer)
                return False

            # 检查 current_step 是否为真正的成功状态
            # "no_progress" / "max_iterations" / "coding_error" 表示 Agent 卡住或超限，不应标记为完成
            if current_step in ('no_progress', 'max_iterations', 'coding_error',
                               'repair_error', 'llm_error', 'cancelled'):
                logger.warning(
                    f"需求 {requirement_id} 因 {current_step} 终止，标记为 failed"
                )
                self._mark_requirement_failed(
                    requirement,
                    f"执行终止: {current_step}",
                    final_state
                )
                # 保存已有的对话历史和代码产物（部分产物可能有用）
                dialogue_history = final_state.get('dialogue_history', [])
                if dialogue_history:
                    requirement.dialogue_history = dialogue_history
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(requirement, 'dialogue_history')
                code_files = workspace.snapshot()
                if code_files:
                    requirement.code_files = code_files
                    logger.info(f"需求 {requirement_id} 失败但保存了 {len(code_files)} 个部分产物")
                    for file_data in code_files:
                        self._send_code(requirement_id, file_data['filename'], file_data['content'])
                db.commit()
                # 失败时也保存 trace（供诊断）
                self._save_trace_on_failure(final_state, requirement_id, tracer)
                self._send_complete(requirement_id)
                return False
            code_files = workspace.snapshot()

            # 保存对话历史
            dialogue_history = final_state.get('dialogue_history', [])
            if dialogue_history:
                requirement.dialogue_history = dialogue_history
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(requirement, 'dialogue_history')

            if code_files:
                requirement.code_files = code_files
                logger.info(f"保存了 {len(code_files)} 个代码文件")
                for file_data in code_files:
                    self._send_code(requirement_id, file_data['filename'], file_data['content'])

            # 检查 verify_passed：如果 Evaluator 明确返回 NEEDS_WORK，标记为 finished_with_issues
            # （区别于 failed：代码已生成但质量未达标，用户可自行判断是否可用）
            verify_passed = final_state.get("verify_passed")
            if verify_passed is False:
                eval_error = "代码评估未通过"
                repair_count = final_state.get("metadata", {}).get("repair_count", 0)
                eval_error += f"（经 {repair_count} 轮修复后仍未达标）"
                # 尝试从 Evaluator 结果中提取具体失败原因
                role_outputs = final_state.get("role_outputs", {}) or {}
                evaluator_data_for_failure = {}
                if "Evaluator" in role_outputs:
                    try:
                        import json as _json
                        evaluator_raw = role_outputs["Evaluator"]
                        evaluator_data_for_failure = _json.loads(evaluator_raw) if isinstance(evaluator_raw, str) else evaluator_raw
                        findings = evaluator_data_for_failure.get("findings", [])
                        if findings:
                            critical_findings = [f for f in findings if f.get("severity") == "critical"]
                            if critical_findings:
                                eval_error += "。关键问题: " + "; ".join(
                                    f['description'][:100] for f in critical_findings[:3]
                                )
                    except Exception:
                        pass

                logger.warning(
                    f"需求 {requirement_id} verify_passed=False（Evaluator 判定 NEEDS_WORK），"
                    f"标记为 finished_with_issues"
                )
                requirement.status = 'finished_with_issues'
                requirement.error_message = eval_error
                # 仍然保存代码产物（用户可以使用部分成果）
                code_files = workspace.snapshot()
                if code_files:
                    requirement.code_files = code_files
                    for file_data in code_files:
                        self._send_code(requirement_id, file_data['filename'], file_data['content'])
                # 保存对话历史
                dialogue_history = final_state.get('dialogue_history', [])
                if dialogue_history:
                    requirement.dialogue_history = dialogue_history
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(requirement, 'dialogue_history')
                db.commit()
                # 保存 trace（供诊断）
                self._save_trace_on_failure(final_state, requirement_id, tracer)
                # 推送 evaluator_result SSE（确保前端能看到评估数据）
                if evaluator_data_for_failure:
                    try:
                        sse.evaluator_result(requirement_id, evaluator_data_for_failure)
                    except Exception:
                        pass
                self._send_complete(requirement_id)

                # 经验学习（即使未达标，也记录以供后续改进）
                try:
                    complexity = final_state.get("metadata", {}).get("complexity", "S")
                    _mgr = _get_memory_manager()
                    _mgr.after_task(
                        requirement=requirement.content,
                        complexity=complexity,
                        code_files=code_files,
                        qa_result={
                            "overall_rating": evaluator_data_for_failure.get("overall_score", 0),
                            "passed": False,
                            "critical_issues": [
                                f"{f.get('severity', '?')}: {f.get('description', '')}"
                                for f in evaluator_data_for_failure.get("findings", [])
                            ],
                        },
                        user_id=requirement.user_id,
                    )
                except Exception as e:
                    logger.warning(f"经验学习失败（不阻断）：{e}")

                return True

            requirement.status = 'finished'
            db.commit()

            # 完成追踪
            trace_id = final_state.get('metadata', {}).get('trace_id', '')
            if trace_id and tracer:
                tracer.end_trace(trace_id)
                trace_data = tracer.get_trace(trace_id)
                if trace_data:
                    sse.trace_summary(requirement_id, trace_data.to_dict())

            # 任务成功完成，清除检查点（避免下次误恢复已完成任务）
            try:
                checkpoint.clear(requirement_id)
            except Exception as e:
                logger.warning(f"清除检查点失败（不阻断）：{e}")

            # 经验学习：任务完成后评估并存储经验
            try:
                complexity = final_state.get("metadata", {}).get("complexity", "S")
                qa_data = None
                # 从 Evaluator 结果中提取质量评分
                role_outputs = final_state.get("role_outputs", {}) or {}
                if "Evaluator" in role_outputs:
                    import json as _json
                    evaluator_raw = role_outputs["Evaluator"]
                    try:
                        evaluator_data = _json.loads(evaluator_raw) if isinstance(evaluator_raw, str) else evaluator_raw
                        qa_data = {
                            "overall_rating": evaluator_data.get("overall_score", 7),
                            "passed": evaluator_data.get("verdict") == "PASS",
                            "critical_issues": [
                                f"{f.get('severity', '?')}: {f.get('description', '')}"
                                for f in evaluator_data.get("findings", [])
                            ],
                        }
                    except (_json.JSONDecodeError, TypeError):
                        pass

                _mgr = _get_memory_manager()
                _mgr.after_task(
                    requirement=requirement.content,
                    complexity=complexity,
                    code_files=code_files,
                    qa_result=qa_data,
                    user_id=requirement.user_id,
                )
                pool_stats = _mgr.stats()
                logger.info(
                    f"需求 {requirement_id} 记忆学习完成，"
                    f"记忆总量={pool_stats['total']}, 均分={pool_stats['avg_rating']}"
                )
            except Exception as e:
                logger.warning(f"经验学习失败（不阻断）：{e}")

            self._send_complete(requirement_id)
            return True

        except Exception as e:
            logger.error(f"处理最终状态时发生异常：{e}", exc_info=True)
            self._mark_requirement_failed(requirement, f"处理最终状态异常: {str(e)[:200]}")
            db.commit()
            # 失败时也保存 trace（供诊断）
            self._save_trace_on_failure(final_state, requirement_id, tracer)
            return False

    def _save_trace_on_failure(self, final_state, requirement_id, tracer):
        """失败时也保存链路追踪数据，供后续诊断"""
        try:
            if not tracer:
                return
            trace_id = final_state.get('metadata', {}).get('trace_id', '')
            if trace_id:
                tracer.end_trace(trace_id)
                logger.info(f"[Trace] 失败任务 {requirement_id} 的 trace 已保存: {trace_id}")
        except Exception as e:
            logger.warning(f"[Trace] 保存失败任务 trace 异常: {e}")

    def _send_question_form(self, requirement_id: int, form_data: dict):
        message = SSEMessage.question_form_message(form_data)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_progress(self, requirement_id: int, agent_name: str, progress: int):
        message = SSEMessage.progress_message(agent_name, progress, 'processing')
        sse_manager.broadcast(str(requirement_id), message)

    def _send_dialogue(self, requirement_id: int, name: str, content: str, role: str = 'agent'):
        message = SSEMessage.dialogue_message(role, name, content, get_current_timestamp())
        sse_manager.broadcast(str(requirement_id), message)

    def _send_code(self, requirement_id: int, filename: str, content: str):
        message = SSEMessage.code_message(filename, content, 0, True)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_complete(self, requirement_id: int):
        message = SSEMessage.complete_message(requirement_id)
        sse_manager.broadcast(str(requirement_id), message)

    # ===== IntentRouter 快速通道处理 =====

    def _handle_quick_answer(self, db, requirement, requirement_id: int,
                             intent_result) -> bool:
        """QUICK 意图：LLM 直接回答，SSE 推送，标记完成"""
        from sqlalchemy.orm.attributes import flag_modified

        sse = SSEReporter(sse_manager)
        sse.progress(requirement_id, 30, '分析问题')

        router = IntentRouter()
        answer = router.handle_quick(
            requirement=requirement.content,
            history=requirement.dialogue_history or [],
            is_chat=False,
        )

        # 保存对话历史
        dialogue_list = list(requirement.dialogue_history or [])
        dialogue_list.append({
            'role': 'agent', 'name': TL_NAME,
            'content': answer,
            'status': 'completed',
            'timestamp': get_current_timestamp(),
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'finished'
        db.commit()

        # SSE 推送
        sse.dialogue(requirement_id, 'agent', TL_NAME, answer, 'completed')
        sse.complete(requirement_id)
        logger.info(f"需求 {requirement_id} QUICK 回答完成")
        return True

    def _handle_search_answer(self, db, requirement, requirement_id: int,
                              intent_result) -> bool:
        """SEARCH 意图：当前降级为增强版 QUICK（提示 LLM 给出时效性说明）"""
        from sqlalchemy.orm.attributes import flag_modified

        sse = SSEReporter(sse_manager)
        sse.progress(requirement_id, 30, '搜索信息')

        router = IntentRouter()
        # 在问题前追加提示，让 LLM 注意时效性
        enhanced_requirement = (
            f"[需要最新信息的问题]\n{requirement.content}"
            f"\n\n注意：如果你没有最新的实时数据，请说明你的知识截止日期，"
            f"并建议用户查阅官方文档获取最新信息。"
        )
        answer = router.handle_quick(
            requirement=enhanced_requirement,
            history=requirement.dialogue_history or [],
            is_chat=False,
        )

        dialogue_list = list(requirement.dialogue_history or [])
        dialogue_list.append({
            'role': 'agent', 'name': TL_NAME,
            'content': answer,
            'status': 'completed',
            'timestamp': get_current_timestamp(),
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'finished'
        db.commit()

        sse.dialogue(requirement_id, 'agent', TL_NAME, answer, 'completed')
        sse.complete(requirement_id)
        logger.info(f"需求 {requirement_id} SEARCH 回答完成")
        return True

    def _handle_ambiguous_direct(self, db, requirement, requirement_id: int) -> bool:
        """AMBIGUOUS 意图：直接生成澄清问题，不进入 TeamLeader"""
        from sqlalchemy.orm.attributes import flag_modified
        from harness.instructions.nodes import (
            _generate_clarify_questions, FALLBACK_CLARIFY_QUESTIONS,
        )
        from llm.client import get_client as _get_llm_client

        try:
            client = _get_llm_client()
            questions = _generate_clarify_questions(client, requirement.content)
        except Exception as e:
            logger.warning(f"澄清问题生成失败: {e}")
            questions = []
        if not questions:
            questions = FALLBACK_CLARIFY_QUESTIONS

        dialogue_list = list(requirement.dialogue_history or [])
        dialogue_list.append({
            'role': 'system', 'name': TL_NAME,
            'content': '需求不够明确，需要补充一些信息',
            'status': 'needs_clarification',
            'question_form': {'questions': questions},
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'pending'
        db.commit()

        # SSE 推送澄清表单
        message = SSEMessage.question_form_message({'questions': questions})
        sse_manager.broadcast(str(requirement_id), message)
        logger.info(f"需求 {requirement_id} 触发澄清（AMBIGUOUS 意图），生成 {len(questions)} 个问题")
        return True

    def _build_code_context_text(self, code_files: list) -> str:
        """构建代码上下文文本（供 QUICK 回答使用）"""
        if not code_files:
            return ""
        lines = ["## 项目文件"]
        for f in code_files:
            fname = f.get('filename', 'unknown')
            content = f.get('content', '')
            line_count = content.count('\n') + 1 if content else 0
            # 取前 10 行作为概览
            preview = '\n'.join(content.split('\n')[:10]) if content else '(空)'
            lines.append(f"\n### {fname} ({line_count} 行)\n```\n{preview}\n```")
        return '\n'.join(lines)


# 全局服务实例
requirement_service = RequirementService()


def process_requirement_async(requirement_id: int):
    """异步处理需求（在 Celery worker 或线程中执行）"""
    service = RequirementService()
    return service.process_requirement(requirement_id)


def confirm_plan_async(requirement_id: int, feedback: str = ""):
    """异步确认 Plan 并继续编码（在 Celery worker 或线程中执行）"""
    service = RequirementService()
    return service.confirm_plan(requirement_id, feedback)
