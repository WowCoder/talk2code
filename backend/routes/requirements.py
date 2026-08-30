# -*- coding: utf-8 -*-
"""需求管理 API 路由"""
import threading
from flask import request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.db import get_db, transactional_db
from utils.sse import get_current_timestamp, SSEMessage
from services.sse_manager import sse_manager
from services.task_queue import task_queue
from factory import app, rate_limit_requirement, rate_limit_chat, limiter, logger
from services.requirement_service import process_requirement_async
from utils.preview_token import make_preview_token
from config import settings

# ==================== 需求管理 API ====================

# ---- Chat 并发管控 ----
# chat_with_requirement 是同步长跑（请求线程内跑完整 LLM 工具循环）。
# 两个守护：
# 1. 同需求互斥：同一 req_id 同时只允许一个 Chat 任务，
#    避免两个循环并发写同一 workspace 造成文件损坏/状态错乱；
# 2. 全局信号量：并发 LLM 循环数对齐 task_queue.max_workers，
#    防止每个请求线程都独立打 provider 造成限流/成本失控。
_chat_semaphore = threading.BoundedSemaphore(settings.TASK_QUEUE_MAX_WORKERS)
_chat_inflight: dict = {}
_chat_inflight_lock = threading.Lock()
_CHAT_ACQUIRE_TIMEOUT = 30  # 等待并发名额的最长秒数，超时返回 429


def _chat_quality_gate(workspace) -> tuple[list[dict], str]:
    """Chat 修改后的轻量质量闸门（审查报告 Phase 3.4）

    修改完成后自动跑一次通用冒烟（一次 15 秒内的无头浏览器调用）：
    - 无缺陷 → 通过，返回 ([], "")
    - 发现确定性缺陷 → 由调用方回滚本次修改并把缺陷清单告知用户
      （人工调整越多越离标准的旧痛点由此闸门兜住）

    Returns:
        (defects, human_summary)：浏览器不可用时返回 ([], "") 即放行降级。
    """
    try:
        index_path = workspace.path / "index.html"
        if not index_path.exists():
            return [], ""
        from harness.tools.preview_runner import run_universal_smoke
        result = run_universal_smoke(index_path)
        if not result.get("available", False):
            return [], ""
        defects = result.get("defects", [])
        summary = "; ".join(
            f"[{d.get('type')}] {d.get('message', '')[:80]}" for d in defects[:3]
        )
        return defects, summary
    except Exception as e:
        logger.warning(f"Chat 质量闸门异常（放行降级）: {e}")
        return [], ""

@app.route('/api/requirements', methods=['POST'])
@rate_limit_requirement
@jwt_required()
def create_requirement():
    """创建需求接口"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    data = request.get_json()

    if not data or not data.get('content'):
        return jsonify({'error': '需求内容不能为空'}), 400

    content = data.get('content', '').strip()
    title = content[:100]

    with transactional_db() as db:
        requirement = Requirement(
            user_id=current_user_id,
            title=title,
            content=content,
            status='pending',
            dialogue_history=[{
                'role': 'user',
                'name': '用户',
                'content': content,
                'timestamp': get_current_timestamp()
            }],
            code_files=[]
        )
        db.add(requirement)
        db.flush()  # flush 执行 INSERT 并填充自增 ID，但不提交事务
        req_id = requirement.id
        req_title = requirement.title
        req_status = requirement.status

    # 事务已提交后再入队：避免 worker 在事务未提交时查不到该行导致任务失败
    logger.info(f"创建需求 {req_id}，准备提交到任务队列")
    task_id = task_queue.submit(req_id, process_requirement_async, req_id)
    if task_id is None:
        # 该需求已有 PENDING/RUNNING 任务：不另起线程（否则并发重复处理）
        logger.warning(f"需求 {req_id} 已有任务在处理中，跳过重复提交")

    return jsonify({
        'message': '需求已提交，正在处理',
        'requirement': {
            'id': req_id,
            'title': req_title,
            'status': req_status
        }
    }), 201


@app.route('/api/requirements', methods=['GET'])
@jwt_required()
def list_requirements():
    """获取需求列表（支持 ?trash=true 查询回收站）"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    show_trash = request.args.get('trash', '').lower() == 'true'

    with get_db() as db:
        query = db.query(Requirement).filter(
            Requirement.user_id == current_user_id,
            Requirement.is_deleted == show_trash
        )
        if show_trash:
            query = query.order_by(Requirement.deleted_at.desc())
        else:
            query = query.order_by(Requirement.create_time.desc())

        requirements = query.all()

        return jsonify({
            'requirements': [
                {
                    'id': r.id,
                    'title': r.title,
                    'status': r.status,
                    'create_time': r.create_time.isoformat() if r.create_time else None,
                    'is_deleted': r.is_deleted,
                    'deleted_at': r.deleted_at.isoformat() if r.deleted_at else None,
                }
                for r in requirements
            ]
        }), 200


