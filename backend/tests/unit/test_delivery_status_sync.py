# -*- coding: utf-8 -*-
"""交付终态同步 + 对话角色归属的回归测试。

背景（验收实测，req 202）：
1. 质量工程师验收通过后，「发布」TAB 一直不可点，必须手动刷新页面才能发布。
   根因是 `complete` 事件不带终态：前端 currentRequirement 是进页面时的 API
   快照，SSE 不更新它，于是状态永远停在 processing，发布门禁（status ==
   'finished'）继续拦截。
2. 对话流里凭空多出一条「用户消息：上一轮已交付。任务状态 handoff 见
   .task/DELIVERY.md，请据此继续。」。根因是交付折叠把系统注入的 handoff
   起点写成了 role='user'，在对话流里伪装成用户发言。

归属规则（本次固化）：
- 用户消息只能来自用户本人；
- Agent 侧消息只归属三个角色（TL / 开发工程师 / 质量工程师）；
- 无法判断归属时归 TL。
"""
import json
from contextlib import contextmanager

import pytest

from harness.agent_names import TL_NAME, DEV_NAME, QA_NAME
from utils.sse import SSEMessage


# ==================== complete 事件必须携带终态 ====================

def test_complete_message_carries_status():
    """带 status 时事件负载里必须有 status 字段（前端据此同步终态）。"""
    msg = SSEMessage.complete_message(202, status='finished')
    assert msg.startswith('event: complete\n')
    payload = json.loads(msg.split('data: ', 1)[1].strip())
    assert payload['requirement_id'] == 202
    assert payload['status'] == 'finished'


def test_complete_message_without_status_keeps_legacy_shape():
    """不传 status 时保持旧负载形状（向后兼容，前端不覆盖状态）。"""
    msg = SSEMessage.complete_message(7)
    payload = json.loads(msg.split('data: ', 1)[1].strip())
    assert payload == {'requirement_id': 7}


@pytest.mark.parametrize('status', [
    'finished', 'finished_with_issues', 'needs_user_input', 'failed',
])
def test_complete_message_passes_through_all_terminal_statuses(status):
    payload = json.loads(
        SSEMessage.complete_message(1, status=status).split('data: ', 1)[1].strip()
    )
    assert payload['status'] == status


# ==================== _send_complete 从 DB 读权威终态 ====================

class _FakeQuery:
    def __init__(self, value, raise_on_scalar=False):
        self._value = value
        self._raise = raise_on_scalar

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        if self._raise:
            raise RuntimeError('db down')
        return self._value


@contextmanager
def _fake_get_db(value='finished', raise_on_scalar=False):
    yield _FakeQuery(value, raise_on_scalar)


@pytest.fixture
def service_with_captured_broadcast(monkeypatch):
    """返回 (service, sent)；sent 收集 (channel, message)。"""
    import services.requirement_service as rs

    sent = []
    monkeypatch.setattr(
        rs.sse_manager, 'broadcast',
        lambda channel, message: sent.append((channel, message)),
    )
    # 不跑 __init__（它会拉整个 workflow），只要一个能调方法的实例
    service = object.__new__(rs.RequirementService)
    return service, sent, rs


def test_send_complete_reads_terminal_status_from_db(
    service_with_captured_broadcast, monkeypatch
):
    service, sent, rs = service_with_captured_broadcast
    monkeypatch.setattr(rs, 'get_db', lambda: _fake_get_db('finished'))

    service._send_complete(202)

    assert len(sent) == 1
    channel, message = sent[0]
    assert channel == '202'
    payload = json.loads(message.split('data: ', 1)[1].strip())
    assert payload['status'] == 'finished'


def test_send_complete_prefers_explicit_status(
    service_with_captured_broadcast, monkeypatch
):
    """调用方显式传入终态时不再查库（省一次连接）。"""
    service, sent, rs = service_with_captured_broadcast

    def _boom():
        raise AssertionError('不应查库')

    monkeypatch.setattr(rs, 'get_db', _boom)
    service._send_complete(1, status='needs_user_input')

    payload = json.loads(sent[0][1].split('data: ', 1)[1].strip())
    assert payload['status'] == 'needs_user_input'


def test_send_complete_degrades_when_db_fails(
    service_with_captured_broadcast, monkeypatch
):
    """读库失败不能被吞成「不推送 complete」——事件照发，只是不带终态。"""
    service, sent, rs = service_with_captured_broadcast
    monkeypatch.setattr(rs, 'get_db', lambda: _fake_get_db(raise_on_scalar=True))

    service._send_complete(9)

    assert len(sent) == 1
    payload = json.loads(sent[0][1].split('data: ', 1)[1].strip())
    assert payload == {'requirement_id': 9}


# ==================== 角色归属规则 ====================

def test_three_role_names_are_the_only_agent_identities():
    """三个角色名是 Agent 侧唯一合法身份（新增角色必须同步前端归一化表）。"""
    assert TL_NAME == 'Leon（技术负责人）'
    assert DEV_NAME == 'Henry（开发工程师）'
    assert QA_NAME == 'Catherine（质量工程师）'
    assert len({TL_NAME, DEV_NAME, QA_NAME}) == 3


def test_handoff_note_is_attributed_to_tl():
    """交付折叠注入的 handoff 起点必须是 agent/TL，不能是 user。"""
    from harness.state.context_pipeline import finalize_delivery

    class _Ws:
        def __init__(self):
            self.files = {}

        def write(self, path, content):
            self.files[path] = content

        def read(self, path):
            return self.files[path]

        def list(self):
            return list(self.files)

    ws = _Ws()
    state = {
        'requirement_content': '做一个贪吃龙',
        'plan': {'file_structure': ['index.html']},
        'dialogue_history': [{'role': 'user', 'content': '做一个贪吃龙'}],
    }
    path = finalize_delivery(state, ws)
    assert path == '.task/DELIVERY.md'

    handoffs = [
        m for m in state['dialogue_history']
        if '上一轮已交付' in str(m.get('content', ''))
    ]
    assert len(handoffs) == 1
    assert handoffs[0]['role'] == 'agent'
    assert handoffs[0]['name'] == TL_NAME
    # 对话流里不得出现任何一条伪装成用户的系统注入消息
    assert not [
        m for m in state['dialogue_history']
        if m.get('role') == 'user' and m.get('name') not in (None, '用户')
    ]
