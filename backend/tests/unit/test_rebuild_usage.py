# -*- coding: utf-8 -*-
"""历史修复预览不能写入，缺失数据不可编造。"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, AgentEvent, AgentPayload, AgentTrace


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _seed(db):
    payload = AgentPayload(requirement_id=101, kind='llm_turn', response={
        'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                  'prompt_tokens_details': {'cached_tokens': 800}}})
    db.add(payload)
    db.flush()
    event = AgentEvent(requirement_id=101, trace_id='old-trace', seq=1,
                       ts=datetime.utcnow(), kind='llm_turn',
                       model='gpt-4o-mini', tokens_in=1000, tokens_out=100,
                       cost=99, payload_id=payload.id)
    db.add(event)
    db.add(AgentTrace(requirement_id=101, user_id=1, trace_id='old-trace',
                       total_tokens=0, total_cost=99, data={}))
    db.commit()
    return event


def test_preview_does_not_mutate_event_or_trace(db):
    from scripts.rebuild_usage import rebuild_usage
    event = _seed(db)
    report = rebuild_usage(db)
    assert report['changed_events'] == 1
    assert report['samples'][0]['before']['cost'] == 99
    assert report['samples'][0]['after']['cost'] == pytest.approx(0.00015)
    db.expire_all()
    assert db.get(AgentEvent, event.id).cost == 99
    assert db.query(AgentTrace).one().total_cost == 99


def test_apply_rebuilds_trace_and_is_idempotent(db):
    from scripts.rebuild_usage import rebuild_usage
    event = _seed(db)
    report = rebuild_usage(db, apply=True)
    assert report['changed_events'] == 1
    db.expire_all()
    assert db.get(AgentEvent, event.id).cached_tokens == 800
    assert db.get(AgentEvent, event.id).meta['historical_estimate'] is True
    trace = db.query(AgentTrace).one()
    assert trace.total_tokens == 1100
    assert trace.total_cost == pytest.approx(0.00015)
    assert rebuild_usage(db, apply=True)['changed_events'] == 0


def test_missing_payload_preserves_existing_known_tokens(db):
    from scripts.rebuild_usage import rebuild_usage
    db.add(AgentEvent(requirement_id=101, seq=1, ts=datetime.utcnow(), kind='llm_turn',
                      tokens_in=123, tokens_out=45, cost=0.1))
    db.commit()
    report = rebuild_usage(db, apply=True)
    assert report['unknown_events'] == 1
    row = db.query(AgentEvent).one()
    assert row.tokens_in == 123
    assert row.cost == 0.1


def test_reapplying_uses_saved_price_snapshot(db, monkeypatch):
    from scripts.rebuild_usage import rebuild_usage
    from harness.observability.cost import CostTracker
    _seed(db)
    rebuild_usage(db, apply=True)
    monkeypatch.setitem(CostTracker.PRICING, 'gpt-4o-mini',
                        {'input': 100, 'output': 200, 'cached': 50})
    assert rebuild_usage(db, apply=True)['changed_events'] == 0
    assert db.query(AgentEvent).one().cost == pytest.approx(0.00015)


def test_rebuilt_trace_roundtrip_preserves_all_usage_fields(db):
    from scripts.rebuild_usage import rebuild_usage
    from harness.observability.tracer import Tracer
    _seed(db)
    row = db.query(AgentTrace).one()
    row.data = {'calls': 0, 'output_tokens': 0}
    db.commit()
    rebuild_usage(db, apply=True)
    restored = Tracer(db_session=db).get_trace('old-trace').to_dict()
    assert restored['tokens_out'] == 100
    assert restored['llm_calls'] == 1
