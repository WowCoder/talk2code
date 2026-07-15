# -*- coding: utf-8 -*-
"""
Talk2Code - Flask 主应用
重构版本：使用模块化架构
"""

import os
import sys
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import JWT_SECRET_KEY, JWT_ACCESS_TOKEN_EXPIRES, LLM_API_KEY, LLM_MODEL, LLM_PROVIDER
from models import init_db
from services.sse_manager import sse_manager
from services.task_queue import task_queue
from services.requirement_service import process_requirement_async
from harness.observability.logger import setup_logger, get_logger, setup_logging
from harness.agent_names import TL_NAME
from utils.rate_limiter import get_user_identity, rate_limit_handler, RATE_LIMITS

# ==================== 日志配置 ====================

# 初始化根 logger 的文件处理器（app.log / agent.log / llm.log）
setup_logging(log_dir="logs", level=os.environ.get("LOG_LEVEL", "INFO"))
setup_logger('sqlalchemy.engine', level=30)  # WARNING 级别
logger = get_logger(__name__)
logger.info("日志系统已初始化")

# ==================== 应用初始化 ====================

app = Flask(__name__, static_folder=None)

# CORS 配置
CORS(app)

# JWT 配置
app.config['JWT_SECRET_KEY'] = JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = JWT_ACCESS_TOKEN_EXPIRES
app.config['JWT_TOKEN_LOCATION'] = ['headers']
app.config['JWT_HEADER_NAME'] = 'Authorization'
app.config['JWT_HEADER_TYPE'] = 'Bearer'

jwt = JWTManager(app)

# 限流配置 - 测试环境下禁用
DISABLE_RATE_LIMIT = os.environ.get('DISABLE_RATE_LIMIT', 'false').lower() == 'true'
if DISABLE_RATE_LIMIT:
    logger.info("测试环境：限流已禁用")
    limiter = None
else:
    limiter = Limiter(
        key_func=get_user_identity,
        app=app,
        default_limits=[RATE_LIMITS['default']],
        storage_uri="memory://",
        headers_enabled=True
    )

# 限流触发处理
@app.errorhandler(429)
def handle_rate_limit_exceeded(e):
    return rate_limit_handler(e)

# 测试环境下使用无操作装饰器
if limiter:
    rate_limit_auth = limiter.limit(RATE_LIMITS['auth'])
    rate_limit_requirement = limiter.limit(RATE_LIMITS['requirement_create'])
    rate_limit_chat = limiter.limit(RATE_LIMITS['chat'])
else:
    # No-op decorator for tests
    rate_limit_auth = rate_limit_requirement = rate_limit_chat = lambda f: f

# 初始化数据库
init_db()

# ==================== 生产环境安全检查 ====================

def check_production_security():
    """
    生产环境安全检查
    在应用启动时验证关键安全配置
    """
    from config import settings

    issues = []

    # 1. 检查 JWT 密钥
    if JWT_SECRET_KEY == 'talk2code-secret-key-change-in-production':
        issues.append("JWT_SECRET_KEY 使用默认值，生产环境必须修改！")
        logger.warning("⚠️  JWT_SECRET_KEY 使用默认值，存在安全风险")

    # 2. 检查 API Key
    if not LLM_API_KEY:
        issues.append("LLM_API_KEY 未配置，AI 功能将不可用")
        logger.warning("⚠️  LLM_API_KEY 未配置")

    # 3. 检查调试模式
    if settings.APP_DEBUG:
        issues.append("APP_DEBUG 已开启，生产环境应关闭")
        logger.warning("⚠️  调试模式已开启，生产环境应关闭")

    if issues:
        logger.warning("=" * 50)
        logger.warning("生产环境安全检查发现问题：")
        for issue in issues:
            logger.warning(f"  - {issue}")
        logger.warning("请修改 .env 文件或环境变量")
        logger.warning("=" * 50)

    return len(issues) == 0


# 执行安全检查
check_production_security()

logger.info("Talk2Code 应用启动")


# ==================== 应用关闭处理 ====================

import atexit

def cleanup():
    """清理资源"""
    logger.info("清理资源...")
    sse_manager.shutdown()
    task_queue.shutdown(wait=False)

atexit.register(cleanup)


# ==================== 前端页面路由 ====================

