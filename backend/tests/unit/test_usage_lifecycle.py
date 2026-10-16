# -*- coding: utf-8 -*-
"""Planning pauses and confirmation preserve execution boundaries and attribution."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import AgentEvent, AgentTrace, Requirement, User
from harness.observability import log_context


@pytest.fixture
def lifecycle(monkeypatch):
    import services.requirement_service as rs
    import harness.observability.trace_writer as writer
    from services.task_queue import task_queue

    engine = create_engine("sqlite:///:memory:")
    for model in (User, Requirement, AgentTrace, AgentEvent):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    user = User(username="usage-life", password_hash="hash")
    db.add(user)
    db.flush()
    req = Requirement(user_id=user.id, title="usage", content="build a timer", status="pending")
    db.add(req)
    db.commit()

    @contextmanager
    def _db():
        yield db

    monkeypatch.setattr(rs, "get_db", _db)
    workspace = Mock()
    workspace.list.return_value = []
    workspace.snapshot.return_value = []
    monkeypatch.setattr(rs, "WorkspaceFS", lambda *a, **k: workspace)
    monkeypatch.setattr(rs, "GitVersioning", Mock())
    monkeypatch.setattr(rs, "create_tool_registry", Mock())
    monkeypatch.setattr(rs, "create_default_hook_manager", Mock())
    monkeypatch.setattr(rs, "ToolCallLoop", Mock())
    monkeypatch.setattr(rs, "shared_cost_tracker", lambda: None)
    monkeypatch.setattr(rs, "_attach_lazy_memory", Mock())
    checkpoint = Mock()
    checkpoint.resume.return_value = None
    monkeypatch.setattr(rs, "CheckpointManager", lambda **kw: checkpoint)
    sse = Mock()
    monkeypatch.setattr(rs, "SSEReporter", lambda *a: sse)
    monkeypatch.setattr(writer, "record_event", Mock())
    monkeypatch.setattr(task_queue, "submit", Mock(return_value="queued"))
    service = rs.RequirementService.__new__(rs.RequirementService)
    log_context.clear()
    yield SimpleNamespace(rs=rs, db=db, req=req, service=service,
                          checkpoint=checkpoint, sse=sse, monkeypatch=monkeypatch)
    log_context.clear()
    db.close()
    engine.dispose()


def _record_call(env, seq):
    trace_id = log_context.current_trace_id()
    assert trace_id != "-", "LLM call must already have an execution trace"
    assert log_context.current_requirement_id() == str(env.req.id)
    env.db.add(AgentEvent(requirement_id=env.req.id, trace_id=trace_id, seq=seq,
                          ts=datetime.utcnow(), kind="llm_turn", tokens_in=100,
                          tokens_out=10, meta={"has_usage": True}))
    env.db.commit()


@pytest.mark.parametrize("step,status", [
    ("presenting_plan", "awaiting_confirmation"),
    ("team_leader_failed", "awaiting_confirmation"),
    ("needs_clarification", "paused"),
])
def test_planning_pause_ends_trace_and_keeps_intent_usage(lifecycle, step, status):
    env = lifecycle

    def classify(_content):
        _record_call(env, 1)
        return SimpleNamespace(intent=env.rs.IntentType.TASK)

    env.monkeypatch.setattr(env.rs, "IntentRouter", lambda: SimpleNamespace(classify=classify))

    def execute(_rid, state):
        _record_call(env, 2)
        return {**state, "current_step": step, "plan": {"features": ["timer"]}}

    env.service._execute_workflow_with_stream = execute
    assert env.service.process_requirement(env.req.id) is True
    traces = env.db.query(AgentTrace).order_by(AgentTrace.id).all()
    assert len(traces) == 2
    assert [r.data["status"] for r in traces] == ["finished", status]
    assert all(r.data["ended_at"] is not None for r in traces)
    from services.usage_summary import summarize_usage
    assert summarize_usage(env.db, requirement_id=env.req.id)["tokens"] == 220
    assert [r.total_tokens for r in traces] == [110, 110]
    assert log_context.current_trace_id() == "-"


def test_workflow_exception_ends_active_trace(lifecycle):
    env = lifecycle
    env.req.content = "build a timer [用户补充说明] confirmed"
    env.db.commit()

    def execute(_rid, _state):
        _record_call(env, 1)
        raise RuntimeError("interrupted planning")

    env.service._execute_workflow_with_stream = execute
    assert env.service.process_requirement(env.req.id) is False
    trace = env.db.query(AgentTrace).one()
    assert trace.data["status"] == "error"
    assert trace.data["ended_at"] is not None
    assert trace.total_tokens == 110


def test_confirm_without_feedback_continues_checkpoint_directly(lifecycle):
    env = lifecycle
    env.req.status = "planning"
    env.db.commit()
    continued = []

    def continue_plan(_db, req, rid):
        continued.append((rid, req.status))
        return True

    env.service._continue_from_plan_checkpoint = continue_plan
    assert env.service.confirm_plan(env.req.id) is True
    assert continued == [(env.req.id, "processing")]


def test_confirmation_starts_new_trace_bound_to_same_requirement_and_turn(lifecycle):
    env = lifecycle
    env.checkpoint.resume.return_value = {"metadata": {"trace_id": "old", "turn_index": 3},
                                          "current_step": "presenting_plan"}

    def execute(_rid, state, _workflow):
        _record_call(env, 1)
        assert state["metadata"]["trace_id"] != "old"
        assert log_context.current_turn_index() == 3
        return {**state, "current_step": "task_complete"}

    env.service._execute_workflow_with_stream = execute
    env.service._process_final_state = lambda *args: True
    env.monkeypatch.setattr(env.rs, "create_workflow_post_plan", lambda: object())
    assert env.service._continue_from_plan_checkpoint(env.db, env.req, env.req.id) is True
    trace = env.db.query(AgentTrace).one()
    assert trace.requirement_id == env.req.id
    assert trace.data["status"] != "running"


def test_chat_binds_trace_before_intent_and_keeps_turn_attribution(lifecycle):
    import inspect
    import routes.requirements as routes
    import harness.runtime as runtime
    import harness.state.workspace as workspace_mod
    import harness.state.versioning as versioning
    import harness.observability.sse_reporter as reporter
    import harness.instructions.intent_router as intent_mod

    env = lifecycle
    @contextmanager
    def db_context():
        yield env.db
    env.monkeypatch.setattr(routes, "transactional_db", db_context)
    env.monkeypatch.setattr(routes, "get_jwt_identity", lambda: env.req.user_id)
    env.monkeypatch.setattr(routes, "_chat_quality_gate", lambda _ws: ([], ""))
    workspace = Mock()
    workspace.list.return_value = []
    workspace.snapshot.return_value = []
    env.monkeypatch.setattr(workspace_mod, "WorkspaceFS", lambda *a: workspace)
    env.monkeypatch.setattr(versioning, "GitVersioning", Mock())
    env.monkeypatch.setattr(reporter, "SSEReporter", lambda *a: env.sse)
    def classify(*a, **kw):
        _record_call(env, 1)
        assert log_context.current_turn_index() == 1
        return SimpleNamespace(intent=env.rs.IntentType.TASK)
    env.monkeypatch.setattr(intent_mod, "IntentRouter", lambda: SimpleNamespace(classify=classify))
    def run(state):
        _record_call(env, 2)
        assert state["metadata"]["trace_id"] == log_context.current_trace_id()
        assert log_context.current_turn_index() == state["metadata"]["turn_index"] == 1
        return {**state, "current_step": "task_complete"}
    env.monkeypatch.setattr(runtime, "ToolCallLoop", lambda **kw: SimpleNamespace(run=run))
    with routes.app.test_request_context(json={"message": "make the timer red"}):
        result = inspect.unwrap(routes.chat_with_requirement)(env.req.id)
    assert result[1] == 200
    trace = env.db.query(AgentTrace).one()
    assert trace.data["status"] == "finished"
    assert trace.total_tokens == 220
    assert log_context.current_trace_id() == "-"


@pytest.mark.parametrize("verify_passed", [True, False])
def test_reflection_call_remains_in_requirement_execution(lifecycle, verify_passed):
    from harness.observability.tracer import Tracer
    import harness.state.context_pipeline as pipeline

    env = lifecycle
    log_context.bind_requirement(env.req.id)
    tracer = Tracer(db_session=env.db)
    trace = tracer.start_trace(env.req.id, env.req.user_id)
    memory = Mock()
    memory.after_task.side_effect = lambda **kw: _record_call(env, 1)
    memory.stats.return_value = {"total": 1, "avg_rating": 7}
    env.monkeypatch.setattr(env.rs, "_get_memory_manager", lambda: memory)
    env.monkeypatch.setattr(pipeline, "finalize_delivery", lambda *a, **kw: None)
    env.service._send_complete = Mock()
    workspace = Mock()
    workspace.snapshot.return_value = []
    state = {"current_step": "task_complete", "verify_passed": verify_passed,
             "metadata": {"trace_id": trace.trace_id}}
    assert env.service._process_final_state(env.db, env.req, env.req.id, state,
                                             workspace, Mock(), tracer, env.sse) is True
    report = env.db.query(AgentTrace).one()
    assert report.total_tokens == 110
    assert report.data["status"] != "running"
