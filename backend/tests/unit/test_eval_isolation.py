# -*- coding: utf-8 -*-
"""轨迹库隔离 —— 评测写自己的 SQLite，运营库零写入。

背景：评测进程此前用的是运营库同一条连接，靠 `requirement_id = 题号`（t01→1）
把事件写进了真实需求的轨迹里，后台「全部时间」视图会把这些行张冠李戴地显示出来。

隔离靠 `TALK2CODE_TRACE_DB` 单点改道（见 harness/observability/trace_db.py）。
这里钉住三件事：
  1. 不设变量时行为与之前一字不差（否则会悄悄改掉生产写入路径）
  2. 设了之后，事件确实落在目标库、主库一条都没有
  3. 目标库在子线程里也能写（SQLite 必须放开同线程限制）
"""

import threading

import pytest


@pytest.fixture
def redirected(tmp_path, monkeypatch):
    """把轨迹库指到临时 SQLite，用例结束恢复。"""
    from harness.observability import trace_db

    url = f"sqlite:///{tmp_path}/trace.db"
    monkeypatch.setenv(trace_db.ENV_VAR, url)
    trace_db.reset_for_tests()
    yield url
    trace_db.reset_for_tests()


@pytest.fixture
def followed(monkeypatch):
    """不设变量 —— 轨迹跟随主库。"""
    from harness.observability import trace_db

    monkeypatch.delenv(trace_db.ENV_VAR, raising=False)
    trace_db.reset_for_tests()
    yield
    trace_db.reset_for_tests()


class TestDefaultFollowsMainDb:
    def test_not_redirected(self, followed):
        from harness.observability import trace_db
        assert trace_db.is_redirected() is False

    def test_engine_and_session_are_main(self, followed):
        from harness.observability import trace_db
        from models import engine, SessionLocal

        assert trace_db.trace_engine() is engine

        session = trace_db.trace_session()
        try:
            assert session.get_bind() is engine
            assert isinstance(session, type(SessionLocal()))
        finally:
            session.close()


class TestRedirected:
    def test_engine_is_separate(self, redirected):
        from harness.observability import trace_db
        from models import engine as main_engine

        assert trace_db.is_redirected() is True
        assert trace_db.trace_engine() is not main_engine
        assert str(trace_db.trace_engine().url).startswith("sqlite")

    def test_schema_created_on_target(self, redirected):
        from sqlalchemy import inspect

        from harness.observability.trace_db import trace_engine

        names = set(inspect(trace_engine()).get_table_names())
        assert {"agent_events", "trace_message_blobs",
                "agent_payloads", "agent_traces"} <= names

    def test_events_do_not_reach_main_db(self, redirected):
        """核心断言：写进轨迹库的事件，主库里查不到。"""
        from harness.observability.event_contract import KIND_CODING
        from harness.observability.trace_db import trace_session
        from harness.observability.trace_writer import TraceWriter
        from models import SessionLocal
        from models.models import AgentEvent

        marker = -987654  # 主库里不可能存在的号
        db = trace_session()
        try:
            TraceWriter(db, requirement_id=marker, trace_id="t-isolation").event(
                KIND_CODING, "隔离自检")
        finally:
            db.close()

        check = trace_session()
        try:
            assert check.query(AgentEvent).filter(
                AgentEvent.requirement_id == marker).count() >= 1
        finally:
            check.close()

        main = SessionLocal()
        try:
            assert main.query(AgentEvent).filter(
                AgentEvent.requirement_id == marker).count() == 0
        finally:
            main.close()

    def test_writable_from_other_thread(self, redirected):
        """SQLite 目标库必须在子线程里可写。

        不配 `check_same_thread=False` 时，池里那条主线程创建的连接被子线程
        复用时，sqlite3 会直接抛 "SQLite objects created in a thread can only
        be used in that same thread"。评测链路里预览/AC 校验正是在子线程触发埋点。
        """
        from harness.observability.event_contract import KIND_CODING
        from harness.observability.trace_db import trace_session
        from harness.observability.trace_writer import TraceWriter

        # 先让池里有一条主线程创建的连接，再交给子线程复用
        db = trace_session()
        try:
            TraceWriter(db, requirement_id=-111, trace_id="t-warm").event(
                KIND_CODING, "主线程预热")
        finally:
            db.close()

        errors = []

        def work():
            try:
                inner = trace_session()
                try:
                    TraceWriter(inner, requirement_id=-112, trace_id="t-thread").event(
                        KIND_CODING, "子线程写入")
                finally:
                    inner.close()
            except Exception as e:  # pragma: no cover - 失败时由断言暴露
                errors.append(e)

        t = threading.Thread(target=work)
        t.start()
        t.join()
        assert errors == []


class TestWiring:
    """反回退：轨迹的两个出口必须走 trace_db。

    否则下一次重构里有人写回 `SessionLocal()`，评测隔离会被悄悄破坏，
    而现象是「运营库又多了些没头没尾的事件」——很难事后归因。
    """

    def _src(self, *parts):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        return (root.joinpath(*parts)).read_text(encoding="utf-8")

    def test_runtime_uses_trace_session(self):
        src = self._src("harness", "runtime.py")
        assert "from harness.observability.trace_db import trace_session" in src
        assert "db = trace_session()" in src
        assert "db = SessionLocal()" not in src

    def test_open_writer_uses_trace_session(self):
        src = self._src("harness", "observability", "trace_writer.py")
        assert "from harness.observability.trace_db import trace_session" in src
        assert "db = trace_session()" in src
        assert "db = SessionLocal()" not in src
