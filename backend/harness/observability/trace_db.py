# -*- coding: utf-8 -*-
"""轨迹库（agent_events / trace_message_blobs / agent_payloads）的连接解析。

为什么需要单独一层
------------------
轨迹与业务数据（需求、记忆）的生命周期完全不同：轨迹是**过程留痕**，业务数据是
**产品资产**。两者共用一条连接时，「谁在写」就没有结构上的约束，只能靠调用方自觉
—— 评测进程正是靠一句 `requirement_id = 题号`，把 1400 条事件写进了真实需求的
轨迹里，事后才被发现。

把「取轨迹库的 session」收成一个点，由环境变量 `TALK2CODE_TRACE_DB` 决定：
- 不设（生产默认）→ 返回 `models.SessionLocal`，行为与之前一字不差
- 设了 → 指向该库；评测靠它把轨迹写进 `eval/runs/<run_id>/trace.db`

于是「评测不污染运营库」是**结构上不可能**，而不是每次记得加过滤条件。

为什么不复用 settings.IS_POSTGRES 判断方言
------------------------------------------
它是 `bool(self.DATABASE_URL)`（`config.py`），判断的是「有没有配 URL」而不是
「是不是 PG」。给 `DATABASE_URL` 塞一个 sqlite 串会让它误判为 True，于是
`check_same_thread=False` 与 WAL 都不会被注册 —— 多线程下直接报
`SQLite objects created in a thread can only be used in that same thread`，
写冲突则报 `database is locked`。这里自己按 URL 前缀判断，不依赖那个属性。
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

ENV_VAR = "TALK2CODE_TRACE_DB"

# 轨迹库需要的表。不整库 create_all：评测是空库起跑，建 20 张表只会让
# 单次运行的文件白白大一倍，且会让「这张表本该在主库」这件事变得含混。
_TRACE_TABLES = ("agent_events", "trace_message_blobs", "agent_payloads", "agent_traces")

_lock = threading.Lock()
_engine = None
_session_factory = None


def trace_url() -> str:
    """配置的轨迹库 URL；空串表示「跟随主库」。"""
    return (os.getenv(ENV_VAR) or "").strip()


def is_redirected() -> bool:
    """轨迹是否写在独立库（评测模式下为 True）。"""
    return bool(trace_url())


def _is_sqlite(url: str) -> bool:
    return url.split(":", 1)[0].lower().startswith("sqlite")


def _build_engine(url: str):
    from sqlalchemy import create_engine, event

    kwargs = {}
    if _is_sqlite(url):
        # 评测链路里预览/AC 校验可能在子线程里触发埋点，必须放开同线程限制
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)

    if _is_sqlite(url):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - 由 DBAPI 回调
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    _ensure_schema(eng)
    return eng


def _ensure_schema(eng) -> None:
    """确保目标库有轨迹表。

    评测是独立进程，不会跑 `init_db()`（那是应用启动时的事）。表不存在时
    埋点会静默降级 —— 跑完 20 分钟才发现一条轨迹都没落，代价太高。
    """
    try:
        from models import Base
        tables = [t for t in Base.metadata.sorted_tables if t.name in _TRACE_TABLES]
        Base.metadata.create_all(bind=eng, tables=tables, checkfirst=True)
    except Exception as e:  # pragma: no cover - 建表失败不该阻断评测
        logger.warning(f"[TraceDB] 轨迹表检查失败（埋点可能丢失）: {e}")


def trace_engine():
    """轨迹库引擎。未配置 `TALK2CODE_TRACE_DB` 时返回主库引擎。"""
    global _engine
    url = trace_url()
    if not url:
        from models import engine as main_engine
        return main_engine
    with _lock:
        if _engine is None:
            _engine = _build_engine(url)
            logger.info(f"[TraceDB] 轨迹写入改道: {url}")
        return _engine


def trace_session():
    """轨迹写入用的 session。未配置时等同 `models.SessionLocal()`。"""
    global _session_factory
    url = trace_url()
    if not url:
        from models import SessionLocal
        return SessionLocal()
    # 先取 engine、再进锁：trace_engine() 自己要拿同一把锁，
    # 持锁去调它会直接死锁 —— 表现为第一次埋点就静默挂住，不报错。
    eng = trace_engine()
    with _lock:
        if _session_factory is None:
            from sqlalchemy.orm import sessionmaker
            _session_factory = sessionmaker(bind=eng, expire_on_commit=False)
        factory = _session_factory
    return factory()


def reset_for_tests() -> None:
    """清掉缓存的引擎/工厂 —— 仅供测试在切换环境变量后重新解析。"""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
        _engine = None
        _session_factory = None
