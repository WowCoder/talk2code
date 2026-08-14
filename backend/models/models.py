# -*- coding: utf-8 -*-
"""
数据库模型
定义用户 (User) 和需求 (Requirement) 表结构
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Float, text, Index
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

# 创建会话工厂
SessionLocal = sessionmaker(bind=engine)

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

    # 迁移：为已有 requirements 表添加软删除列（SQLite 兼容）
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE requirements ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
            conn.commit()
    except Exception:
        pass  # 列已存在

    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE requirements ADD COLUMN deleted_at DATETIME"))
            conn.commit()
    except Exception:
        pass  # 列已存在

    # 修复已有数据：将 NULL 的 is_deleted 统一设为 0（非删除状态）
    try:
        with engine.connect() as conn:
            conn.execute(text("UPDATE requirements SET is_deleted = 0 WHERE is_deleted IS NULL"))
            conn.commit()
    except Exception:
        pass

    # 迁移：为已有 requirements 表添加 error_message 列
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE requirements ADD COLUMN error_message TEXT"))
            conn.commit()
    except Exception:
        pass  # 列已存在


