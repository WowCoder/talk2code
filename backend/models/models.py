# -*- coding: utf-8 -*-
"""
数据库模型
定义用户 (User) 和需求 (Requirement) 表结构
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Float, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from config import DATABASE_URI

# 创建数据库引擎
engine = create_engine(DATABASE_URI, connect_args={'check_same_thread': False})

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
    node_name = Column(String(64), nullable=False)  # planner / tool_coder / tool_executor
    state_json = Column(Text, nullable=False)  # JSON 序列化的 AgentState
    created_at = Column(DateTime, default=func.now())


# 初始化数据库（创建所有表）
def init_db():
    """初始化数据库，创建所有表并执行迁移"""
    Base.metadata.create_all(engine)

    # 迁移：为已有 requirements 表添加软删除列
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


# 获取数据库会话
def get_db():
    """获取数据库会话，使用完毕后需关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
