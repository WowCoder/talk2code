# -*- coding: utf-8 -*-
"""
Atoms.dev Vibe Coding Demo - Flask 主应用
实现用户认证、需求管理、AI 多智能体协同、SSE 实时推送
"""

import os
import json
import sys
import time
import threading
import queue
from datetime import datetime
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from models import init_db, get_db, User, Requirement, SessionLocal
from utils import hash_password, verify_password, SSEMessage, get_current_timestamp, chunk_content
from config import JWT_SECRET_KEY, JWT_ACCESS_TOKEN_EXPIRES, DEFAULT_SPEED, CODE_GEN_SPEED
from llm_client import get_client, chat_with_llm
from prompts import (
    RESEARCHER_SYSTEM_PROMPT, RESEARCHER_USER_PROMPT,
    PRODUCT_MANAGER_SYSTEM_PROMPT, PRODUCT_MANAGER_USER_PROMPT,
    ARCHITECT_SYSTEM_PROMPT, ARCHITECT_USER_PROMPT,
    ENGINEER_SYSTEM_PROMPT, ENGINEER_USER_PROMPT,
    FALLBACK_RESPONSES
)

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

# 存储活跃的 SSE 连接（使用 Queue 实现消息推送）
sse_clients = {}  # {client_id: [queue.Queue, ...]}

# 初始化数据库
init_db()


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


# ==================== 用户系统 API ====================

@app.route('/api/register', methods=['POST'])
def register():
    """
    用户注册接口
    POST /api/register
    Body: {"username": "test", "password": "123456"}
    """
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
        # 检查用户名是否已存在
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            return jsonify({'error': '用户名已存在'}), 409

        # 创建新用户
        password_hash = hash_password(password)
        new_user = User(username=username, password_hash=password_hash)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return jsonify({
            'message': '注册成功',
            'user': {
                'id': new_user.id,
                'username': new_user.username
            }
        }), 201
    finally:
        db.close()


@app.route('/api/login', methods=['POST'])
def login():
    """
    用户登录接口
    POST /api/login
    Body: {"username": "test", "password": "123456"}
    """
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

        # 生成 JWT Token
        access_token = create_access_token(
            identity=str(user.id),
            expires_delta=JWT_ACCESS_TOKEN_EXPIRES
        )

        return jsonify({
            'message': '登录成功',
            'token': access_token,
            'user': {
                'id': user.id,
                'username': user.username
            }
        }), 200
    finally:
        db.close()


@app.route('/api/user/info', methods=['GET'])
@jwt_required()
def get_user_info():
    """
    获取当前用户信息
    GET /api/user/info
    Headers: Authorization: Bearer <token>
    """
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
@jwt_required()
def create_requirement():
    """
    创建需求接口
    POST /api/requirements
    Headers: Authorization: Bearer <token>
    Body: {"content": "开发一个待办清单 App"}
    """
    current_user_id = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get('content'):
        return jsonify({'error': '需求内容不能为空'}), 400

    content = data.get('content', '').strip()
    title = content[:100]  # 取前 100 字符作为标题

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

        print(f"[信息] 创建需求 {requirement.id}，准备启动智能体处理线程...")
        sys.stdout.flush()

        # 启动 AI 智能体处理线程（不使用 daemon，让线程能完整执行）
        thread = threading.Thread(
            target=process_requirement_with_agents,
            args=(requirement.id,),
            daemon=False  # 设置为非守护线程，确保能完整执行
        )
        thread.start()
        print(f"[信息] 智能体处理线程已启动：{thread.name}")
        sys.stdout.flush()

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


@app.route('/api/requirements/<int:req_id>', methods=['GET'])
@jwt_required()
def get_requirement(req_id):
    """
    获取需求详情
    GET /api/requirements/<req_id>
    """
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


@app.route('/api/requirements', methods=['GET'])
@jwt_required()
def list_requirements():
    """
    获取用户需求列表
    GET /api/requirements
    """
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


