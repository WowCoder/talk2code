# -*- coding: utf-8 -*-
"""
需求管理服务（6 层架构集成版）
封装 AI 多智能体协同处理需求的业务逻辑
"""

import json
import threading
from typing import Optional

from models import Requirement
from utils.db import get_db
from harness.observability.logger import get_logger
from utils.sse import SSEMessage, get_current_timestamp
from services.sse_manager import sse_manager
from harness.graph import get_workflow, create_workflow_post_plan
from harness.harness_context import set_all as set_harness_components, clear_all as clear_harness_components
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

# 超出纯前端能力边界时的澄清问题（确定性构造，零 LLM）。
#
# 设计取舍：不直接拒绝。用户说"要后端"，真实意图通常是"数据要存下来""要多人看到"，
# 一句"做不到"把需求堵死；而"本地存储版 + 讲清局限"既守住边界又给得出东西。
# 选项刻意写成可交付的形态，用户选完即可开工，不需要再追问一轮。
OUT_OF_SCOPE_QUESTIONS = [
    {
        "id": "data_scope",
        "type": "radio",
        "label": (
            "这个需求有一部分要靠服务器（数据库 / 用户账号 / 跨设备同步），"
            "而这里产出的是纯前端站点——做出来是一个双击就能打开、功能完整、"
            "数据保存在你这台设备上的应用。你想要哪种形态？"
        ),
        "options": [
            "改成浏览器本地存储 -- 功能完整可用，数据保存在你这台设备上（推荐）",
            "内置示例数据做演示 -- 预置一批数据，重点展示界面和交互效果",
            "只做界面与交互 -- 数据部分留空接口，后续你自己接后端",
        ],
    },
    {
        "id": "visual_style",
        "type": "radio",
        "label": "你偏好哪种视觉风格？",
        "options": [
            "极简白 -- 白色背景，灰黑文字，大量留白，功能优先",
            "暖柔风格 -- 暖色调、圆角卡片、柔和阴影 (默认)",
            "暗黑科技 -- 深色背景、霓虹强调色、终端风格",
            "活泼多彩 -- 明亮渐变、大色块、趣味性设计",
            "无偏好，自动选择",
        ],
    },
]

OUT_OF_SCOPE_NOTICE = (
    "这个需求里有需要服务器支撑的部分（比如数据库、账号体系、跨设备同步）。"
    "这里生成的是纯前端站点——没有服务器和数据库，数据保存在浏览器本地。"
    "选一个下面可行的做法，我立刻开工。"
)

# 全局记忆管理器（持久化到 agent_memories_v2 表，跨进程重启保留）
_memory_manager: Optional[MemoryManager] = None


