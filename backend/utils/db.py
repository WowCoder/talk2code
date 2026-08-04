# -*- coding: utf-8 -*-
"""
数据库 Session 上下文管理器
统一管理 SQLAlchemy Session 生命周期，消除手动 try/finally 样板代码。
"""

from contextlib import contextmanager
from typing import Generator

from models import SessionLocal


@contextmanager
def get_db() -> Generator:
    """
    获取数据库 Session（只读或手动提交）。

    用法：
        with get_db() as db:
            user = db.query(User).filter_by(id=uid).first()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transactional_db() -> Generator:
    """
    获取数据库 Session（自动提交/回滚）。

    用法：
        with transactional_db() as db:
            db.add(new_user)
            # 退出时自动 commit，异常时自动 rollback
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
