# -*- coding: utf-8 -*-
"""
持久化集成测试 —— 验证 MemoryStore / CheckpointManager / Tracer
注入 db_session 后真的读写 SQLite，重启后数据保留。

使用 pytest tmp_path 创建临时 SQLite 文件，每个测试独立隔离。
"""

import json
import pytest
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import func

# ---- 最小化 model 复用（不从 models.models 导入 engine，避免影响主 DB） ----
_TestBase = declarative_base()


class _AgentMemory(_TestBase):
    __tablename__ = "agent_memories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    requirement_id = Column(Integer, nullable=True)
    memory_type = Column(String(32), default="domain_knowledge")
    fact = Column(Text, nullable=False)
    importance = Column(Float, default=0.5)
    access_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    last_accessed_at = Column(DateTime, onupdate=func.now())


class _AgentTrace(_TestBase):
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


class _CheckpointRecord(_TestBase):
    __tablename__ = "agent_checkpoints"
    id = Column(Integer, primary_key=True, autoincrement=True)
    checkpoint_id = Column(String(64), unique=True, nullable=False, index=True)
    requirement_id = Column(Integer, nullable=False, index=True)
    node_name = Column(String(64), nullable=False)
    state_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())


@pytest.fixture
def db(tmp_path):
    """创建临时 SQLite + session，每个测试独立"""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    _TestBase.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


# ---- MemoryStore 持久化 ----

class TestMemoryStorePersistence:

    def test_remember_persists_to_db(self, db):
        from harness.state.memory_store import MemoryStore
        store = MemoryStore(db_session=db, llm_client=None)
        store.remember(1, "用户喜欢暗色主题", "user_preference", 0.8)

        rows = db.query(_AgentMemory).filter_by(user_id=1).all()
        assert len(rows) == 1
        assert rows[0].fact == "用户喜欢暗色主题"
        assert rows[0].importance == 0.8

    def test_recall_after_db_roundtrip(self, db):
        """写入 DB 后，新建一个 MemoryStore 实例读回来（模拟重启）"""
        from harness.state.memory_store import MemoryStore
        # 写入
        store1 = MemoryStore(db_session=db, llm_client=None)
        store1.remember(1, "使用 Tailwind CSS", "domain_knowledge", 0.7)
        store1.remember(1, "偏好蓝色主题", "user_preference", 0.9)

        # 新实例读回（模拟重启）
        store2 = MemoryStore(db_session=db, llm_client=None)
        memories = store2._get_user_memories(1)
        facts = {m["fact"] for m in memories}
        assert "使用 Tailwind CSS" in facts
        assert "偏好蓝色主题" in facts

    def test_similar_memory_boosts_importance_in_db(self, db):
        from harness.state.memory_store import MemoryStore
        store = MemoryStore(db_session=db, llm_client=None)
        store.remember(1, "the user prefers the blue theme design", "user_preference", 0.7)
        store.remember(1, "the user prefers the blue theme design style", "user_preference", 0.7)

        row = db.query(_AgentMemory).filter_by(user_id=1).first()
        assert row.importance == 0.75  # 0.7 + 0.05

    def test_decay_deletes_expired_in_db(self, db):
        from harness.state.memory_store import MemoryStore
        store = MemoryStore(db_session=db, llm_client=None)
        store.remember(1, "old fact", "domain_knowledge", 0.05)  # below 0.1 threshold
        store.decay()
        rows = db.query(_AgentMemory).filter_by(user_id=1).all()
        # decay 会把 0.05 * 0.95 = 0.0475，低于 0.1 会被删除
        assert len(rows) == 0


# ---- CheckpointManager 持久化 ----

class TestCheckpointPersistence:

    def test_save_and_resume_via_db(self, db):
        from harness.state.checkpoint import CheckpointManager
        cm = CheckpointManager(db_session=db)
        state = {"requirement_id": 42, "current_step": "tool_coder", "dialogue_history": ["hi"]}
        cp_id = cm.save(42, "tool_coder", state)

        assert cp_id.startswith("cp_42_")

        # 新实例 resume
        cm2 = CheckpointManager(db_session=db)
        resumed = cm2.resume(42)
        assert resumed is not None
        assert resumed["dialogue_history"] == ["hi"]

    def test_clear_removes_from_db(self, db):
        from harness.state.checkpoint import CheckpointManager
        cm = CheckpointManager(db_session=db)
        cm.save(42, "tool_coder", {"step": "mid"})

        cm.clear(42)
        assert cm.resume(42) is None
        rows = db.query(_CheckpointRecord).filter_by(requirement_id=42).all()
        assert len(rows) == 0

    def test_overwrite_keeps_latest(self, db):
        """同一 requirement_id 多次 save 只保留最新"""
        from harness.state.checkpoint import CheckpointManager
        cm = CheckpointManager(db_session=db)
        cm.save(42, "planner", {"step": "1"})
        cm.save(42, "tool_coder", {"step": "2"})

        rows = db.query(_CheckpointRecord).filter_by(requirement_id=42).all()
        assert len(rows) == 1
        assert json.loads(rows[0].state_json)["step"] == "2"

    def test_resume_returns_none_for_completed(self, db):
        from harness.state.checkpoint import CheckpointManager
        cm = CheckpointManager(db_session=db)
        cm.save(42, "end", {"step": "done"})
        assert cm.resume(42) is None  # end 状态不恢复


# ---- Tracer 持久化 ----

class TestTracerPersistence:

    def test_end_trace_persists_to_db(self, db):
        from harness.observability.tracer import Tracer
        from harness.observability.cost import CostTracker
        cost = CostTracker()
        tracer = Tracer(db_session=db, cost_tracker=cost)
        trace = tracer.start_trace(1, 100)
        span = tracer.start_span(trace.trace_id, "test_span", metadata={"tokens": 50})
        tracer.end_span(span)
        cost.record(trace.trace_id, 100, 200, "qwen-plus")
        tracer.end_trace(trace.trace_id)

        row = db.query(_AgentTrace).filter_by(trace_id=trace.trace_id).first()
        assert row is not None
        # end_trace 用 span metadata 的 tokens 聚合（50），cost 由 cost_tracker 提供
        assert row.total_tokens == 50
        assert row.data["span_count"] == 1
        assert row.total_cost > 0  # cost_tracker 已 record 过

    def test_get_trace_after_restart(self, db):
        """跨实例 get_trace：模拟重启后查历史"""
        from harness.observability.tracer import Tracer
        from harness.observability.cost import CostTracker

        # 写入
        cost = CostTracker()
        tracer1 = Tracer(db_session=db, cost_tracker=cost)
        trace = tracer1.start_trace(1, 100)
        tracer1.end_trace(trace.trace_id)

        # 新实例查询
        tracer2 = Tracer(db_session=db)
        loaded = tracer2.get_trace(trace.trace_id)
        assert loaded is not None
        assert loaded.requirement_id == 1

    def test_recent_traces_still_works_without_db(self):
        """无 db 注入时 recent_traces 走内存（向后兼容）"""
        from harness.observability.tracer import Tracer
        tracer = Tracer()
        traces = tracer.recent_traces(limit=5)
        assert isinstance(traces, list)
