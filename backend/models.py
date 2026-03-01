# -*- coding: utf-8 -*-
"""
数据库模型
定义用户 (User) 和需求 (Requirement) 表结构
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
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


# 初始化数据库（创建所有表）
def init_db():
    """初始化数据库，创建所有表"""
    Base.metadata.create_all(engine)


# 获取数据库会话
def get_db():
    """获取数据库会话，使用完毕后需关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