# Vue SPA 静态文件目录
SPA_DIST = os.path.join(os.path.dirname(__file__), '..', 'frontend-vue', 'dist')


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    """SPA fallback — 非 API 路径返回 index.html，由 Vue Router 处理"""
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    # 尝试返回静态文件（js/css/assets）
    if path:
        try:
            return send_from_directory(SPA_DIST, path)
        except Exception:
            pass
    # 否则返回 index.html，由 Vue Router 处理路由
    try:
        return send_from_directory(SPA_DIST, 'index.html')
    except Exception:
        return jsonify({'error': 'Frontend not built. Run: cd frontend-vue && npm run build'}), 503


# ==================== 用户认证 API ====================

@app.route('/api/register', methods=['POST'])
@rate_limit_auth
def register():
    """用户注册接口"""
    from models import User, SessionLocal
    from utils.security import hash_password

    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    if len(username) < 3:
        return jsonify({'error': '用户名至少 3 个字符'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码至少 6 个字符'}), 400

    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            return jsonify({'error': '用户名已存在'}), 409

        password_hash = hash_password(password)
        new_user = User(username=username, password_hash=password_hash)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        logger.info(f"用户注册成功：{username}")
        return jsonify({
            'message': '注册成功',
            'user': {'id': new_user.id, 'username': new_user.username}
        }), 201
    finally:
        db.close()


@app.route('/api/login', methods=['POST'])
@rate_limit_auth
def login():
    """用户登录接口"""
    from models import User, SessionLocal
    from utils.security import verify_password

    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return jsonify({'error': '用户名或密码错误'}), 401

        access_token = create_access_token(identity=str(user.id), expires_delta=JWT_ACCESS_TOKEN_EXPIRES)
        logger.info(f"用户登录成功：{username}")

        return jsonify({
            'message': '登录成功',
            'token': access_token,
            'user': {'id': user.id, 'username': user.username}
        }), 200
    finally:
        db.close()


@app.route('/api/user/info', methods=['GET'])
@jwt_required()
def get_user_info():
    """获取当前用户信息"""
    from models import User, SessionLocal

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == current_user_id).first()
        if not user:
            return jsonify({'error': '用户不存在'}), 404

        return jsonify({
            'user': {
                'id': user.id,
                'username': user.username,
                'create_time': user.create_time.isoformat() if user.create_time else None
            }
        }), 200
    finally:
        db.close()


# ==================== 需求管理 API ====================

@app.route('/api/requirements', methods=['POST'])
@rate_limit_requirement
@jwt_required()
def create_requirement():
    """创建需求接口"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get('content'):
        return jsonify({'error': '需求内容不能为空'}), 400

    content = data.get('content', '').strip()
    title = content[:100]

    db = SessionLocal()
    try:
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
        db.commit()
        db.refresh(requirement)

        logger.info(f"创建需求 {requirement.id}，准备提交到任务队列")

        # 提交到任务队列
        task_id = task_queue.submit(
            requirement.id,
            process_requirement_async,
            requirement.id
        )

        if task_id is None:
            # 任务已存在，直接启动线程处理
            import threading
            thread = threading.Thread(
                target=process_requirement_async,
                args=(requirement.id,),
                daemon=False
            )
            thread.start()
            logger.info(f"任务已存在，启动独立线程处理：{requirement.id}")

        return jsonify({
            'message': '需求已提交，正在处理',
            'requirement': {
                'id': requirement.id,
                'title': requirement.title,
                'status': requirement.status
            }
        }), 201
    finally:
        db.close()


@app.route('/api/requirements', methods=['GET'])
@jwt_required()
def list_requirements():
    """获取需求列表（支持 ?trash=true 查询回收站）"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    show_trash = request.args.get('trash', '').lower() == 'true'

    db = SessionLocal()
    try:
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
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>', methods=['GET'])
@jwt_required()
def get_requirement(req_id):
    """获取需求详情"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
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
            }
        }
        if trace_data:
            result['trace'] = trace_data
        if evaluator_data:
            result['evaluator'] = evaluator_data
        return jsonify(result), 200
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/trash', methods=['PUT'])
@jwt_required()
def trash_requirement(req_id):
    """软删除需求（移入回收站）"""
    from models import Requirement, SessionLocal
    from datetime import datetime

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        requirement.is_deleted = True
        requirement.deleted_at = datetime.utcnow()
        db.commit()

        logger.info(f"需求 {req_id} 已移入回收站")
        return jsonify({'message': '已移入回收站', 'requirement_id': req_id}), 200
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'操作失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/restore', methods=['PUT'])
@jwt_required()
def restore_requirement(req_id):
    """从回收站恢复需求"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
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
        db.commit()

        logger.info(f"需求 {req_id} 已从回收站恢复")
        return jsonify({'message': '已恢复', 'requirement_id': req_id}), 200
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'操作失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>', methods=['DELETE'])
@jwt_required()
def delete_requirement(req_id):
    """彻底删除需求"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        db.delete(requirement)
        db.commit()

        logger.info(f"需求 {req_id} 已彻底删除")
        return jsonify({'message': '已彻底删除', 'requirement_id': req_id}), 200
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'删除失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/chat', methods=['POST'])
@rate_limit_chat
@jwt_required()
def chat_with_requirement(req_id):
    """与需求对话（基于工具调用循环修改代码）"""
    from models import Requirement, SessionLocal
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

    current_user_id = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get('message'):
        return jsonify({'error': '消息内容不能为空'}), 400

    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        user_message = data.get('message', '').strip()

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
            # TASK: 继续以下流程（意图分类统一由 IntentRouter 处理，
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
        dialogue_list.append({
            'role': 'user', 'name': '用户',
            'content': user_message,
            'timestamp': get_current_timestamp()
        })
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

        final_state = tool_loop.run(state)

        # 获取更新后的文件
        updated_files = workspace.snapshot()

        # 保存结果
        final_dialogue = final_state.get('dialogue_history', [])
        requirement.dialogue_history = final_dialogue
        requirement.code_files = updated_files
        # 检查 current_step：只有真正完成才标记 finished
        current_step = final_state.get('current_step', '')
        if current_step in ('no_progress', 'max_iterations'):
            requirement.status = 'failed'
            requirement.error_message = f"对话执行终止: {current_step}"
        else:
            requirement.status = 'finished'
        db.commit()

        return jsonify({
            'message': 'success',
            'code_files': updated_files,
            'dialogue_history': final_dialogue,
            'updated_files': [f['filename'] for f in updated_files],
        }), 200

    except Exception as e:
        logger.error(f"处理对话失败：{e}", exc_info=True)
        db.rollback()
        # 单独更新状态为 failed（避免在过期对象上操作）
        try:
            from models import Requirement
            db.query(Requirement).filter(Requirement.id == req_id).update(
                {'status': 'failed', 'error_message': f"对话异常: {str(e)}"},
                synchronize_session=False
            )
            db.commit()
        except Exception:
            db.rollback()
        return jsonify({'error': f'处理失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/clarify', methods=['POST'])
@jwt_required()
def clarify_requirement(req_id):
    """处理需求澄清答案，补充到需求内容后重新执行工作流"""
    from models import Requirement, SessionLocal

    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    answers = data.get('answers', {})

    if not answers:
        return jsonify({'error': '请提供澄清答案'}), 400

    db = SessionLocal()
    try:
        req_record = db.query(Requirement).filter(
            Requirement.id == req_id, Requirement.user_id == user_id
        ).first()
        if not req_record:
            return jsonify({'error': '需求不存在'}), 404

        # 拼接答案到原需求
        answer_text = '；'.join(f'{q}: {a}' for q, a in answers.items())
        original_content = req_record.content
        req_record.content = f'{original_content}\n\n[用户补充说明]\n{answer_text}'
        req_record.status = 'pending'  # 保持 pending，由 submitted 标记区分是否已提交

        # 重建 dialogue_history 并用 flag_modified 确保 SQLAlchemy 持久化嵌套修改
        from sqlalchemy.orm.attributes import flag_modified
        dialogue_list = list(req_record.dialogue_history or [])

        # 标记已有的 question_form 消息为"已提交"（防止前端刷新后恢复成未提交状态）
        for i, msg in enumerate(dialogue_list):
            if isinstance(msg, dict) and msg.get('question_form'):
                dialogue_list[i] = {
                    **msg,
                    'question_form': {
                        **msg['question_form'],
                        'submitted': True,
                        'answers': answers,
                    }
                }
                break

        dialogue_list.append({
            'role': 'user',
            'name': '用户',
            'content': answer_text,
            'timestamp': get_current_timestamp(),
            'preserve': True,
        })
        req_record.dialogue_history = dialogue_list
        flag_modified(req_record, 'dialogue_history')
        db.commit()

        # 重新提交到任务队列；若已有任务在处理，启动独立线程兜底
        task_id = task_queue.submit(req_id, process_requirement_async, req_id)
        if task_id is None:
            import threading
            thread = threading.Thread(
                target=process_requirement_async,
                args=(req_id,),
                daemon=False
            )
            thread.start()
            logger.info(f"需求 {req_id} 已有任务在处理，启动独立线程")
        else:
            logger.info(f"需求 {req_id} 收到澄清答案，重新处理：{task_id}")

        return jsonify({'message': '澄清答案已提交', 'requirement_id': req_id})
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'处理失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/confirm', methods=['POST'])
@jwt_required()
def confirm_plan(req_id):
    """用户确认 TL 生成的开发计划，继续执行编码流程"""
    from models import Requirement, SessionLocal
    from services.requirement_service import RequirementService

    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    feedback = data.get('feedback', '').strip()

    db = SessionLocal()
    try:
        req_record = db.query(Requirement).filter(
            Requirement.id == req_id, Requirement.user_id == user_id
        ).first()
        if not req_record:
            return jsonify({'error': '需求不存在'}), 404

        if req_record.status != 'planning':
            return jsonify({'error': f'需求状态为 {req_record.status}，无需确认'}), 400

        db.close()

        # 提交到任务队列（后台异步执行编码流程）
        service = RequirementService()
        task_id = task_queue.submit(req_id, service.confirm_plan, req_id, feedback)
        if task_id is None:
            import threading
            thread = threading.Thread(
                target=service.confirm_plan,
                args=(req_id, feedback),
                daemon=False
            )
            thread.start()
            logger.info(f"需求 {req_id} 确认 Plan，启动独立线程执行编码流程")
        else:
            logger.info(f"需求 {req_id} 确认 Plan，提交任务：{task_id}")

        return jsonify({'message': 'Plan 已确认，开始编码', 'requirement_id': req_id})
    except Exception as e:
        return jsonify({'error': f'确认失败：{str(e)}'}), 500


@app.route('/api/requirements/<int:req_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_requirement(req_id):
    """取消正在处理的需求"""
    from models import Requirement, SessionLocal
    from services.requirement_service import RequirementService
    from services.task_queue import task_queue
    from services.sse_manager import sse_manager
    from utils.sse import SSEMessage

    user_id = int(get_jwt_identity())
    db = SessionLocal()
    try:
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
        db.commit()

        # 4. SSE 推送取消通知
        cancel_msg = SSEMessage.format_event('cancelled', {
            'message': '操作已被用户取消',
            'requirement_id': req_id,
        })
        sse_manager.broadcast(str(req_id), cancel_msg)

        logger.info(f"需求 {req_id} 已被用户取消")
        return jsonify({'message': '操作已取消', 'requirement_id': req_id}), 200
    except Exception as e:
        db.rollback()
        logger.error(f"取消需求 {req_id} 失败：{e}", exc_info=True)
        return jsonify({'error': f'取消失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/code', methods=['POST'])
@jwt_required()
def save_code(req_id):
    """保存用户修改的代码"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get('filename'):
        return jsonify({'error': '文件名不能为空'}), 400

    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()

        if not requirement:
            return jsonify({'error': '需求不存在'}), 404

        filename = data.get('filename', '').strip()
        content = data.get('content', '')

        code_files = requirement.code_files or []
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

        dialogue_history = requirement.dialogue_history or []
        dialogue_history.append({
            'role': 'user',
            'name': '用户',
            'content': f'修改了文件 {filename}',
            'timestamp': get_current_timestamp(),
            'type': 'code_edit'
        })
        requirement.dialogue_history = dialogue_history

        db.commit()

        return jsonify({
            'message': '代码已保存',
            'filename': filename,
            'code_files': code_files
        }), 200

    except Exception as e:
        logger.error(f"保存代码失败：{e}")
        db.rollback()
        return jsonify({'error': f'保存失败：{str(e)}'}), 500
    finally:
        db.close()


