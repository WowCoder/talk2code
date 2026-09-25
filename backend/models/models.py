# -*- coding: utf-8 -*-
"""
数据库模型
定义用户 (User) 和需求 (Requirement) 表结构
"""

from datetime import datetime
from sqlalchemy import create_engine, event, Column, Integer, String, Text, DateTime, Date, Boolean, ForeignKey, JSON, Float, SmallInteger, text, Index, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from config import settings

# 创建数据库引擎（支持 PostgreSQL + SQLite 自动切换）
engine = create_engine(
    settings.DATABASE_URI,
    connect_args=settings.DATABASE_CONNECT_ARGS,
    **settings.DATABASE_ENGINE_KWARGS,
)

if not settings.IS_POSTGRES:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record):
        """SQLite 并发写防护（TaskQueue 多 worker + 请求线程共享库文件）：
        - WAL 模式：读写不互斥，避免 "database is locked"
        - busy_timeout：写冲突时等待而非立即抛错
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

# 创建会话工厂
# expire_on_commit=False：提交后实例保留属性值（避免 detached 后访问触发 refresh 报错）
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# 基类
Base = declarative_base()


class User(Base):
    """
    用户表
    存储用户名、密码哈希、创建时间
    """
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    create_time = Column(DateTime, default=datetime.utcnow)

    # 关联需求
    requirements = relationship('Requirement', back_populates='user', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.username}>'


class Requirement(Base):
    """
    需求表
    存储用户提交的产品需求、AI 对话历史、生成的代码文件
    """
    __tablename__ = 'requirements'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    title = Column(String(500), nullable=False)  # 需求标题/摘要
    content = Column(Text, nullable=False)  # 完整需求内容
    status = Column(String(20), default='pending')  # pending/processing/finished/failed
    create_time = Column(DateTime, default=datetime.utcnow)
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 失败原因（仅 status='failed' 时有值，持久化错误详情供前端展示）
    error_message = Column(Text, nullable=True)

    # 软删除（回收站）
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)

    # AI 对话历史 (JSON 格式)
    # 结构：[{"role": "user/agent", "name": "研究员/产品经理/...", "content": "...", "timestamp": "..."}]
    dialogue_history = Column(JSON, default=list)

    # 代码文件 (JSON 格式)
    # 结构：[{"filename": "index.html", "content": "...", "status": "pending/generating/completed", "total_lines": 0}]
    code_files = Column(JSON, default=list)

    # 关联用户
    user = relationship('User', back_populates='requirements')

    def __repr__(self):
        return f'<Requirement {self.id}: {self.title[:50]}...>'


class AgentMemory(Base):
    """Agent 长期记忆表"""
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requirement_id = Column(Integer, nullable=True)
    memory_type = Column(String(32), default="domain_knowledge")
    fact = Column(Text, nullable=False)
    importance = Column(Float, default=0.5)
    access_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    last_accessed_at = Column(DateTime, onupdate=func.now())


class AgentMemoryV2(Base):
    """Agent 结构化记忆表 v2 —— LLM 反思后的任务经验

    每条记忆对应一个已完成的任务，包含 LLM 的事后反思（3 问自答）。
    支持持久化（跨进程重启保留）和合并（定期去重）。
    """
    __tablename__ = "agent_memories_v2"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requirement = Column(Text, nullable=False)
    complexity = Column(String(16), default="standard")
    code_summary = Column(Text, default="")
    rating = Column(Float, default=7.0)

    # LLM 反思字段（3 问自答）
    reflection = Column(Text, default="")       # "这次和预期有什么不同？为什么？"
    lesson = Column(Text, default="")           # "下次做类似任务，我会怎么做？"
    reusable_pattern = Column(Text, default="") # "有没有可复用的代码模式？"

    # 元数据
    tags = Column(JSON, default=list)           # ["localStorage", "CRUD", "表单"]
    importance = Column(Float, default=0.5)     # LLM 判断的重要性 0-1
    access_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())

    # 生命周期管理
    merged_from = Column(JSON, default=list)    # 合并来源记忆 ID 列表
    superseded = Column(Boolean, default=False) # 是否被更新的记忆替代

    def __repr__(self):
        return f"<MemoryV2 {self.id}: {self.requirement[:50]}... rating={self.rating}>"


class MemoryHit(Base):
    """记忆注入记账表 —— "注入即记账"，让每条记忆的价值可归因。

    在此之前，判断记忆有没有用只有 eval 整体通过率这一个粗糙指标，无法
    归因到单条记忆：100 条记忆里是哪 5 条在拖后腿，没有事实依据。
    本表把每次注入记成一行 pending，任务出结果后回填 outcome，于是每条
    记忆都能算出"被注入 N 次、其中 M 次任务通过"。

    归因边界（避免过度解读）：一条记忆被注入到通过的任务里，不等于任务
    通过是它的功劳 —— 这里记的是相关性不是因果性。证明因果要靠 A/B
    （eval --with-memory），本表只负责把相关性变成可查询的事实。
    """
    __tablename__ = "memory_hits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, ForeignKey("agent_memories_v2.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    requirement_id = Column(Integer, nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)  # eval 的 run_id，便于按批次聚合

    # 注入快照
    inject_position = Column(Integer, default=0)   # 在注入块中的排序（1..6）
    inject_tokens = Column(Integer, default=0)     # 该条注入的估算 token 数
    injected_at = Column(DateTime, default=func.now())

    # 结果回填（任务结束后由 resolve_hits 写入）
    outcome = Column(String(16), default="pending", index=True)  # pending / pass / fail
    resolved_at = Column(DateTime, nullable=True)


class AgentTrace(Base):
    """Agent 链路追踪表"""
    __tablename__ = "agent_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(32), unique=True, nullable=False, index=True)
    requirement_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    data = Column(JSON, default=dict)
    total_tokens = Column(Integer, default=0)
    total_cost = Column(Float, default=0.0)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())


class CheckpointRecord(Base):
    """工作流检查点表 —— 支持断点恢复（每个 requirement 保留最近一条）"""
    __tablename__ = "agent_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    checkpoint_id = Column(String(64), unique=True, nullable=False, index=True)
    requirement_id = Column(Integer, nullable=False, index=True)
    node_name = Column(String(64), nullable=False)  # team_leader / tool_coder / tool_executor
    state_json = Column(Text, nullable=False)  # JSON 序列化的 AgentState
    created_at = Column(DateTime, default=func.now())


class AgentMemoryVector(Base):
    """Agent 记忆向量表 —— pgvector 存储 BGE-M3 embeddings

    与 agent_memories_v2 表一一对应（通过 memory_id 外键）。
    支持增量 upsert（不再全量重建索引），重启后向量不丢失。
    使用 HNSW 索引加速近似最近邻搜索。
    """
    __tablename__ = "agent_memory_vectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, ForeignKey("agent_memories_v2.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    # embedding 列由 init_db() 在 PG 上用 raw SQL 创建（VECTOR(1024) 类型）
    # SQLite 回退时用普通 TEXT 列存 JSON
    embedding_text = Column(Text, nullable=True)  # SQLite 回退存储
    created_at = Column(DateTime, default=func.now())


class PublishedBundle(Base):
    """不可变产物（内容寻址）。

    详见 docs/design/publish-and-sandbox.md §4。
    """
    __tablename__ = "published_bundles"

    content_hash = Column(String(64), primary_key=True)
    store_key = Column(String(255), nullable=False)
    size_bytes = Column(Integer, default=0)
    file_count = Column(Integer, default=0)
    entry = Column(String(64), default="index.html")
    created_at = Column(DateTime, default=func.now())


class PublishedSite(Base):
    """发布槽位（可变指针）。

    - slug 与内容解耦：同一个 slug 可指向不同 version 的 bundle
    - requirement_id 不加外键：需求删除后站点仍可保留
    - verified_at / verify_status 由 Ship C 发布后复验写入
    """
    __tablename__ = "published_sites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requirement_id = Column(Integer, nullable=True, index=True)
    title = Column(String(500), default="")
    runtime_tier = Column(SmallInteger, default=0)            # 0=static（v1 唯一值）
    visibility = Column(String(16), default="unlisted")       # public / unlisted
    current_hash = Column(String(64), ForeignKey("published_bundles.content_hash"), nullable=True)
    version = Column(Integer, default=1)
    view_count = Column(Integer, default=0)
    verified_at = Column(DateTime, nullable=True)
    verify_status = Column(String(16), default="pending")     # pending / ok / degraded
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # ---- 创意市集（marketplace）----
    # ⚠️ market_visible 默认 False 是**不可回退约束**：上架必须 opt-in。
    # 现状所有站点都是 unlisted，用户的预期是"我只是在分享一个链接"；
    # 默认搬进公共列表是对既有预期的背叛，且产物中可能含私人内容。
    market_visible = Column(Boolean, default=False, nullable=False)
    market_listed_at = Column(DateTime, nullable=True)        # 上架时间 = 热度的时间基准
    badge_enabled = Column(Boolean, default=True, nullable=False)
    author_note = Column(String(120), default="")
    # 市集分类（Ship 3）。空串 = 未分类，列表里归入「全部」。
    category = Column(String(16), default="")


class SiteVisitDedup(Base):
    """市集去重访客账本。

    只存指纹哈希，不存 IP / UA 明文。指纹 = sha256(salt|ip|ua|day)[:32]，
    按天分桶，使账本天然过期、可定期清理。唯一约束就是去重实现本身，
    不依赖"先查再插"的时序（并发下先查再插必然漏判）。
    """
    __tablename__ = "site_visit_dedup"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(Integer, ForeignKey("published_sites.id"), nullable=False, index=True)
    fingerprint = Column(String(32), nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("site_id", "fingerprint", "day", name="uq_site_visit_dedup"),)


class SiteLike(Base):
    """市集点赞。唯一约束兜底重复点赞，前端禁用按钮只是体验优化。"""
    __tablename__ = "site_likes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(Integer, ForeignKey("published_sites.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("site_id", "user_id", name="uq_site_like"),)


class SiteComment(Base):
    """市集留言（Ship 2）。

    **不支持匿名留言**：匿名区没有可追责主体，是垃圾内容的温床。删除一律软删
    （`is_deleted`）而非物理删 —— 保留时间戳与归属，便于事后追溯与申诉。
    """
    __tablename__ = "site_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(Integer, ForeignKey("published_sites.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    body = Column(String(200), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_comment_site_created", "site_id", "created_at"),)


class UserFollow(Base):
    """市集关注关系（Ship 2）。

    只记「谁关注了谁」这一条有向边；粉丝数/关注数一律实时聚合，不做冗余计数列
    —— 冗余列会在软删、并发下漂移，而这里的量级完全撑得住 COUNT。
    """
    __tablename__ = "user_follows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    followee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("follower_id", "followee_id", name="uq_user_follow"),)


def _sql_false() -> str:
    return "FALSE" if settings.IS_POSTGRES else "0"


def _sql_true() -> str:
    return "TRUE" if settings.IS_POSTGRES else "1"


def _add_column(table: str, column: str, *, pg: str, sqlite: str) -> None:
    """给既有表补列。DDL **必须按方言分别给出**，不能只写一份 SQLite 语法。

    为什么：SQLite 认 `BOOLEAN DEFAULT 0` / `DATETIME`，PostgreSQL 会拒绝
    （布尔列收到整型默认值 → DatatypeMismatch；`DATETIME` 类型不存在）。
    而这类失败长期被 `except Exception: pass` 当成"列已存在"抹平 —— 列其实
    根本没建出来，直到运行时以 UndefinedColumn 炸在某个离迁移很远的查询上。

    另一条：失败必须显式 rollback。连接回池后若仍处在 aborted 事务里，
    后续每条语句都会跟着失败，错误会被误读成"别的列也建不了"。
    """
    ddl = pg if settings.IS_POSTGRES else sqlite
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            conn.commit()
    except Exception:
        try:
            with engine.connect() as conn:
                conn.rollback()
        except Exception:
            pass  # 列已存在是唯一可忽略的失败


def _backfill_null(table: str, column: str, literal: str) -> None:
    """把既有行的 NULL 新列回填成默认值（ALTER 的 DEFAULT 不改写既有行）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text(f"UPDATE {table} SET {column} = {literal} WHERE {column} IS NULL"))
            conn.commit()
    except Exception:
        try:
            with engine.connect() as conn:
                conn.rollback()
        except Exception:
            pass


