# -*- coding: utf-8 -*-
"""真实客户端边界的请求尝试记账回归，无网络。"""
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from harness.observability import log_context
from harness.observability.cost import shared_cost_tracker
from llm.client import LLMClient
from models.models import Base, AgentEvent


@pytest.fixture
def accounting_db(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr('harness.observability.trace_db.trace_session', sessions)
    log_context.bind_requirement(901)
    log_context.bind_trace_id('attempt-test')
    shared_cost_tracker().clear()
    yield sessions
    log_context.clear()
    shared_cost_tracker().clear()
    engine.dispose()


def _response(content, usage, reasoning=None, status=200):
    response = Mock(status_code=status)
    message = {'content': content}
    if reasoning:
        message['reasoning_content'] = reasoning
    response.json.return_value = {
        'choices': [{'message': message, 'finish_reason': 'stop'}],
        'usage': usage,
    }
    return response


def _client():
    client = LLMClient(api_key='test', base_url='http://example.invalid',
                       provider='openai_compatible', model='gpt-4o-mini',
                       max_retries=0, max_tokens=1000)
    client._has_backup = False
    return client


def test_reasoning_retry_records_each_attempt_once(accounting_db, monkeypatch):
    replies = [
        _response('', {'prompt_tokens': 100, 'completion_tokens': 1000}, 'thinking'),
        _response('done', {'prompt_tokens': 100, 'completion_tokens': 2000}),
    ]
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=replies))
    result = _client().chat_with_tools([{'role': 'user', 'content': 'hello'}], [])
    from harness.observability.trace_writer import record_llm_turn
    record_llm_turn(901, stage='coding', response=result, model='gpt-4o-mini',
                    trace_id='attempt-test', messages=[])
    with accounting_db() as db:
        rows = db.query(AgentEvent).filter_by(kind='llm_turn').order_by(AgentEvent.seq).all()
        assert len(rows) == 2
        assert len({r.call_id for r in rows}) == 2
        assert sum(r.tokens_in + r.tokens_out for r in rows) == 3200
        assert {r.stage for r in rows} == {'coding'}
    report = shared_cost_tracker().get_report('attempt-test')
    assert report.total_tokens == 3200


def test_error_response_with_usage_is_recorded(accounting_db, monkeypatch):
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=
        _response('', {'prompt_tokens': 100, 'completion_tokens': 1000})))
    result = _client().chat_with_tools([{'role': 'user', 'content': 'hello'}], [])
    assert result.is_error
    with accounting_db() as db:
        row = db.query(AgentEvent).filter_by(kind='llm_turn').one()
        assert row.tokens_in + row.tokens_out == 1100
    assert shared_cost_tracker().get_report('attempt-test').total_tokens == 1100


def test_network_failure_preserves_unknown_usage(accounting_db, monkeypatch):
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=requests.Timeout('timeout')))
    _client().chat_with_tools([{'role': 'user', 'content': 'hello'}], [])
    with accounting_db() as db:
        row = db.query(AgentEvent).filter_by(kind='llm_turn').one()
        assert row.status == 'error'
        assert row.meta['has_usage'] is False
        assert row.cached_tokens is None


def test_duplicate_attempt_is_idempotent(accounting_db):
    from harness.observability.llm_accounting import record_attempt
    attempt = dict(attempt_id='same-attempt', provider='openai_compatible',
                   model='gpt-4o-mini', request={'messages': []},
                   response={'usage': {'prompt_tokens': 100, 'completion_tokens': 10}},
                   duration_ms=10)
    record_attempt(**attempt)
    record_attempt(**attempt)
    with accounting_db() as db:
        assert db.query(AgentEvent).filter_by(kind='llm_turn').count() == 1
    assert shared_cost_tracker().get_report('attempt-test').total_tokens == 110