@app.route('/api/requirements/<int:req_id>/code/all', methods=['PUT'])
@jwt_required()
def save_all_code(req_id):
    """批量保存所有代码文件"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get('code_files'):
        return jsonify({'error': '代码文件列表不能为空'}), 400

    db = SessionLocal()
    try:
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
        db.commit()

        return jsonify({
            'message': '所有代码已保存',
            'code_files': new_code_files
        }), 200

    except Exception as e:
        logger.error(f"批量保存代码失败：{e}")
        db.rollback()
        return jsonify({'error': f'保存失败：{str(e)}'}), 500
    finally:
        db.close()


# ==================== SSE 实时推送 ====================

@app.route('/api/sse/<int:req_id>')
@limiter.exempt if limiter else (lambda f: f)
def sse_stream(req_id):
    """SSE 实时推送连接"""
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


# ==================== 健康检查 API ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    健康检查接口

    检查项目：
    - 数据库连接状态
    - LLM API 配置状态
    - 系统基本信息

    Returns:
        {
            'status': 'healthy' | 'degraded' | 'unhealthy',
            'checks': {
                'database': {'status': 'ok' | 'error', ...},
                'llm': {'status': 'ok' | 'not_configured' | 'error', ...},
            },
            'version': '2.1.0',
            'timestamp': '...'
        }
    """
    from models import SessionLocal
    import time

    checks = {}
    overall_status = 'healthy'

    # 1. 检查数据库连接
    db_status = 'ok'
    db_error = None
    try:
        db = SessionLocal()
        # 执行简单查询验证连接 (SQLAlchemy 2.x 需要 text())
        from sqlalchemy import text
        db.execute(text('SELECT 1'))
        db.close()
    except Exception as e:
        db_status = 'error'
        db_error = str(e)
        overall_status = 'unhealthy'

    checks['database'] = {
        'status': db_status,
        'type': 'sqlite',
        'error': db_error
    }

    # 2. 检查 LLM API 配置
    llm_status = 'ok'
    llm_error = None
    try:
        if not LLM_API_KEY:
            llm_status = 'not_configured'
            overall_status = 'degraded'
        else:
            llm_status = 'configured'
    except Exception as e:
        llm_status = 'error'
        llm_error = str(e)
        overall_status = 'degraded'

    checks['llm'] = {
        'status': llm_status,
        'provider': LLM_PROVIDER,
        'model': LLM_MODEL,
        'error': llm_error
    }

    # 3. 检查任务队列
    queue_status = 'ok'
    try:
        active_tasks = len(task_queue._tasks) if hasattr(task_queue, '_tasks') else 0
        checks['task_queue'] = {
            'status': queue_status,
            'active_tasks': active_tasks
        }
    except Exception as e:
        checks['task_queue'] = {
            'status': 'error',
            'error': str(e)
        }

    # 4. 检查 Harness 层状态
    try:
        from harness.tools.registry import create_tool_registry
        tools = create_tool_registry()
        checks['tool_registry'] = {
            'status': 'ok',
            'tools_count': len(tools.list_tools()),
        }
    except Exception as e:
        checks['tool_registry'] = {'status': 'error', 'error': str(e)}

    try:
        from harness.environment.sandbox import SandboxExecutor
        sandbox = SandboxExecutor()
        checks['sandbox'] = {'status': 'ok'}
    except Exception as e:
        checks['sandbox'] = {'status': 'error', 'error': str(e)}

    try:
        from harness.state.memory_store import MemoryStore
        memory = MemoryStore()
        checks['memory_store'] = {'status': 'ok'}
    except Exception as e:
        checks['memory_store'] = {'status': 'error', 'error': str(e)}

    return jsonify({
        'status': overall_status,
        'checks': checks,
        'version': '2.1.0',
        'timestamp': get_current_timestamp()
    }), 200 if overall_status != 'unhealthy' else 503


