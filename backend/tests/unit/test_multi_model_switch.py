# -*- coding: utf-8 -*-
"""多厂商切换（Agnes / DeepSeek）配置派生守卫

背景（2026-10-05 实测）：两个端点的「最优配置」并不通用 —— 换端点时最容易漏掉的
不是 base_url/model，而是下面这几项**厂商私有约束**：

  | 配置项                        | Agnes            | DeepSeek                     |
  |-------------------------------|------------------|------------------------------|
  | LLM_MAX_TOKENS                | 32000（关思考）  | 12000（强制思考，额度被挤占）|
  | LLM_THINKING_ECHO_REASONING   | false            | **true**（否则 tools 请求 400）|
  | LLM_SLOW_TURN_TIMEOUT         | 0                | 0（强制思考型别开熔断）      |

其中 `LLM_THINKING_ECHO_REASONING` 是唯一一个「写错就必然 400」的，而 .env 里
它出现在多模型段**之后**（dotenv 后者胜），于是「只改 base_url + model」的切换
会静默踩雷。本文件锁定：只要厂商是 DeepSeek，回传 reasoning 就必须生效。
"""

from config import settings
from llm.client import LLMClient


def _client(url: str, **kw) -> LLMClient:
    return LLMClient(api_key="test_key", base_url=url, **kw)


class TestEchoReasoningDerivation:
    """echo_reasoning 由「显式开启 OR 厂商推断」决定"""

    def test_deepseek_forces_echo_even_when_setting_says_false(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_VENDOR", "auto")
        monkeypatch.setattr(settings, "LLM_THINKING_ECHO_REASONING", False)
        client = _client("https://api.deepseek.com/v1")
        assert client._resolve_vendor() == "deepseek"
        assert client.echo_reasoning is True, (
            "DeepSeek + tools 场景不回传 reasoning_content 必然 400；"
            "该组合不存在能跑通的场景，必须自动兜住"
        )

    def test_agnes_keeps_setting_false(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_VENDOR", "auto")
        monkeypatch.setattr(settings, "LLM_THINKING_ECHO_REASONING", False)
        client = _client("https://api.agnes-ai.cn/v1")
        assert client._resolve_vendor() == "agnes"
        assert client.echo_reasoning is False

    def test_explicit_vendor_wins_over_url(self, monkeypatch):
        """LLM_VENDOR 显式写成 deepseek 时，即使 base_url 是别的中转地址也认"""
        monkeypatch.setattr(settings, "LLM_VENDOR", "deepseek")
        monkeypatch.setattr(settings, "LLM_THINKING_ECHO_REASONING", False)
        client = _client("https://some-relay.example.com/v1")
        assert client._resolve_vendor() == "deepseek"
        assert client.echo_reasoning is True