def test_stream_usage_chunk_is_accounted(accounting_db, monkeypatch):
    import json
    response = Mock(status_code=200)
    response.iter_lines.return_value = [
        b'data: ' + json.dumps({'choices': [{'delta': {'content': 'done'}}]}).encode(),
        b'data: ' + json.dumps({'choices': [], 'usage': {
            'prompt_tokens': 80, 'completion_tokens': 20}}).encode(),
        b'data: [DONE]',
    ]
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    assert ''.join(_client().chat_stream('hello')) == 'done'
    with accounting_db() as db:
        row = db.query(AgentEvent).filter_by(kind='llm_turn').one()
        assert row.tokens_in + row.tokens_out == 100


def test_backup_request_uses_actual_model(accounting_db, monkeypatch):
    from llm.client import _Endpoint, CircuitBreaker
    client = _client()
    client._backup_circuit_breaker = CircuitBreaker()
    monkeypatch.setattr(client, '_backup_endpoint', lambda: _Endpoint(
        api_key='test', base_url='http://backup.invalid',
        provider='openai_compatible', model='gpt-4o'))
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=[
        requests.Timeout('primary timeout'),
        _response('done', {'prompt_tokens': 100, 'completion_tokens': 10}),
    ]))
    result = client.chat_with_tools([{'role': 'user', 'content': 'hello'}], [])
    assert result.model == 'gpt-4o'
    with accounting_db() as db:
        rows = db.query(AgentEvent).order_by(AgentEvent.seq).all()
        assert [r.model for r in rows] == ['gpt-4o-mini', 'gpt-4o']
        assert rows[0].meta['has_usage'] is False
        assert rows[1].tokens_in == 100


def test_provider_reported_model_is_preserved(accounting_db, monkeypatch):
    response = _response('done', {'prompt_tokens': 100, 'completion_tokens': 10})
    response.json.return_value['model'] = 'gpt-4o-mini-2024-07-18'
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    result = _client().chat('hello')
    assert result.model == 'gpt-4o-mini-2024-07-18'
    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert row.model == result.model
        assert row.meta['requested_model'] == 'gpt-4o-mini'


def test_stream_http_error_still_records_known_usage(accounting_db, monkeypatch):
    response = _response('', {'prompt_tokens': 100, 'completion_tokens': 20}, status=400)
    response.raise_for_status.side_effect = requests.HTTPError('400', response=response)
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    list(_client().chat_stream('hello'))
    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert row.status == 'error'
        assert row.tokens_in + row.tokens_out == 120
        assert row.meta['has_usage'] is True


def test_client_accounting_does_not_invalidate_long_writer_sequence(accounting_db, monkeypatch):
    from harness.observability.trace_writer import (
        TraceWriter, record_llm_turn, write_failure_count,
    )
    with accounting_db() as db:
        writer = TraceWriter(db, 901, trace_id='attempt-test')
        writer.event('coding', 'start')
        before = write_failure_count()
        monkeypatch.setattr('llm.client.requests.post', Mock(return_value=
            _response('done', {'prompt_tokens': 100, 'completion_tokens': 10})))
        result = _client().chat_with_tools([{'role': 'user', 'content': 'hello'}], [])
        record_llm_turn(901, stage='coding', response=result, writer=writer,
                        trace_id='attempt-test', messages=[])
        writer.event('tool_call', 'tool completed')
        assert write_failure_count() == before
        assert [r.seq for r in db.query(AgentEvent).order_by(AgentEvent.seq)] == [1, 2, 3]


def _stream_response(*, terminated=True):
    response = Mock(status_code=200)
    response.iter_lines.return_value = [
        b'data: {"choices":[{"delta":{"content":"done"}}]}',
        b'data: {"choices":[],"usage":{"prompt_tokens":80,"completion_tokens":20}}',
    ] + ([b'data: [DONE]'] if terminated else [])
    return response


def test_stream_close_failure_preserves_result_and_event(accounting_db, monkeypatch):
    response = _stream_response()
    response.close.side_effect = OSError('close failed')
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))

    assert ''.join(_client().chat_stream('hello')) == 'done'

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.status, row.tokens_in, row.tokens_out) == ('ok', 80, 20)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('log_name', ['_log_llm_request', '_log_llm_response'])
def test_traffic_log_failure_preserves_result_and_event(
        accounting_db, monkeypatch, stream, log_name):
    response = _stream_response() if stream else _response(
        'done', {'prompt_tokens': 80, 'completion_tokens': 20})
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    monkeypatch.setattr('llm.client.' + log_name,
                        Mock(side_effect=RuntimeError('traffic log unavailable')))

    client = _client()
    if stream:
        assert ''.join(client.chat_stream('hello')) == 'done'
    else:
        result = client.chat('hello')
        assert result.content == 'done'
        assert result.is_error is False

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.status, row.tokens_in, row.tokens_out) == ('ok', 80, 20)