@app.route('/api/health/live', methods=['GET'])
def liveness_check():
    """
    Kubernetes liveness probe

    只检查应用是否存活，不检查依赖服务
    """
    return jsonify({'status': 'alive'}), 200


@app.route('/api/health/ready', methods=['GET'])
def readiness_check():
    """
    Kubernetes readiness probe

    检查应用是否准备好接收请求
    """
    from models import SessionLocal

    # 只检查数据库
    try:
        db = SessionLocal()
        from sqlalchemy import text
        db.execute(text('SELECT 1'))
        db.close()
        return jsonify({'status': 'ready'}), 200
    except Exception as e:
        return jsonify({'status': 'not_ready', 'error': str(e)}), 503


@app.route('/api/metrics', methods=['GET'])
def metrics():
    """Prometheus 监控指标端点"""
    import time
    metrics_data = {
        'talk2code_requests_total': 0,
        'talk2code_active_sessions': 0,
        'timestamp': time.time(),
        'uptime_seconds': time.time() - app.config.get('START_TIME', time.time()),
    }

    try:
        from models import SessionLocal
        db = SessionLocal()
        from sqlalchemy import text
        result = db.execute(text("SELECT COUNT(*) FROM requirements")).fetchone()
        metrics_data['talk2code_requests_total'] = result[0] if result else 0
        db.close()
    except Exception:
        pass

    # Prometheus text 格式
    lines = []
    for key, value in metrics_data.items():
        if isinstance(value, (int, float)):
            safe_key = key.replace('.', '_')
            lines.append(f"talk2code_{safe_key} {value}")
    return '\n'.join(lines) + '\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}