@app.route('/api/requirements/<int:req_id>/chat', methods=['POST'])
@jwt_required()
def chat_with_requirement(req_id):
    """
    与需求对话（持续对话）
    POST /api/requirements/<req_id>/chat
    Headers: Authorization: Bearer <token>
    Body: {"message": "继续优化代码"}
    """
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
        llm_instance_id = f"requirement_{req_id}"
        llm = get_llm(llm_instance_id)

        # 从数据库加载对话历史
        dialogue_history = requirement.dialogue_history or []
        llm.load_memory_from_db(dialogue_history)

        # 保存用户消息到对话历史
        dialogue_history.append({
            'role': 'user',
            'name': '用户',
            'content': user_message,
            'timestamp': get_current_timestamp()
        })

        # 使用 LLM 生成回复（流式）
        system_prompt = """你是一个专业的 AI 编程助手，帮助用户完善代码、解答疑问。
保持简洁专业的回答，如果涉及代码修改，请提供完整的代码片段。"""

        # 构建对话上下文
        user_prompt = f"""用户需求：{requirement.content}

用户问题：{user_message}

请帮助解答用户的问题。"""

        # 调用 LLM 获取回复
        ai_response = llm.chat(user_prompt, system_prompt, use_memory=True)

        # 保存 AI 回复到对话历史
        dialogue_history.append({
            'role': 'agent',
            'name': 'AI 助手',
            'content': ai_response,
            'timestamp': get_current_timestamp()
        })

        # 更新对话历史到数据库
        requirement.dialogue_history = dialogue_history
        db.commit()

        return jsonify({
            'message': 'success',
            'dialogue_history': dialogue_history
        }), 200

    except Exception as e:
        print(f"[错误] 处理对话失败：{e}")
        db.rollback()
        return jsonify({'error': f'处理失败：{str(e)}'}), 500
    finally:
        db.close()


# ==================== SSE 实时推送 ====================

@app.route('/api/sse/<int:req_id>')
def sse_stream(req_id):
    """
    SSE 实时推送连接
    GET /api/sse/<req_id>
    """
    # 为每个客户端创建独立的消息队列
    client_queue = queue.Queue()
    client_id = f"{req_id}"

    if client_id not in sse_clients:
        sse_clients[client_id] = []
    sse_clients[client_id].append(client_queue)

    def generate():
        try:
            # 发送初始连接消息
            yield SSEMessage.format_event('connected', {'requirement_id': req_id})

            # 持续监听队列中的消息
            while True:
                try:
                    # 非阻塞方式获取消息
                    message = client_queue.get(timeout=30)  # 30 秒超时
                    if message is None:  # None 表示结束连接
                        break
                    yield message
                except queue.Empty:
                    # 超时，发送心跳保持连接
                    yield ': heartbeat\n\n'
        except GeneratorExit:
            pass  # 客户端断开连接
        finally:
            # 清理客户端
            if client_id in sse_clients and client_queue in sse_clients[client_id]:
                sse_clients[client_id].remove(client_queue)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


# ==================== AI 多智能体协同处理 ====================

def process_requirement_with_agents(requirement_id: int):
    """
    AI 多智能体协同处理需求
    按顺序执行：研究员 → 产品经理 → 架构师 → 工程师
    简单串行调用，不使用复杂的 Memory 机制
    """
    db = SessionLocal()
    try:
        requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            print(f"[错误] 需求 {requirement_id} 不存在")
            return

        # 如果需求已经完成或正在处理中，跳过
        if requirement.status in ['finished', 'processing']:
            print(f"[信息] 需求 {requirement_id} 状态为 {requirement.status}，跳过处理")
            return

        # 更新状态为处理中
        requirement.status = 'processing'
        db.commit()

        # 智能体按顺序执行，前面的输出传递给后面的智能体作为上下文
        agent_outputs = []  # 收集所有智能体的输出
        client_id = f"{requirement_id}"

        # 发送开始处理通知
        send_sse_progress(client_id, '研究员', 25)

        # 1. 研究员智能体
        print("[信息] 研究员智能体开始工作...")
        sys.stdout.flush()
        output = run_researcher(requirement.content)
        agent_outputs.append({'name': '研究员', 'output': output})
        save_agent_result(db, requirement, '研究员', output)
        send_sse_message(client_id, '研究员', output, 'agent')

        # 发送进度更新
        send_sse_progress(client_id, '产品经理', 50)

        # 2. 产品经理智能体
        print("[信息] 产品经理智能体开始工作...")
        sys.stdout.flush()
        output = run_product_manager(requirement.content)
        agent_outputs.append({'name': '产品经理', 'output': output})
        save_agent_result(db, requirement, '产品经理', output)
        send_sse_message(client_id, '产品经理', output, 'agent')

        # 发送进度更新
        send_sse_progress(client_id, '架构师', 75)

        # 3. 架构师智能体
        print("[信息] 架构师智能体开始工作...")
        sys.stdout.flush()
        output = run_architect(requirement.content)
        agent_outputs.append({'name': '架构师', 'output': output})
        save_agent_result(db, requirement, '架构师', output)
        send_sse_message(client_id, '架构师', output, 'agent')

        # 发送进度更新
        send_sse_progress(client_id, '工程师', 100)

        # 4. 工程师智能体 - 传入前面所有智能体的输出作为上下文
        print("[信息] 工程师智能体开始工作...")
        sys.stdout.flush()
        # 构建清晰的上下文格式
        context = "\n\n---\n\n".join([f"【{a['name']}输出】\n{a['output']}" for a in agent_outputs])
        code_output = run_engineer(requirement.content, context)

        # 解析并保存代码文件
        try:
            code_files = json.loads(code_output)
            if isinstance(code_files, list):
                requirement.code_files = code_files
                print(f"[信息] 保存了 {len(code_files)} 个代码文件")
                # 发送代码 SSE 消息
                for file_data in code_files:
                    filename = file_data.get('filename', 'unknown.txt')
                    content = file_data.get('content', '')
                    send_sse_message(client_id, filename, content, 'code')
        except Exception as e:
            print(f"[错误] 解析代码文件失败：{e}")
            requirement.code_files = generate_fallback_code(requirement.content)

        # 更新状态为完成
        requirement.status = 'finished'
        db.commit()
        print(f"[信息] 需求 {requirement_id} 处理完成")

        # 发送完成通知
        send_sse_message(client_id, 'complete', str(requirement_id), 'complete')

    except Exception as e:
        print(f"[错误] 处理需求时发生异常：{e}")
        try:
            requirement.status = 'failed'
            db.commit()
        except:
            pass
    finally:
        db.close()


