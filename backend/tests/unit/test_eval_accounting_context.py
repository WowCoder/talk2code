# -*- coding: utf-8 -*-
"""Eval requests keep their attribution inside the independent trace database."""
import importlib.util
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    from harness.observability import log_context, trace_db
    from llm.client import LLMClient
    from models import Base
    import models

    spec = importlib.util.spec_from_file_location(
        "eval_accounting_under_test", Path(__file__).resolve().parents[3] / "eval/run_eval.py")
    runner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "_RUNS_DIR", tmp_path / "runs")

    # A decoy operational store makes an accidental fallback observable without
    # connecting to the configured business database.
    operational = create_engine(f"sqlite:///{tmp_path / 'operational.db'}")
    tables = [table for table in Base.metadata.sorted_tables if table.name in {
        "agent_events", "agent_payloads", "trace_message_blobs", "agent_traces"}]
    Base.metadata.create_all(operational, tables=tables)
    monkeypatch.setattr(models, "engine", operational)
    monkeypatch.setattr(models, "SessionLocal", sessionmaker(bind=operational))
    monkeypatch.setenv("TALK2CODE_TRACE_DB", f"sqlite:///{tmp_path / 'trace.db'}")
    trace_db.reset_for_tests()
    log_context.clear()
    monkeypatch.setattr("harness.observability.llm_accounting._spool_directory",
                        lambda: tmp_path / "pending" / hashlib.sha256(
                            trace_db.trace_url().encode()).hexdigest())

    client = LLMClient(api_key="test-only", base_url="http://offline.invalid",
                       provider="openai_compatible", model="gpt-4o", max_retries=0)
    monkeypatch.setattr("llm.client.get_client", lambda *args, **kwargs: client)

    class OfflineResponse:
        status_code = 200

        def json(self):
            return {"model": "gpt-4o", "choices": [{
                "message": {"role": "assistant", "content": "好"},
                "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 1}}

        def raise_for_status(self):
            pass

    monkeypatch.setattr("llm.client.requests.post", lambda *args, **kwargs: OfflineResponse())
    yield SimpleNamespace(runner=runner, client=client, operational=operational,
                          tmp_path=tmp_path)
    trace_db.reset_for_tests()
    log_context.clear()
    operational.dispose()


def _events():
    from harness.observability.trace_db import trace_session
    from models.models import AgentEvent
    with trace_session() as db:
        return [{"requirement_id": row.requirement_id, "trace_id": row.trace_id,
                 "stage": row.stage, "meta": dict(row.meta or {}),
                 "attempt_id": row.attempt_id, "tokens_in": row.tokens_in,
                 "tokens_out": row.tokens_out}
                for row in db.query(AgentEvent).filter_by(kind="llm_turn").order_by(
                    AgentEvent.requirement_id, AgentEvent.seq)]


def _assert_operational_empty(evaluation):
    from models.models import AgentEvent, AgentTrace
    with sessionmaker(bind=evaluation.operational)() as db:
        assert db.query(AgentEvent).count() == 0
        assert db.query(AgentTrace).count() == 0


def _install_task_pipeline(evaluation, monkeypatch):
    from harness.observability import log_context
    from harness.observability.trace_writer import record_llm_turn
    from harness.state.workspace import WorkspaceFS

    runner = evaluation.runner
    monkeypatch.setattr(runner, "load_pipeline", lambda: None)
    monkeypatch.setattr(runner, "WorkspaceFS", WorkspaceFS)
    monkeypatch.setattr(runner, "create_tool_registry", lambda: None)
    monkeypatch.setattr(runner, "create_default_hook_manager", lambda: None)

    def request(stage, requirement_id):
        response = evaluation.client.chat("offline fixture", thinking="disabled")
        assert response.attempt_ids  # Exercise the real client's accounting path.
        record_llm_turn(requirement_id, stage=stage, response=response, model="gpt-4o")

    def plan(state):
        request("planning", state["requirement_id"])
        return {"plan": {"file_structure": ["index.html"]},
                "implementation_order": ["index.html"], "metadata": {}}

    def code(state):
        request("coding", state["requirement_id"])
        state["metadata"]["_workspace"].write("index.html", "<h1>offline</h1>")
        return {}

    monkeypatch.setattr("harness.instructions.nodes.team_leader_node", plan)
    monkeypatch.setattr("harness.instructions.nodes.coder_node", code)
    def loop(**kwargs):
        def run(state):
            code(state)
            return state
        return SimpleNamespace(run=run)

    monkeypatch.setattr(runner, "ToolCallLoop", loop)
    phases = []
    original_check = runner.AssertionChecker.check

    def check(checker, assertion):
        capture = getattr(log_context, "capture_accounting_context", lambda: {})
        phases.append(capture())
        return original_check(checker, assertion)

    monkeypatch.setattr(runner.AssertionChecker, "check", check)
    return phases


def _task(number):
    return {"id": f"t{number:02d}", "name": "offline task", "level": 1,
            "requirement": "创建测试页面", "assertions": [
                {"type": "file_exists", "filename": "index.html"}]}


def _args(run_id):
    return SimpleNamespace(run_id=run_id, with_plan=True, with_memory=False,
                           no_preview=True, keep_failed=False)


def test_preflight_is_evaluation_overhead_and_restores_callers_context(evaluation):
    from harness.observability import log_context
    log_context.bind_requirement(900)
    log_context.bind_trace_id("caller-trace")
    log_context.bind_turn_index(7)
    log_context.bind_call_id("caller-call")

    ok, _ = evaluation.runner._preflight_llm()

    assert ok
    rows = _events()
    assert len(rows) == 1
    assert rows[0]["requirement_id"] == 0
    assert rows[0]["trace_id"] is None
    assert rows[0]["stage"] == "planning"
    assert rows[0]["meta"]["origin"] == "evaluation"
    assert rows[0]["meta"]["purpose"] == "preflight"
    assert (log_context.current_requirement_id(), log_context.current_trace_id(),
            log_context.current_turn_index()) == ("900", "caller-trace", 7)
    assert log_context.current_call_id() == "caller-call"
    _assert_operational_empty(evaluation)


@pytest.mark.parametrize("with_plan", [True, False])
def test_task_attempts_keep_task_trace_and_stage_in_independent_database(
        evaluation, monkeypatch, with_plan):
    phases = _install_task_pipeline(evaluation, monkeypatch)
    args = _args("first")
    args.with_plan = with_plan

    first = evaluation.runner.run_one_task(_task(11), args)
    second = evaluation.runner.run_one_task(_task(12), args)

    assert first.passed and second.passed
    rows = _events()
    expected = ([(11, "planning"), (11, "coding"), (12, "planning"), (12, "coding")]
                if with_plan else [(11, "coding"), (12, "coding")])
    assert [(row["requirement_id"], row["stage"]) for row in rows] == expected
    assert all(row["attempt_id"] and row["tokens_in"] == 20 and row["tokens_out"] == 1
               and row["meta"]["origin"] == "evaluation" for row in rows)
    task_traces = {row["requirement_id"]: row["trace_id"] for row in rows}
    assert all(row["trace_id"] == task_traces[row["requirement_id"]] for row in rows)
    assert task_traces[11] and task_traces[11] != task_traces[12]
    assert [phase.get("stage") for phase in phases] == ["verifying", "verifying"]
    from harness.observability import log_context
    assert log_context.current_requirement_id() == "-"
    assert log_context.current_trace_id() == "-"
    assert log_context.current_call_id() == ""
    _assert_operational_empty(evaluation)


def test_task_context_is_restored_when_workspace_initialization_raises(evaluation, monkeypatch):
    from harness.observability import log_context
    _install_task_pipeline(evaluation, monkeypatch)
    log_context.bind_requirement(900)
    log_context.bind_trace_id("caller-trace")

    def broken_workspace(*args, **kwargs):
        raise RuntimeError("workspace failure")

    monkeypatch.setattr(evaluation.runner, "WorkspaceFS", broken_workspace)
    with pytest.raises(RuntimeError, match="workspace failure"):
        evaluation.runner.run_one_task(_task(11), _args("failed"))

    assert (log_context.current_requirement_id(), log_context.current_trace_id()) == (
        "900", "caller-trace")
    from harness.observability.trace_db import trace_session
    from models.models import AgentTrace
    with trace_session() as db:
        traces = db.query(AgentTrace).all()
        assert len(traces) == 1
        trace = traces[0]
        assert trace.requirement_id == 11
        assert trace.data["status"] == "error"
    _assert_operational_empty(evaluation)


@pytest.mark.parametrize("entry", ["task", "preflight"])
def test_direct_eval_entry_creates_trace_isolation_when_not_configured(
        evaluation, monkeypatch, entry):
    from harness.observability import trace_db
    _install_task_pipeline(evaluation, monkeypatch)
    monkeypatch.delenv("TALK2CODE_TRACE_DB")
    monkeypatch.setattr(evaluation.runner, "new_run_id", lambda: "direct")
    trace_db.reset_for_tests()

    if entry == "task":
        assert evaluation.runner.run_one_task(_task(11), _args("direct")).passed
    else:
        assert evaluation.runner._preflight_llm()[0]

    assert trace_db.is_redirected()
    assert (evaluation.tmp_path / "runs/direct/trace.db").exists()
    assert len(_events()) == (2 if entry == "task" else 1)
    _assert_operational_empty(evaluation)


def test_failed_preflight_retains_attempt_and_restores_context(evaluation, monkeypatch):
    from harness.observability import log_context
    from requests.exceptions import ReadTimeout

    def timeout(*args, **kwargs):
        raise ReadTimeout("offline fixture timeout")

    monkeypatch.setattr("llm.client.requests.post", timeout)
    log_context.bind_requirement(900)
    log_context.bind_trace_id("caller-trace")

    assert evaluation.runner._preflight_llm()[0] is False
    rows = _events()
    assert len(rows) == 1
    assert rows[0]["requirement_id"] == 0
    assert rows[0]["meta"]["origin"] == "evaluation"
    assert rows[0]["meta"]["purpose"] == "preflight"
    assert rows[0]["meta"]["has_usage"] is False
    assert (log_context.current_requirement_id(), log_context.current_trace_id()) == (
        "900", "caller-trace")
    _assert_operational_empty(evaluation)


def test_optional_memory_calls_inherit_task_scope_and_finalize_in_delivery(
        evaluation, monkeypatch):
    from harness.observability.log_context import capture_accounting_context
    _install_task_pipeline(evaluation, monkeypatch)
    delivery_context = []

    def memory(*args, **kwargs):
        evaluation.client.chat("offline memory fixture", thinking="disabled")
        return "test memory", [1]

    monkeypatch.setattr(evaluation.runner, "_inject_memory", memory)
    monkeypatch.setattr(evaluation.runner, "_get_eval_memory_manager", lambda: SimpleNamespace(
        resolve_hits=lambda *args: delivery_context.append(capture_accounting_context())))
    args = _args("memory")
    args.with_memory = True

    assert evaluation.runner.run_one_task(_task(11), args).passed
    rows = _events()
    assert len(rows) == 3
    assert rows[0]["requirement_id"] == 11
    assert rows[0]["stage"] == "coding"
    assert rows[0]["meta"]["purpose"] == "memory_selection"
    assert rows[0]["meta"]["origin"] == "evaluation"
    assert rows[0]["trace_id"] == rows[1]["trace_id"] == rows[2]["trace_id"]
    assert delivery_context[0]["stage"] == "delivering"
    assert delivery_context[0]["origin"] == "evaluation"
    _assert_operational_empty(evaluation)


def test_preflight_replays_temporary_accounting_failure_without_new_model_request(
        evaluation, monkeypatch):
    from harness.observability import llm_accounting
    persist = llm_accounting._persist_attempt
    failed = False

    def once(payload):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("temporary local database outage")
        return persist(payload)

    monkeypatch.setattr(llm_accounting, "_persist_attempt", once)
    assert evaluation.runner._preflight_llm()[0]
    assert len(_events()) == 1
    assert llm_accounting.pending_attempt_count() == 0
    _assert_operational_empty(evaluation)


def test_accounting_replay_failure_warns_without_aborting_eval(evaluation, monkeypatch, capsys):
    _install_task_pipeline(evaluation, monkeypatch)

    def unavailable():
        raise OSError("temporary recovery failure")

    monkeypatch.setattr("harness.observability.llm_accounting.replay_pending_attempts", unavailable)
    assert evaluation.runner.run_one_task(_task(11), _args("replay-failure")).passed
    assert "补记" in capsys.readouterr().err
    assert len(_events()) == 2
    _assert_operational_empty(evaluation)