def _get_memory_manager() -> MemoryManager:
    """懒初始化全局 MemoryManager（首次调用时加载模型）"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(llm_client=get_client())
    return _memory_manager


def _build_injected_memory_block(requirement_content: str, user_id: int,
                                 requirement_id: Optional[int] = None):
    """检索并渲染待注入的历史经验文本块 + 记账行 id。

    任何失败都返回 ("", [])，绝不阻断主流程。

    抽成独立函数是因为生成阶段与定向补全阶段都要用，且必须与"每任务算一次、
    结果缓存复用"的策略配合 —— 放进 _build_system_prompt 里会导致每个
    LLM turn 重新检索。

    返回的 hit_ids 是本次注入写下的记账行主键，任务到**终态**时用 resolve_hits()
    回填结果。

    回填口径（2026-09-28 补齐，此前只记账不回填）：只在结局明确的终态回填 ——
      · 需求交付成功（status='finished'，且拿到 qa_data）→ 按 qa_data['passed'] 记
      · 交付失败分支（critical 缺陷未清、转 needs_user_input）→ 记 fail
    澄清 / planning / 取消等中间态**不回填**，这些行保持 pending：
    pending 既不算命中也不算未命中，不污染统计，同时保留「任务没跑完」的线索。
    这样绕开了原注释担心的"成功语义模糊"问题，又让"注入的记忆到底有没有用"
    在生产侧第一次有数据可答（此前 139 条生产命中全是 pending，只能靠 eval 的
    小样本外推）。
    """
    try:
        return _get_memory_manager().inject_with_receipt(
            requirement_content, user_id, requirement_id=requirement_id)
    except Exception as e:
        logger.warning(f"记忆检索失败（不阻断，降级为无记忆注入）：{e}")
        return "", []


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
        # 进度百分比不再在本地维护一张表：统一走 harness.observability.progress_plan，
        # 它保证「需求分析 → 编码 → 验证 → 修复」单调不减（此前 repair=85 低于
        # verify=90，修复轮进度条会往回跳）。

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
        with get_db() as db:
            try:
                requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
                if not requirement:
                    logger.error(f"需求不存在：{requirement_id}")
                    return False
    
                if requirement.status in ['finished', 'processing']:
                    logger.info(f"需求 {requirement_id} 状态为 {requirement.status}，跳过")
                    return False
    
                # 清除上一次可能遗留的取消信号，避免新任务被旧信号立即判定为已取消
                RequirementService.clear_cancel(requirement_id)
    
                requirement.status = 'processing'
                db.commit()
                logger.info(f"需求 {requirement_id} 开始处理")
    
                # ===== 意图路由：非 TASK 请求快速返回，不进入完整工作流 =====
                if '[用户补充说明]' not in requirement.content:
                    router = IntentRouter()
                    intent_result = router.classify(requirement.content)
                    logger.info(f"需求 {requirement_id} 意图分类: {intent_result.intent.value}")

                    # 里程碑：意图路由结果。时间线的第一条事件 ——
                    # 「需求根本没进工作流」这种情况在后台要一眼可见，
                    # 否则排查时会以为任务跑了却什么都没记录。
                    try:
                        from harness.observability.trace_writer import record_event
                        record_event(
                            requirement_id, "intent",
                            f"意图识别 · {intent_result.intent.value}",
                            status="ok",
                            meta={"intent": intent_result.intent.value,
                                  "confidence": getattr(intent_result, "confidence", None),
                                  "skill_name": getattr(intent_result, "skill_name", None)},
                        )
                    except Exception:
                        pass

                    if intent_result.intent == IntentType.QUICK:
                        return self._handle_quick_answer(db, requirement, requirement_id, intent_result)
                    elif intent_result.intent == IntentType.SEARCH:
                        return self._handle_search_answer(db, requirement, requirement_id, intent_result)
                    elif intent_result.intent == IntentType.AMBIGUOUS:
                        return self._handle_ambiguous_direct(db, requirement, requirement_id)
                    elif intent_result.intent == IntentType.OUT_OF_SCOPE:
                        return self._handle_out_of_scope(db, requirement, requirement_id)
                    elif intent_result.intent == IntentType.SKILL:
                        # SKILL 进入完整工作流：SkillLoader 会将该技能的 SKILL.md
                        # 注入编码 Prompt，Coder 可经 run_skill 工具编排/组合子技能。
                        logger.info(
                            f"需求 {requirement_id} 命中工作流技能: "
                            f"{intent_result.skill_name}，进入完整工作流"
                        )
    
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
    
                # 记忆注入：将历史经验作为 few-shot 示例追加到 System Prompt
                #
                # 注入内容在一个任务内不会变化，所以必须在任务开始时算一次并缓存。
                # 若像以前那样放在 _build_system_prompt 内部计算，由于 system prompt
                # 是每个 LLM turn 重建一次的，检索开销会被放大到每个 turn
                # （全表查询 + 索引一致性检查 + LLM 校验）。
                _original_builder = tool_loop._build_system_prompt
                _req_content = requirement.content
                _req_user_id = requirement.user_id
                # 记账行 id，任务终态回填 outcome。先置空，避免检索分支未走到时
                # 终态回填抛 NameError。
                _memory_hit_ids = []
                _memory_block, _memory_hit_ids = _build_injected_memory_block(
                    _req_content, _req_user_id, requirement_id=requirement_id)

                # 挂到 loop 上供 file_coder 的逐文件编码阶段复用：
                # 该阶段会整体替换 _build_system_prompt，拿不到这里的包装结果。
                tool_loop._memory_block = _memory_block

                def _memory_aware_prompt(state):
                    base = _original_builder(state)
                    return base + _memory_block if _memory_block else base

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
                    # 断点停在「TL 已出 plan、等用户确认」→ 直接进 Post-Plan 编码，
                    # 不重跑 TeamLeader。req 164 的教训：failed 后 resume 落到这个
                    # 断点仍从完整图的 TL 节点跑起，用户确认过的计划被推翻、TL 重新
                    # 分析后又要求确认一遍，状态滚回 planning，确认卡片再次出现。
                    _plan = resumed_state.get('plan')
                    if (resumed_state.get('current_step') == 'presenting_plan'
                            and isinstance(_plan, dict) and _plan):
                        logger.info(
                            f"需求 {requirement_id} 断点为 presenting_plan，"
                            "跳过 TL 重跑，直接进入 Post-Plan 编码"
                        )
                        requirement.status = 'processing'
                        db.commit()
                        return self._continue_from_plan_checkpoint(
                            db, requirement, requirement_id)
                    initial_state = {**initial_state, **resumed_state}
                    # 检查点里的对话历史是断点时刻的快照，不含之后追加的用户消息
                    # （如确认 Plan 的记录）。req 164：resume 用快照覆盖了 DB，
                    # 用户「已确认开发计划」的消息凭空消失。对话历史一律以 DB 为准。
                    initial_state['dialogue_history'] = list(requirement.dialogue_history or [])
    
                # 开始链路追踪
                trace = tracer.start_trace(requirement_id, requirement.user_id)
                initial_state['metadata']['trace_id'] = trace.trace_id
    
                from harness.observability import progress_plan as _pp_seq
                sse.progress(
                    requirement_id, _pp_seq.START, '开始处理需求', stage='planning'
                )
    
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
                except Exception as mark_err:
                    logger.warning(f"标记需求失败状态时异常（忽略）：{mark_err}")
                return False
    
            finally:
                # 清理 ContextVar，避免线程池复用时下一个任务拿到上一个任务的 tool_loop/workspace
                clear_harness_components()
                # 任务结束：回收 SPEC 推送标记与取消信号，避免全局集合无界增长
                RequirementService._spec_pushed_ids.discard(requirement_id)
                RequirementService.clear_cancel(requirement_id)

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
                                    role,
                                    timestamp=dialogue.get('timestamp'))
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
                        # 需求契约在前：用户签字的对象必须和验收依据同源。
                        # acceptance_criteria 此前就推了，但前端卡片没接，
                        # 导致「用户确认的内容」与「判定的内容」是两份。
                        'requirement_restated': plan.get('requirement_restated', ''),
                        'features': plan.get('features', []),
                        'assumptions': plan.get('assumptions', []),
                        'acceptance_criteria': plan.get('acceptance_criteria', []),
                        # 工程契约在后：折叠在技术细节区
                        'tech_stack': plan.get('tech_stack', {}),
                        'file_structure': plan.get('file_structure', []),
                        'complexity': plan.get('complexity', 'S'),
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
                        'complexity': 'S',
                        'error': f"分析失败: {final_state.get('error', '未知错误')}",
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
            # 文案从「角色名」改为「动作描述」：角色名不携带任何进展信息，
            # 前端只能显示成"开发工程师工作中…"这类无信息文案。
            node_name = self._detect_node_name(current_step)
            if node_name:
                # 百分比与「本轮轮次」统一由 progress_plan 决定，避免修复轮
                # 退回上一轮的位置（详情见该模块的 docstring）。
                from harness.observability import progress_plan as _pp
                progress = _pp.node_percent(
                    node_name, _pp.round_index(final_state or {})
                )
                display_name = {
                    'team_leader': '正在分析需求，拆解实现计划',
                    'coder': '正在编写代码',
                    'verify': '正在浏览器里验证效果',
                    'repair': '正在修复发现的问题',
                }.get(node_name, node_name)
                stage = {
                    'team_leader': 'planning',
                    'coder': 'coding',
                    'verify': 'verifying',
                    'repair': 'repairing',
                }.get(node_name, '')
                self._send_progress(requirement_id, display_name, progress, stage)

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

        with get_db() as db:
            try:
                requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
                if not requirement:
                    logger.error(f"需求不存在：{requirement_id}")
                    return False
    
                if requirement.status != 'planning':
                    logger.warning(f"需求 {requirement_id} 状态为 {requirement.status}，非 planning，跳过")
                    return False

                # 里程碑：用户确认计划。它把时间线切成「规划」与「编码」两段，
                # 也是排查「用户到底确认的是哪一版计划」的锚点。
                try:
                    from harness.observability.trace_writer import record_event
                    record_event(
                        requirement_id, "confirm",
                        "用户确认计划" + ("（附修改意见）" if feedback else ""),
                        status="ok", meta={"has_feedback": bool(feedback),
                                           "feedback_len": len(feedback or "")},
                    )
                except Exception:
                    pass

    
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
    
                # 异步重新执行完整工作流（经由 task_queue 提交：
                # 获得全局并发上限，且不阻塞进程退出；在新的 RequirementService
                # 实例上执行，避免状态污染）。
                # dedupe=False：本函数**自身就跑在需求 X 的任务里**，默认去重会把
                # 这个后续任务判定成"已在处理中"直接丢弃 —— 结果是状态被置回
                # pending 但没有任何 worker 接手，需求永久卡住。
                from services.task_queue import task_queue
                new_service = RequirementService()
                task_id = task_queue.submit(
                    requirement_id,
                    new_service.process_requirement,
                    requirement_id,
                    dedupe=False,
                )
                if task_id is None:
                    logger.error(
                        f"[ConfirmPlan] 需求 {requirement_id} 的反馈重跑任务提交失败，"
                        "需求已重置为 pending 但无人接手"
                    )
                    return False
                return True
    
                requirement.status = 'processing'
                db.commit()
                return self._continue_from_plan_checkpoint(db, requirement, requirement_id)

            except Exception as e:
                logger.error(f"[ConfirmPlan] 异常：{e}", exc_info=True)
                try:
                    requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
                    if requirement:
                        requirement.status = 'failed'
                        requirement.error_message = f"确认计划异常: {str(e)[:200]}"
                        db.commit()
                except Exception as mark_err:
                    logger.warning(f"标记需求失败状态时异常（忽略）：{mark_err}")
                return False

            finally:
                clear_harness_components()

    def _continue_from_plan_checkpoint(self, db, requirement, requirement_id: int) -> bool:
        """从「TL 已出 plan、等确认」的检查点直接进入 Post-Plan 编码流程。

        confirm_plan（用户点了确认）与 process_requirement 的断点恢复共用这段：
        req 164 的教训是后者恢复到 presenting_plan 断点时仍从完整图的 team_leader
        节点跑起——用户确认过的计划被推翻、TL 重新分析又要求确认一遍，
        而此间任何一次 LLM 超时都会把这个循环再滚一圈。
        调用方需保证 requirement.status 已置为 processing 且 harness 组件已清空。
        """
        from harness.runtime import ToolCallLoop
        from harness.tools.registry import create_tool_registry
        from harness.constraints.hooks import create_default_hook_manager
        from harness.observability.tracer import Tracer
        from harness.observability.cost import CostTracker
        from sqlalchemy.orm.attributes import flag_modified

        try:
    
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
    
            # 记忆注入（每任务算一次并缓存，不要放进 builder 内部逐 turn 计算）
            _original_builder = tool_loop._build_system_prompt
            _req_content = requirement.content
            _req_user_id = requirement.user_id
            _memory_block, _memory_hit_ids = _build_injected_memory_block(
                _req_content, _req_user_id, requirement_id=requirement_id)
            tool_loop._memory_block = _memory_block

            def _memory_aware_prompt(state):
                base = _original_builder(state)
                return base + _memory_block if _memory_block else base

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
    
            from harness.observability import progress_plan as _pp_confirm
            sse.progress(
                requirement_id, _pp_confirm.PLAN_CONFIRMED,
                '用户已确认 Plan，开始编码', stage='coding',
            )
    
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
            logger.error(f"[PostPlan] 编码流程异常：{e}", exc_info=True)
            try:
                requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
                if requirement:
                    requirement.status = 'failed'
                    requirement.error_message = f"确认计划异常: {str(e)[:200]}"
                    db.commit()
            except Exception as mark_err:
                logger.warning(f"标记需求失败状态时异常（忽略）：{mark_err}")
            return False
    
        finally:
            clear_harness_components()

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

            # 检查 verify_passed：如果 Evaluator 明确返回 NEEDS_WORK，
            # 按交付门禁（审查报告 Phase 3.3）决定最终状态：
            # - critical 缺陷未清零且门禁开启 → needs_user_input + 差异报告
            #   （不再自动放行为 finished_with_issues——假完成是对用户信任的最大消耗）
            # - 仅剩 major/minor → finished_with_issues（用户可自行判断可用性）
            verify_passed = final_state.get("verify_passed")
            if verify_passed is False:
                repair_count = final_state.get("metadata", {}).get("repair_count", 0)
                eval_error = f"代码评估未通过（经 {repair_count} 轮修复后仍未达标）"
                # 从 Evaluator 结果中提取具体失败原因
                role_outputs = final_state.get("role_outputs", {}) or {}
                evaluator_data_for_failure = {}
                if "Evaluator" in role_outputs:
                    try:
                        import json as _json
                        evaluator_raw = role_outputs["Evaluator"]
                        evaluator_data_for_failure = _json.loads(evaluator_raw) if isinstance(evaluator_raw, str) else evaluator_raw
                    except Exception:
                        evaluator_data_for_failure = {}

                findings_for_failure = evaluator_data_for_failure.get("findings", [])
                critical_findings = [f for f in findings_for_failure if f.get("severity") == "critical"]
                unmet_acs = [
                    r for r in (evaluator_data_for_failure.get("ac_results") or [])
                    if not r.get("passed")
                ]
                if critical_findings:
                    eval_error += "。关键问题: " + "; ".join(
                        f['description'][:100] for f in critical_findings[:3]
                    )

                # ---- 交付门禁：critical 未清零不自动放行 ----
                from config import settings as _settings
                gate_blocks = bool(critical_findings) and _settings.DELIVERY_GATE_STRICT

                # 构建用户可读的差异报告（未达成的 AC + 关键缺陷清单）
                diff_report_lines = []
                if unmet_acs:
                    diff_report_lines.append("**未达成的验收条件**:")
                    diff_report_lines += [
                        f"- ❌ [{r.get('ac_id', '?')}] {r.get('label', '')}"
                        + (f" — {'; '.join(r.get('failures', []))}" if r.get("failures") else "")
                        for r in unmet_acs
                    ]
                if critical_findings:
                    diff_report_lines.append("**关键缺陷**:")
                    diff_report_lines += [
                        f"- 🔴 {f.get('description', '')[:120]}"
                        for f in critical_findings[:5]
                    ]
                diff_report = "\n".join(diff_report_lines) if diff_report_lines else "（无明细）"

                logger.warning(
                    f"需求 {requirement_id} verify_passed=False "
                    f"(critical={len(critical_findings)}, 未达成AC={len(unmet_acs)}, "
                    f"交付门禁={'拦截' if gate_blocks else '放行'})"
                )
                requirement.error_message = eval_error
                # 里程碑：交付门禁结论。「为什么这个需求没标成完成」
                # 在后台上必须一眼可见，而不是只能靠 error_message 猜。
                try:
                    from harness.observability.trace_writer import record_event
                    record_event(
                        requirement_id, "quality_gate",
                        ("交付拦截 · critical 未清零"
                         if gate_blocks else "交付放行 · 仅剩非 critical 问题"),
                        status="blocked" if gate_blocks else "warning",
                        meta={"blocked": bool(gate_blocks),
                              "critical_count": len(critical_findings),
                              "unmet_acs": len(unmet_acs),
                              "repair_rounds": repair_count},
                    )
                except Exception:
                    pass
                if gate_blocks:
                    requirement.status = 'needs_user_input'
                    # 差异报告写入对话历史，前端可见"哪些没做完"
                    dialogue_history = final_state.get('dialogue_history') or \
                        list(requirement.dialogue_history or [])
                    dialogue_history.append({
                        'role': 'agent',
                        'name': QA_NAME,
                        'content': (
                            "## ⚠️ 交付拦截：存在未解决的关键缺陷\n\n"
                            f"经 {repair_count} 轮修复仍未清零 critical 缺陷，"
                            "系统不会将此结果标记为已完成。\n\n"
                            f"{diff_report}\n\n"
                            "**你可以选择**: 在对话中描述调整方向继续修复，"
                            "或自行修改代码后使用。"
                        ),
                        'status': 'completed',
                        'delivery_gate': {
                            'blocked': True,
                            'critical_count': len(critical_findings),
                            'unmet_acs': len(unmet_acs),
                        },
                    })
                    requirement.dialogue_history = dialogue_history
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(requirement, 'dialogue_history')
                else:
                    requirement.status = 'finished_with_issues'

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

                # 经验学习（负样本同样入库——七连败必须沉淀为教训）
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

                # 记账回填（失败结局）：此前生产侧只记账不回填，139 条命中记录
                # 永远是 pending，于是"注入的记忆到底有没有用"这个问题在数据上
                # 根本无法回答（eval 侧 33% 的通过率没有生产对照）。
                try:
                    _get_memory_manager().resolve_hits(_memory_hit_ids, passed=False)
                except Exception as e:
                    logger.warning(f"记忆记账回填失败（不阻断）：{e}")

                return True

            requirement.status = 'finished'
            db.commit()

            # 里程碑：交付完成。时间线的最后一条 —— 交付时刻、经过几轮修复、
            # 门禁口径（strict/loose）都在这里，供后续对比不同时期的交付质量。
            try:
                from harness.observability.trace_writer import record_event
                from config import settings as _dlv_settings
                _rep = int((final_state.get('metadata') or {}).get(
                    'defect_repair_count', 0) or 0)
                record_event(
                    requirement_id, "deliver", "交付完成",
                    status="ok",
                    meta={"repair_rounds": _rep,
                          "gate": ("strict"
                                   if getattr(_dlv_settings, "DELIVERY_GATE_STRICT", False)
                                   else "loose")},
                )
            except Exception:
                pass

            # 交付边界折叠（短期记忆 v2 §3.H）：写 .task/DELIVERY.md（handoff）并清空工具轨迹。
            # 不可重建项（需求/计划/决策/反馈）已落地到 requirement 状态 + TASK_STATE.md，
            # 加上此处 handoff，故可丢弃上一轮 read/edit/verify 结果（100% 可重建）。
            try:
                from harness.state.context_pipeline import finalize_delivery
                finalize_delivery(final_state, workspace)
                requirement.dialogue_history = final_state.get("dialogue_history", [])
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(requirement, 'dialogue_history')
                db.commit()
            except Exception as e:
                logger.warning(f"[Delivery] handoff 折叠失败（不阻断）: {e}")

            # 完成追踪
            trace_id = final_state.get('metadata', {}).get('trace_id', '')
            if trace_id and tracer:
                tracer.end_trace(trace_id)
                trace_data = tracer.get_trace(trace_id)
                if trace_data:
                    sse.trace_summary(requirement_id, trace_data.to_dict())

            # 任务成功完成，清除检查点（避免下次误恢复已完成任务）
            # 注：此处必须自建 CheckpointManager —— 之前直接引用未定义的
            # `checkpoint`，NameError 被 except 吞成一条 warning，导致检查点
            # 从未被清除：残留的旧检查点会让后续的 /resume 恢复出过期状态。
            try:
                CheckpointManager(db_session=db).clear(requirement_id)
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
                # 记账回填（终局）：把本次注入的记忆与任务结局关联起来，
                # 让每条记忆能算出"被注入 N 次、其中 M 次任务通过"。
                # 注意这是相关性不是因果性 —— 要证明因果得靠 A/B（eval --with-memory）。
                try:
                    _passed = bool(qa_data.get("passed")) if isinstance(qa_data, dict) else True
                    _get_memory_manager().resolve_hits(_memory_hit_ids, passed=_passed)
                except Exception as e:
                    logger.warning(f"记忆记账回填失败（不阻断）：{e}")

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
                # 显式标记 error：start_trace 时已落库 running 占位，
                # 此处只更新终态，失败任务也能被后台列表检索到。
                tracer.end_trace(trace_id, status="error")
                logger.info(f"[Trace] 失败任务 {requirement_id} 的 trace 已保存: {trace_id}")
        except Exception as e:
            logger.warning(f"[Trace] 保存失败任务 trace 异常: {e}")

    def _send_question_form(self, requirement_id: int, form_data: dict):
        message = SSEMessage.question_form_message(form_data)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_progress(self, requirement_id: int, agent_name: str, progress: int,
                       stage: str = ''):
        message = SSEMessage.progress_message(agent_name, progress, 'processing', stage)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_dialogue(self, requirement_id: int, name: str, content: str,
                       role: str = 'agent', timestamp: str | None = None):
        # timestamp 必须用消息在 dialogue_history 里的原始时间，不能用推送时刻。
        # 前端按「role+name+content+timestamp」做幂等去重：SSE 重连时后端会整段回放
        # 缓冲消息，若回放时间与首推时间不同，同一条消息会被当成两条（req 164：
        # 一轮生成刷新两次页面，TL 分析显示 4 遍）。
        message = SSEMessage.dialogue_message(
            role, name, content, timestamp or get_current_timestamp())
        sse_manager.broadcast(str(requirement_id), message)

    def _send_code(self, requirement_id: int, filename: str, content: str):
        message = SSEMessage.code_message(filename, content, 0, True)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_complete(self, requirement_id: int, status: str = None):
        """推送完成事件，并带上需求的**终态**。

        status 从 DB 读、不由调用方传：三个调用点分布在「成功 / 评估未通过 /
        异常终止」三条分支上，逐个传参总会漏；调用时各分支都已 commit，读到的
        就是权威终态。DB 读失败时退化成不带 status（前端不覆盖状态，行为与修复
        前一致），绝不阻断完成事件本身。
        """
        if not status:
            try:
                with get_db() as db:
                    status = db.query(Requirement.status).filter(
                        Requirement.id == requirement_id
                    ).scalar()
            except Exception as e:
                logger.warning(f"读取需求 {requirement_id} 终态失败（不阻断）: {e}")
                status = None
        message = SSEMessage.complete_message(requirement_id, status)
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
        sse.complete(requirement_id, requirement.status)
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
        sse.complete(requirement_id, requirement.status)
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

    def _handle_out_of_scope(self, db, requirement, requirement_id: int) -> bool:
        """OUT_OF_SCOPE 意图：讲清纯前端边界，并给出可交付的替代方案。

        此前缺少这一类：需求会直接进 TASK 流程，Coder 闷头用 localStorage 伪造一个
        "登录/数据库"，验收不通过也不告诉用户真实原因——用户只觉得"这平台做不好"。
        这里把边界摆到台面上，让用户自己选一条走得通的路。
        """
        from sqlalchemy.orm.attributes import flag_modified

        dialogue_list = list(requirement.dialogue_history or [])
        dialogue_list.append({
            'role': 'system', 'name': TL_NAME,
            'content': OUT_OF_SCOPE_NOTICE,
            'status': 'needs_clarification',
            'question_form': {'questions': OUT_OF_SCOPE_QUESTIONS},
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'pending'
        db.commit()

        sse_manager.broadcast(
            str(requirement_id),
            SSEMessage.question_form_message({'questions': OUT_OF_SCOPE_QUESTIONS}),
        )
        logger.info(f"需求 {requirement_id} 判定超出纯前端能力边界，已推送边界澄清")
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