# 初始化数据库（创建所有表）
def init_db():
    """初始化数据库，创建所有表并执行迁移"""
    Base.metadata.create_all(engine)

    # PostgreSQL 专用：启用 pgvector 扩展 + 创建 vector 列 + HNSW 索引
    if settings.IS_POSTGRES:
        with engine.connect() as conn:
            # 启用 pgvector 扩展
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()

            # 为 agent_memory_vectors 添加 vector 类型列（如果不存在）
            try:
                conn.execute(text("ALTER TABLE agent_memory_vectors ADD COLUMN embedding vector(1024)"))
                conn.commit()
            except Exception:
                pass  # 列已存在

            # 创建 HNSW 索引（余弦距离）
            try:
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_memory_vectors_hnsw
                    ON agent_memory_vectors
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """))
                conn.commit()
            except Exception:
                pass  # 索引已存在

    # 迁移：为已有表补列。create_all 只建新表，不给既有表加列。
    # ⚠️ 一律走 _add_column()，不要裸写 try/except + 方言不分的 DDL（见其注释）。
    _add_column("requirements", "is_deleted", pg="BOOLEAN DEFAULT FALSE", sqlite="BOOLEAN DEFAULT 0")
    _add_column("requirements", "deleted_at", pg="TIMESTAMP", sqlite="DATETIME")
    _add_column("requirements", "error_message", pg="TEXT", sqlite="TEXT")

    # 修复已有数据：将 NULL 的 is_deleted 统一设为假（非删除状态）
    _backfill_null("requirements", "is_deleted", _sql_false())

    # 迁移：创意市集。
    _add_column("published_sites", "market_visible", pg="BOOLEAN DEFAULT FALSE", sqlite="BOOLEAN DEFAULT 0")
    _add_column("published_sites", "market_listed_at", pg="TIMESTAMP", sqlite="DATETIME")
    _add_column("published_sites", "badge_enabled", pg="BOOLEAN DEFAULT TRUE", sqlite="BOOLEAN DEFAULT 1")
    _add_column("published_sites", "author_note", pg="VARCHAR(120) DEFAULT ''", sqlite="VARCHAR(120) DEFAULT ''")
    _add_column("published_sites", "category", pg="VARCHAR(16) DEFAULT ''", sqlite="VARCHAR(16) DEFAULT ''")

    # 回填：既有行的新列是 NULL，会漏过 market_visible.is_(True) 过滤，
    # 也会让 badge_enabled 判成假 → 必须显式落默认值。
    _backfill_null("published_sites", "category", "''")
    _backfill_null("published_sites", "market_visible", _sql_false())
    _backfill_null("published_sites", "badge_enabled", _sql_true())