@app.route('/api/requirements/<int:req_id>', methods=['GET'])
@jwt_required()
def get_requirement(req_id):
    """获取需求详情"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    with get_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        # 查询 trace 数据（已完成/失败需求加载时前端可展示执行详情面板）
        trace_data = None
        if requirement.status in ('finished', 'failed'):
            from models.models import AgentTrace
            trace_row = db.query(AgentTrace).filter(
                AgentTrace.requirement_id == req_id
            ).order_by(AgentTrace.id.desc()).first()
            if trace_row and trace_row.data:
                trace_data = trace_row.data

        # 查询 evaluator 评估结果（页面刷新后恢复评分展示）
        evaluator_data = None
        try:
            from harness.state.workspace import WorkspaceFS
            import json as _json
            ws = WorkspaceFS(requirement.user_id, req_id)
            if ws.exists(".task/evaluator/result.json"):
                raw = ws.read(".task/evaluator/result.json")
                if raw and raw.strip():
                    evaluator_data = _json.loads(raw)
        except Exception:
            pass  # evaluator 数据不存在或无法读取，不影响正常流程

        # 推导 plan_status: 从 requirement.status 和 dialogue_history 判断
        plan_status = None
        if requirement.status == 'planning':
            plan_status = 'needs_confirmation'
        elif requirement.status in ('processing', 'finished'):
            # 检查是否有 TL 生成的 plan（对话历史中包含 plan 字段的消息）
            for msg in (requirement.dialogue_history or []):
                if isinstance(msg, dict) and msg.get('plan'):
                    plan_status = 'confirmed'
                    break

        result = {
            'requirement': {
                'id': requirement.id,
                'title': requirement.title,
                'content': requirement.content,
                'status': requirement.status,
                'plan_status': plan_status,
                'dialogue_history': requirement.dialogue_history or [],
                'code_files': requirement.code_files or [],
                'create_time': requirement.create_time.isoformat() if requirement.create_time else None,
                'update_time': requirement.update_time.isoformat() if requirement.update_time else None,
                'is_deleted': requirement.is_deleted,
                'deleted_at': requirement.deleted_at.isoformat() if requirement.deleted_at else None,
                'error_message': requirement.error_message or None,
                'preview_token': make_preview_token(requirement.user_id, requirement.id),
            }
        }
        if trace_data:
            result['trace'] = trace_data
        if evaluator_data:
            result['evaluator'] = evaluator_data
        return jsonify(result), 200


@app.route('/api/requirements/<int:req_id>/trash', methods=['PUT'])
@jwt_required()
def trash_requirement(req_id):
    """软删除需求（移入回收站）"""
    from models import Requirement
    from datetime import datetime

    current_user_id = int(get_jwt_identity())
    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        requirement.is_deleted = True
        requirement.deleted_at = datetime.utcnow()

        logger.info(f"需求 {req_id} 已移入回收站")
        return jsonify({'message': '已移入回收站', 'requirement_id': req_id}), 200


@app.route('/api/requirements/<int:req_id>/restore', methods=['PUT'])
@jwt_required()
def restore_requirement(req_id):
    """从回收站恢复需求"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404
        if not requirement.is_deleted:
            return jsonify({'error': '该需求不在回收站中'}), 400

        requirement.is_deleted = False
        requirement.deleted_at = None

        logger.info(f"需求 {req_id} 已从回收站恢复")
        return jsonify({'message': '已恢复', 'requirement_id': req_id}), 200

