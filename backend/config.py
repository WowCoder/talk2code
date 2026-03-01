# -*- coding: utf-8 -*-
"""
配置文件
包含数据库配置、JWT 配置、SSE 配置等
"""

import os
from datetime import timedelta
from pathlib import Path

# 基础路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# 加载 .env 文件中的环境变量
ENV_FILE = Path(BACKEND_DIR) / '.env'
if ENV_FILE.exists():
    with open(ENV_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # 移除引号
                value = value.strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)

# 数据库配置 - 使用 SQLite 轻量数据库
DATABASE_PATH = os.path.join(BACKEND_DIR, 'atoms.db')
DATABASE_URI = f'sqlite:///{DATABASE_PATH}'

# JWT 配置
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'vibe-coding-secret-key-change-in-production')
JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)  # Token 过期时间

# SSE 配置
SSE_RETRY_TIMEOUT = 1000  # 重连时间 (ms)

# AI 智能体配置 - 代码生成速度 (字/秒)
CODE_GEN_SPEED = {
    'slow': 10,
    'medium': 30,
    'fast': 60
}

# 默认生成速度
DEFAULT_SPEED = 'medium'

# 阿里云百炼大模型配置
# 获取 API Key: https://bailian.console.aliyun.com/
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY', '')
DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
DASHSCOPE_MODEL = os.environ.get('DASHSCOPE_MODEL', 'qwen-plus')  # 可选：qwen-plus, qwen-turbo, qwen-max