def test_http_error_log_failure_preserves_http_error(accounting_db, monkeypatch):
    response = _response('', {'prompt_tokens': 80, 'completion_tokens': 20}, status=400)
    response.raise_for_status.side_effect = requests.HTTPError('400', response=response)
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    monkeypatch.setattr('llm.client._log_llm_response',
                        Mock(side_effect=RuntimeError('traffic log unavailable')))
    client = _client()

    with pytest.raises(requests.HTTPError):
        client._post_json('http://example.invalid', headers={}, data={}, timeout=1,
                          endpoint=client._primary_endpoint())

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.status, row.tokens_in, row.tokens_out) == ('error', 80, 20)
        assert row.meta['error'] == '400'


def test_stream_premature_eof_is_recorded_as_interrupted(accounting_db, monkeypatch):
    monkeypatch.setattr('llm.client.requests.post',
                        Mock(return_value=_stream_response(terminated=False)))

    assert ''.join(_client().chat_stream('hello')) == 'done'

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert row.status == 'error'
        assert row.meta['error'] == 'interrupted'
        assert (row.tokens_in, row.tokens_out) == (80, 20)


def test_cancelled_stream_uses_request_context_even_if_cleanup_fails(
        accounting_db, monkeypatch):
    response = _stream_response()
    response.close.side_effect = OSError('close failed during cancellation')
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    log_context.bind_turn_index(3)
    client = _client()
    stream = client._stream_lines(
        'http://example.invalid', headers={}, data={}, timeout=1,
        endpoint=client._primary_endpoint())
    next(stream)
    log_context.clear()

    stream.close()

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.requirement_id, row.trace_id, row.turn_index) == (901, 'attempt-test', 3)
        assert row.status == 'error'
        assert row.meta['error'] == 'cancelled'
        assert row.meta['has_usage'] is False


def test_nonstream_accounting_uses_context_from_before_http(accounting_db, monkeypatch):
    response = _response('done', {'prompt_tokens': 80, 'completion_tokens': 20})

    def post(*args, **kwargs):
        log_context.clear()
        return response

    monkeypatch.setattr('llm.client.requests.post', post)
    log_context.bind_turn_index(3)

    assert _client().chat('hello').content == 'done'

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert (row.requirement_id, row.trace_id, row.turn_index) == (901, 'attempt-test', 3)
        assert (row.status, row.tokens_in, row.tokens_out) == ('ok', 80, 20)


def test_stream_cleanup_failure_does_not_replace_transport_error(accounting_db, monkeypatch):
    response = _stream_response()

    def lines():
        yield b'data: {"usage":{"prompt_tokens":80,"completion_tokens":20}}'
        raise requests.exceptions.ChunkedEncodingError('stream disconnected')

    response.iter_lines.side_effect = lines
    response.close.side_effect = OSError('close failed')
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=response))
    client = _client()

    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        list(client._stream_lines(
            'http://example.invalid', headers={}, data={}, timeout=1,
            endpoint=client._primary_endpoint()))

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert row.status == 'error'
        assert row.meta['error'] == 'ChunkedEncodingError'
        assert (row.tokens_in, row.tokens_out) == (80, 20)


def test_nonstream_keyboard_interrupt_is_recorded_as_error(accounting_db, monkeypatch):
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=KeyboardInterrupt))
    client = _client()

    with pytest.raises(KeyboardInterrupt):
        client._post_json('http://example.invalid', headers={}, data={}, timeout=1,
                          endpoint=client._primary_endpoint())

    with accounting_db() as db:
        row = db.query(AgentEvent).one()
        assert row.status == 'error'
        assert row.meta['error'] == 'KeyboardInterrupt'
