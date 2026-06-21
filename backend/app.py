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
from harness.observability.logger import setup_logger, get_logger
from utils.rate_limiter import get_user_identity, rate_limit_handler, RATE_LIMITS

# ==================== 日志配置 ====================

logger = get_logger(__name__)
setup_logger('sqlalchemy.engine', level=30)  # WARNING 级别

# ==================== 应用初始化 ====================

app = Flask(__name__, static_folder='../frontend', static_url_path='')

# CORS 配置
CORS(app)

# JWT 配置
app.config['JWT_SECRET_KEY'] = JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = JWT_ACCESS_TOKEN_EXPIRES
app.config['JWT_TOKEN_LOCATION'] = ['headers', 'json']
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

@app.route('/')
def index():
    """首页 - 重定向到登录页"""
    return send_from_directory(app.static_folder, 'login.html')


@app.route('/login.html')
def login_page():
    """登录/注册页面"""
    return send_from_directory(app.static_folder, 'login.html')


@app.route('/index.html')
def home_page():
    """首页 - 需求输入页"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/detail.html')
def detail_page():
    """需求详情页"""
    return send_from_directory(app.static_folder, 'detail.html')


@app.route('/<path:filename>')
def static_files(filename):
    """静态文件服务"""
    return send_from_directory(app.static_folder, filename)


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
    """获取需求列表"""
    from models import Requirement, SessionLocal

    current_user_id = get_jwt_identity()
    db = SessionLocal()
    try:
        requirements = db.query(Requirement).filter(
            Requirement.user_id == current_user_id
        ).order_by(Requirement.create_time.desc()).all()

        return jsonify({
            'requirements': [
                {
                    'id': r.id,
                    'title': r.title,
                    'status': r.status,
                    'create_time': r.create_time.isoformat() if r.create_time else None
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

        return jsonify({
            'requirement': {
                'id': requirement.id,
                'title': requirement.title,
                'content': requirement.content,
                'status': requirement.status,
                'dialogue_history': requirement.dialogue_history or [],
                'code_files': requirement.code_files or [],
                'create_time': requirement.create_time.isoformat() if requirement.create_time else None,
                'update_time': requirement.update_time.isoformat() if requirement.update_time else None
            }
        }), 200
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
    from harness.environment.permissions import PermissionManager
    from harness.instructions.compactor import ContextCompactor
    from harness.observability.sse_reporter import SSEReporter
    from harness.observability.tracer import Tracer
    from harness.observability.cost import CostTracker
    from harness.runtime import ToolCallLoop

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

        # 初始化 harness 层
        workspace = WorkspaceFS(current_user_id, req_id)
        workspace.init(requirement.code_files)
        git = GitVersioning(workspace)
        tools = create_tool_registry()
        hooks = create_default_hook_manager()
        permissions = PermissionManager()
        permissions.grant(req_id, 'write')  # chat 中自动授权写入
        sse = SSEReporter(sse_manager)
        tracer = Tracer()
        cost_tracker = CostTracker()

        # 构建对话历史（保留已有对话 + 新消息）
        existing_dialogue = list(requirement.dialogue_history or [])
        existing_dialogue.append({
            'role': 'user', 'name': '用户',
            'content': user_message,
            'timestamp': get_current_timestamp()
        })

        # 构建代码上下文
        existing_files = workspace.list()
        file_list_text = "\n".join(f"- {f}" for f in existing_files) if existing_files else "(空)"

        # 进入 Coder ReAct 循环（跳过 Planner），使用修改专用 prompt
        tool_loop = ToolCallLoop(
            workspace=workspace, git=git, tools=tools,
            hooks=hooks, tracer=tracer, cost_tracker=cost_tracker,
            sse_reporter=sse, permission_manager=permissions,
        )
        # 临时替换 system prompt 为修改模式
        original_build = tool_loop._build_system_prompt
        def _chat_prompt(state):
            return f"""你是资深前端工程师。用户想要修改代码。

## 用户要求
{user_message}

## 当前文件列表
{file_list_text}

## 工作方式
1. 分析用户要求，判断需要修改哪个文件（通常只需改1个）
2. 用 read_file 读取该文件（只读1次）
3. 立即用 write_file 写入修改后的完整内容
4. 告诉我改了什么

## 规则
- 一次请求最多修改1个文件
- 读文件只读1次，读完立刻改
- 不要重复读同一个文件"""
        tool_loop._build_system_prompt = _chat_prompt

        state = {
            'requirement_id': req_id,
            'requirement_content': requirement.content,
            'user_id': current_user_id,
            'plan': {},
            'current_step': 'tool_coder_ready',
            'code_files': requirement.code_files or [],
            'dialogue_history': existing_dialogue,
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
        req_record.status = 'pending'
        req_record.dialogue_history = req_record.dialogue_history or []
        req_record.dialogue_history.append({
            'role': 'user',
            'name': '用户',
            'content': answer_text,
            'timestamp': get_current_timestamp()
        })
        db.commit()

        # 重新提交到任务队列
        task_queue.submit(req_id, process_requirement_async, req_id)
        logger.info(f"需求 {req_id} 收到澄清答案，重新处理")

        return jsonify({'message': '澄清答案已提交', 'requirement_id': req_id})
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'处理失败：{str(e)}'}), 500
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


@app.route('/api/requirements/<int:req_id>/permission', methods=['POST'])
@jwt_required()
def permission_approval(req_id):
    """接收用户权限审批决策"""
    user_id = get_jwt_identity()
    data = request.get_json() or {}
    decision = data.get('decision', 'deny')  # 'allow' | 'deny'

    from models import SessionLocal, Requirement
    db = SessionLocal()
    requirement = db.query(Requirement).filter_by(id=req_id, user_id=user_id).first()
    if not requirement:
        db.close()
        return jsonify({'error': '需求不存在'}), 404

    logger.info(f"用户 {user_id} 对需求 {req_id} 的权限请求做出了 {decision} 决定")

    # 允许写入权限
    if decision == 'allow':
        from harness.environment.permissions import PermissionManager
        pm = PermissionManager()
        pm.grant(req_id, 'write')

    db.close()
    return jsonify({'status': 'ok', 'decision': decision})


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


# ==================== 主程序入口 ====================

if __name__ == '__main__':
    logger.info("启动 Flask 应用，端口 5001")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
