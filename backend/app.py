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
from utils.logger import setup_logger, get_logger
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
    """与需求对话（支持 diff 代码修改）"""
    from models import Requirement, SessionLocal
    from llm.client import get_client
    from prompts import CODE_EDIT_SYSTEM_PROMPT, CODE_EDIT_USER_PROMPT
    from diff_utils import parse_diff, apply_diff, validate_diff

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
        dialogue_history = requirement.dialogue_history or []
        code_files = requirement.code_files or []

        # ==================== 阶段 2.1+2.3: 传递对话历史 + 版本感知 ====================
        # 计算修改次数（用于版本感知）
        modification_count = sum(1 for msg in dialogue_history if msg.get('type') == 'code_updated')

        # 构建对话上下文（最近 10 轮对话）
        recent_dialogues = dialogue_history[-10:] if len(dialogue_history) > 10 else dialogue_history
        context_parts = []
        for msg in recent_dialogues:
            if msg.get('role') == 'user':
                context_parts.append(f"用户：{msg.get('content', '')}")
            elif msg.get('role') in ('agent', 'assistant'):
                context_parts.append(f"AI: {msg.get('content', '')}")
            elif msg.get('type') == 'code_updated':
                context_parts.append(f"系统：{msg.get('content', '')}")
        dialogue_context = '\n'.join(context_parts)

        # ==================== 获取当前代码 ====================
        current_code = ""
        for file in code_files:
            current_code += f"\n\n// === {file.get('filename', 'unknown')} ===\n{file.get('content', '')}"

        # 保存用户消息
        dialogue_history.append({
            'role': 'user',
            'name': '用户',
            'content': user_message,
            'timestamp': get_current_timestamp()
        })

        # ==================== 阶段 2.2: 优化 prompt ====================
        user_prompt = CODE_EDIT_USER_PROMPT.format(
            requirement=requirement.content,
            current_code=current_code if current_code else '暂无代码',
            user_message=user_message,
            dialogue_context=dialogue_context if dialogue_context else '无历史对话',
            modification_count=modification_count
        )

        client = get_client()
        response = client.chat(user_prompt, CODE_EDIT_SYSTEM_PROMPT, use_memory=False, max_tokens=3500, timeout=60)
        ai_response = response.content

        if response.is_error:
            raise Exception(response.error or "LLM 响应错误")

        # ==================== 阶段 1.3: Diff 格式校验 ====================
        is_valid, diff_error = validate_diff(ai_response)
        if not is_valid:
            # LLM 没有返回有效的 diff，可能是纯文本回复
            logger.info(f"LLM 返回的内容不是有效的 diff: {diff_error}")
            # 直接返回 AI 回复，不修改代码
            dialogue_history.append({
                'role': 'agent',
                'name': 'AI 助手',
                'content': ai_response,
                'timestamp': get_current_timestamp()
            })
            requirement.dialogue_history = dialogue_history
            db.commit()

            return jsonify({
                'message': 'success',
                'dialogue_history': dialogue_history,
                'code_files': code_files,
                'ai_response': ai_response,
                'updated_files': [],
                'warning': 'AI 返回的是文本回复而非代码修改'
            }), 200

        # ==================== 阶段 1.2 + 1.4: Diff 应用 + 失败回滚 + 日志 ====================
        code_updated = False
        updated_files = []
        failed_files = []

        for diff_file in parse_diff(ai_response):
            original_file = None
            for f in code_files:
                if f.get('filename') == diff_file.filename:
                    original_file = f
                    break

            if original_file:
                try:
                    # apply_diff 现在返回 (new_content, success, error_message)
                    new_content, success, error_msg = apply_diff(original_file.get('content', ''), diff_file)

                    if success and new_content != original_file.get('content', ''):
                        original_file['content'] = new_content
                        original_file['status'] = 'modified'
                        code_updated = True
                        updated_files.append(diff_file.filename)
                        logger.info(f"[Chat] 文件 {diff_file.filename} 已通过 diff 更新")
                    else:
                        # Diff 应用失败
                        error_detail = f"文件 {diff_file.filename}: {error_msg}"
                        logger.warning(f"[Chat] Diff 应用失败：{error_detail}")
                        failed_files.append({'filename': diff_file.filename, 'error': error_msg})

                except Exception as e:
                    error_detail = f"文件 {diff_file.filename}: {str(e)}"
                    logger.error(f"[Chat] Diff 应用异常：{error_detail}", exc_info=True)
                    failed_files.append({'filename': diff_file.filename, 'error': str(e)})
            else:
                logger.warning(f"[Chat] Diff 引用的文件不存在：{diff_file.filename}")

        # ==================== 构建返回消息 ====================
        # 保存 AI 回复和修改结果
        if code_updated:
            dialogue_history.append({
                'role': 'system',
                'name': '系统',
                'content': f'已更新文件：{", ".join(updated_files)}',
                'timestamp': get_current_timestamp(),
                'type': 'code_updated'
            })

            # 如果有失败的文件，也记录下来
            if failed_files:
                failed_names = [f['filename'] for f in failed_files]
                dialogue_history.append({
                    'role': 'system',
                    'name': '系统',
                    'content': f'以下文件修改失败：{", ".join(failed_names)}',
                    'timestamp': get_current_timestamp(),
                    'type': 'code_update_failed'
                })
        else:
            dialogue_history.append({
                'role': 'agent',
                'name': 'AI 助手',
                'content': ai_response,
                'timestamp': get_current_timestamp()
            })

        requirement.dialogue_history = dialogue_history
        requirement.code_files = code_files
        db.commit()

        # ==================== 阶段 3.1: API 返回修改文件列表 ====================
        result = {
            'message': 'success',
            'dialogue_history': dialogue_history,
            'code_files': requirement.code_files,
            'ai_response': ai_response,
            'updated_files': updated_files
        }

        if failed_files:
            result['failed_files'] = failed_files

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"处理对话失败：{e}", exc_info=True)
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


# ==================== 辅助函数 ====================

from utils.sse import SSEMessage
from utils.time_utils import get_current_timestamp


# ==================== 主程序入口 ====================

if __name__ == '__main__':
    logger.info("启动 Flask 应用，端口 5001")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
