# -*- coding: utf-8 -*-
"""
LLM 客户端并发安全测试

回归保护：LLMClient 的全局单例被 TaskQueue 的线程池（max_workers=3）共享，
历史上 chat()/chat_with_tools() 会临时修改 self.max_tokens/self.timeout 再恢复，
多线程并发时会出现「A 的 max_tokens 被 B 覆盖」的竞态。

修复后：max_tokens/timeout 作为调用级参数贯穿到请求层，绝不修改实例状态。
本测试用真实线程并发 + 不同 max_tokens 校验每个请求都带上了自己指定的值。
"""

import threading
from unittest.mock import patch, MagicMock

import pytest

from llm.client import LLMClient


def _make_ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    resp.raise_for_status = MagicMock()
    return resp


class TestChatConcurrency:
    """chat() 并发不修改实例状态"""

    def test_chat_does_not_mutate_max_tokens(self):
        """单线程：chat(max_tokens=X) 后实例 max_tokens 不变"""
        client = LLMClient(api_key="test_key", max_tokens=4000)

        with patch("llm.client.requests.post", return_value=_make_ok_response()):
            client.chat("hi", use_memory=False, max_tokens=12345)

        assert client.max_tokens == 4000  # 未被覆盖

    def test_chat_does_not_mutate_timeout(self):
        client = LLMClient(api_key="test_key", timeout=60)

        with patch("llm.client.requests.post", return_value=_make_ok_response()):
            client.chat("hi", use_memory=False, timeout=7)

        assert client.timeout == 60

    def test_concurrent_chat_each_request_carries_own_max_tokens(self):
        """
        多线程：每个线程用不同的 max_tokens 并发调用共享 client，
        断言每次发往 API 的请求体都带有该线程指定的 max_tokens（不被别的线程覆盖）。
        """
        client = LLMClient(api_key="test_key", max_tokens=4000)
        captured = []  # [(thread_tag, max_tokens_sent)]
        lock = threading.Lock()

        def fake_post(url, headers=None, json=None, timeout=None, **kw):
            with lock:
                captured.append((json["messages"][0]["content"], json["max_tokens"]))
            return _make_ok_response()

        token_values = [111, 222, 333, 444, 555]
        threads = []

        with patch("llm.client.requests.post", side_effect=fake_post):
            def worker(tag, mt):
                client.chat(tag, use_memory=False, max_tokens=mt)

            for mt in token_values:
                t = threading.Thread(target=worker, args=(f"req-{mt}", mt))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        # 每个请求的 max_tokens 必须等于它自己传入的值（一一对应，无串扰）
        sent_map = {tag: mt for tag, mt in captured}
        for mt in token_values:
            assert sent_map[f"req-{mt}"] == mt, (
                f"竞态：req-{mt} 期望 max_tokens={mt}，实际={sent_map[f'req-{mt}']}"
            )


class TestChatWithToolsConcurrency:
    """chat_with_tools() 并发不修改实例状态"""

    def test_chat_with_tools_does_not_mutate_max_tokens(self):
        client = LLMClient(api_key="test_key", provider="openai_compatible", max_tokens=4000)

        with patch("llm.client.requests.post", return_value=_make_ok_response()):
            client.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                max_tokens=999,
            )

        assert client.max_tokens == 4000

    def test_concurrent_chat_with_tools_carries_own_max_tokens(self):
        client = LLMClient(api_key="test_key", provider="openai_compatible", max_tokens=4000)
        captured = []
        lock = threading.Lock()

        def fake_post(url, headers=None, json=None, timeout=None, **kw):
            with lock:
                captured.append((json["messages"][0]["content"], json["max_tokens"]))
            return _make_ok_response()

        token_values = [100, 200, 300]
        with patch("llm.client.requests.post", side_effect=fake_post):
            threads = []
            for mt in token_values:
                t = threading.Thread(target=client.chat_with_tools,
                                     args=([{"role": "user", "content": f"tw-{mt}"}], []),
                                     kwargs={"max_tokens": mt})
                threads.append(t)
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        sent_map = {tag: mt for tag, mt in captured}
        for mt in token_values:
            assert sent_map[f"tw-{mt}"] == mt


class TestUseMemoryDefault:
    """use_memory 默认值改为 False，避免单例跨请求串扰"""

    def test_chat_default_use_memory_is_false(self):
        """不显式传 use_memory 时默认 False，不写入共享 _messages"""
        client = LLMClient(api_key="test_key")
        with patch("llm.client.requests.post", return_value=_make_ok_response()):
            client.chat("hi")  # 不传 use_memory
        assert len(client._messages) == 0
