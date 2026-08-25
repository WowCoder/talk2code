# -*- coding: utf-8 -*-
"""
Pytest 配置和共享 fixtures
"""

import os
import sys
import pytest
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# 设置测试环境变量
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret-key-for-testing-only')
os.environ.setdefault('LLM_API_KEY', 'test-api-key')
os.environ.setdefault('DATABASE_NAME', 'test_vcd.db')
# 强制清空 DATABASE_URL：否则 .env 中的 PostgreSQL 连接串会优先于默认值，
# 导致测试去连生产 PG 而非 SQLite（config 单例 + models 引擎在导入期已绑定）
os.environ['DATABASE_URL'] = ''
os.environ.setdefault('APP_DEBUG', 'false')
os.environ.setdefault('DISABLE_RATE_LIMIT', 'true')  # Disable rate limiting for tests


@pytest.fixture(autouse=True)
def _reset_llm_singleton_breaker():
    """共享 LLM 单例的熔断器状态不得在用例间泄漏（保证测试顺序无关）。

    此前 test_llm_client.py::test_chat_with_llm_function 在全量运行时失败：
    更早运行的 functional/integration 用例把单例熔断器打到 open 状态，
    导致"单跑通过、全量失败"。
    """
    from llm.client import get_client as _get_client
    try:
        client = _get_client()
        for attr in ("_circuit_breaker", "_backup_circuit_breaker"):
            cb = getattr(client, attr, None)
            if cb is not None:
                cb._failure_count = 0
                cb._state = "closed"
                cb._probe_in_flight = False
    except Exception:
        pass
    yield


@pytest.fixture(scope='session')
def test_config():
    """测试配置"""
    from config import Settings
    return Settings(
        JWT_SECRET_KEY='test-secret-key-for-testing-only',
        LLM_API_KEY='test-api-key',
        DATABASE_NAME='test_vcd.db',
        APP_DEBUG=False
    )


@pytest.fixture(scope='function')
def app_client():
    """Flask 应用测试客户端"""
    # 创建测试应用
    from flask import Flask
    from flask.testing import FlaskClient

    # 使用测试配置初始化应用
    os.environ['DATABASE_NAME'] = ':memory:'  # 使用内存数据库
    os.environ['LLM_API_KEY'] = 'test-api-key'
    os.environ['JWT_SECRET_KEY'] = 'test-secret-key'

    # 导入并配置应用
    import app as app_module
    from models import init_db

    # 初始化内存数据库
    init_db()

    # 返回测试客户端
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture(scope='function')
def mock_llm_response():
    """模拟 LLM 响应"""
    return {
        'content': 'This is a test response',
        'usage': {'total_tokens': 100},
        'finish_reason': 'stop'
    }


@pytest.fixture(scope='function')
def sample_diff_text():
    """示例 diff 文本"""
    return """```diff
--- a/index.html
+++ b/index.html
@@ -1,5 +1,6 @@
 <html>
 <head>
-    <title>Old Title</title>
+    <title>New Title</title>
+    <meta charset="utf-8">
 </head>
 <body>
```"""


@pytest.fixture(scope='function')
def sample_code_files():
    """示例代码文件列表"""
    return [
        {
            'filename': 'index.html',
            'content': '<html>\n<head>\n    <title>Old Title</title>\n</head>\n<body>\n    <h1>Hello</h1>\n</body>\n</html>',
            'status': 'generated'
        },
        {
            'filename': 'app.js',
            'content': 'function main() {\n    console.log("Hello");\n}\nmain();',
            'status': 'generated'
        }
    ]


@pytest.fixture(scope='function')
def sample_dialogue_history():
    """示例对话历史"""
    return [
        {
            'role': 'user',
            'name': '用户',
            'content': '帮我做一个待办事项应用',
            'timestamp': '2024-01-01 10:00:00'
        },
        {
            'role': 'agent',
            'name': '研究员',
            'content': '分析需求完成',
            'timestamp': '2024-01-01 10:01:00'
        },
        {
            'role': 'system',
            'name': '系统',
            'content': '已生成代码文件：index.html, app.js',
            'timestamp': '2024-01-01 10:05:00',
            'type': 'code_updated'
        }
    ]


@pytest.fixture(scope='function')
def sample_requirement_data():
    """示例需求数据"""
    return {
        'id': 1,
        'title': '待办事项应用',
        'content': '帮我创建一个简单的待办事项应用，可以添加、删除、标记完成',
        'status': 'completed',
        'dialogue_history': [],
        'code_files': []
    }


@pytest.fixture(scope='function')
def test_user():
    """创建测试用户 fixture"""
    from models import User, SessionLocal
    from utils.security import hash_password

    # Clean up any existing user with username 'test_func'
    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.username == 'test_func').first()
        if existing_user:
            db.delete(existing_user)
            db.commit()

        # Create new test user
        hashed = hash_password('test123456')
        user = User(username='test_func', password_hash=hashed)
        db.add(user)
        db.commit()
        db.refresh(user)

        return {'username': 'test_func', 'password': 'test123456', 'id': user.id}
    finally:
        db.close()


@pytest.fixture(scope='function')
def auth_token(app_client, test_user):
    """获取认证 token fixture

    登录响应不再回传 body token（仅 httpOnly cookie），
    这里从测试客户端的 cookie 中取值，供需要显式 Authorization 头的用例使用。
    """
    response = app_client.post('/api/login', json={
        'username': test_user['username'],
        'password': test_user['password']
    })
    assert response.status_code == 200, f"Login failed: {response.get_json()}"
    cookie = app_client.get_cookie('access_token_cookie')
    assert cookie is not None, "登录后未设置 access_token_cookie"
    return cookie.value