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
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'  # 忽略额外字段
    )

    # ==================== 基础配置 ====================

    # 基础路径
    BASE_DIR: Path = Field(default=Path(__file__).parent.parent)
    BACKEND_DIR: Path = Field(default=Path(__file__).parent)

    # ==================== 数据库配置 ====================

    DATABASE_NAME: str = Field(default='vcd.db', description='数据库文件名')

    @property
    def DATABASE_PATH(self) -> str:
        return str(self.BACKEND_DIR / self.DATABASE_NAME)

    @property
    def DATABASE_URI(self) -> str:
        return f'sqlite:///{self.DATABASE_PATH}'

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
    LLM_MAX_TOKENS: int = Field(default=4000, ge=100, le=32000, description='LLM 最大生成 token 数')
    LLM_TIMEOUT: int = Field(default=60, ge=10, le=300, description='LLM 调用超时时间（秒）')
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5, description='LLM 调用最大重试次数')

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

    # ==================== 日志配置 ====================

    LOG_LEVEL: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = Field(
        default='INFO',
        description='日志级别'
    )
    LOG_FILE: str = Field(default='logs/app.log', description='日志文件路径')

    # ==================== 安全配置 ====================

    PASSWORD_MIN_LENGTH: int = Field(default=6, description='密码最小长度')
    USERNAME_MIN_LENGTH: int = Field(default=3, description='用户名最小长度')

    # ==================== 应用配置 ====================

    APP_HOST: str = Field(default='0.0.0.0', description='应用监听地址')
    APP_PORT: int = Field(default=5001, ge=1, le=65535, description='应用端口')
    APP_DEBUG: bool = Field(default=False, description='调试模式')

    def validate_production(self) -> bool:
        """
        验证生产环境配置

        Returns:
            配置是否有效
        """
        errors = []

        if self.JWT_SECRET_KEY == 'talk2code-secret-key-change-in-production':
            errors.append("JWT_SECRET_KEY 使用默认值，生产环境必须修改")

        if not self.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")

        if self.APP_DEBUG:
            errors.append("生产环境不应开启 DEBUG 模式")

        if errors:
            for error in errors:
                import warnings
                warnings.warn(error, UserWarning, stacklevel=2)
            return False

        return True


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
LOG_LEVEL = settings.LOG_LEVEL
LOG_FILE = settings.LOG_FILE