def save_agent_result(db, requirement, agent_name, output):
    """保存智能体输出到对话历史"""
    dialogue = requirement.dialogue_history or []
    dialogue.append({
        'role': 'agent',
        'name': agent_name,
        'content': output,
        'timestamp': get_current_timestamp(),
        'status': 'completed'
    })
    requirement.dialogue_history = dialogue
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(requirement, 'dialogue_history')
    db.commit()


def send_sse_message(client_id, name, content, msg_type):
    """发送 SSE 消息"""
    if client_id in sse_clients and sse_clients[client_id]:
        for client_queue in sse_clients[client_id]:
            try:
                if msg_type == 'agent':
                    client_queue.put_nowait(SSEMessage.dialogue_message('agent', name, content, get_current_timestamp()))
                elif msg_type == 'code':
                    client_queue.put_nowait(SSEMessage.code_message(name, content, 0, True))
                elif msg_type == 'complete':
                    client_queue.put_nowait(SSEMessage.complete_message(content))
            except queue.Full:
                pass


def send_sse_progress(client_id, agent_name, progress):
    """发送 SSE 进度更新消息"""
    if client_id in sse_clients and sse_clients[client_id]:
        for client_queue in sse_clients[client_id]:
            try:
                client_queue.put_nowait(SSEMessage.progress_message(agent_name, progress, 'processing'))
            except queue.Full:
                pass


def generate_fallback_code(requirement: str) -> list:
    """
    生成备用代码（当 LLM 失败时使用）
    """
    requirement_lower = requirement.lower()

    if '待办' in requirement or 'todo' in requirement_lower or '清单' in requirement:
        return generate_todo_app_code()
    elif '计算器' in requirement or '计算' in requirement:
        return generate_calculator_app_code()
    elif '笔记' in requirement or '备忘录' in requirement:
        return generate_note_app_code()
    else:
        return generate_generic_app_code(requirement)


# ==================== AI 智能体定义 ====================
# 简化版本：直接使用 chat_with_llm 调用，不使用复杂的 Memory 机制


def run_researcher(requirement: str) -> str:
    """
    研究员智能体：分析需求，生成市场适配性摘要
    """
    user_prompt = RESEARCHER_USER_PROMPT.format(requirement=requirement)
    try:
        response = chat_with_llm(user_prompt, RESEARCHER_SYSTEM_PROMPT)
        return f"【市场与需求分析】\n\n{response}"
    except Exception as e:
        print(f"研究员 LLM 调用失败：{e}")
        return f"【市场与需求分析】\n\n{FALLBACK_RESPONSES['researcher'].format(requirement=requirement)}"


def run_product_manager(requirement: str) -> str:
    """
    产品经理智能体：拆解需求，生成功能清单
    """
    user_prompt = PRODUCT_MANAGER_USER_PROMPT.format(requirement=requirement)
    try:
        response = chat_with_llm(user_prompt, PRODUCT_MANAGER_SYSTEM_PROMPT)
        return f"【产品功能规划】\n\n{response}"
    except Exception as e:
        print(f"产品经理 LLM 调用失败：{e}")
        return f"【产品功能规划】\n\n{FALLBACK_RESPONSES['product_manager'].format(requirement=requirement)}"