@app.route('/api/requirements/<int:req_id>', methods=['DELETE'])
@jwt_required()
def delete_requirement(req_id):
    """彻底删除需求"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        db.delete(requirement)

        logger.info(f"需求 {req_id} 已彻底删除")
        return jsonify({'message': '已彻底删除', 'requirement_id': req_id}), 200
@app.route('/api/requirements/<int:req_id>/chat', methods=['POST'])
@rate_limit_chat
@jwt_required()
def chat_with_requirement(req_id):
    """与需求对话（基于工具调用循环修改代码）"""
    from models import Requirement
    from harness.state.workspace import WorkspaceFS
    from harness.state.versioning import GitVersioning
    from harness.tools.registry import create_tool_registry
    from harness.constraints.hooks import create_default_hook_manager
    from harness.instructions.compactor import ContextCompactor
    from harness.observability.sse_reporter import SSEReporter
    from harness.observability.tracer import Tracer
    from harness.observability.cost import CostTracker
    from harness.runtime import ToolCallLoop
    from harness.instructions.intent_router import IntentRouter, IntentType

    current_user_id = int(get_jwt_identity())
    data = request.get_json()

    if not data or not data.get('message'):
        return jsonify({'error': '消息内容不能为空'}), 400

    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        user_message = data.get('message', '').strip()
        # 澄清表单提交时随消息携带：{'questions': [...], 'answers': {...}}
        clarify = data.get('clarify')

        # ===== 意图路由：Chat 模式下区分"提问"和"修改指令" =====
        if '[用户补充说明]' not in user_message:
            chat_router = IntentRouter()
            chat_intent = chat_router.classify(
                user_message, is_chat=True,
                history=requirement.dialogue_history or [],
            )
            logger.info(f"Chat 意图分类: {chat_intent.intent.value} (req_id={req_id})")

            if chat_intent.intent == IntentType.QUICK:
                return _handle_chat_quick(
                    req_id, requirement, user_message,
                    chat_router, db
                )
            elif chat_intent.intent == IntentType.AMBIGUOUS:
                return _handle_chat_ambiguous(
                    req_id, requirement, user_message, db
                )
            elif chat_intent.intent == IntentType.SKILL:
                # SKILL 进入完整工作流（见 requirement_service 的同名处理）
                logger.info(
                    f"Chat 命中工作流技能: {chat_intent.skill_name} (req_id={req_id})"
                )
            # TASK / SKILL: 继续以下流程（意图分类统一由 IntentRouter 处理，
            # 不再重复调用 _is_vague_requirement 做二次检测）

        # 初始化 harness 层
        workspace = WorkspaceFS(current_user_id, req_id)
        workspace.init(requirement.code_files)
        git = GitVersioning(workspace)
        tools = create_tool_registry()
        hooks = create_default_hook_manager()
        sse = SSEReporter(sse_manager)
        tracer = Tracer(db_session=db)
        cost_tracker = CostTracker()

        # 保存用户消息到数据库（立即持久化，防止崩溃丢失）
        from sqlalchemy.orm.attributes import flag_modified
        dialogue_list = list(requirement.dialogue_history or [])
        user_entry = {
            'role': 'user', 'name': '用户',
            'content': user_message,
            'timestamp': get_current_timestamp()
        }
        if clarify:
            # 澄清表单提交：标记此前的表单消息为已提交，
            # 本条 user 消息持久化为已完成卡片（content 仅存答案摘要，LLM 上下文用完整 user_message）
            cl_answers = clarify.get('answers', {})
            cl_questions = clarify.get('questions', [])
            for i in range(len(dialogue_list) - 1, -1, -1):
                msg = dialogue_list[i]
                if isinstance(msg, dict) and msg.get('question_form') \
                        and not msg['question_form'].get('submitted'):
                    dialogue_list[i] = {
                        **msg,
                        'question_form': {
                            **msg['question_form'],
                            'submitted': True,
                            'answers': cl_answers,
                        }
                    }
                    break
            answer_text = '；'.join(f'{q}: {a}' for q, a in cl_answers.items() if a)
            user_entry['content'] = answer_text or '已确认'
            user_entry['question_form'] = {
                'questions': cl_questions,
                'submitted': True,
                'answers': cl_answers,
            }
        dialogue_list.append(user_entry)
        requirement.dialogue_history = dialogue_list
        flag_modified(requirement, 'dialogue_history')  # 确保 JSON 列变更被检测到
        requirement.status = 'processing'
        db.commit()

        # 构建代码上下文
        existing_files = workspace.list()
        file_list_text = "\n".join(f"- {f}" for f in existing_files) if existing_files else "(空)"

        # Chat 模式增量持久化回调
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

        # 进入 FrontendEngineer ReAct 循环（跳过 TeamLeader），使用修改专用 prompt
        tool_loop = ToolCallLoop(
            workspace=workspace, git=git, tools=tools,
            hooks=hooks, tracer=tracer, cost_tracker=cost_tracker,
            sse_reporter=sse,
            on_iteration=_persist_dialogue,
        )
        # Chat 模式下降低迭代上限（修改任务应该在 3-5 轮内完成）
        tool_loop.MAX_ITERATIONS = 4

        # 临时替换 system prompt 为修改模式
        def _chat_prompt(state):
            from harness.instructions.prompts import load_prompt_template
            return load_prompt_template("coding/chat_modify.md",
                user_message=user_message,
                file_list_text=file_list_text,
            )
        tool_loop._build_system_prompt = _chat_prompt

        state = {
            'requirement_id': req_id,
            'requirement_content': requirement.content,
            'user_id': current_user_id,
            'plan': {},
            'current_step': 'tool_coder_ready',
            'code_files': requirement.code_files or [],
            'dialogue_history': dialogue_list,
            'metadata': {'trace_id': '', 'is_chat': True},
            'tool_call_count': 0,
            'no_progress_count': 0,
            'last_file_list': existing_files,
            'hook_failures': {},
        }

        # ---- 并发管控：同需求互斥 + 全局并发上限（见模块头部说明）----
        with _chat_inflight_lock:
            if _chat_inflight.get(req_id):
                return jsonify({'error': '该需求正在对话处理中，请稍候再试'}), 409
            _chat_inflight[req_id] = True
        if not _chat_semaphore.acquire(timeout=_CHAT_ACQUIRE_TIMEOUT):
            with _chat_inflight_lock:
                _chat_inflight.pop(req_id, None)
            return jsonify({'error': '系统繁忙（并发对话已满），请稍后重试'}), 429

        try:
            # 修改前快照：闸门判定引入新缺陷时用于回滚（零成本，纯内存）
            pre_chat_files = {
                f['filename']: f['content'] for f in workspace.snapshot()
            }
            final_state = tool_loop.run(state)
        except Exception as e:
            # 关键：同步长跑异常时不能把 requirement 永久卡死 processing
            logger.error(f"Chat 执行异常 (req_id={req_id}): {e}", exc_info=True)
            requirement.status = 'failed'
            requirement.error_message = f"对话执行异常: {str(e)[:200]}"
            db.commit()
            return jsonify({'error': f'对话执行失败: {str(e)[:200]}'}), 500
        finally:
            # 无论成功/异常都要释放并发名额与互斥标记
            _chat_semaphore.release()
            with _chat_inflight_lock:
                _chat_inflight.pop(req_id, None)

        # ---- 轻量质量闸门：改完自动跑一次 smoke，引入缺陷则回滚本次修改 ----
        gate_note = None
        if final_state.get('current_step') == 'task_complete':
            defects, defect_summary = _chat_quality_gate(workspace)
            if defects:
                logger.warning(
                    f"Chat 质量闸门拦截 (req_id={req_id}): {len(defects)} 个确定性缺陷，"
                    f"回滚本次修改。{defect_summary[:200]}"
                )
                # 回滚到修改前快照：恢复旧文件、删除新增文件
                try:
                    current_files = set(workspace.list())
                    for fname, content in pre_chat_files.items():
                        workspace.write(fname, content)
                    for fname in current_files - set(pre_chat_files.keys()):
                        workspace.delete(fname)
                except Exception as rollback_err:
                    logger.error(f"Chat 质量闸门回滚失败 (req_id={req_id}): {rollback_err}")
                gate_note = (
                    "## ⛔ 本次修改已被质量闸门回滚\n\n"
                    f"修改后的代码在浏览器冒烟测试中发现了确定性缺陷：\n"
                    f"{defect_summary}\n\n"
                    "已自动恢复为修改前的版本。请换一种描述方式，"
                    "或把改动拆得更小再试。"
                )

        # 获取更新后的文件
        updated_files = workspace.snapshot()

        # 保存结果
        final_dialogue = final_state.get('dialogue_history', [])
        if gate_note:
            final_dialogue = list(final_dialogue) + [{
                'role': 'system', 'name': 'System',
                'content': gate_note,
                'type': 'quality_gate_rollback',
            }]
        requirement.dialogue_history = final_dialogue
        requirement.code_files = updated_files
        # 检查 current_step：只有真正完成才标记 finished
        current_step = final_state.get('current_step', '')
        if current_step in ('no_progress', 'max_iterations', 'llm_error', 'coding_error'):
            requirement.status = 'failed'
            # 构建诊断错误信息
            error_parts = [f"对话执行终止: {current_step}"]
            error_detail = final_state.get('error', '')
            if error_detail:
                error_parts.append(str(error_detail)[:200])
            tc_count = final_state.get('tool_call_count', 0)
            if tc_count:
                error_parts.append(f"(共 {tc_count} 轮迭代)")
            np_count = final_state.get('no_progress_count', 0)
            if np_count:
                error_parts.append(f"(连续 {np_count} 轮无进展)")
            requirement.error_message = " — ".join(error_parts)
        else:
            requirement.status = 'finished'
        db.commit()

        return jsonify({
            'message': 'success',
            'code_files': updated_files,
            'dialogue_history': final_dialogue,
            'updated_files': [f['filename'] for f in updated_files],
        }), 200

@app.route('/api/requirements/<int:req_id>/clarify', methods=['POST'])
@jwt_required()
def clarify_requirement(req_id):
    """处理需求澄清答案，补充到需求内容后重新执行工作流"""
    from models import Requirement

    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    answers = data.get('answers', {})

    if not answers:
        return jsonify({'error': '请提供澄清答案'}), 400

    with transactional_db() as db:
        req_record = db.query(Requirement).filter(
            Requirement.id == req_id, Requirement.user_id == user_id
        ).first()
        if not req_record:
            return jsonify({'error': '需求不存在'}), 404

        # 拼接答案到原需求
        is_skip = '_skip' in answers
        answer_text = '；'.join(f'{q}: {a}' for q, a in answers.items())
        original_content = req_record.content
        req_record.content = f'{original_content}\n\n[用户补充说明]\n{answer_text}'
        req_record.status = 'pending'  # 保持 pending，由 submitted 标记区分是否已提交

        # 重建 dialogue_history 并用 flag_modified 确保 SQLAlchemy 持久化嵌套修改
        from sqlalchemy.orm.attributes import flag_modified
        dialogue_list = list(req_record.dialogue_history or [])

        # 标记待提交的 question_form 消息为"已提交"（防止前端刷新后恢复成未提交状态）
        questions = []
        display_answers = answers
        for i, msg in enumerate(dialogue_list):
            if isinstance(msg, dict) and msg.get('question_form') \
                    and not msg['question_form'].get('submitted'):
                questions = msg['question_form'].get('questions', [])
                if is_skip:
                    display_answers = {
                        q.get('id'): '（使用默认方案）' for q in questions
                    }
                dialogue_list[i] = {
                    **msg,
                    'question_form': {
                        **msg['question_form'],
                        'submitted': True,
                        'answers': display_answers,
                    }
                }
                break

        # 已完成的补充信息作为结构化 user 消息持久化（前端按已提交卡片样式渲染）
        dialogue_list.append({
            'role': 'user',
            'name': '用户',
            'content': answer_text,
            'timestamp': get_current_timestamp(),
            'preserve': True,
            'question_form': {
                'questions': questions,
                'submitted': True,
                'answers': display_answers,
            },
        })
        req_record.dialogue_history = dialogue_list
        flag_modified(req_record, 'dialogue_history')

    # 事务已提交后再入队（避免竞态）；已有任务则不重复启动线程
    task_id = task_queue.submit(req_id, process_requirement_async, req_id)
    if task_id is None:
        logger.warning(f"需求 {req_id} 已有任务在处理，跳过重复提交")
    else:
        logger.info(f"需求 {req_id} 收到澄清答案，重新处理：{task_id}")

    return jsonify({'message': '澄清答案已提交', 'requirement_id': req_id})
@app.route('/api/requirements/<int:req_id>/confirm', methods=['POST'])
@jwt_required()
def confirm_plan(req_id):
    """用户确认 TL 生成的开发计划，继续执行编码流程"""
    from models import Requirement
    from services.requirement_service import RequirementService

    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    feedback = data.get('feedback', '').strip()

    # DB operations within transactional context
    with transactional_db() as db:
        req_record = db.query(Requirement).filter(
            Requirement.id == req_id, Requirement.user_id == user_id
        ).first()
        if not req_record:
            return jsonify({'error': '需求不存在'}), 404

        if req_record.status != 'planning':
            return jsonify({'error': f'需求状态为 {req_record.status}，无需确认'}), 400

        # 直接确认（无反馈）时，把确认动作作为结构化 user 消息持久化（前端按已确认卡片样式渲染）
        # 有反馈时会重新走 TL 分析，不落确认卡片（由 plan_feedback 消息记录）
        if not feedback:
            from sqlalchemy.orm.attributes import flag_modified
            plan_data = {}
            plan_insert_idx = -1
            dialogue_list = list(req_record.dialogue_history or [])
            for i, msg in enumerate(dialogue_list):
                if isinstance(msg, dict) and msg.get('plan'):
                    plan_data = msg['plan']
                    plan_insert_idx = i  # 确认卡片应在此 TL plan 消息之前
                    break
            confirm_card = {
                'role': 'user',
                'name': '用户',
                'content': '已确认开发计划，开始编码',
                'timestamp': get_current_timestamp(),
                'preserve': True,
                'plan_confirmed': {
                    'features': plan_data.get('features', []),
                    'tech_stack': plan_data.get('tech_stack', {}),
                    'file_structure': plan_data.get('file_structure', []),
                    'complexity': plan_data.get('complexity', 'S'),
                },
            }
            if plan_insert_idx >= 0:
                dialogue_list.insert(plan_insert_idx, confirm_card)
            else:
                dialogue_list.append(confirm_card)
            req_record.dialogue_history = dialogue_list
            flag_modified(req_record, 'dialogue_history')
            db.commit()

    # 提交到任务队列（后台异步执行编码流程，在 DB session 外部）
    from services.requirement_service import confirm_plan_async
    task_id = task_queue.submit(req_id, confirm_plan_async, req_id, feedback)
    if task_id is None:
        logger.warning(f"需求 {req_id} 已有任务在处理，跳过重复提交")
    else:
        logger.info(f"需求 {req_id} 确认 Plan，提交任务：{task_id}")

    return jsonify({'message': 'Plan 已确认，开始编码', 'requirement_id': req_id})


@app.route('/api/requirements/<int:req_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_requirement(req_id):
    """取消正在处理的需求"""
    from models import Requirement
    from services.requirement_service import RequirementService
    from services.task_queue import task_queue
    from services.sse_manager import sse_manager
    from utils.sse import SSEMessage

    user_id = int(get_jwt_identity())
    with transactional_db() as db:
        req_record = db.query(Requirement).filter(
            Requirement.id == req_id, Requirement.user_id == user_id
        ).first()
        if not req_record:
            return jsonify({'error': '需求不存在'}), 404

        if req_record.status not in ('processing', 'planning'):
            return jsonify({'error': f'需求状态为 {req_record.status}，无法取消'}), 400

        # 1. 取消任务队列中的任务
        task_queue.cancel_task(req_id)

        # 2. 发送取消信号（触发 ToolCallLoop 和工作流中断）
        RequirementService.signal_cancel(req_id)

        # 3. 更新需求状态并追加取消消息
        req_record.status = 'failed'
        dialogue_list = list(req_record.dialogue_history or [])
        dialogue_list.append({
            'role': 'system',
            'name': 'System',
            'content': '操作已被用户取消',
        })
        req_record.dialogue_history = dialogue_list
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(req_record, 'dialogue_history')

        # 4. SSE 推送取消通知
        cancel_msg = SSEMessage.format_event('cancelled', {
            'message': '操作已被用户取消',
            'requirement_id': req_id,
        })
        sse_manager.broadcast(str(req_id), cancel_msg)

        logger.info(f"需求 {req_id} 已被用户取消")
        return jsonify({'message': '操作已取消', 'requirement_id': req_id}), 200
@app.route('/api/requirements/<int:req_id>/code', methods=['POST'])
@jwt_required()
def save_code(req_id):
    """保存用户修改的代码"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    data = request.get_json()

    if not data or not data.get('filename'):
        return jsonify({'error': '文件名不能为空'}), 400

    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        from sqlalchemy.orm.attributes import flag_modified

        filename = data.get('filename', '').strip()
        content = data.get('content', '')

        # 重建新列表对象 + flag_modified：就地修改同一引用会导致
        # SQLAlchemy 变更检测 old is new，不产生 UPDATE，保存静默丢失
        code_files = list(requirement.code_files or [])
        file_found = False
        for i, file in enumerate(code_files):
            if file.get('filename') == filename:
                code_files[i]['content'] = content
                code_files[i]['status'] = 'modified'
                file_found = True
                break

        if not file_found:
            code_files.append({
                'filename': filename,
                'content': content,
                'status': 'modified'
            })

        requirement.code_files = code_files
        flag_modified(requirement, 'code_files')

        dialogue_history = list(requirement.dialogue_history or [])
        dialogue_history.append({
            'role': 'user',
            'name': '用户',
            'content': f'修改了文件 {filename}',
            'timestamp': get_current_timestamp(),
            'type': 'code_edit'
        })
        requirement.dialogue_history = dialogue_history
        flag_modified(requirement, 'dialogue_history')


        return jsonify({
            'message': '代码已保存',
            'filename': filename,
            'code_files': code_files
        }), 200

