# -*- coding: utf-8 -*-
"""
单轮 LLM 长尾熔断守卫。

背景（req 146-159 实测 155 轮）：延迟 >60s 的轮次只占 20%，却吃掉 71% 的
LLM 总时间，且这些慢轮输出很短（中位 583 token）——是空转/抖动，不是"写太长"。
所以耗时优化的第一杠杆不是"少写点"，而是别在空转的轮次上干等。

守四条不可回退的行为：
1. 首试必须关闭内部重试——否则实际墙钟 = 预算 ×(N+1)，熔断形同虚设；
2. 只有超时才熔断重试，HTTP 4xx 之类重试必然复现，不该浪费一轮；
3. 熔断最多一次，重试给更宽的预算——防止误杀真的需要长输出的轮次；
4. **重试同样必须带 timeout 且关闭内部重试**（req 162 的教训）：
   此前重试两个参数都不传，timeout 落到 LLM_TIMEOUT、max_retries 落到
   LLM_MAX_RETRIES，单次"降级重试"最坏挂 3 × 300 = 900s。45s 的熔断
   本想快速止损，结果换来三轮各 300s 的慢失败——需求等满 15 分钟后
   仍然判 failed，一个文件都没写出来。
"""

from types import SimpleNamespace

from harness.runtime import ToolCallLoop


class _Resp:
    def __init__(self, error=None, content="ok"):
        self.error = error
        self.content = content
        self.is_error = bool(error)
        self.tool_calls = None
        self.finish_reason = None


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def _make_loop(budget: int) -> ToolCallLoop:
    # 绕过 __init__：这里只测熔断方法，不需要真实的 workspace/工具注册表
    loop = ToolCallLoop.__new__(ToolCallLoop)
    loop._settings = SimpleNamespace(LLM_SLOW_TURN_TIMEOUT=budget, LLM_TIMEOUT=300)
    loop._max_tokens = 32000
    return loop


def test_budget_disabled_calls_once_without_timeout():
    loop = _make_loop(0)
    client = _FakeClient([_Resp()])
    loop._chat_with_breaker(client, [], [], "disabled", 0)

    assert len(client.calls) == 1
    assert "timeout" not in client.calls[0]


def test_fast_turn_is_not_retried():
    loop = _make_loop(45)
    client = _FakeClient([_Resp()])
    loop._chat_with_breaker(client, [], [], "disabled", 0)

    assert len(client.calls) == 1
    assert client.calls[0]["timeout"] == 45
    # 关键：内部重试必须关掉，否则 45s 预算会被放大成 135s
    assert client.calls[0]["max_retries"] == 0


def test_timeout_triggers_single_degraded_retry():
    loop = _make_loop(45)
    client = _FakeClient([
        _Resp(error="[错误] 工具调用失败：HTTPSConnectionPool Read timed out"),
        _Resp(content="ok"),
    ])
    resp = loop._chat_with_breaker(client, [], [], "disabled", 2)

    assert len(client.calls) == 2
    assert resp.is_error is False
    # 降级：输出上限减半
    assert client.calls[1]["max_tokens"] == 16000
    # 重试放宽预算（45 × 3），但仍必须显式约束 timeout 与 max_retries：
    # 不传就会落回默认 300s × 3 次重试，把 45s 熔断变成 15 分钟的等待
    assert client.calls[1]["timeout"] == 135
    assert client.calls[1]["max_retries"] == 0


def test_retry_budget_never_exceeds_configured_llm_timeout():
    """重试预算以 LLM_TIMEOUT 封顶：熔断预算被调大时也不能突破单次调用上限。

    下限 120s 保证短预算场景（如 budget=10）仍有可用的重试窗口。
    """
    loop = _make_loop(200)  # 200 × 3 = 600 > LLM_TIMEOUT(300)
    client = _FakeClient([
        _Resp(error="[错误] Read timed out"),
        _Resp(content="ok"),
    ])
    loop._chat_with_breaker(client, [], [], "disabled", 0)

    assert client.calls[1]["timeout"] == 300

    loop_short = _make_loop(10)  # 10 × 3 = 30 < 下限 120
    client_short = _FakeClient([
        _Resp(error="[错误] Read timed out"),
        _Resp(content="ok"),
    ])
    loop_short._chat_with_breaker(client_short, [], [], "disabled", 0)

    assert client_short.calls[1]["timeout"] == 120


def test_timeout_marker_is_case_insensitive():
    loop = _make_loop(45)
    client = _FakeClient([_Resp(error="[错误] Read Timeout"), _Resp()])
    loop._chat_with_breaker(client, [], [], "disabled", 0)
    assert len(client.calls) == 2


def test_non_timeout_error_is_not_retried():
    """HTTP 400 之类重试必然复现，多打一轮纯属浪费。"""
    loop = _make_loop(45)
    client = _FakeClient([_Resp(error="[错误] 工具调用失败：HTTP 400 Bad Request")])
    loop._chat_with_breaker(client, [], [], "disabled", 0)

    assert len(client.calls) == 1


def test_retry_failure_is_propagated_not_swallowed():
    """熔断重试也失败时，必须把错误交给上层，不能伪装成成功。"""
    loop = _make_loop(45)
    client = _FakeClient([
        _Resp(error="[错误] Read timed out"),
        _Resp(error="[错误] Read timed out"),
    ])
    resp = loop._chat_with_breaker(client, [], [], "disabled", 0)

    assert len(client.calls) == 2
    assert resp.is_error is True