def run_architect(requirement: str) -> str:
    """
    架构师智能体：设计技术方案
    """
    user_prompt = ARCHITECT_USER_PROMPT.format(requirement=requirement)
    try:
        response = chat_with_llm(user_prompt, ARCHITECT_SYSTEM_PROMPT)
        return f"【技术架构设计】\n\n{response}"
    except Exception as e:
        print(f"架构师 LLM 调用失败：{e}")
        return f"【技术架构设计】\n\n{FALLBACK_RESPONSES['architect'].format(requirement=requirement)}"


def run_engineer(requirement: str, context: str = None) -> str:
    """
    工程师智能体：生成代码
    接收前面智能体的输出作为上下文参考
    """
    user_prompt = ENGINEER_USER_PROMPT.format(
        requirement=requirement,
        context=context if context else "请根据需求生成代码。"
    )

    try:
        # 工程师需要生成代码，使用更大的 max_tokens
        response = chat_with_llm(user_prompt, ENGINEER_SYSTEM_PROMPT, max_tokens=8000)
        print(f"[调试] LLM 响应长度：{len(response)}")
        sys.stdout.flush()

        # 清理可能的 markdown 标记
        response = response.strip()
        if response.startswith('```json'):
            response = response[7:]
        if response.endswith('```'):
            response = response[:-3]
        response = response.strip()

        # 验证 JSON 格式
        files = json.loads(response)
        if isinstance(files, list):
            print(f"[调试] JSON 解析成功，返回 {len(files)} 个文件")
            sys.stdout.flush()
            return json.dumps(files, ensure_ascii=False)
        else:
            print(f"[调试] JSON 解析失败：返回类型不是列表")
            sys.stdout.flush()

    except json.JSONDecodeError as e:
        print(f"[错误] 工程师 JSON 解析失败：{e}")
        sys.stdout.flush()
    except Exception as e:
        print(f"工程师 LLM 调用失败：{e}")
        sys.stdout.flush()

    # LLM 失败时使用预设模板
    print(f"[调试] 使用 Fallback 代码模板")
    sys.stdout.flush()
    return json.dumps(generate_fallback_code(requirement), ensure_ascii=False)


# 保留原有函数（向后兼容）
def agent_researcher_with_memory(requirement: str, llm=None, agent_outputs=None) -> str:
    """保留的兼容接口"""
    return run_researcher(requirement)


def agent_product_manager_with_memory(requirement: str, llm=None, agent_outputs=None) -> str:
    """保留的兼容接口"""
    return run_product_manager(requirement)


def agent_architect_with_memory(requirement: str, llm=None, agent_outputs=None) -> str:
    """保留的兼容接口"""
    return run_architect(requirement)


def agent_engineer_with_memory(requirement: str, llm=None, agent_outputs=None) -> str:
    """保留的兼容接口"""
    context = ""
    if agent_outputs:
        context = "\n\n".join([f"{a['name']}输出:\n{a['output']}" for a in agent_outputs])
    return run_engineer(requirement, context)


