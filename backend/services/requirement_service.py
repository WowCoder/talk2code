# -*- coding: utf-8 -*-
"""
需求管理服务（6 层架构集成版）
封装 AI 多智能体协同处理需求的业务逻辑
"""

import json
from typing import Optional

from models import SessionLocal, Requirement
from harness.observability.logger import get_logger
from utils.sse import SSEMessage, get_current_timestamp
from services.sse_manager import sse_manager
from harness.graph import get_workflow
from harness.state.agent_state import AgentState
from harness.state.workspace import WorkspaceFS
from harness.state.versioning import GitVersioning
from harness.state.checkpoint import CheckpointManager
from harness.tools.registry import create_tool_registry
from harness.tools.file_tools import FileToolHandler
from harness.tools.code_tools import CodeToolHandler
from harness.constraints.hooks import create_default_hook_manager
from harness.environment.permissions import PermissionManager
from harness.observability.tracer import Tracer
from harness.observability.cost import CostTracker
from harness.observability.sse_reporter import SSEReporter
from harness.runtime import ToolCallLoop
from harness.instructions.intent_router import IntentRouter, IntentType
from harness.instructions.orchestrator import RoleOrchestrator
from harness.experience import ExperiencePool
from harness.learning import FeedbackLoop

logger = get_logger(__name__)

# 全局经验池（跨请求持久化，进程重启后清空）
_experience_pool = ExperiencePool()
_feedback_loop = FeedbackLoop(_experience_pool)


