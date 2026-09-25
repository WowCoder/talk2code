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
    LLM_MAX_TOKENS: int = Field(default=12000, ge=100, le=65500, description='LLM 最大生成 token 数（输出上限，非上下文窗口）')
    # 「content 为空但有 reasoning_content」时的重试额度上限。
    # 背景：对强制思考型模型（lkeap 的 glm 全系，服务端明确「该模型始终思考，不支持关闭」），
    # max_tokens 是 **reasoning + content 的共享额度且 reasoning 先扣**。
    # 实测同一 prompt：cap=8000 → 188s 全部耗在思考上，正文 **0 字**（finish=length）。
    # 而流水线里大量按需写死的小额度调用（500/1000/2000/3000），一旦失败就把
    # 重试额度抬到全局 LLM_MAX_TOKENS（32000）——单轮随即可达 ~680s，必然撞穿
    # LLM_TIMEOUT，整节点表现为「假挂死」。故这里给一个**绝对天花板**。
    LLM_REASONING_FALLBACK_TOKENS: int = Field(
        default=8000, ge=500, le=65500,
        description='推理模型 token 耗尽时的重试额度上限'
    )
    LLM_TIMEOUT: int = Field(default=60, ge=10, le=300, description='LLM 调用超时时间（秒）')
    # 缺陷修复的 LLM 调用要输出整文件 JSON（16k~32k tokens），响应天然更慢，故单独给上限。
    # 此前该值硬编码 150s：LLM 端点慢时单次请求挂 2.5 分钟，前端长时间无 SSE 更新，
    # 观感等同"卡死"（req 146 实测两次 150s 读超时）。
    DEFECT_REPAIR_TIMEOUT: int = Field(default=90, ge=10, le=300, description='缺陷修复 LLM 调用超时时间（秒）')
    # 长尾熔断：单轮 LLM 超过此预算就中断并按更小的 max_tokens 重试一次。
    # 实测（req 146-159，155 轮）延迟 >60s 的轮次只占 20%，却吃掉 71% 的 LLM 总时间，
    # 且这些慢轮输出很短（中位 583 token）——是空转/抖动，不是"写太长"。
    # 与其干等 300s，不如 45s 断掉重来一次；第二次不熔断，避免误杀真需要长输出的轮次。
    # 设为 0 关闭熔断。
    LLM_SLOW_TURN_TIMEOUT: int = Field(default=45, ge=0, le=300, description='单轮 LLM 熔断预算（秒），0=关闭')
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5, description='LLM 调用最大重试次数')
    LLM_CRAFT_ENABLED: bool = Field(default=True, description='是否启用 Craft 设计质量规则注入')

    # 思考模式（reasoning 模型）：coder 节点在 runtime 中强制 thinking=enabled，
    # 不依赖此全局开关。此处保持 disabled：让 team_leader/verify/memory 等小预算
    # 调用（AC 翻译 2000、评估 4000）不发思考字段，避免 reasoning token 挤占输出
    # 导致内容为空。coder 的思考由其调用参数保证。
    LLM_THINKING: Literal['enabled', 'disabled'] = Field(
        default='disabled',
        description='LLM 思考模式开关（OpenAI 格式 {"thinking": {"type": ...}}）'
    )
    # 思考强度（仅当该次调用 thinking=enabled 时生效；thinking=disabled 时忽略）。
    # coder 强制开思考，effort=high 已实证显著降低运行时逻辑缺陷
    # （贪吃蛇 A/B 实验：effort=low 时 preview 报 1 个运行错误，effort=high 时零错误，
    #   同模型同框架同 plan）。小预算节点 thinking=disabled，effort 对其无影响。
    LLM_REASONING_EFFORT: Literal['low', 'high', 'max'] = Field(
        default='high',
        description='思考强度（low/high/max），仅当 thinking=enabled 时生效'
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

    # ==================== Agent 质量门禁配置 ====================

    # coder 回环修复轮数上限（graph 路由与 prompt 文案共用同一配置源，
    # 避免「prompt 说 1 轮、graph 实际跑 2 轮」的文案漂移）
    CODER_MAX_REPAIR_ROUNDS: int = Field(
        default=2, ge=0, le=5,
        description='verify NEEDS_WORK 后允许回到 coder 修复的最大轮数'
    )
    # 小上下文定向修复最大轮数（graph 与文档共用）
    DEFECT_REPAIR_MAX_ROUNDS: int = Field(
        default=2, ge=0, le=5,
        description='冒烟确定性缺陷走小上下文定向修复的最大轮数'
    )
    # 交付门禁：critical 缺陷未清零时不自动放行为 finished_with_issues，
    # 而是转 needs_user_input 并附差异报告（Phase 3 行为变更开关）
    DELIVERY_GATE_STRICT: bool = Field(
        default=True,
        description='true 时 critical 缺陷未清零的需求转 needs_user_input，不自动标记完成'
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

    # ==================== Agent 执行明细日志（开发排查用） ====================

    # 打开后每个 LLM 轮次 / 工具调用落一条 JSONL（含完整请求参数与返回值），
    # 供开发排查「Agent 到底收发了什么」。默认关闭：属**开发视角**，不进前端、
    # 不给用户看，避免生产环境无界增长。
    AGENT_EXEC_LOG: bool = Field(
        default=False,
        description='是否记录 Agent 执行明细（LLM 请求/返回 + 工具调用）到 JSONL'
    )
    AGENT_EXEC_LOG_DIR: str = Field(
        default='logs/agent_exec',
        description='Agent 执行明细日志目录（相对 BACKEND_DIR）'
    )

    # ==================== 任务中断自愈 ====================

    # 任务队列是纯内存的，进程重启后上一进程在跑的需求会永久停在 processing。
    # 开启后启动时把这类僵尸需求改判为 interrupted，用户可经
    # POST /api/requirements/<id>/resume 从检查点续跑。
    MARK_STALE_ON_BOOT: bool = Field(
        default=True,
        description='启动时把僵死在 processing 的需求改判为 interrupted（可续跑）'
    )

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

    # ==================== 发布配置（一键发布） ====================

    PUBLISH_APEX: str = Field(
        default='',
        description='发布站点 apex 域名（如 wowcoder.cn）。空=关闭 Host 路由'
    )
    # 发布链接的协议与端口必须可配：生产是 https + 标准端口，而本地开发
    # 跑在 http://<slug>.localhost:<后端端口>（*.localhost 由系统解析到
    # 127.0.0.1，零外部 DNS 依赖）。此前链接被硬编码成 https 且不带端口，
    # 本地永远拼不出可用地址，才会出现前端自拼 nip.io 的假兜底。
    PUBLISH_URL_SCHEME: str = Field(
        default='https',
        description='发布链接协议：生产 https；本地开发 http'
    )
    PUBLISH_URL_PORT: str = Field(
        default='',
        description='发布链接端口：本地开发填后端端口（如 5001），生产留空'
    )
    PUBLISH_STORE_DIR: str = Field(
        default='',
        description='发布产物存储目录。空则用 BACKEND_DIR/published'
    )
    # 复验要不要在后台线程跑。默认 True：复验要起 Chromium 跑 smoke + AC +
    # same-origin 探针，单次 15s 起、带 AC 更久；同步跑在 POST /api/publish 的
    # 请求线程里会让接口长时间不返回（前端可能先超时报错，而站点其实已经发布）。
    # 置 False 只在测试里用（同步返回便于断言 verify_status）。
    PUBLISH_VERIFY_ASYNC: bool = Field(
        default=True,
        description='发布后复验是否异步执行（False=在发布请求线程内同步跑，仅测试用）'
    )
    PUBLISH_BADGE_ENABLED: bool = Field(
        default=True,
        description='已发布站点是否注入来源 badge（全局开关；作者侧另有 badge_enabled）'
    )

    # ==================== 创意市集配置 ====================

    MARKET_PAGE_SIZE: int = Field(default=20, description='市集列表默认分页大小')
    MARKET_MAX_PAGE_SIZE: int = Field(default=50, description='市集列表分页大小上限')
    MARKET_VISIT_SALT: str = Field(
        default='',
        description='访客指纹加盐。空则回退 JWT_SECRET_KEY 前 16 位'
    )
    MARKET_SITE_URL: str = Field(
        default='',
        description='主站市集地址（badge 跳转目标）。空则按 PUBLISH_APEX + /market 推导'
    )
    # 留言审核：不内置"假装能用"的词库，词表由部署方提供。
    # 为空表示不启用关键词拦截（此时仍受长度、限流、重复内容三道约束）。
    MARKET_COMMENT_BLOCKWORDS: str = Field(
        default='',
        description='留言屏蔽词，逗号分隔。为空则不启用关键词拦截'
    )
    MARKET_COMMENT_MAX_PER_MINUTE: int = Field(
        default=10, description='留言频率上限（条/分钟）'
    )

    @property
    def PUBLISH_STORE_PATH(self) -> Path:
        return Path(self.PUBLISH_STORE_DIR) if self.PUBLISH_STORE_DIR else (self.BACKEND_DIR / "published")

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
