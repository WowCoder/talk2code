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
    # ⚠️ 天花板只对「大额度调用」起作用（需求 220 实证）：client 里的取值是
    # min(max(req*2, req+4000), 本值)，小额度调用（500/1000/2000）
    # 由 max(req*2, req+4000) 兜住抬不高，所以把本值从 8000 提到 16000 只影响
    # 本就很大的调用，不会让小调用变慢。
    # 定 8000 时的实证缺陷：修复调用 req=8000 → fallback ≤ 8000
    # → 走「放弃重试」分支，救援**从未生效**，两轮修复都死在 8000。
    # ⚠️ 本值必须**大于主路径的 LLM_MAX_TOKENS**，救援才可能生效（否则
    # fallback ≤ req 一律放弃）。配对关系（2026-10-05 实测）：
    #   deepseek-v4-flash：LLM_MAX_TOKENS=12000 + 本值 16000 → 救援可用（已验证）；
    #   agnes-3.0-flash  ：LLM_MAX_TOKENS=32000 → coder 的 req 就是 32000，
    #                      本值需 >32000 才救得动，而 48000 一档单轮要 ~680s、
    #                      必然撞穿 LLM_TIMEOUT，故对 Agnes 大额度调用「不救援」
    #                      是有意为之 —— 别为了让它"看起来生效"而调高本值。
    LLM_REASONING_FALLBACK_TOKENS: int = Field(
        default=16000, ge=500, le=65500,
        description='推理模型 token 耗尽时的重试额度上限'
    )
    LLM_TIMEOUT: int = Field(default=60, ge=10, le=300, description='LLM 调用超时时间（秒）')
    # 缺陷修复的 LLM 调用要输出整文件 JSON（16k~32k tokens），响应天然更慢，故单独给上限。
    # 此前该值硬编码 150s：LLM 端点慢时单次请求挂 2.5 分钟，前端长时间无 SSE 更新，
    # 观感等同"卡死"（req 146 实测两次 150s 读超时）。
    DEFECT_REPAIR_TIMEOUT: int = Field(default=90, ge=10, le=900, description='缺陷修复 LLM 调用超时时间（秒）')
    # 超时自动重算开关：按 max_tokens ÷ 实测吞吐 反推真实所需时间，取 min(计算值, 上限)。
    # 背景：官方标称 252.7 tok/s 实测达不到 —— 官方端点长输出实测 68.6/88.7/91.8 tok/s
    # （中位 88.7），中转网关生产日志中位 64~78 tok/s，只有标称值的约 1/3。
    # 按标称值算 16K→150s 会继续超时；这里用保守实测值重算。
    DEFECT_REPAIR_TIMEOUT_AUTO: bool = Field(
        default=True,
        description='是否按实测吞吐自动计算缺陷修复超时（True 时忽略 DEFECT_REPAIR_TIMEOUT 固定值）'
    )
    # 实测输出吞吐（token/秒），用于超时反推。保守取实测下四分位，避免乐观值导致超时。
    LLM_MEASURED_TPS: int = Field(
        default=60, ge=10, le=500,
        description='实测输出吞吐 token/秒（官方标称 252.7 实际打不到，实测 68-92）'
    )
    # 超时安全系数与固定开销（首 token 延迟 + 排队）
    DEFECT_REPAIR_TIMEOUT_FACTOR: float = Field(
        default=1.3, ge=1.0, le=3.0,
        description='超时计算安全系数'
    )
    DEFECT_REPAIR_TIMEOUT_OVERHEAD: int = Field(
        default=20, ge=0, le=120,
        description='超时计算固定开销（秒），覆盖首 token 延迟与排队'
    )
    DEFECT_REPAIR_TIMEOUT_MAX: int = Field(
        default=600, ge=60, le=1800,
        description='缺陷修复超时绝对上限（秒），防止重算值过大把节点拖死'
    )
    # 缺陷修复的输出形态：whole_file（旧，输出整个文件，16K~32K tokens，实测需 180~360s）
    # vs diff（新，只输出 unified diff / 局部补丁，1~3K tokens，20~50s）。
    # 提超时只是止血，降输出量才是根治 —— 默认切到 diff。
    DEFECT_REPAIR_OUTPUT_MODE: str = Field(
        default='diff',
        description='缺陷修复输出形态：diff=只输出差异补丁（快），whole_file=输出整个文件（慢）'
    )
    # 长尾熔断：单轮 LLM 超过此预算就中断并按更小的 max_tokens 重试一次。
    # 实测（req 146-159，155 轮）延迟 >60s 的轮次只占 20%，却吃掉 71% 的 LLM 总时间，
    # 且这些慢轮输出很短（中位 583 token）——是空转/抖动，不是"写太长"。
    # 与其干等 300s，不如 45s 断掉重来一次；第二次不熔断，避免误杀真需要长输出的轮次。
    # 设为 0 关闭熔断。
    LLM_SLOW_TURN_TIMEOUT: int = Field(default=45, ge=0, le=300, description='单轮 LLM 熔断预算（秒），0=关闭')
    # 辅助 LLM 调用超时（research / 文件审查 / 记忆整合 / Playwright 用例分析 / 闲聊问答）。
    # 此前这些调用点各自硬编码 timeout=15/20/30：对 reasoning 模型严重偏紧 ——
    # agnes-3.0-flash 会返回 reasoning_content，req 186 多次
    # `Read timed out (read timeout=30)` 并直接把重试额度耗光（辅助调用失败即降级，
    # 不重跑，等于静默丢功能）。统一提到 60s 并由配置控制。
    LLM_AUX_TIMEOUT: int = Field(
        default=60, ge=5, le=300,
        description='辅助 LLM 调用超时时间（秒）'
    )
    # TeamLeader 规划调用（结构化 plan JSON，max_tokens 6000~10000 且开启 thinking）。
    # 与辅助档分开：辅助调用是小输出（500 token 以内），60s 够；规划调用是**长结构化
    # 输出**，实测中位 40s 出头，需求多轮澄清后 prompt 变长就会顶到 60s 上限并连续
    # Read timed out（req 205 实测：同一份 prompt 连撞 3 次 60s，整条链路白等 3 分钟后
    # 判 TL 失败）。给到 150s，约为实测中位的 3.6 倍。
    LLM_PLAN_TIMEOUT: int = Field(
        default=150, ge=30, le=600,
        description='TeamLeader 规划调用超时时间（秒）'
    )
    # AC 脚本翻译调用（把验收条件翻成 Playwright 操作序列）。
    # 单列一档：它不是"小输出"——输出是多步 JSON 脚本，且开 thinking。
    # 实测（需求 206，一批 2 条 AC）约 35s，单条最慢 78s，60s 会周期性撞穿
    # 并触发无谓重试（重试还要再等一轮 thinking，纯浪费）。
    # 注意：这里只管**超时**；预算吃紧的根因是批大小，见 nodes._AC_TRANSLATE_BATCH。
    LLM_AC_TRANSLATE_TIMEOUT: int = Field(
        default=120, ge=15, le=600,
        description='AC 脚本翻译调用超时时间（秒）'
    )
    # Evaluator（验收评估）调用超时。评估要读完整产物并开 thinking，
    # 实测耗时 54~107s，用辅助档的 60s 会误杀。
    # 这两个值此前是裸写的 110 / 150：之所以一直没被
    # `test_llm_aux_timeout.py` 的守卫拦下，是因为那条守卫的正则只匹配
    # 裸写的 15/20/30（当时要清的债），110/150 从缝里漏过去了。
    # 现统一由配置控制，并把守卫放宽到「任何裸数字」。
    LLM_EVALUATOR_TIMEOUT: int = Field(
        default=110, ge=30, le=600,
        description='验收评估 LLM 调用超时时间（秒）'
    )
    # 截断（finish_reason=length）后以 max_tokens 3000→6000 重试的那一档。
    # 输出预算翻倍、耗时近似线性，故比首试档更高。
    LLM_EVALUATOR_RETRY_TIMEOUT: int = Field(
        default=150, ge=30, le=900,
        description='验收评估截断重试的 LLM 调用超时时间（秒）'
    )
    # 极轻量分类/筛选调用（max_tokens ≤ 500，同步阻塞用户输入的意图路由、记忆校验）。
    # 单独一档：这类调用在用户敲下回车的那一刻同步等待，给 60s 会让界面明显卡顿，
    # 但 15s 对 reasoning 模型又不够，折中 30s。
    LLM_CLASSIFY_TIMEOUT: int = Field(
        default=30, ge=5, le=120,
        description='极轻量分类/筛选 LLM 调用超时时间（秒）'
    )
    # 单轮 LLM 的**墙钟上限**（秒，含内部重试）—— 与「单次请求超时」解耦。
    #
    # 为什么需要它：`timeout` 管的是**一次请求**，而用户感知的是**一轮**。
    # 此前 `LLM_TIMEOUT=300` × ( `LLM_MAX_RETRIES=2` + 1 ) = 900s/轮，但代码里
    # 没有任何一处写出过这个 900 —— 它只存在于"两个参数相乘"这个隐式关系里，
    # 调参时极容易看漏（LLM_TIMEOUT 的注释甚至称它为"绝对天花板"，与实现不符）。
    # 现在把墙钟显式化：由它反推「单次尝试超时 × 允许的重试次数」。
    #
    # 定值依据：`llm_traffic.log` 1450 条请求实测 —— P99 60.6s、最大 172s，
    # 单次 300s 已是实测峰值的 1.7 倍。所以保留 1 次重试（吸收端点抖动），
    # 墙钟收敛到 600s；再往上意味着最坏 10 分钟以上任何产出都推不到前端。
    # 设 0 = 不设墙钟上限，退回 "timeout × (retries+1)" 的隐式行为。
    LLM_TURN_MAX_WALL_S: int = Field(
        default=600, ge=0, le=3600,
        description='单轮 LLM 墙钟上限（秒，含内部重试）；0=不限'
    )
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5, description='LLM 调用最大重试次数')

    # 限流（HTTP 429）专用退避。为什么不复用普通退避的 10s 上限：
    # 429 的窗口通常按分钟计，10s 后重试基本必然再撞一次，等于"重试了但一定还失败"，
    # 白白吃掉墙钟时间。免费额度账号被自己的批量任务（评测/多需求并行）打满 429
    # 时尤其明显。服务端若返回 Retry-After，则以服务端为准。
    LLM_RATE_LIMIT_BACKOFF_BASE_S: float = Field(
        default=5.0, ge=0.1, le=60.0,
        description='429 限流的退避基数（秒），第 n 次重试等待 base×2^n（受上限约束）'
    )
    LLM_RATE_LIMIT_BACKOFF_MAX_S: float = Field(
        default=60.0, ge=1.0, le=600.0,
        description='429 限流的退避上限（秒）。按分钟计的限流窗口建议 ≥60'
    )
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

    # ==================== 多模型能力适配（Agnes / DeepSeek 切换） ====================
    # 两个厂商的 thinking 参数格式完全不同，写死任何一种都会在切换时静默失效或报错。
    #   agnes-3.0-flash：OpenAI 格式 = chat_template_kwargs:{"enable_thinking":true}
    #                    Anthropic 格式 = thinking:{"type":"enabled","budget_tokens":N}
    #   deepseek-flash：OpenAI 格式 = thinking:{"type":"enabled"}（SDK 需走 extra_body）
    #                   + 顶层 reasoning_effort；无 budget_tokens
    # 故这里做成配置项，切换模型时只改 .env，不动代码。
    # 注意：不要复用 LLM_PROVIDER —— 那个字段是「协议类型」(openai_compatible /
    # anthropic_compatible)，这里是「厂商」，两者正交，改名避免覆盖。
    LLM_VENDOR: Literal['agnes', 'deepseek', 'auto'] = Field(
        default='auto',
        description='LLM 厂商：agnes / deepseek / auto（auto 按 base_url 推断）'
    )
    LLM_THINKING_FORMAT: Literal['auto', 'openai', 'anthropic'] = Field(
        default='auto',
        description='thinking 参数格式：auto=按厂商自动选，openai / anthropic 强制指定'
    )
    # 思考 token 预算（仅 agnes 的 anthropic 兼容格式生效）。
    # 语义是**上限**：设了就会在思考达到该长度时被截断。
    # 默认 0 = 不限制 —— 保持改动前的行为。coder 强制 thinking=enabled，复杂多文件
    # 生成需要长思考，给它加帽反而会压低代码质量，与本轮「提质」目标相悖。
    # 实测参考：把一次简单任务的思考从 675 字符压到 330 字符，延迟 3.49s → 2.79s。
    # 想拿延迟换质量时再调小（如 1024）。
    LLM_THINKING_BUDGET_TOKENS: int = Field(
        default=0, ge=0, le=32768,
        description='思考 token 预算上限（仅 agnes anthropic 格式生效，0=不限制）'
    )
    # DeepSeek 硬性要求：请求携带 tools 时，历史轮次的 reasoning_content 必须完整回传，
    # 否则 API 返回 400。Agnes 实测不要求。切 DeepSeek 后必须为 True。
    LLM_THINKING_ECHO_REASONING: bool = Field(
        default=False,
        description='是否回传历史 reasoning_content（DeepSeek + tools + thinking 场景必须为 True）'
    )
    # DeepSeek 思考模式下 temperature / presence_penalty / frequency_penalty 不生效
    # （不报错但无效）；top_p 仅 0.95~1.0 有效。开启后自动剔除无效参数，避免误以为调参生效。
    LLM_STRIP_INVALID_PARAMS: bool = Field(
        default=True,
        description='思考模式下是否剔除厂商不支持的采样参数（temperature/top_p 等）'
    )

    # ==================== Skills 注入预算 ====================
    # 现状：正则 OR 匹配全部命中全注入，无数量上限 —— 五子棋一次命中 6 个 ≈15.8K 字符。
    # 硬上限 2 个：名额①常驻 core（generic + anti-ai-slop 合并），名额②场景 skill 取 priority 最高 1 个。
    # 注意：若只加数量上限而不合并 always 类，generic(100)/anti-ai-slop(100) 会吃满 2 个名额，
    # 场景 skill 与 UI skill 全部落选 —— 反而更糟。故二者必须同时生效。
    SKILL_MAX_COUNT: int = Field(
        default=2, ge=1, le=10,
        description='单次注入的 skill 数量硬上限（含常驻位）'
    )
    SKILL_TOKEN_BUDGET: int = Field(
        default=3000, ge=500, le=20000,
        description='全部 skill 合计 token 预算，超出按 priority 从低往高丢弃'
    )

    # ==================== Evaluator 视觉输入 ====================
    # 截图每轮都已生成在 .task/evaluator/screenshot.png，但深度评估没把它传给 LLM，
    # 导致 ui_quality 是"读 CSS 代码猜的"盲评。这里配置是否/如何把视觉证据传给评估模型。
    #   dom_css  ：不传图，提取 DOM + getComputedStyle 关键属性（默认，零外部依赖，值更准）
    #   image_url：公网 URL（Agnes 仅支持此方式；需 PREVIEW_PUBLIC_BASE_URL）
    #   base64   ：data:image/png;base64,...（DeepSeek 支持，本地文件直传）
    #   auto     ：有公网 URL 用 url，否则本地文件用 base64，都不可用降级 dom_css
    EVALUATOR_VISION_MODE: Literal['dom_css', 'image_url', 'base64', 'auto'] = Field(
        default='dom_css',
        description='evaluator 视觉证据承载方式'
    )
    EVALUATOR_VISION_DETAIL: Literal['low', 'high', 'original', 'auto'] = Field(
        default='low',
        description='图片细节级别（low=缩放到 512x512，更快更省 token）'
    )
    EVALUATOR_VISION_MAX_IMAGES: int = Field(
        default=2, ge=1, le=10,
        description='单次评估最多传几张图'
    )
    EVALUATOR_SCREENSHOT_MAX_BYTES: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024,
        description='截图超过此字节数则该传图模式不可用，自动降级为 dom_css'
    )
    # 视觉硬伤（对比度 <3:1 / 点区 <24px / 字号 <10px）是否作为确定性缺陷送入修复循环。
    # 关闭后视觉证据仍会进 evaluator 的评估内容，但不会阻塞交付 —— 即回到
    # 「UI 丑但不拦截」的旧行为。默认开启：页面美观是明确的产品诉求，
    # 只有让不合格可判定，预置模板的收益才落得下来。
    UI_LINT_AS_DEFECT: bool = Field(
        default=True,
        description='是否把浏览器实测的视觉硬伤转成确定性缺陷（进入 defect_repair）'
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

    # ==================== 演示模式配置 ====================

    # 演示帐号是 users 表里的一行真实用户（由 manage.py demo init 创建）。
    # 演示模式以它的身份登录，JWT 额外带 demo claim，写操作被 before_request 守卫拦下。
    # 之所以复用真实用户身份：所有按 user_id 过滤的既有查询无需任何改动。
    DEMO_USERNAME: str = Field(
        default='demo',
        description='演示帐号用户名（真实存在于 users 表）'
    )

    # ==================== 邀请码配置 ====================

    # 邀请码有效期：一次性 + 有限期，比永久码多一层「码扩散后自动失效」的兜底
    INVITE_CODE_TTL_DAYS: int = Field(default=7, ge=1, le=365, description='邀请码有效期（天）')
    # 同一邮箱的重复申请冷却时间。0=不限制
    INVITE_REQUEST_COOLDOWN_HOURS: int = Field(
        default=24, ge=0, le=720,
        description='同一邮箱重复提交申请的冷却时间（小时）'
    )
    INVITE_CODE_PREFIX: str = Field(default='T2C-', description='邀请码前缀')

    # ==================== 邮件配置（邀请码发放） ====================

    # 默认关闭：不配置 SMTP 也必须能完整跑通审批流（后台直接展示码明文供手动复制）。
    # 发信与审批解耦 —— 发信失败不会让审批失败，只记 delivery_status=failed 供重发。
    SMTP_ENABLED: bool = Field(default=False, description='是否启用邮件发送')
    SMTP_HOST: str = Field(default='', description='SMTP 服务器地址')
    SMTP_PORT: int = Field(default=587, ge=1, le=65535, description='SMTP 端口')
    SMTP_USER: str = Field(default='', description='SMTP 用户名')
    SMTP_PASSWORD: str = Field(default='', description='SMTP 密码')
    SMTP_FROM: str = Field(default='', description='发件人地址，为空时回退 SMTP_USER')
    SMTP_USE_TLS: bool = Field(default=True, description='是否使用 STARTTLS')

    # ==================== 管理后台配置 ====================

    # 后台 token 走 Authorization header（不进 cookie），有效期比前台短得多。
    # 短有效期是「token 存 sessionStorage」这一取舍的主要补偿手段。
    ADMIN_TOKEN_EXPIRES_HOURS: int = Field(
        default=2, ge=1, le=24,
        description='后台管理员 token 有效期（小时）'
    )

    @property
    def ADMIN_TOKEN_EXPIRES(self) -> timedelta:
        return timedelta(hours=self.ADMIN_TOKEN_EXPIRES_HOURS)

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
