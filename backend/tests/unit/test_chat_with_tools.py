# -*- coding: utf-8 -*-
"""
测试 chat_with_tools() 请求格式、响应解析、tool_calls 提取
对应 tasks.md 1.5
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from llm.client import LLMClient, LLMResponse, ToolCall, Message


class TestChatWithToolsOpenAI:
    """OpenAI function calling 协议测试"""

    def test_openai_tool_call_response_parsing(self):
        """测试 OpenAI tool call 响应正确解析为 ToolCall 列表"""
        client = LLMClient(api_key="test_key", provider="openai_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "我来创建文件",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"filename": "index.html", "content": "<html></html>"}'
                            }
                        }
                    ]
                }
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_openai_with_tools(
                [{"role": "user", "content": "创建 index.html"}],
                [{"type": "function", "function": {"name": "write_file", "description": "写入文件", "parameters": {}}}],
                "auto"
            )

        assert len(tool_calls) == 1
        assert tool_calls[0].name == "write_file"
        assert tool_calls[0].arguments == {"filename": "index.html", "content": "<html></html>"}
        assert usage == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        assert content == "我来创建文件"

    def test_openai_multiple_tool_calls(self):
        """测试 OpenAI 多工具调用响应"""
        client = LLMClient(api_key="test_key", provider="openai_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {"name": "read_file", "arguments": '{"filename": "style.css"}'}
                        },
                        {
                            "function": {"name": "write_file", "arguments": '{"filename": "app.js", "content": "console.log(1)"}'}
                        }
                    ]
                }
            }],
            "usage": None
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_openai_with_tools(
                [{"role": "user", "content": "修改文件"}],
                [], "auto"
            )

        assert len(tool_calls) == 2
        assert tool_calls[0].name == "read_file"
        assert tool_calls[1].name == "write_file"
        assert tool_calls[1].arguments["filename"] == "app.js"

    def test_openai_no_tool_calls(self):
        """测试 OpenAI 无工具调用的响应"""
        client = LLMClient(api_key="test_key", provider="openai_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "任务完成",
                    "tool_calls": []
                }
            }],
            "usage": {"total_tokens": 30}
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_openai_with_tools(
                [{"role": "user", "content": "ok"}], [], "auto"
            )

        assert tool_calls is None
        assert content == "任务完成"

    def test_openai_broken_json_repair(self):
        """测试 OpenAI tool call 损坏 JSON 自动修复"""
        client = LLMClient(api_key="test_key", provider="openai_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "write_file",
                            "arguments": '{"filename": "test.html", "content": "<h1>Hello'  # 不完整 JSON
                        }
                    }]
                }
            }],
            "usage": None
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_openai_with_tools(
                [{"role": "user", "content": "创建文件"}], [], "auto"
            )

        # 修复后应该能解析为 dict
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "write_file"
        assert tool_calls[0].arguments["filename"] == "test.html"

    def test_openai_non_string_arguments(self):
        """测试 OpenAI tool call arguments 已是 dict 格式"""
        client = LLMClient(api_key="test_key", provider="openai_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "write_file",
                            "arguments": {"filename": "test.html", "content": "ok"}
                        }
                    }]
                }
            }],
            "usage": None
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_openai_with_tools(
                [{"role": "user", "content": "创建文件"}], [], "auto"
            )

        assert len(tool_calls) == 1
        assert tool_calls[0].arguments == {"filename": "test.html", "content": "ok"}


class TestChatWithToolsAnthropic:
    """Anthropic tool use 协议测试"""

    def test_anthropic_tool_use_format_conversion(self):
        """测试 Anthropic 工具格式转换为 OpenAI 兼容格式"""
        client = LLMClient(api_key="test_key", provider="anthropic_compatible")

        tools = [{
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写入文件",
                "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}}
            }
        }]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {"type": "text", "text": "我将创建文件"},
                {"type": "tool_use", "name": "write_file", "input": {"filename": "test.html", "content": "ok"}}
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_anthropic_with_tools(
                [{"role": "user", "content": "创建文件"}], tools
            )

        assert len(tool_calls) == 1
        assert tool_calls[0].name == "write_file"
        assert tool_calls[0].arguments == {"filename": "test.html", "content": "ok"}
        assert content == "我将创建文件"
        assert usage == {"input_tokens": 100, "output_tokens": 50}

    def test_anthropic_text_only_response(self):
        """测试 Anthropic 纯文本响应（无工具调用）"""
        client = LLMClient(api_key="test_key", provider="anthropic_compatible")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {"type": "text", "text": "所有文件已创建完成"}
            ],
            "usage": None
        }

        with patch("llm.client.requests.post", return_value=mock_response):
            content, _reasoning, tool_calls, usage = client._request_anthropic_with_tools(
                [{"role": "user", "content": "ok"}], []
            )

        assert tool_calls is None
        assert content == "所有文件已创建完成"


class TestLLMResponse:
    """LLMResponse 数据类测试"""

    def test_llm_response_with_tool_calls(self):
        """测试 LLMResponse 包含 tool_calls 字段"""
        tc = ToolCall(name="write_file", arguments={"filename": "test.html"})
        response = LLMResponse(
            content="created",
            tool_calls=[tc],
            usage={"total_tokens": 100},
            finish_reason="tool_use"
        )
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "write_file"
        assert response.is_error is False

    def test_llm_response_error(self):
        """测试 LLMResponse 错误状态"""
        response = LLMResponse(content="", error="API 超时")
        assert response.is_error is True

    def test_llm_response_no_tool_calls(self):
        """测试 LLMResponse 无 tool_calls"""
        response = LLMResponse(content="done")
        assert response.tool_calls is None
        assert response.content == "done"


class TestToolCall:
    """ToolCall 数据类测试"""

    def test_tool_call_basic(self):
        """测试 ToolCall 基本创建"""
        tc = ToolCall(name="read_file", arguments={"filename": "test.html"})
        assert tc.name == "read_file"
        assert tc.arguments == {"filename": "test.html"}

    def test_tool_call_empty_arguments(self):
        """测试 ToolCall 空参数"""
        tc = ToolCall(name="list_files", arguments={})
        assert tc.name == "list_files"
        assert tc.arguments == {}