@app.route('/api/requirements/<int:req_id>/code/all', methods=['PUT'])
@jwt_required()
def save_all_code(req_id):
    """批量保存所有代码文件"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    data = request.get_json()

    if not data or not data.get('code_files'):
        return jsonify({'error': '代码文件列表不能为空'}), 400

    with transactional_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        new_code_files = []
        for file in data.get('code_files', []):
            if 'filename' in file and 'content' in file:
                new_code_files.append({
                    'filename': file['filename'],
                    'content': file['content'],
                    'status': 'modified'
                })

        requirement.code_files = new_code_files
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(requirement, 'code_files')

        return jsonify({
            'message': '所有代码已保存',
            'code_files': new_code_files
        }), 200

@app.route('/api/sse/<int:req_id>')
@jwt_required()
@limiter.exempt if limiter else (lambda f: f)
def sse_stream(req_id):
    """SSE 实时推送连接（JWT 鉴权 + 需求归属校验）"""
    from models import Requirement

    current_user_id = int(get_jwt_identity())
    with get_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id,
        ).first()
        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

    import queue

    client_queue = queue.Queue()
    client_id = str(req_id)

    # 添加到 SSE 管理器
    sse_manager.add_client(client_id, client_queue)
    logger.debug(f"SSE 客户端已连接：client_id={client_id}")

    def generate():
        try:
            # 发送初始连接消息
            yield SSEMessage.format_event('connected', {'requirement_id': req_id})

            # 持续监听队列中的消息
            while True:
                try:
                    message = client_queue.get(timeout=30)
                    if message is None:
                        break
                    yield message
                except queue.Empty:
                    yield ': heartbeat\n\n'
        except GeneratorExit:
            logger.debug(f"SSE 客户端断开：client_id={client_id}")
        finally:
            sse_manager.remove_client(client_id, client_queue)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


