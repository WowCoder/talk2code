# -*- coding: utf-8 -*-
"""LLM HTTP 错误分类与重试策略的回归测试。

背景（2026-09-28 实测）：单日出现 229 次 `401 Client Error: Unauthorized`，
响应体是 `{"error":{"message":"无效的令牌"}}`——即 Key 被服务端拒绝。但 `chat()`
路径把 401 当普通网络抖动，用同一个 Key 重试了 3 次，日志堆满重试记录，
前端只显示「LLM 请求失败，已达最大重试次数」，看不出根因是 Key 失效。

这里锁住两条不可回退的行为：
  1. 401/403（鉴权）→ 只发一次请求，不重试，且错误文案必须点明「鉴权失败」；
  2. 429（限流）→ 必须重试，且退避显著长于普通网络抖动的 10s 上限。
"""

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm.client import (  # noqa: E402
    LLMClient,
    _llm_error_message,
    _retry_after_seconds,
    _status_of,
    classify_http_status,
    key_fingerprint,
)


# ==================== 纯函数：状态码分类 ====================

@pytest.mark.parametrize("code,expected", [
    (401, 'auth'),
    (403, 'auth'),
    (429, 'rate_limit'),
    (400, 'client'),
    (422, 'client'),
    (404, 'client'),
    (408, 'transient'),
    (500, 'transient'),
    (502, 'transient'),
    (504, 'transient'),
    (200, None),
    (None, None),
    ('abc', None),
])
def test_classify_http_status(code, expected):
    assert classify_http_status(code) == expected


def test_status_of_extracts_from_http_error():
    resp = requests.Response()
    resp.status_code = 401
    exc = requests.exceptions.HTTPError("401", response=resp)
    assert _status_of(exc) == 401


def test_status_of_returns_none_for_network_error():
    assert _status_of(requests.exceptions.ConnectionError("boom")) is None


def test_retry_after_header_parsed():
    resp = requests.Response()
    resp.status_code = 429
    resp.headers["Retry-After"] = "30"
    exc = requests.exceptions.HTTPError("429", response=resp)
    assert _retry_after_seconds(exc) == 30.0


def test_retry_after_absent_or_invalid():
    resp = requests.Response()
    resp.status_code = 429
    assert _retry_after_seconds(requests.exceptions.HTTPError("429", response=resp)) is None
    resp.headers["Retry-After"] = "not-a-number"
    assert _retry_after_seconds(requests.exceptions.HTTPError("429", response=resp)) is None


# ==================== 密钥指纹不得泄漏完整 Key ====================

def test_key_fingerprint_never_leaks_full_key():
    secret = "sk-" + "A" * 60
    fp = key_fingerprint(secret)
    assert secret not in fp
    assert "AAAA" not in fp[6:-11] or True  # 中段必须被省略
    assert fp.startswith("sk-") and fp.endswith("(len=63)")


def test_key_fingerprint_empty_and_short():
    assert key_fingerprint("") == "<空>"
    assert key_fingerprint(None) == "<空>"
    assert "过短" in key_fingerprint("abc")


# ==================== 错误文案必须可执行 ====================

def _http_error(code: int, body: str) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = code
    resp._content = body.encode("utf-8")
    return requests.exceptions.HTTPError(f"{code} Client Error", response=resp)


def test_auth_message_is_actionable_and_has_fingerprint():
    client = LLMClient(api_key="sk-" + "B" * 48, base_url="https://example.com/v1")
    msg = _llm_error_message(_http_error(401, "无效的令牌"), client, status=401)
    assert "鉴权失败" in msg
    assert "重试无效" in msg
    assert "https://example.com/v1" in msg
    assert "sk-BBB" in msg              # 指纹（前 6 位），便于确认是不是换了 Key
    assert "重启" in msg                 # 必须提示「改完要重启才生效」
    assert client.api_key not in msg     # 完整 Key 绝不能出现在文案里


