# -*- coding: utf-8 -*-
"""
配置管理模块
使用 Pydantic 进行配置验证
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import Dict, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), '.env'),
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'  # 忽略额外字段
    )

    # ==================== 基础配置 ====================

    # 基础路径
    BASE_DIR: Path = Field(default=Path(__file__).parent.parent)
    BACKEND_DIR: Path = Field(default=Path(__file__).parent)

    # ==================== 数据库配置 ====================

    # SQLite 兼容（仅当 DATABASE_URL 为空时使用）
    DATABASE_NAME: str = Field(default='vcd.db', description='数据库文件名(SQLite 兼容)')

    # PostgreSQL 连接串（生产环境必须配置）
    DATABASE_URL: str = Field(
        default='',
        description='PostgreSQL 连接串，例如 postgresql+psycopg://user:pass@host:5432/db。为空时回退到 SQLite'
    )

    # 数据库连接池配置
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=50, description='连接池大小')
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=50, description='连接池最大溢出')
    DATABASE_POOL_PRE_PING: bool = Field(default=True, description='连接池预先 ping 检测存活')

    @property
    def DATABASE_PATH(self) -> str:
        return str(self.BACKEND_DIR / self.DATABASE_NAME)

    @property
    def DATABASE_URI(self) -> str:
        """返回数据库连接 URI，优先 PostgreSQL，回退 SQLite"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f'sqlite:///{self.DATABASE_PATH}'

    @property
    def IS_POSTGRES(self) -> bool:
        """是否使用 PostgreSQL"""
        return bool(self.DATABASE_URL)

    @property
    def DATABASE_CONNECT_ARGS(self) -> dict:
        """数据库连接参数（SQLite 需要 check_same_thread=False，PG 不需要）"""
        if self.IS_POSTGRES:
            return {}
        return {'check_same_thread': False}

    @property
    def DATABASE_ENGINE_KWARGS(self) -> dict:
        """SQLAlchemy create_engine 的额外参数"""
        if self.IS_POSTGRES:
            return {
                'pool_size': self.DATABASE_POOL_SIZE,
                'max_overflow': self.DATABASE_MAX_OVERFLOW,
                'pool_pre_ping': self.DATABASE_POOL_PRE_PING,
            }
        return {}

    # ==================== Redis 配置 ====================

    REDIS_URL: str = Field(
        default='redis://localhost:6379/0',
        description='Redis 连接串'
    )

    # ==================== Celery 配置 ====================

    CELERY_BROKER_URL: str = Field(
        default='',
        description='Celery broker URL。为空时使用 REDIS_URL 的 /1 库'
    )
    CELERY_RESULT_BACKEND: str = Field(
        default='',
        description='Celery result backend。为空时使用 REDIS_URL 的 /2 库'
    )
    CELERY_WORKER_CONCURRENCY: int = Field(default=3, ge=1, le=20, description='Celery worker 并发数')
    CELERY_ENABLED: bool = Field(
        default=True,
        description='是否启用 Celery 任务队列。False 时使用 ThreadPoolExecutor'
    )

    @property
    def CELERY_BROKER(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL.replace('/0', '/1')

    @property
    def CELERY_RESULT(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL.replace('/0', '/2')

    # ==================== JWT 配置 ====================

    JWT_SECRET_KEY: str = Field(
        default='talk2code-secret-key-change-in-production',
        description='JWT 密钥（生产环境必须修改）'
    )
    JWT_ACCESS_TOKEN_EXPIRES_HOURS: int = Field(default=24, description='Token 过期时间（小时）')

    @property
    def JWT_ACCESS_TOKEN_EXPIRES(self) -> timedelta:
        return timedelta(hours=self.JWT_ACCESS_TOKEN_EXPIRES_HOURS)

    @field_validator('JWT_SECRET_KEY')
    @classmethod
    def validate_jwt_secret(cls, v):
        if v == 'talk2code-secret-key-change-in-production':
            import warnings
            warnings.warn(
                "⚠️  使用默认 JWT 密钥，生产环境请修改 JWT_SECRET_KEY 环境变量",
                UserWarning,
                stacklevel=2
            )
        return v

    # ==================== SSE 配置 ====================

    SSE_RETRY_TIMEOUT: int = Field(default=1000, description='SSE 重连时间（毫秒）')
    SSE_HEARTBEAT_INTERVAL: int = Field(default=30, description='SSE 心跳间隔（秒）')
    SSE_CLIENT_TIMEOUT: int = Field(default=300, description='SSE 客户端超时时间（秒）')

    # ==================== AI 智能体配置 ====================

    # 代码生成速度 (字/秒)
    CODE_GEN_SPEED: Dict[str, int] = Field(
        default={'slow': 10, 'medium': 30, 'fast': 60}
    )
    DEFAULT_SPEED: Literal['slow', 'medium', 'fast'] = Field(default='medium')

    # ==================== LLM 配置 ====================

    # LLM 协议类型
    LLM_PROVIDER: Literal['openai_compatible', 'anthropic_compatible'] = Field(
        default='openai_compatible',
        description='LLM 协议类型：openai_compatible 或 anthropic_compatible'
    )

    # LLM 通用配置
    LLM_API_KEY: str = Field(default='', description='LLM API Key')
    LLM_BASE_URL: str = Field(default='', description='LLM API 地址')
    LLM_MODEL: str = Field(default='qwen-plus', description='LLM 模型名称')

    # LLM 调用配置
    LLM_TEMPERATURE: float = Field(default=0.7, ge=0, le=2, description='LLM 温度参数')
    LLM_MAX_TOKENS: int = Field(default=12000, ge=100, le=32000, description='LLM 最大生成 token 数')
    LLM_TIMEOUT: int = Field(default=60, ge=10, le=300, description='LLM 调用超时时间（秒）')
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5, description='LLM 调用最大重试次数')
    LLM_CRAFT_ENABLED: bool = Field(default=True, description='是否启用 Craft 设计质量规则注入')

    # 思考模式（reasoning 模型）：DeepSeek V4 默认开启思考且 effort=high，
    # 会消耗大量 reasoning token（被程序过滤丢弃，纯浪费）。默认关闭以提速。
    LLM_THINKING: Literal['enabled', 'disabled'] = Field(
        default='disabled',
        description='LLM 思考模式开关（OpenAI 格式 {"thinking": {"type": ...}}）'
    )
    # 思考强度（仅当 LLM_THINKING=enabled 时生效）
    # 注意：effort=high 会消耗大量 reasoning token，对 max_tokens 较小的调用
    # （如 AC 翻译 2000、评估 4000）会把 token 全耗在思考上导致内容为空。
    # 默认用 low：既保证 JSON 格式正确，又避免 reasoning token 挤占输出预算。
    LLM_REASONING_EFFORT: Literal['low', 'high', 'max'] = Field(
        default='low',
        description='思考强度（low/high/max），仅当 LLM_THINKING=enabled 时生效'
    )

    # LLM 熔断器配置
    LLM_CIRCUIT_BREAKER_THRESHOLD: int = Field(
        default=5, ge=2, le=20,
        description='LLM 连续失败多少次后触发熔断'
    )
    LLM_CIRCUIT_BREAKER_TIMEOUT: int = Field(
        default=30, ge=10, le=300,
        description='熔断器打开后等待多少秒进入半开状态'
    )

    # 备用 LLM 配置（主模型不可用时自动切换，可选）
    LLM_BACKUP_BASE_URL: str = Field(default='', description='备用 LLM API 地址')
    LLM_BACKUP_MODEL: str = Field(default='', description='备用 LLM 模型名称')
    LLM_BACKUP_API_KEY: str = Field(default='', description='备用 LLM API Key')
    LLM_BACKUP_PROVIDER: str = Field(default='openai_compatible', description='备用 LLM 协议类型')

    @field_validator('LLM_API_KEY')
    @classmethod
    def validate_api_key(cls, v):
        if not v:
            import warnings
            warnings.warn(
                "⚠️  未配置 LLM_API_KEY，请在 .env 文件中设置",
                UserWarning,
                stacklevel=2
            )
        return v

    # ==================== 任务队列配置 ====================

    TASK_QUEUE_MAX_WORKERS: int = Field(default=3, description='任务队列最大工作线程数')

    # ==================== 工作区配置 ====================

    WORKSPACE_DIR: str = Field(
        default='',
        description='工作区根目录。为空时使用 BACKEND_DIR/workspaces（持久化，重启不丢失）'
    )

    # ==================== 日志配置 ====================

    LOG_LEVEL: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = Field(
        default='INFO',
        description='日志级别'
    )
    LOG_FILE: str = Field(default='logs/app.log', description='日志文件路径')
    LOG_DIR: str = Field(default='logs', description='日志目录（项目根目录下）')
    AGENT_LOG_RETENTION_DAYS: int = Field(default=30, description='agent/llm 日志保留天数')
    APP_LOG_RETENTION_DAYS: int = Field(default=90, description='app/access 日志保留天数')
    LOG_FILE_MAX_SIZE_MB: int = Field(default=50, description='单日志文件最大大小 (MB)')

    # ==================== 安全配置 ====================

    PASSWORD_MIN_LENGTH: int = Field(default=6, description='密码最小长度')
    USERNAME_MIN_LENGTH: int = Field(default=3, description='用户名最小长度')

    # 显式豁免"默认密钥禁止启动"检查（仅限无法配置密钥的隔离环境，如一次性容器演示）
    ALLOW_INSECURE_SECRETS: bool = Field(
        default=False,
        description='true 时允许使用默认 JWT_SECRET_KEY 启动（显式豁免，需逐环境手动开启）'
    )

    # 是否信任反向代理头（X-Forwarded-For）。仅在应用确实部署于可信反代之后才开启，
    # 否则限流/审计的客户端 IP 可被请求方伪造。
    TRUST_PROXY_HEADERS: bool = Field(
        default=False,
        description='true 时从 X-Forwarded-For 解析客户端 IP（须部署在可信反向代理之后）'
    )

    # 预览能力 URL 的外部基础地址（如 https://preview.example.com）。
    # 为空时生成同源相对路径（本机/同源反代场景无需配置）。
    PREVIEW_PUBLIC_BASE_URL: str = Field(
        default='',
        description='预览能力 URL 的外部基础地址，空则使用同源相对路径'
    )

    # ==================== 应用配置 ====================

    APP_HOST: str = Field(default='0.0.0.0', description='应用监听地址')
    APP_PORT: int = Field(default=5001, ge=1, le=65535, description='应用端口')
    APP_DEBUG: bool = Field(default=False, description='调试模式')

    # CORS 配置
    CORS_ORIGINS: str = Field(
        default='http://localhost:5100,http://localhost:5001',
        description='允许的 CORS 源（逗号分隔）'
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]


# 全局配置实例
_settings: Settings = None


def get_settings() -> Settings:
    """获取配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# 便捷访问（兼容旧代码）
settings = get_settings()

# 兼容旧代码的属性导出
BASE_DIR = settings.BASE_DIR
BACKEND_DIR = settings.BACKEND_DIR
DATABASE_URI = settings.DATABASE_URI
JWT_SECRET_KEY = settings.JWT_SECRET_KEY
JWT_ACCESS_TOKEN_EXPIRES = settings.JWT_ACCESS_TOKEN_EXPIRES
SSE_RETRY_TIMEOUT = settings.SSE_RETRY_TIMEOUT
CODE_GEN_SPEED = settings.CODE_GEN_SPEED
DEFAULT_SPEED = settings.DEFAULT_SPEED
LLM_PROVIDER = settings.LLM_PROVIDER
LLM_API_KEY = settings.LLM_API_KEY
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_MODEL = settings.LLM_MODEL
LLM_THINKING = settings.LLM_THINKING
LLM_REASONING_EFFORT = settings.LLM_REASONING_EFFORT
LOG_LEVEL = settings.LOG_LEVEL
LOG_FILE = settings.LOG_FILE
LOG_DIR = settings.LOG_DIR
AGENT_LOG_RETENTION_DAYS = settings.AGENT_LOG_RETENTION_DAYS
LOG_FILE_MAX_SIZE_MB = settings.LOG_FILE_MAX_SIZE_MB
