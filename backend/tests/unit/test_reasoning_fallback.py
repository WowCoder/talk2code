# -*- coding: utf-8 -*-
"""
reasoning 模型 token 耗尽时的重试额度边界测试

背景（实测数据，见今日开发日志）：
对强制思考型模型（lkeap glm 全系，服务端明确「该模型始终思考，不支持关闭」），
max_tokens 是 **reasoning + content 的共享额度且 reasoning 先扣**。
流水线里大量按需写死的小额度调用（500/1000/2000/3000）一旦失败，原实现会把重试
额度抬到全局 LLM_MAX_TOKENS（32000）——该额度下单轮可达 ~680s，必然撞穿
LLM_TIMEOUT，整节点表现为「假挂死」。

本文件守护三条不变量：
  1. 重试额度有绝对天花板，不会被放大到全局 LLM_MAX_TOKENS；
  2. 天花板压不住时（req 已 >= 上限）如实放弃重试，不做无意义且必然超时的放大；
  3. 有正文时绝不触发重试。
"""

from unittest.mock import MagicMock, patch

import pytest

from llm.client import LLMClient

GLOBAL_MAX_TOKENS = 32000


def _make_client(fallback_cap: int = 8000) -> LLMClient:
    """构造一个模拟线上配置（全局 32000）的 client"""
    client = LLMClient(max_tokens=GLOBAL_MAX_TOKENS, max_retries=0)
    client.reasoning_fallback_tokens = fallback_cap
    return client


def _resp(content: str = '', reasoning: str = '') -> MagicMock:
    """mock 一次 chat/completions 的响应"""
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    r.json.return_value = {
        'choices': [{
            'message': {'content': content, 'reasoning_content': reasoning},
            'finish_reason': 'length' if not content else 'stop',
        }]
    }
    return r


class TestReasoningFallbackBound:
    """重试额度必须有边界"""

    def test_small_budget_retry_capped_not_amplified(self):
        """小额度调用（2000）失败后重试额度抬到 6000，绝不放大到全局 32000"""
        client = _make_client(fallback_cap=8000)
        with patch('llm.client.requests.post', side_effect=[_resp('', 'think' * 100), _resp('ok')]) as mp:
            out = ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                                 stream=False, max_tokens=2000))
        assert out == 'ok'
        budgets = [c.kwargs['json']['max_tokens'] for c in mp.call_args_list]
        assert budgets == [2000, 6000]
        assert max(budgets) < GLOBAL_MAX_TOKENS

    def test_never_exceeds_configured_cap_when_req_is_mid(self):
        """中等额度（5000）：抬到天花板 8000 而非翻倍到 10000"""
        client = _make_client(fallback_cap=8000)
        with patch('llm.client.requests.post', side_effect=[_resp('', 'x' * 50), _resp('ok')]) as mp:
            ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                           stream=False, max_tokens=5000))
        budgets = [c.kwargs['json']['max_tokens'] for c in mp.call_args_list]
        assert budgets == [5000, 8000]

    def test_no_retry_when_cap_cannot_help(self):
        """原额度已 >= 天花板：放弃重试，不做必然超时的放大"""
        client = _make_client(fallback_cap=8000)
        with patch('llm.client.requests.post', side_effect=[_resp('', 'think')]) as mp:
            out = ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                                 stream=False, max_tokens=12000))
        assert out == ''
        assert mp.call_count == 1, "额度已压不住时不应发起无意义的重试"

    def test_retry_still_empty_returns_empty_without_raising(self):
        """重试后仍为空：如实返回空串，交由上层错误处理，不抛异常"""
        client = _make_client()
        with patch('llm.client.requests.post',
                   side_effect=[_resp('', 't'), _resp('', 't' * 300)]) as mp:
            out = ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                                 stream=False, max_tokens=2000))
        assert out == ''
        assert mp.call_count == 2

    def test_no_retry_when_content_present(self):
        """有正文时不触发任何重试"""
        client = _make_client()
        with patch('llm.client.requests.post', side_effect=[_resp('hello', 'think')]) as mp:
            out = ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                                 stream=False, max_tokens=2000))
        assert out == 'hello'
        assert mp.call_count == 1


class TestReasoningFallbackConfig:
    """配置项本身可被调优"""

    def test_cap_is_configurable(self):
        """下调天花板后重试额度随之收敛"""
        client = _make_client(fallback_cap=4000)
        with patch('llm.client.requests.post', side_effect=[_resp('', 't'), _resp('ok')]) as mp:
            ''.join(client._request_openai([{'role': 'user', 'content': 'hi'}],
                                           stream=False, max_tokens=1000))
        budgets = [c.kwargs['json']['max_tokens'] for c in mp.call_args_list]
        assert budgets == [1000, 4000]

    def test_default_cap_present_in_settings(self):
        """配置默认值存在且位于合理区间"""
        from config import settings
        cap = settings.LLM_REASONING_FALLBACK_TOKENS
        assert 500 <= cap <= 20000, f"默认天花板 {cap} 超出合理区间"