# ==================== 辅助函数 ====================

from utils.sse import SSEMessage
from utils.sse import get_current_timestamp


# ==================== Chat 意图路由辅助函数 ====================

def _handle_chat_quick(req_id, requirement, user_message, chat_router, db):
    """Chat 模式 QUICK 意图：直接回答用户关于代码的问题，不修改代码"""
    from sqlalchemy.orm.attributes import flag_modified
    from harness.observability.sse_reporter import SSEReporter

    # 构建代码上下文
    code_context = ""
    if requirement.code_files:
        lines = ["## 当前项目文件"]
        for f in requirement.code_files:
            fname = f.get('filename', 'unknown')
            content = f.get('content', '')
            line_count = content.count('\n') + 1 if content else 0
            preview = '\n'.join(content.split('\n')[:15]) if content else '(空)'
            lines.append(f"\n### {fname} ({line_count} 行)\n```\n{preview}\n```")
        code_context = '\n'.join(lines)

    sse_reporter = SSEReporter(sse_manager)

    answer = chat_router.handle_quick(
        requirement=user_message,
        history=requirement.dialogue_history or [],
        code_context=code_context,
        is_chat=True,
    )

    # 保存对话历史
    dialogue_list = list(requirement.dialogue_history or [])
    dialogue_list.append({
        'role': 'user', 'name': '用户',
        'content': user_message,
        'timestamp': get_current_timestamp(),
    })
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
    sse_reporter.dialogue(req_id, 'user', '用户', user_message)
    sse_reporter.dialogue(req_id, 'agent', TL_NAME, answer, 'completed')
    sse_reporter.complete(req_id)

    logger.info(f"Chat QUICK 回答完成 (req_id={req_id})")
    return jsonify({
        'message': 'success',
        'intent': 'quick',
        'answer': answer,
        'dialogue_history': dialogue_list,
    }), 200


