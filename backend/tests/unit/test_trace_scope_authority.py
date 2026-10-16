# -*- coding: utf-8 -*-
"""A unique trace includes every origin, independent of reporting-page scopes."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness.observability.cost import CostTracker
from harness.observability.log_context import clear
from harness.observability.tracer import Tracer
from models.models import AgentEvent, AgentPayload, AgentTrace, Base


@pytest.fixture
def trace_db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    clear()
    engine.dispose()


def _event(trace_id, seq, origin, *, tokens_in=0, tokens_out=0,
           cached_tokens=None, cost=0, has_usage=True, **extra):
    return AgentEvent(
        requirement_id=0, trace_id=trace_id, seq=seq,
        ts=datetime.utcnow(), kind='llm_turn', model='gpt-4o-mini',
        tokens_in=tokens_in, tokens_out=tokens_out,
        cached_tokens=cached_tokens, cost=cost,
        meta={'origin': origin, 'has_usage': has_usage, **extra},
    )


@pytest.mark.parametrize('origin', ['evaluation', 'independent'])
@pytest.mark.parametrize('partial_tracker', [False, True])
def test_end_trace_uses_complete_database_for_every_origin(
        trace_db, origin, partial_tracker):
    tracker = CostTracker() if partial_tracker else None
    tracer = Tracer(db_session=trace_db, cost_tracker=tracker)
    trace = tracer.start_trace(0, 0)
    if tracker is not None:
        tracker.record(trace.trace_id, 100, 10, 'gpt-4o-mini', cached_tokens=40)
    trace_db.add_all([
        _event(trace.trace_id, 1, origin, tokens_in=100, tokens_out=10,
               cached_tokens=40, cost=.01),
        _event(trace.trace_id, 2, origin, tokens_in=200, tokens_out=20, cost=.02),
        _event(trace.trace_id, 3, origin, has_usage=False),
        _event('another-trace', 4, origin, tokens_in=9000, tokens_out=900, cost=99),
    ])
    trace_db.commit()

    tracer.end_trace(trace.trace_id)

    report = tracer.get_trace(trace.trace_id).to_dict()
    assert (report['input_tokens'], report['output_tokens'], report['total_tokens']) == (300, 30, 330)
    assert report['llm_calls'] == 3
    assert report['usage_reported_calls'] == 2
    assert report['usage_coverage'] == .6667
    assert report['cached_tokens'] == 40
    assert report['cache_reported_tokens_in'] == 100
    assert report['cache_reported_calls'] == 1
    assert report['cost'] == pytest.approx(.03)
    saved = trace_db.query(AgentTrace).filter_by(trace_id=trace.trace_id).one()
    assert (saved.total_tokens, saved.data['llm_calls']) == (330, 3)
    assert saved.total_cost == pytest.approx(.03)


@pytest.mark.parametrize('origin', ['evaluation', 'independent'])
def test_rebuild_trace_includes_current_and_historical_attempts_for_every_origin(
        trace_db, origin):
    from scripts.rebuild_usage import rebuild_usage
    payload = AgentPayload(requirement_id=0, kind='llm_turn', response={
        'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                  'prompt_tokens_details': {'cached_tokens': 800}}})
    trace_db.add(payload)
    trace_db.flush()
    old = _event('historical-trace', 1, origin, tokens_in=1, cost=99)
    old.payload_id = payload.id
    trace_db.add_all([
        old,
        _event('historical-trace', 2, origin, tokens_in=100, tokens_out=10,
               cost=.001, accounting_source='client_attempt'),
        _event('another-trace', 3, origin, tokens_in=9000, cost=99,
               accounting_source='client_attempt'),
        AgentTrace(requirement_id=0, user_id=0, trace_id='historical-trace',
                   total_tokens=1, total_cost=99, data={}),
    ])
    trace_db.commit()

    report = rebuild_usage(trace_db, apply=True)

    assert report['updated_traces'] == 1
    saved = trace_db.query(AgentTrace).filter_by(trace_id='historical-trace').one()
    assert saved.total_tokens == 1210
    assert saved.total_cost == pytest.approx(.00115)
    assert saved.data['llm_calls'] == 2
    assert saved.data['tokens_in'] == 1100
    assert saved.data['tokens_out'] == 110
    assert saved.data['cached_tokens'] == 800
