# -*- coding: utf-8 -*-
"""Accounting context and durable retry behavior, using isolated SQLite only."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from harness.observability import log_context
from harness.observability import llm_accounting as accounting
from harness.observability.cost import shared_cost_tracker
from models.models import Base, AgentEvent
REAL_SPOOL_DIRECTORY = accounting._spool_directory


@pytest.fixture
def recovery_db(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr('harness.observability.trace_db.trace_session', sessions)
    monkeypatch.setattr(accounting, '_spool_directory', lambda: tmp_path / 'pending', raising=False)
    log_context.clear()
    shared_cost_tracker().clear()
    yield sessions
    log_context.clear()
    shared_cost_tracker().clear()
    engine.dispose()


def _attempt(attempt_id='recover-me', **extra):
    return accounting.record_attempt(
        attempt_id=attempt_id, provider='openai_compatible', model='gpt-4o-mini',
        request={'messages': [{'role': 'user', 'content': 'fixture'}]},
        response={'usage': {'prompt_tokens': 100, 'completion_tokens': 10}},
        duration_ms=12, **extra,
    )


def test_nested_context_clears_explicit_none_and_restores_after_failure():
    log_context.clear()
    log_context.bind_requirement(42)
    log_context.bind_trace_id('outer')
    try:
        with log_context.llm_context(stage='coding', purpose='generation'):
            with pytest.raises(RuntimeError):
                with log_context.llm_context(requirement_id=None, trace_id=None,
                                             origin='evaluation', purpose='preflight'):
                    snapshot = log_context.capture_accounting_context()
                    assert snapshot['requirement_id'] is None
                    assert snapshot['trace_id'] is None
                    assert snapshot['stage'] == 'coding'
                    assert snapshot['origin'] == 'evaluation'
                    raise RuntimeError('interrupted')
            snapshot = log_context.capture_accounting_context()
            assert snapshot['requirement_id'] == 42
            assert snapshot['trace_id'] == 'outer'
            assert snapshot['purpose'] == 'generation'
            assert snapshot['origin'] == 'requirement'
    finally:
        log_context.clear()
    assert log_context.capture_accounting_context()['origin'] == 'independent'


def test_independent_attempt_is_retained_without_requirement(recovery_db):
    _attempt()
    with recovery_db() as db:
        row = db.query(AgentEvent).one()
        assert row.requirement_id == 0
        assert row.meta['origin'] == 'independent'
        assert row.tokens_in == 100


def test_failed_db_write_replays_original_context_and_price_exactly_once(recovery_db, monkeypatch):
    from harness.observability.trace_writer import TraceWriter
    original = TraceWriter.llm_call
    monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
    with log_context.llm_context(requirement_id=42, trace_id='recover-trace',
                                 stage='planning', purpose='intent_classification', turn_index=2):
        _attempt()
    assert accounting.pending_attempt_count() == 1
    # A restart discards ContextVars and CostTracker. Replay must use the snapshot.
    log_context.clear()
    shared_cost_tracker().clear()
    monkeypatch.setattr(TraceWriter, 'llm_call', original)
    monkeypatch.setattr('harness.observability.cost.quote_cost',
                        lambda *a, **k: pytest.fail('replay must not reprice'))
    assert accounting.replay_pending_attempts() == 1
    assert accounting.replay_pending_attempts() == 0
    assert accounting.pending_attempt_count() == 0
    with recovery_db() as db:
        row = db.query(AgentEvent).one()
        assert row.requirement_id == 42
        assert row.trace_id == 'recover-trace'
        assert row.stage == 'planning'
        assert row.turn_index == 2
        assert row.meta['purpose'] == 'intent_classification'
        assert float(row.meta['cost_decimal']) == pytest.approx(0.000021)
        assert row.tokens_in + row.tokens_out == 110


def test_semantic_enrichment_survives_pending_attempt(recovery_db, monkeypatch):
    from harness.observability.trace_writer import TraceWriter
    original = TraceWriter.llm_call
    monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
    with log_context.llm_context(requirement_id=42):
        _attempt()
    accounting.enrich_attempts(42, ['recover-me'], stage='verifying',
                               iteration=3, turn_index=2, trace_id='trace', label='验收')
    monkeypatch.setattr(TraceWriter, 'llm_call', original)
    accounting.replay_pending_attempts()
    with recovery_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.stage, row.iteration, row.turn_index, row.label) == ('verifying', 3, 2, '验收')


def test_existing_event_is_not_duplicated_when_pending_cleanup_failed(recovery_db, monkeypatch):
    from pathlib import Path
    original = Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if path.suffix == '.json':
            raise OSError('disk temporarily read-only')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    _attempt()
    assert accounting.pending_attempt_count() == 1
    monkeypatch.setattr(Path, 'unlink', original)
    accounting.replay_pending_attempts()
    with recovery_db() as db:
        assert db.query(AgentEvent).count() == 1
    assert accounting.pending_attempt_count() == 0


def test_spool_failure_still_attempts_db_and_reports_failure(recovery_db, monkeypatch):
    from harness.observability.trace_writer import write_failure_count
    before = write_failure_count()
    def broken_spool(*a, **kw):
        raise OSError('disk full')
    monkeypatch.setattr(accounting, '_write_pending', broken_spool, raising=False)
    _attempt()
    with recovery_db() as db:
        assert db.query(AgentEvent).count() == 1
    assert write_failure_count() > before


def test_pending_file_is_private_and_replay_keeps_original_time(recovery_db, monkeypatch):
    import json
    import stat
    from harness.observability.trace_writer import TraceWriter
    original = TraceWriter.llm_call
    monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
    _attempt()
    path, = accounting._spool_directory().glob('*.json')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    original_time = json.loads(path.read_text())['ts']
    monkeypatch.setattr(TraceWriter, 'llm_call', original)
    accounting.replay_pending_attempts()
    with recovery_db() as db:
        assert db.query(AgentEvent).one().ts.isoformat() == original_time


def test_replay_does_not_recount_live_trace_cost(recovery_db, monkeypatch):
    from harness.observability.trace_writer import TraceWriter
    original = TraceWriter.llm_call
    monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
    with log_context.llm_context(requirement_id=42, trace_id='live'):
        _attempt()
    monkeypatch.setattr(TraceWriter, 'llm_call', original)
    accounting.replay_pending_attempts()
    assert shared_cost_tracker().get_report('live').total_tokens == 110


def test_spool_targets_do_not_cross_database_boundaries(recovery_db, monkeypatch):
    from harness.observability.trace_writer import TraceWriter
    # Exercise the real directory resolver with two destination URLs.
    monkeypatch.setattr(accounting, '_spool_directory', REAL_SPOOL_DIRECTORY)
    import tempfile
    from config import settings
    with tempfile.TemporaryDirectory() as folder:
        monkeypatch.setattr(settings, 'LOG_DIR', folder)
        monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
        monkeypatch.setenv('TALK2CODE_TRACE_DB', 'sqlite:///eval-private.db')
        first = accounting._spool_directory()
        _attempt()
        assert accounting.pending_attempt_count() == 1
        monkeypatch.setenv('TALK2CODE_TRACE_DB', 'sqlite:///other.db')
        assert accounting._spool_directory() != first
        assert accounting.pending_attempt_count() == 0
        assert accounting.replay_pending_attempts() == 0
        assert len(list(first.glob('*.json'))) == 1


def test_diagnostic_logger_failure_cannot_skip_healthy_database(recovery_db, monkeypatch):
    def fail(*a, **k):
        raise OSError('disk and log unavailable')
    monkeypatch.setattr(accounting, '_write_pending', fail)
    monkeypatch.setattr(accounting.logger, 'warning', fail)
    _attempt()
    with recovery_db() as db:
        assert db.query(AgentEvent).count() == 1


@pytest.mark.parametrize('choices', [[None], [{'message': None}], 'malformed'])
def test_malformed_response_still_preserves_reported_usage(recovery_db, choices):
    accounting.record_attempt(
        attempt_id='malformed', provider='openai_compatible', model='gpt-4o-mini',
        request={'messages': []},
        response={'choices': choices, 'usage': {'prompt_tokens': 100, 'completion_tokens': 10}},
        duration_ms=12, error='invalid_response',
    )
    with recovery_db() as db:
        row = db.query(AgentEvent).one()
        assert row.tokens_in + row.tokens_out == 110
        assert row.status == 'error'


def test_password_rotation_keeps_pending_destination_identity(recovery_db, monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(accounting, '_spool_directory', REAL_SPOOL_DIRECTORY)
    monkeypatch.setattr(settings, 'LOG_DIR', str(tmp_path / 'spool'))
    monkeypatch.setenv('TALK2CODE_TRACE_DB', 'postgresql://user:first-password@db/app')
    before = accounting._spool_directory()
    monkeypatch.setenv('TALK2CODE_TRACE_DB', 'postgresql://user:rotated-password@db/app')
    assert accounting._spool_directory() == before


def test_replay_worker_drains_persisted_queue_on_start(recovery_db, monkeypatch):
    import threading
    from config import settings
    from harness.observability.trace_writer import TraceWriter
    original_writer = TraceWriter.llm_call
    monkeypatch.setattr(TraceWriter, 'llm_call', lambda *a, **k: 0)
    _attempt()
    monkeypatch.setattr(TraceWriter, 'llm_call', original_writer)
    monkeypatch.setattr(settings, 'LLM_ACCOUNTING_REPLAY_INTERVAL_SECONDS', 0.01)
    committed = threading.Event()
    persist = accounting._persist_attempt
    def observed(payload):
        result = persist(payload)
        committed.set()
        return result
    monkeypatch.setattr(accounting, '_persist_attempt', observed)
    try:
        accounting.start_accounting_replay()
        assert committed.wait(2), 'startup recovery never committed pending event'
    finally:
        accounting.stop_accounting_replay()
    assert accounting.pending_attempt_count() == 0
    with recovery_db() as db:
        assert db.query(AgentEvent).one().attempt_id == 'recover-me'