def generate_todo_app_code() -> list:
    """生成待办清单应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>待办清单 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-2xl">
        <!-- 标题 -->
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">📝 待办清单</h1>

        <!-- 输入区域 -->
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <div class="flex gap-3">
                <input
                    type="text"
                    id="todoInput"
                    class="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="添加新的待办事项..."
                />
                <button
                    id="addBtn"
                    class="px-6 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors font-medium"
                >
                    添加
                </button>
            </div>
        </div>

        <!-- 筛选器 -->
        <div class="flex gap-2 mb-4">
            <button class="filter-btn active" data-filter="all">全部</button>
            <button class="filter-btn" data-filter="active">未完成</button>
            <button class="filter-btn" data-filter="completed">已完成</button>
        </div>

        <!-- 待办列表 -->
        <div class="bg-white rounded-lg shadow-md">
            <ul id="todoList" class="divide-y divide-gray-200"></ul>
            <div id="emptyState" class="text-center py-12 text-gray-500">
                <p>暂无待办事项，添加一个吧！</p>
            </div>
        </div>

        <!-- 统计信息 -->
        <div class="text-center mt-4 text-gray-500 text-sm">
            <span id="totalCount">0</span> 项待办 ·
            <span id="completedCount">0</span> 项已完成
        </div>
    </div>

    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 52
        },
        {
            'filename': 'style.css',
            'content': '''/* 待办清单 App 样式 */

/* 筛选器按钮样式 */
.filter-btn {
    @apply px-4 py-2 rounded-lg border transition-colors text-sm font-medium;
}

.filter-btn.active {
    @apply bg-blue-500 text-white border-blue-500;
}

.filter-btn:not(.active) {
    @apply bg-white text-gray-600 border-gray-300 hover:bg-gray-50;
}

/* 待办事项样式 */
.todo-item {
    @apply flex items-center gap-3 p-4 hover:bg-gray-50 transition-colors;
}

.todo-item.completed .todo-text {
    @apply line-through text-gray-400;
}

.todo-checkbox {
    @apply w-5 h-5 rounded border-gray-300 text-blue-500 focus:ring-blue-500 cursor-pointer;
}

.todo-text {
    @apply flex-1 text-gray-800;
}

.delete-btn {
    @apply px-3 py-1 text-red-500 hover:bg-red-50 rounded transition-colors text-sm;
}

/* 空状态 */
#emptyState {
    display: none;
}

#todoList:empty + #emptyState {
    display: block;
}

/* 动画效果 */
.todo-item {
    animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
    from {
        opacity: 0;
        transform: translateY(-10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}''',
            'status': 'completed',
            'total_lines': 45
        },
        {
            'filename': 'script.js',
            'content': '''// 待办清单 App - 核心逻辑

// 数据结构
let todos = [];
let currentFilter = 'all';

// DOM 元素
const todoInput = document.getElementById('todoInput');
const addBtn = document.getElementById('addBtn');
const todoList = document.getElementById('todoList');
const totalCountEl = document.getElementById('totalCount');
const completedCountEl = document.getElementById('completedCount');
const emptyState = document.getElementById('emptyState');
const filterBtns = document.querySelectorAll('.filter-btn');

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadTodos();
    renderTodos();
    setupEventListeners();
});

// 设置事件监听
function setupEventListeners() {
    // 添加待办
    addBtn.addEventListener('click', addTodo);
    todoInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') addTodo();
    });

    // 筛选器
    filterBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            filterBtns.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentFilter = e.target.dataset.filter;
            renderTodos();
        });
    });
}

// 添加待办事项
function addTodo() {
    const text = todoInput.value.trim();
    if (!text) return;

    const todo = {
        id: Date.now(),
        text: text,
        completed: false,
        createdAt: new Date().toISOString()
    };

    todos.push(todo);
    saveTodos();
    renderTodos();

    todoInput.value = '';
    todoInput.focus();
}

// 切换完成状态
function toggleTodo(id) {
    const todo = todos.find(t => t.id === id);
    if (todo) {
        todo.completed = !todo.completed;
        saveTodos();
        renderTodos();
    }
}

// 删除待办
function deleteTodo(id) {
    todos = todos.filter(t => t.id !== id);
    saveTodos();
    renderTodos();
}

// 渲染待办列表
function renderTodos() {
    // 筛选
    let filtered = todos;
    if (currentFilter === 'active') {
        filtered = todos.filter(t => !t.completed);
    } else if (currentFilter === 'completed') {
        filtered = todos.filter(t => t.completed);
    }

    // 渲染
    todoList.innerHTML = filtered.map(todo => `
        <li class="todo-item ${todo.completed ? 'completed' : ''}" data-id="${todo.id}">
            <input
                type="checkbox"
                class="todo-checkbox"
                ${todo.completed ? 'checked' : ''}
                onchange="toggleTodo(${todo.id})"
            />
            <span class="todo-text">${escapeHtml(todo.text)}</span>
            <button class="delete-btn" onclick="deleteTodo(${todo.id})">删除</button>
        </li>
    `).join('');

    // 更新统计
    updateStats();
}

// 更新统计
function updateStats() {
    const total = todos.length;
    const completed = todos.filter(t => t.completed).length;
    totalCountEl.textContent = total;
    completedCountEl.textContent = completed;

    // 空状态
    emptyState.style.display = total === 0 ? 'block' : 'none';
}

// 保存到本地存储
function saveTodos() {
    localStorage.setItem('todos', JSON.stringify(todos));
}

// 从本地存储加载
function loadTodos() {
    const saved = localStorage.getItem('todos');
    if (saved) {
        todos = JSON.parse(saved);
    }
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}''',
            'status': 'completed',
            'total_lines': 118
        }
    ]


def generate_calculator_app_code() -> list:
    """生成计算器应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>计算器 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gradient-to-br from-gray-900 to-gray-800 min-h-screen flex items-center justify-center">
    <div class="calculator">
        <!-- 显示屏 -->
        <div class="display">
            <div id="previousOperand" class="previous-operand"></div>
            <div id="currentOperand" class="current-operand">0</div>
        </div>

        <!-- 按钮区 -->
        <div class="buttons">
            <button class="btn btn-span-2 bg-gray-300 hover:bg-gray-400 text-gray-800" onclick="calculator.clear()">AC</button>
            <button class="btn bg-gray-300 hover:bg-gray-400 text-gray-800" onclick="calculator.delete()">DEL</button>
            <button class="btn bg-orange-500 hover:bg-orange-600" onclick="calculator.chooseOperation('÷')">÷</button>

            <button class="btn" onclick="calculator.appendNumber('7')">7</button>
            <button class="btn" onclick="calculator.appendNumber('8')">8</button>
            <button class="btn" onclick="calculator.appendNumber('9')">9</button>
            <button class="btn bg-orange-500 hover:bg-orange-600" onclick="calculator.chooseOperation('×')">×</button>

            <button class="btn" onclick="calculator.appendNumber('4')">4</button>
            <button class="btn" onclick="calculator.appendNumber('5')">5</button>
            <button class="btn" onclick="calculator.appendNumber('6')">6</button>
            <button class="btn bg-orange-500 hover:bg-orange-600" onclick="calculator.chooseOperation('-')">-</button>

            <button class="btn" onclick="calculator.appendNumber('1')">1</button>
            <button class="btn" onclick="calculator.appendNumber('2')">2</button>
            <button class="btn" onclick="calculator.appendNumber('3')">3</button>
            <button class="btn bg-orange-500 hover:bg-orange-600" onclick="calculator.chooseOperation('+')">+</button>

            <button class="btn" onclick="calculator.appendNumber('0')">0</button>
            <button class="btn" onclick="calculator.appendNumber('.')">.</button>
            <button class="btn btn-span-2 bg-orange-500 hover:bg-orange-600" onclick="calculator.compute()">=</button>
        </div>
    </div>

    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 45
        },
        {
            'filename': 'style.css',
            'content': '''/* 计算器 App 样式 */

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.calculator {
    width: 100%;
    max-width: 350px;
    background: linear-gradient(145deg, #2d2d2d, #1a1a1a);
    border-radius: 20px;
    padding: 20px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}

/* 显示屏 */
.display {
    background: linear-gradient(145deg, #1a1a1a, #0d0d0d);
    border-radius: 15px;
    padding: 25px;
    margin-bottom: 20px;
    text-align: right;
    min-height: 100px;
    word-wrap: break-word;
    word-break: break-all;
}

.previous-operand {
    color: #888;
    font-size: 1.2rem;
    min-height: 1.5rem;
    margin-bottom: 5px;
}

.current-operand {
    color: white;
    font-size: 3rem;
    font-weight: 300;
}

/* 按钮区 */
.buttons {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
}

.btn {
    aspect-ratio: 1;
    border: none;
    border-radius: 12px;
    font-size: 1.5rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
    background: #3d3d3d;
    color: white;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
}

.btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
}

.btn:active {
    transform: translateY(0);
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
}

.btn-span-2 {
    grid-column: span 2;
    aspect-ratio: auto;
}''',
            'status': 'completed',
            'total_lines': 68
        },
        {
            'filename': 'script.js',
            'content': '''// 计算器 App - 核心逻辑

class Calculator {
    constructor(previousOperandEl, currentOperandEl) {
        this.previousOperandEl = previousOperandEl;
        this.currentOperandEl = currentOperandEl;
        this.clear();
    }

    // 清空
    clear() {
        this.currentOperand = '0';
        this.previousOperand = '';
        this.operation = undefined;
        this.updateDisplay();
    }

    // 删除最后一位
    delete() {
        if (this.currentOperand === '0') return;
        if (this.currentOperand.length === 1) {
            this.currentOperand = '0';
        } else {
            this.currentOperand = this.currentOperand.slice(0, -1);
        }
        this.updateDisplay();
    }

    // 添加数字
    appendNumber(number) {
        if (number === '.' && this.currentOperand.includes('.')) return;
        if (this.currentOperand === '0' && number !== '.') {
            this.currentOperand = number;
        } else {
            this.currentOperand += number;
        }
        this.updateDisplay();
    }

    // 选择运算符
    chooseOperation(operation) {
        if (this.currentOperand === '') return;
        if (this.previousOperand !== '') {
            this.compute();
        }
        this.operation = operation;
        this.previousOperand = this.currentOperand + ' ' + operation;
        this.currentOperand = '0';
        this.updateDisplay();
    }

    // 计算
    compute() {
        let computation;
        const prev = parseFloat(this.previousOperand);
        const current = parseFloat(this.currentOperand);

        if (isNaN(prev) || isNaN(current)) return;

        switch (this.operation) {
            case '+':
                computation = prev + current;
                break;
            case '-':
                computation = prev - current;
                break;
            case '×':
                computation = prev * current;
                break;
            case '÷':
                computation = current !== 0 ? prev / current : '错误';
                break;
            default:
                return;
        }

        this.currentOperand = computation.toString();
        this.operation = undefined;
        this.previousOperand = '';
        this.updateDisplay();
    }

    // 更新显示
    updateDisplay() {
        this.currentOperandEl.textContent = this.currentOperand;
        this.previousOperandEl.textContent = this.previousOperand;
    }
}

// 初始化计算器
const previousOperandEl = document.getElementById('previousOperand');
const currentOperandEl = document.getElementById('currentOperand');
const calculator = new Calculator(previousOperandEl, currentOperandEl);

// 键盘支持
document.addEventListener('keydown', (e) => {
    if (e.key >= '0' && e.key <= '9') calculator.appendNumber(e.key);
    if (e.key === '.') calculator.appendNumber('.');
    if (e.key === '+') calculator.chooseOperation('+');
    if (e.key === '-') calculator.chooseOperation('-');
    if (e.key === '*') calculator.chooseOperation('×');
    if (e.key === '/') {
        e.preventDefault();
        calculator.chooseOperation('÷');
    }
    if (e.key === 'Enter' || e.key === '=') calculator.compute();
    if (e.key === 'Escape') calculator.clear();
    if (e.key === 'Backspace') calculator.delete();
});''',
            'status': 'completed',
            'total_lines': 95
        }
    ]