def _handle_chat_ambiguous(req_id, requirement, user_message, db):
    """Chat 模式 AMBIGUOUS 意图：生成澄清问题"""
    from sqlalchemy.orm.attributes import flag_modified
    from harness.instructions.nodes import _generate_clarify_questions
    from llm.client import get_client as _get_llm_client

    try:
        client = _get_llm_client()
        questions = _generate_clarify_questions(client, user_message)
        if not questions:
            questions = [
                {"id": "q1", "type": "text", "label": "请更具体地描述你想要的修改效果"},
                {"id": "visual_style", "type": "radio",
                 "label": "修改后你偏好哪种视觉风格？",
                 "options": ["保持现有风格", "极简白", "暖柔风格", "暗黑科技", "活泼多彩", "无偏好"]},
            ]
    except Exception as e:
        logger.warning(f"Chat 澄清问题生成失败: {e}")
        questions = [
            {"id": "q1", "type": "text", "label": "请更具体地描述你想要的修改"},
        ]

    dialogue_list = list(requirement.dialogue_history or [])
    dialogue_list.append({
        'role': 'user', 'name': '用户',
        'content': user_message,
        'timestamp': get_current_timestamp(),
    })
    dialogue_list.append({
        'role': 'system', 'name': TL_NAME,
        'content': '修改意见不够明确，需要补充一些信息',
        'status': 'needs_clarification',
        'question_form': {'questions': questions},
    })
    requirement.dialogue_history = dialogue_list
    flag_modified(requirement, 'dialogue_history')
    requirement.status = 'finished'
    db.commit()

    # SSE 推送澄清表单
    msg = SSEMessage.format_event('question-form', {'questions': questions})
    sse_manager.broadcast(str(req_id), msg)

    logger.info(f"Chat 触发澄清 (AMBIGUOUS), req_id={req_id}")
    return jsonify({
        'needs_clarification': True,
        'question_form': {'questions': questions},
        'dialogue_history': dialogue_list,
    }), 200


