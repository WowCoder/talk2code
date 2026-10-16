# -*- coding: utf-8 -*-
"""Stage/purpose must exist at the HTTP boundary, before semantic enrichment."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from harness.observability.log_context import clear, llm_context
from harness.observability.cost import shared_cost_tracker
from llm.client import LLMClient
from models.models import AgentEvent, Base


@pytest.fixture
def stage_env(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr('harness.observability.trace_db.trace_session', sessions)
    client = LLMClient(api_key='test', base_url='http://example.invalid',
                       provider='openai_compatible', model='gpt-4o-mini',
                       max_retries=0, max_tokens=1000)
    client._has_backup = False
    monkeypatch.setattr('llm.client.get_client', lambda *args, **kwargs: client)
    monkeypatch.setattr('harness.instructions.nodes.get_client', lambda: client)
    monkeypatch.setattr('harness.instructions.intent_router.get_client', lambda: client)
    shared_cost_tracker().clear()
    with llm_context(requirement_id=901, trace_id='stage-test', turn_index=3):
        yield SimpleNamespace(client=client, sessions=sessions)
    clear()
    shared_cost_tracker().clear()
    engine.dispose()


def _http_reply(content='[]'):
    response = Mock(status_code=200)
    response.json.return_value = {
        'model': 'gpt-4o-mini',
        'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 80, 'completion_tokens': 20},
    }
    return response


def _rows(env):
    with env.sessions() as db:
        return [(row.stage, row.meta.get('purpose'), row.status)
                for row in db.query(AgentEvent).filter_by(kind='llm_turn')
                .order_by(AgentEvent.seq).all()]


@pytest.mark.parametrize('failed', [False, True])
def test_intent_attempt_has_planning_stage_before_result(stage_env, monkeypatch, failed):
    from harness.instructions.intent_router import IntentRouter
    monkeypatch.setattr('llm.client.requests.post', Mock(
        side_effect=requests.Timeout('failed intent') if failed else None,
        return_value=_http_reply('TASK')))
    router = IntentRouter()
    router._client = stage_env.client

    router.classify('Build a counter without external dependencies')

    assert _rows(stage_env) == [
        ('planning', 'intent_classification', 'error' if failed else 'ok')]


def test_clarification_failure_has_planning_stage(stage_env, monkeypatch):
    from harness.instructions.nodes import _generate_clarify_questions_ex
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=requests.Timeout('failed clarify')))

    _generate_clarify_questions_ex(stage_env.client, 'Build a simple counter')

    assert _rows(stage_env) == [('planning', 'clarification', 'error')]


def test_planning_retries_keep_planning_stage(stage_env, monkeypatch):
    from harness.instructions.nodes import team_leader_node
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=_http_reply('{')))

    team_leader_node({'requirement_content': '[用户补充说明] 极简风格计数器',
                      'requirement_id': 901, 'metadata': {}})

    rows = _rows(stage_env)
    assert len(rows) >= 2
    assert {(stage, purpose) for stage, purpose, _ in rows} == {('planning', 'plan_generation')}


@pytest.mark.parametrize('repair_count,stage', [(0, 'coding'), (1, 'repairing')])
def test_coder_entry_attributes_failures_to_current_round(
        stage_env, monkeypatch, repair_count, stage):
    from harness.instructions import nodes
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=requests.Timeout('failed code')))

    def implementation(state):
        stage_env.client.chat('code', use_memory=False)
        return {'current_step': 'coding_done'}

    monkeypatch.setattr(nodes, '_coder_node_impl', implementation)
    monkeypatch.setattr(nodes, '_emit_coding_milestone', lambda *args: None)

    nodes.coder_node({'metadata': {'repair_count': repair_count}})

    assert _rows(stage_env) == [(stage, 'code_generation', 'error')]


def test_coder_legacy_enrichment_does_not_replace_repair_stage(stage_env, monkeypatch):
    from harness.instructions import nodes
    from harness.observability.trace_writer import record_llm_turn
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=_http_reply('done')))

    def implementation(state):
        response = stage_env.client.chat('repair', use_memory=False)
        record_llm_turn(901, stage='coding', response=response, trace_id='stage-test')
        return {'current_step': 'coding_done'}

    monkeypatch.setattr(nodes, '_coder_node_impl', implementation)
    monkeypatch.setattr(nodes, '_emit_coding_milestone', lambda *args: None)

    nodes.coder_node({'metadata': {'repair_count': 1}})

    assert _rows(stage_env) == [('repairing', 'code_generation', 'ok')]


def test_ac_translation_failure_has_verifying_stage(stage_env, monkeypatch):
    from harness.instructions.nodes import _translate_one_ac_batch
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=requests.Timeout('failed translation')))

    _translate_one_ac_batch([{'id': 'AC-1', 'label': 'counter updates'}], '', 'dom', '')

    assert _rows(stage_env) == [('verifying', 'ac_translation', 'error')]


def test_reflection_failure_is_delivering(stage_env, monkeypatch):
    from harness.state.memory import MemoryManager
    manager = MemoryManager.__new__(MemoryManager)
    manager._llm = stage_env.client
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=requests.Timeout('failed reflection')))

    with llm_context(stage='verifying', purpose='quality_evaluation'):
        manager._reflect('counter', 'index.html', 7.0)

    assert _rows(stage_env) == [('delivering', 'memory_reflection', 'error')]


@pytest.mark.parametrize('stage', ['planning', 'coding', None])
def test_memory_filter_inherits_stage_without_inventing_one(stage_env, monkeypatch, stage):
    from harness.state.memory import Memory, MemoryManager
    manager = MemoryManager.__new__(MemoryManager)
    manager._llm = stage_env.client
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=_http_reply('[0]')))

    with llm_context(stage=stage, purpose='parent'):
        manager._llm_verify('counter', [Memory(requirement='counter') for _ in range(3)])

    assert _rows(stage_env) == [(stage, 'memory_filter', 'ok')]


def test_legacy_memory_filter_inherits_stage(stage_env, monkeypatch):
    from harness.state.memory_store import MemoryStore
    store = MemoryStore(llm_client=stage_env.client)
    monkeypatch.setattr('llm.client.requests.post', Mock(return_value=_http_reply('[0]')))

    with llm_context(stage='repairing'):
        store._llm_filter([{'memory_type': 'user_preference', 'fact': 'simple'}] * 3,
                          'counter', top_k=1)

    assert _rows(stage_env) == [('repairing', 'memory_filter', 'ok')]


@pytest.mark.parametrize('intent,answer,expected_stages', [
    ('QUICK', 'A counter stores a number.',
     [('planning', 'intent_classification', 'ok'), ('delivering', 'quick_answer', 'ok')]),
    ('AMBIGUOUS', '[{"id":"q1","label":"What should change?"}]',
     [('planning', 'intent_classification', 'ok'), ('planning', 'clarification', 'ok')]),
])
def test_chat_auxiliary_routes_use_existing_handlers(
        stage_env, monkeypatch, intent, answer, expected_stages):
    from flask_jwt_extended import create_access_token
    from models.models import Requirement, User
    import routes.requirements as routes
    monkeypatch.setattr(routes, 'transactional_db', stage_env.sessions)
    monkeypatch.setattr('harness.observability.sse_reporter.SSEReporter', lambda *args: Mock())
    monkeypatch.setattr('routes.preview.sse_manager.broadcast', lambda *args: None)
    monkeypatch.setattr('llm.client.requests.post', Mock(side_effect=[
        _http_reply(intent), _http_reply(answer)]))
    with stage_env.sessions() as db:
        user = User(username='stage-route-user', password_hash='test')
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(Requirement(id=901, user_id=user_id, title='counter', content='counter',
                           status='finished', dialogue_history=[], code_files=[]))
        db.commit()
    with routes.app.app_context():
        token = create_access_token(identity=str(user_id))

    response = routes.app.test_client().post(
        '/api/requirements/901/chat', json={'message': 'Explain this counter'},
        headers={'Authorization': 'Bearer ' + token})

    assert response.status_code == 200
    assert response.json.get('error') is None
    assert _rows(stage_env) == expected_stages