def generate_note_app_code() -> list:
    """生成笔记应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>笔记 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-yellow-50 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">📝 我的笔记</h1>

        <div class="flex gap-6">
            <!-- 侧边栏：笔记列表 -->
            <div class="w-80 bg-white rounded-lg shadow-md p-4 h-[600px] overflow-y-auto">
                <button id="newNoteBtn" class="w-full py-3 bg-green-500 text-white rounded-lg hover:bg-green-600 mb-4 font-medium">
                    + 新建笔记
                </button>
                <div id="noteList" class="space-y-2"></div>
            </div>

            <!-- 主编辑区 -->
            <div class="flex-1 bg-white rounded-lg shadow-md p-6 h-[600px] flex flex-col">
                <input
                    id="noteTitle"
                    type="text"
                    class="text-2xl font-bold border-b pb-3 mb-4 focus:outline-none"
                    placeholder="笔记标题..."
                />
                <textarea
                    id="noteContent"
                    class="flex-1 resize-none focus:outline-none text-gray-700 leading-relaxed"
                    placeholder="开始记录你的想法..."
                ></textarea>
                <div class="flex justify-between items-center mt-4 pt-4 border-t">
                    <span id="lastSaved" class="text-sm text-gray-500"></span>
                    <div class="flex gap-2">
                        <button id="deleteBtn" class="px-4 py-2 text-red-500 hover:bg-red-50 rounded">删除</button>
                        <button id="saveBtn" class="px-6 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">保存</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 42
        },
        {
            'filename': 'style.css',
            'content': '''/* 笔记 App 样式 */

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* 笔记列表项 */
.note-item {
    @apply p-3 rounded-lg cursor-pointer transition-all hover:bg-yellow-50 border border-transparent;
}

.note-item.active {
    @apply bg-blue-50 border-blue-200;
}

.note-item-title {
    @apply font-medium text-gray-800 truncate;
}

.note-item-preview {
    @apply text-sm text-gray-500 truncate mt-1;
}

.note-item-date {
    @apply text-xs text-gray-400 mt-2;
}

/* 编辑器 */
#noteTitle {
    font-family: inherit;
}

#noteContent {
    font-family: inherit;
    line-height: 1.8;
}

/* 滚动条美化 */
::-webkit-scrollbar {
    width: 8px;
}

::-webkit-scrollbar-track {
    background: #f1f1f1;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb {
    background: #c1c1c1;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: #a1a1a1;
}''',
            'status': 'completed',
            'total_lines': 42
        },
        {
            'filename': 'script.js',
            'content': '''// 笔记 App - 核心逻辑

let notes = [];
let currentNoteId = null;

// DOM 元素
const noteList = document.getElementById('noteList');
const noteTitle = document.getElementById('noteTitle');
const noteContent = document.getElementById('noteContent');
const newNoteBtn = document.getElementById('newNoteBtn');
const saveBtn = document.getElementById('saveBtn');
const deleteBtn = document.getElementById('deleteBtn');
const lastSaved = document.getElementById('lastSaved');

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadNotes();
    renderNoteList();
    setupEventListeners();
});

function setupEventListeners() {
    newNoteBtn.addEventListener('click', createNewNote);
    saveBtn.addEventListener('click', saveCurrentNote);
    deleteBtn.addEventListener('click', deleteCurrentNote);

    // 自动保存
    noteTitle.addEventListener('input', debouncedSave);
    noteContent.addEventListener('input', debouncedSave);
}

// 创建新笔记
function createNewNote() {
    currentNoteId = Date.now();
    const note = {
        id: currentNoteId,
        title: '无标题笔记',
        content: '',
        updatedAt: new Date().toISOString()
    };
    notes.unshift(note);
    saveNotes();
    renderNoteList();
    loadNote(note.id);
}

// 保存当前笔记
function saveCurrentNote() {
    if (!currentNoteId) return;

    const note = notes.find(n => n.id === currentNoteId);
    if (note) {
        note.title = noteTitle.value.trim() || '无标题笔记';
        note.content = noteContent.value;
        note.updatedAt = new Date().toISOString();
        saveNotes();
        renderNoteList();
        showSavedStatus();
    }
}

// 防抖保存
let saveTimeout;
function debouncedSave() {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(saveCurrentNote, 1000);
}

// 删除笔记
function deleteCurrentNote() {
    if (!currentNoteId) return;
    if (!confirm('确定要删除这篇笔记吗？')) return;

    notes = notes.filter(n => n.id !== currentNoteId);
    currentNoteId = null;
    noteTitle.value = '';
    noteContent.value = '';
    saveNotes();
    renderNoteList();
}

// 加载笔记到编辑器
function loadNote(id) {
    currentNoteId = id;
    const note = notes.find(n => n.id === id);
    if (note) {
        noteTitle.value = note.title;
        noteContent.value = note.content;
    }
    renderNoteList();
}

// 渲染笔记列表
function renderNoteList() {
    noteList.innerHTML = notes.map(note => `
        <div class="note-item ${note.id === currentNoteId ? 'active' : ''}" onclick="loadNote(${note.id})">
            <div class="note-item-title">${escapeHtml(note.title)}</div>
            <div class="note-item-preview">${escapeHtml(note.content.slice(0, 50)) || '无内容'}</div>
            <div class="note-item-date">${formatDate(note.updatedAt)}</div>
        </div>
    `).join('');
}

// 保存到本地存储
function saveNotes() {
    localStorage.setItem('notes', JSON.stringify(notes));
}

// 从本地存储加载
function loadNotes() {
    const saved = localStorage.getItem('notes');
    if (saved) {
        notes = JSON.parse(saved);
    }
}

// 显示保存状态
function showSavedStatus() {
    lastSaved.textContent = '已保存于 ' + new Date().toLocaleTimeString();
    setTimeout(() => {
        lastSaved.textContent = '';
    }, 3000);
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 格式化日期
function formatDate(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diff = now - date;

    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
    return date.toLocaleDateString();
}''',
            'status': 'completed',
            'total_lines': 118
        }
    ]


def generate_generic_app_code(requirement: str) -> list:
    """生成通用应用代码"""
    # 根据需求提取关键词生成个性化应用
    return [
        {
            'filename': 'index.html',
            'content': f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>应用</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-4xl">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">🚀 {requirement[:20]}...</h1>

        <div class="bg-white rounded-lg shadow-md p-8">
            <p class="text-gray-600 text-center mb-6">
                这是一个基于您的需求生成的应用原型
            </p>

            <div id="app" class="space-y-4">
                <!-- 应用内容将在这里生成 -->
            </div>
        </div>
    </div>

    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 25
        },
        {
            'filename': 'style.css',
            'content': '''/* 通用应用样式 */

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

#app {
    min-height: 300px;
}

/* 卡片样式 */
.card {
    @apply bg-white rounded-lg shadow p-6;
}

/* 按钮样式 */
.btn-primary {
    @apply px-6 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors font-medium;
}

.btn-secondary {
    @apply px-6 py-3 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition-colors font-medium;
}''',
            'status': 'completed',
            'total_lines': 20
        },
        {
            'filename': 'script.js',
            'content': '''// 通用应用 - 核心逻辑

document.addEventListener('DOMContentLoaded', () => {
    const app = document.getElementById('app');

    // 初始化应用
    app.innerHTML = `
        <div class="text-center py-8">
            <p class="text-gray-500">应用已加载，开始构建您的功能...</p>
        </div>
    `;

    console.log('应用初始化完成');
});''',
            'status': 'completed',
            'total_lines': 12
        }
    ]


# ==================== 主程序入口 ====================

if __name__ == '__main__':
    # 创建默认测试用户
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == 'test').first()
        if not existing:
            user = User(username='test', password_hash=hash_password('123456'))
            db.add(user)
            db.commit()
            print("✓ 已创建测试账号：test / 123456")
    finally:
        db.close()

    # 启动服务
    print("=" * 50)
    print("Atoms.dev Vibe Coding Demo")
    print("=" * 50)
    print("后端服务启动中...")
    print("前端页面：http://localhost:5001/login.html")
    print("=" * 50)

    app.run(debug=False, host='0.0.0.0', port=5001, threaded=True)