class RequirementService:
    """需求管理服务（集成 harness 6 层）"""

    def __init__(self):
        self.workflow = get_workflow()
        self._progress_map = {
            'team_leader': 40,
            'engineer': 80,
        }

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
            permissions = PermissionManager()
            # 初始生成流程：用户已提交需求，写入权限自动授予
            permissions.grant(requirement_id, 'write')
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
            tool_loop = ToolCallLoop(
                workspace=workspace,
                git=git,
                tools=tools,
                hooks=hooks,
                tracer=tracer,
                cost_tracker=cost_tracker,
                sse_reporter=sse,
                permission_manager=permissions,
                checkpoint=checkpoint,
            )

            # 经验注入：包装 _build_system_prompt，在原始 prompt 后追加 few-shot 示例
            _original_builder = tool_loop._build_system_prompt
            _req_content = requirement.content

            def _experience_aware_prompt(state):
                base = _original_builder(state)
                return _feedback_loop.inject_experience(_req_content, base)

            tool_loop._build_system_prompt = _experience_aware_prompt

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

            # 执行 LangGraph 工作流 (team_leader → END)
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

            # TeamLeader 完成后，根据复杂度选择执行路径
            if final_state.get('current_step') in ('team_leader_done', 'team_leader_failed'):
                complexity = final_state.get('metadata', {}).get('complexity', 'S')

                if complexity in ('M', 'L'):
                    # M/L: 多角色协作流程 (PM → Architect → Engineer → QA)
                    logger.info(f"需求 {requirement_id} 复杂度={complexity}，启动多角色协作")
                    orchestrator = RoleOrchestrator(
                        workspace=workspace,
                        sse_reporter=sse,
                        tools=tools,
                        tool_loop_factory=None,
                    )
                    # 注入 tool_loop 供 FrontendEngineer 使用
                    final_state["metadata"]["_tool_loop"] = tool_loop
                    final_state = orchestrator.execute(final_state)
                else:
                    # XS/S: 单角色流程（当前行为），设置角色名为 FrontendEngineer
                    final_state.setdefault("metadata", {})["coder_name"] = "FrontendEngineer"
                    final_state["metadata"]["thinking_name"] = "FrontendEngineer"
                    final_state = tool_loop.run(final_state)

            # 处理最终状态
            return self._process_final_state(db, requirement, requirement_id, final_state,
                                             workspace, git, tracer, sse)

        except Exception as e:
            logger.error(f"处理需求时发生异常：{e}", exc_info=True)
            try:
                requirement.status = 'failed'
                db.commit()
            except:
                pass
            return False

        finally:
            db.close()

    def _execute_workflow_with_stream(self, requirement_id: int, initial_state: AgentState) -> Optional[AgentState]:
        """流式执行 LangGraph 工作流"""
        final_state = None
        last_dialogue_count = len(initial_state.get('dialogue_history', []) or [])
        last_code_count = 0

        for event in self.workflow.stream(initial_state, stream_mode='values'):
            final_state = event

            if final_state.get('current_step') == 'needs_clarification':
                # 优先从 metadata 获取 question_form，fallback 到 dialogue_history
                question_form = final_state.get('metadata', {}).get('question_form', {})
                if not question_form:
                    # 从 dialogue_history 中提取（持久化恢复场景）
                    for msg in (final_state.get('dialogue_history') or []):
                        if msg.get('question_form'):
                            question_form = msg['question_form']
                            break
                if question_form:
                    self._send_question_form(requirement_id, question_form)
                break

            current_step = final_state.get('current_step', '')
            node_name = 'team_leader' if 'team_leader' in current_step else 'engineer' if 'generating' in current_step else ''
            if node_name:
                progress = self._progress_map.get(node_name, 0)
                self._send_progress(requirement_id, {'team_leader': 'TeamLeader', 'engineer': 'FrontendEngineer'}.get(node_name, node_name), progress)

            dialogues = final_state.get('dialogue_history', []) or []
            for dialogue in dialogues[last_dialogue_count:]:
                self._send_dialogue(requirement_id, dialogue.get('name', 'AI'), dialogue.get('content', ''))
            last_dialogue_count = len(dialogues)

            code_files = final_state.get('code_files', []) or []
            for file_data in code_files[last_code_count:]:
                self._send_code(requirement_id, file_data.get('filename', 'unknown.txt'), file_data.get('content', ''))
            last_code_count = len(code_files)

            if final_state.get('error'):
                logger.error(f"工作流执行错误：{final_state['error']}")
                break

        return final_state

    def _process_final_state(self, db, requirement, requirement_id, final_state, workspace, git, tracer, sse) -> bool:
        """处理最终状态"""
        try:
            if final_state.get('error'):
                requirement.status = 'failed'
                db.commit()
                return False

            # 从 workspace 获取最终文件
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
                # 从多角色流程中提取 QA 审查结果
                role_outputs = final_state.get("role_outputs", {}) or {}
                role_history = final_state.get("role_history", []) or []
                for entry in role_history:
                    if entry.get("role_name") == "QAReviewer" and entry.get("success"):
                        # QA 角色已存储 structured_output 到 role_outputs
                        pass
                # 从 final_state 的 role_outputs 检查是否有 QA 结果
                if "QAReviewer" in role_outputs:
                    import json as _json
                    qa_raw = role_outputs["QAReviewer"]
                    try:
                        qa_data = _json.loads(qa_raw) if isinstance(qa_raw, str) else qa_raw
                    except _json.JSONDecodeError:
                        pass

                _feedback_loop.learn_from_result(
                    requirement=requirement.content,
                    complexity=complexity,
                    code_files=code_files,
                    qa_result=qa_data,
                )
                pool_stats = _feedback_loop.stats()
                logger.info(
                    f"需求 {requirement_id} 经验学习完成，"
                    f"经验池总量={pool_stats['total']}, 均分={pool_stats['avg_rating']}"
                )
            except Exception as e:
                logger.warning(f"经验学习失败（不阻断）：{e}")

            self._send_complete(requirement_id)
            return True

        except Exception as e:
            logger.error(f"处理最终状态时发生异常：{e}", exc_info=True)
            requirement.status = 'failed'
            db.commit()
            return False

    def _send_question_form(self, requirement_id: int, form_data: dict):
        message = SSEMessage.question_form_message(form_data)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_progress(self, requirement_id: int, agent_name: str, progress: int):
        message = SSEMessage.progress_message(agent_name, progress, 'processing')
        sse_manager.broadcast(str(requirement_id), message)

    def _send_dialogue(self, requirement_id: int, name: str, content: str):
        message = SSEMessage.dialogue_message('agent', name, content, get_current_timestamp())
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
            'role': 'agent', 'name': 'AI',
            'content': answer,
            'status': 'completed',
            'timestamp': get_current_timestamp(),
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'finished'
        db.commit()

        # SSE 推送
        sse.dialogue(requirement_id, 'agent', 'AI', answer, 'completed')
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
            'role': 'agent', 'name': 'AI',
            'content': answer,
            'status': 'completed',
            'timestamp': get_current_timestamp(),
        })
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')
        requirement.status = 'finished'
        db.commit()

        sse.dialogue(requirement_id, 'agent', 'AI', answer, 'completed')
        sse.complete(requirement_id)
        logger.info(f"需求 {requirement_id} SEARCH 回答完成")
        return True

    def _handle_ambiguous_direct(self, db, requirement, requirement_id: int) -> bool:
        """AMBIGUOUS 意图：直接生成澄清问题，不进入 TeamLeader"""
        from sqlalchemy.orm.attributes import flag_modified
        from harness.instructions.nodes import _generate_clarify_questions
        from llm.client import get_client as _get_llm_client

        try:
            client = _get_llm_client()
            questions = _generate_clarify_questions(client, requirement.content)
        except Exception as e:
            logger.warning(f"澄清问题生成失败: {e}")
            questions = [
                {"id": "q1", "type": "text", "label": "请更具体地描述你的需求"},
                {"id": "visual_style", "type": "radio",
                 "label": "你偏好哪种视觉风格？",
                 "options": ["极简白", "暖柔风格", "暗黑科技", "活泼多彩", "无偏好"]},
            ]

        dialogue_list = list(requirement.dialogue_history or [])
        dialogue_list.append({
            'role': 'system', 'name': 'TeamLeader',
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
    """异步处理需求（在线程中执行）"""
    service = RequirementService()
    return service.process_requirement(requirement_id)
