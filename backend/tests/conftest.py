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
os.environ.setdefault('DASHSCOPE_API_KEY', 'test-api-key')
os.environ.setdefault('DATABASE_NAME', 'test_vcd.db')
os.environ.setdefault('APP_DEBUG', 'false')


@pytest.fixture(scope='session')
def test_config():
    """测试配置"""
    from config import Settings
    return Settings(
        JWT_SECRET_KEY='test-secret-key-for-testing-only',
        DASHSCOPE_API_KEY='test-api-key',
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
    os.environ['DASHSCOPE_API_KEY'] = 'test-api-key'
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