# ==================== 预览文件服务 ====================

# MIME 类型映射，用于预览端点返回正确的 Content-Type
_PREVIEW_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.txt': 'text/plain; charset=utf-8',
}


def _get_mime_type(filepath: str) -> str:
    """根据文件扩展名返回 MIME 类型"""
    import os as _os
    _, ext = _os.path.splitext(filepath)
    return _PREVIEW_MIME_TYPES.get(
        ext.lower(), 'application/octet-stream'
    )


@app.route('/api/preview/<int:req_id>/<path:filepath>')
def preview_serve(req_id: int, filepath: str):
    """
    预览文件服务端点

    为前端 iframe 提供生成的代码文件，支持子目录路径（如 css/style.css）。
    相对路径引用（<link href="css/style.css">、<script src="js/game.js">）
    通过此端点自动解析。

    数据源优先级：
    1. Workspace 磁盘文件（实时生成中，SSE 推送后立即可用）
    2. 数据库 code_files（已完成的任务，作为持久化兜底）

    安全：拒绝路径穿越（.. / ~）。
    """
    # 安全校验：拒绝路径穿越
    if '..' in filepath or filepath.startswith('/') or filepath.startswith('~'):
        logger.warning(f"预览请求拒绝非法路径: req_id={req_id}, path={filepath}")
        return _preview_error_html('非法文件路径', 403)

    from models import Requirement, SessionLocal

    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter_by(id=req_id).first()
        if not requirement:
            return _preview_error_html('需求不存在', 404)

        # 优先从 workspace 读取（实时生成时更及时）
        from harness.state.workspace import WorkspaceFS
        user_id = requirement.user_id
        workspace = WorkspaceFS(user_id, req_id)
        if workspace.exists(filepath):
            content = workspace.read(filepath)
            if content.strip():  # 非空才返回
                mime = _get_mime_type(filepath)
                return Response(content, mimetype=mime)

        # 回退到数据库 code_files（已完成的任务）
        if requirement.code_files:
            for f in requirement.code_files:
                if f.get('filename') == filepath:
                    content = f.get('content', '')
                    if content.strip():  # 非空才返回
                        mime = _get_mime_type(filepath)
                        return Response(content, mimetype=mime)

        # 文件不存在（返回 HTML 而非 JSON，iframe 可友好展示）
        return _preview_error_html(f'文件尚未生成: {filepath}', 404)

    finally:
        db.close()


def _preview_error_html(message: str, status: int = 404):
    """生成预览错误页面（HTML 格式，iframe 友好）"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
  body {{ display:flex; align-items:center; justify-content:center; min-height:100vh;
         margin:0; font-family:system-ui,-apple-system,sans-serif;
         background:#f8f9fa; color:#6b7280; }}
  .box {{ text-align:center; padding:40px; }}
  .code {{ font-size:64px; font-weight:200; color:#d1d5db; margin-bottom:12px; }}
  .msg {{ font-size:15px; }}
</style></head>
<body><div class="box">
<div class="code">{status}</div><div class="msg">{message}</div>
</div></body></html>"""
    return Response(html, status=status, mimetype='text/html; charset=utf-8')


# ==================== 主程序入口 ====================

if __name__ == '__main__':
    logger.info("启动 Flask 应用，端口 5001")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
