# -*- coding: utf-8 -*-
"""Actual LLM attempts share one accounting scope, including unknown usage."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import AgentEvent, AgentTrace


@pytest.fixture
def usage_db():
    engine = create_engine("sqlite:///:memory:")
    AgentEvent.__table__.create(engine)
    AgentTrace.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def _attempt(db, seq, **kwargs):
    values = dict(requirement_id=71, seq=seq, trace_id="plan", kind="llm_turn",
                  tokens_in=0, tokens_out=0, cached_tokens=None, cost=0,
                  ts=datetime(2026, 10, 16, 1), meta={})
    values.update(kwargs)
    db.add(AgentEvent(**values))
    db.commit()


def test_attempt_summary_counts_unknown_usage_and_preserves_legacy_events(usage_db):
    from services.usage_summary import summarize_usage

    _attempt(usage_db, 1, tokens_in=100, tokens_out=20, cached_tokens=40, cost=99,
             meta={"has_usage": True, "cost_decimal": "0.000123456789",
                   "cost_fallback": False, "cache_savings": "0.00005"})
    _attempt(usage_db, 2, trace_id="code", status="error",
             meta={"has_usage": False, "cost_fallback": True})
    _attempt(usage_db, 3, trace_id="code", tokens_in=300, tokens_out=10,
             cost=.001, meta={})  # legacy events remain part of the total
    _attempt(usage_db, 4, trace_id="code", tokens_in=100, cached_tokens=0,
             status="error", meta={"has_usage": True, "cost_fallback": True})
    _attempt(usage_db, 5, kind="intent", tokens_in=999, tokens_out=999, cost=100)

    summary = summarize_usage(usage_db, requirement_id=71)
    assert summary["tokens"] == 530
    assert summary["tokens_in"] == 500
    assert summary["tokens_out"] == 30
    assert summary["llm_calls"] == 4
    assert summary["usage_reported_calls"] == 3
    assert summary["usage_coverage"] == .75
    assert summary["cached_tokens"] == 40
    assert summary["cache_reported_calls"] == 2
    assert summary["cache_reported_tokens_in"] == 200
    assert summary["cache_hit_rate"] == .2
    assert summary["cache_coverage"] == .4
    assert summary["cost"] == pytest.approx(.001123456789)
    assert summary["cost_fallback_calls"] == 1
    assert summary["cache_savings"] == .00005
    assert summary["cost_currency"] == "USD"
    assert summary["cost_is_estimate"] is True
    assert summarize_usage(usage_db, trace_id="plan")["tokens"] == 120
    assert summarize_usage(usage_db, trace_id="code")["tokens"] == 410


def test_summary_distinguishes_unknown_cache_from_zero_hits_and_filters_time(usage_db):
    from services.usage_summary import summarize_usage

    _attempt(usage_db, 1, tokens_in=100, meta={"has_usage": True})
    summary = summarize_usage(usage_db, requirement_id=71)
    assert summary["cache_hit_rate"] is None
    assert summary["cache_coverage"] == 0
    assert summary["usage_coverage"] == 1
    assert summary["cache_savings"] is None
    empty = summarize_usage(usage_db, since=datetime(2026, 10, 17))
    assert empty["llm_calls"] == 0
    assert empty["usage_coverage"] is None
    assert empty["cache_coverage"] is None
    assert empty["cache_savings"] is None
    _attempt(usage_db, 2, tokens_in=100, cached_tokens=0,
             ts=datetime(2026, 10, 16, 2), meta={"has_usage": True})
    zero = summarize_usage(usage_db, since=datetime(2026, 10, 16, 1, 30))
    assert zero["cache_hit_rate"] == 0
    assert zero["cache_coverage"] == 1


def test_trace_persists_same_attempt_summary_and_restores_paused_state(usage_db):
    from harness.observability.tracer import Tracer

    tracer = Tracer(db_session=usage_db)
    trace = tracer.start_trace(71, 1)
    _attempt(usage_db, 1, trace_id=trace.trace_id, tokens_in=100, tokens_out=30,
             cached_tokens=40, meta={"has_usage": True, "cost_decimal": "0.000000123456"})
    _attempt(usage_db, 2, trace_id=trace.trace_id, meta={"has_usage": False})
    tracer.end_trace(trace.trace_id, status="awaiting_confirmation")
    reloaded = Tracer(db_session=usage_db).get_trace(trace.trace_id).to_dict()
    assert reloaded["status"] == "awaiting_confirmation"
    assert reloaded["started_at"] == trace.start_time
    assert reloaded["ended_at"] is not None
    assert reloaded["total_tokens"] == 130
    assert reloaded["input_tokens"] == 100
    assert reloaded["llm_calls"] == 2
    assert reloaded["usage_reported_calls"] == 1
    assert reloaded["usage_coverage"] == .5
    assert reloaded["total_cost"] == pytest.approx(.000000123456)


def test_default_usage_scope_excludes_independent_and_evaluation_but_keeps_legacy(usage_db):
    from services.usage_summary import summarize_usage

    _attempt(usage_db, 1, tokens_in=100, meta={"has_usage": True, "cost_decimal": "0.1"})
    _attempt(usage_db, 2, tokens_in=200, meta={"origin": "requirement", "has_usage": True, "cost_decimal": "0.2"})
    _attempt(usage_db, 3, tokens_in=300, meta={"origin": "independent", "has_usage": True, "cost_decimal": "0.3"})
    _attempt(usage_db, 4, tokens_in=400, meta={"origin": "evaluation", "has_usage": True, "cost_decimal": "0.4"})
    _attempt(usage_db, 1, requirement_id=0, tokens_in=500,
             meta={"origin": "independent", "has_usage": True, "cost_decimal": "0.5"})

    default = summarize_usage(usage_db)
    assert (default["llm_calls"], default["tokens"]) == (2, 300)
    assert default["cost"] == pytest.approx(.3)
    independent = summarize_usage(usage_db, scope="independent")
    assert (independent["llm_calls"], independent["tokens"]) == (2, 800)
    assert independent["cost"] == pytest.approx(.8)
    evaluation = summarize_usage(usage_db, scope="evaluation")
    assert (evaluation["llm_calls"], evaluation["tokens"]) == (1, 400)
    assert evaluation["cost"] == pytest.approx(.4)
    assert summarize_usage(usage_db, scope=None)["llm_calls"] == 5
    assert summarize_usage(usage_db, requirement_id=71)["tokens"] == 300


def test_trace_usage_accepts_explicit_evaluation_scope_and_legacy_default(usage_db):
    from services.usage_summary import summarize_usage

    _attempt(usage_db, 1, trace_id="old-eval", tokens_in=120, meta={"has_usage": True})
    _attempt(usage_db, 2, trace_id="new-eval", tokens_in=230,
             meta={"origin": "evaluation", "has_usage": True})
    assert summarize_usage(usage_db, trace_id="old-eval")["tokens"] == 120
    assert summarize_usage(usage_db, trace_id="new-eval", scope="evaluation")["tokens"] == 230