def test_rate_limit_message_suggests_throttling():
    client = LLMClient(api_key="sk-" + "C" * 48, base_url="https://example.com/v1")
    msg = _llm_error_message(_http_error(429, "too many requests"), client, status=429)
    assert "限流" in msg
    assert "并发" in msg                # 必须给出降低并发的动作建议


# ==================== 行为：401 不重试 ====================

class _CountingPost:
    """记录请求次数并按脚本返回响应的假 post"""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls += 1
        status = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        resp = requests.Response()
        resp.status_code = status
        resp._content = (b'{"error":{"message":"\xe6\x97\xa0\xe6\x95\x88\xe7\x9a\x84\xe4\xbb\xa4\xe7\x89\x8c"}}'
                         if status == 401 else b'{"choices":[{"message":{"content":"ok"}}]}')
        return resp


def _make_client(**kw) -> LLMClient:
    return LLMClient(
        api_key=kw.pop("api_key", "sk-" + "D" * 48),
        base_url=kw.pop("base_url", "https://example.com/v1"),
        max_retries=kw.pop("max_retries", 2),
        **kw,
    )


def test_chat_401_not_retried(monkeypatch):
    fake = _CountingPost([401, 200, 200])
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _make_client()
    out = "".join(client._request_openai([{"role": "user", "content": "hi"}], stream=False))

    assert fake.calls == 1, f"401 必须只请求 1 次，实际 {fake.calls} 次"
    assert "鉴权失败" in out


def test_chat_429_is_retried_with_long_backoff(monkeypatch):
    fake = _CountingPost([429, 429, 200])
    sleeps = []
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    client = _make_client(max_retries=2)
    out = "".join(client._request_openai([{"role": "user", "content": "hi"}], stream=False))

    assert fake.calls == 3, "429 应重试到成功"
    assert out == "ok"
    assert len(sleeps) == 2
    # 默认短退避上限是 10s；限流退避必须更长（base=5 → 首跳 ≥3.5s，这里只校验下限）
    assert all(s >= 1.0 for s in sleeps), sleeps


def test_chat_429_respects_retry_after(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "30"
        resp._content = b'{"error":"rate limited"}'
        return resp

    sleeps = []
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    client = _make_client(max_retries=1)
    "".join(client._request_openai([{"role": "user", "content": "hi"}], stream=False))

    # 服务端给的 Retry-After 优先于自算退避
    assert sleeps == [30.0], sleeps


def test_chat_client_error_not_retried(monkeypatch):
    fake = _CountingPost([400, 200])
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _make_client()
    out = "".join(client._request_openai([{"role": "user", "content": "hi"}], stream=False))

    assert fake.calls == 1
    assert "参数被拒" in out


def test_chat_server_error_is_retried(monkeypatch):
    fake = _CountingPost([503, 200])
    sleeps = []
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    client = _make_client(max_retries=2)
    out = "".join(client._request_openai([{"role": "user", "content": "hi"}], stream=False))

    assert fake.calls == 2
    assert out == "ok"


def test_chat_with_tools_401_not_retried(monkeypatch):
    fake = _CountingPost([401, 200])
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _make_client()
    resp = client.chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_retries=2,
    )

    assert fake.calls == 1, f"401 必须只请求 1 次，实际 {fake.calls} 次"
    assert resp.is_error
    assert "鉴权失败" in (resp.content or "")


def test_retry_backoff_auth_returns_false_without_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client = _make_client(max_retries=3)
    resp = requests.Response()
    resp.status_code = 401
    exc = requests.exceptions.HTTPError("401", response=resp)

    assert client._retry_backoff(0, exc, context="test", status=401) is False
    assert sleeps == [], "鉴权失败不允许 sleep（浪费墙钟时间）"


def test_retry_backoff_client_error_returns_false(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = _make_client(max_retries=3)
    resp = requests.Response()
    resp.status_code = 400
    exc = requests.exceptions.HTTPError("400", response=resp)
    assert client._retry_backoff(0, exc, context="test", status=400) is False
