# -*- coding: utf-8 -*-
"""
LLM 客户端单元测试
使用 mock 避免实际 API 调用
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import requests

from llm.client import (
    LLMClient,
    LLMResponse,
    Message,
    get_client,
    clear_client_memory,
    chat_with_llm,
    chat_with_llm_stream
)


class TestMessage:
    """Message 数据类测试"""

    def test_message_creation(self):
        """测试消息创建"""
        msg = Message(role='user', content='Hello')
        assert msg.role == 'user'
        assert msg.content == 'Hello'
        assert msg.timestamp is not None

    def test_message_timestamp_format(self):
        """测试时间戳格式"""
        msg = Message(role='assistant', content='Response')
        # 时间戳格式应该是 YYYY-MM-DD HH:MM:SS
        assert len(msg.timestamp) == 19
        assert '-' in msg.timestamp
        assert ':' in msg.timestamp


class TestLLMResponse:
    """LLMResponse 数据类测试"""

    def test_response_creation_success(self):
        """测试成功响应创建"""
        response = LLMResponse(content='Test response')
        assert response.content == 'Test response'
        assert response.is_error is False
        assert response.error is None

    def test_response_creation_error(self):
        """测试错误响应创建"""
        response = LLMResponse(content='', error='API failed')
        assert response.is_error is True
        assert response.error == 'API failed'

    def test_response_with_usage(self):
        """测试带用量信息的响应"""
        response = LLMResponse(
            content='Test',
            usage={'total_tokens': 100}
        )
        assert response.usage['total_tokens'] == 100


class TestLLMClientInit:
    """LLMClient 初始化测试"""

    def test_client_init_with_api_key(self):
        """测试使用 API Key 初始化"""
        client = LLMClient(api_key='test_key')
        assert client.api_key == 'test_key'
        assert client.model is not None  # 从 .env 加载的模型名

    def test_client_init_default_params(self):
        """测试默认参数（对照 config 实际值，不与本地 .env 覆盖耦合）"""
        from config import settings
        client = LLMClient(api_key='test_key')
        assert client.temperature == 0.7
        assert client.max_tokens == settings.LLM_MAX_TOKENS
        assert client.timeout == settings.LLM_TIMEOUT
        assert client.max_retries == settings.LLM_MAX_RETRIES

    def test_client_init_custom_params(self):
        """测试自定义参数"""
        client = LLMClient(
            api_key='test_key',
            temperature=0.5,
            max_tokens=2000,
            timeout=30
        )
        assert client.temperature == 0.5
        assert client.max_tokens == 2000
        assert client.timeout == 30

    def test_client_init_no_api_key_raises(self):
        """测试缺少 API Key 抛出异常"""
        with patch('llm.client.LLM_API_KEY', ''):
            with pytest.raises(ValueError, match="API_KEY"):
                LLMClient()

    def test_client_empty_memory_on_init(self):
        """测试初始化时记忆为空"""
        client = LLMClient(api_key='test_key')
        assert len(client._messages) == 0


class TestLLMClientMemory:
    """LLMClient 记忆管理测试"""

    def test_clear_memory(self):
        """测试清空记忆"""
        client = LLMClient(api_key='test_key')
        client._messages.append(Message(role='user', content='Test'))
        client.clear_memory()
        assert len(client._messages) == 0

    def test_load_memory(self):
        """测试加载记忆"""
        client = LLMClient(api_key='test_key')
        dialogue_history = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi'},
            {'role': 'system', 'content': 'System message'}
        ]
        client.load_memory(dialogue_history)
        assert len(client._messages) == 3
        assert client._messages[0].role == 'user'
        assert client._messages[1].role == 'assistant'
        assert client._messages[2].role == 'system'

    def test_get_memory(self):
        """测试获取记忆"""
        client = LLMClient(api_key='test_key')
        client._messages.append(Message(role='user', content='Test'))
        memory = client.get_memory()
        assert len(memory) == 1
        assert memory[0] == {'role': 'user', 'content': 'Test'}

    def test_memory_load_with_agent_role(self):
        """测试加载 agent 角色（映射为 assistant）"""
        client = LLMClient(api_key='test_key')
        dialogue_history = [
            {'role': 'agent', 'content': 'Agent message'}
        ]
        client.load_memory(dialogue_history)
        assert client._messages[0].role == 'assistant'


class TestLLMClientChat:
    """LLMClient 聊天测试"""

    @patch('llm.client.requests.post')
    def test_chat_success(self, mock_post):
        """测试成功聊天"""
        mock_response = Mock()
        mock_response.json.return_value = {
            'choices': [
                {'message': {'content': 'AI response'}}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = LLMClient(api_key='test_key')
        response = client.chat('Hello', use_memory=False)

        assert response.content == 'AI response'
        assert response.is_error is False

    @patch('llm.client.requests.post')
    def test_chat_with_system_prompt(self, mock_post):
        """测试带系统提示的聊天"""
        mock_response = Mock()
        mock_response.json.return_value = {
            'choices': [
                {'message': {'content': 'Response'}}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = LLMClient(api_key='test_key')
        response = client.chat('Hello', system_prompt='Be helpful', use_memory=False)

        # 验证请求包含系统提示
        call_args = mock_post.call_args
        messages = call_args[1]['json']['messages']
        assert messages[0]['role'] == 'system'
        assert messages[0]['content'] == 'Be helpful'

    @patch('llm.client.requests.post')
    def test_chat_saves_to_memory(self, mock_post):
        """测试聊天保存到记忆"""
        mock_response = Mock()
        mock_response.json.return_value = {
            'choices': [
                {'message': {'content': 'AI response'}}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = LLMClient(api_key='test_key')
        response = client.chat('Hello', use_memory=True)

        assert len(client._messages) == 2
        assert client._messages[0].role == 'user'
        assert client._messages[1].role == 'assistant'

    @patch('llm.client.requests.post')
    def test_chat_error_handling(self, mock_post):
        """测试错误处理"""
        mock_post.side_effect = Exception("API Error")

        client = LLMClient(api_key='test_key')
        response = client.chat('Hello', use_memory=False)

        assert response.is_error is True
        assert "错误" in response.content


class TestLLMClientStream:
    """LLMClient 流式聊天测试"""

    @patch('llm.client.requests.post')
    def test_chat_stream_success(self, mock_post):
        """测试流式聊天成功"""
        # 模拟流式响应
        mock_response = Mock()
        mock_response.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            b'data: {"choices":[{"delta":{"content":" World"}}]}',
            b'data: [DONE]'
        ]
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = LLMClient(api_key='test_key')
        chunks = list(client.chat_stream('Test', use_memory=False))

        assert len(chunks) == 2
        assert chunks[0] == 'Hello'
        assert chunks[1] == ' World'


class TestGetClient:
    """get_client 函数测试"""

    def test_get_client_singleton(self):
        """测试获取默认客户端单例"""
        with patch('llm.client._client', None):
            with patch('llm.client.LLM_API_KEY', 'test_key'):
                client1 = get_client()
                client2 = get_client()
                assert client1 is client2

    def test_get_client_with_instance_id(self):
        """测试使用实例 ID 获取客户端"""
        with patch('llm.client.LLM_API_KEY', 'test_key'):
            client1 = get_client('instance1')
            client2 = get_client('instance2')
            assert client1 is not client2

    def test_clear_client_memory(self):
        """测试清空客户端记忆"""
        with patch('llm.client.LLM_API_KEY', 'test_key'):
            client = get_client('test_clear')
            client._messages.append(Message(role='user', content='Test'))
            clear_client_memory('test_clear')
            assert len(client._messages) == 0


class TestChatWithLLM:
    """chat_with_llm 快捷函数测试"""

    @patch('llm.client.requests.post')
    def test_chat_with_llm_function(self, mock_post):
        """测试快捷聊天函数"""
        mock_response = Mock()
        mock_response.json.return_value = {
            'choices': [
                {'message': {'content': 'Response'}}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch('llm.client.LLM_API_KEY', 'test_key'):
            result = chat_with_llm('Hello', 'Be helpful')
            assert result == 'Response'


class TestLLMClientRetry:
    """LLMClient 重试逻辑测试"""

    @patch('llm.client.requests.post')
    @patch('llm.client.time.sleep')
    def test_retry_on_failure(self, mock_sleep, mock_post):
        """测试失败后重试"""
        # 第一次失败，第二次成功（用 RequestException 触发内层重试）
        mock_post.side_effect = [
            requests.exceptions.ConnectionError("First error"),
            Mock(json=lambda: {'choices': [{'message': {'content': 'Success'}}]}, raise_for_status=Mock())
        ]

        client = LLMClient(api_key='test_key', max_retries=1)
        response = client.chat('Test', use_memory=False)

        assert mock_post.call_count == 2
        assert response.content == 'Success'

    @patch('llm.client.requests.post')
    @patch('llm.client.time.sleep')
    def test_max_retries_exceeded(self, mock_sleep, mock_post):
        """测试超过最大重试次数（无备份模型时）"""
        mock_post.side_effect = requests.exceptions.ConnectionError("Always fails")

        client = LLMClient(api_key='test_key', max_retries=2)
        client._has_backup = False  # 禁用备份，只测试主模型重试逻辑
        response = client.chat('Test', use_memory=False)

        assert mock_post.call_count == 3  # 1 + 2 retries
        assert response.is_error is True

    @patch('llm.client.requests.post')
    @patch('llm.client.time.sleep')
    def test_failover_to_backup_on_primary_failure(self, mock_sleep, mock_post):
        """测试主模型失败后自动切换到备用模型"""
        mock_post.side_effect = requests.exceptions.ConnectionError("Always fails")

        from llm.client import CircuitBreaker
        client = LLMClient(api_key='test_key', max_retries=1)
        # 手动配置备份模型参数（含熔断器，否则 _has_backup=True 时 backup 熔断器为 None）
        client._has_backup = True
        client.backup_base_url = 'https://backup.api.com/v1'
        client.backup_model = 'backup-model'
        client.backup_api_key = 'backup-key'
        client.backup_provider = 'openai_compatible'
        client._backup_circuit_breaker = CircuitBreaker()
        response = client.chat('Test', use_memory=False)

        # 主模型 2 次 + 备份模型 2 次 = 4 次
        assert mock_post.call_count == 4
        assert response.is_error is True  # 备模型也失败了

class TestSanitizeMessagesForReplay:
    """DeepSeek thinking 模式 reasoning_content 回传校验的消息消毒（需求 118 事故修复）"""

    def test_bare_assistant_converted_to_user(self):
        from llm.client import _sanitize_messages_for_replay
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "TL 的分析结果"},
            {"role": "user", "content": "开始编码"},
        ]
        out = _sanitize_messages_for_replay(msgs)
        assert out[1]["role"] == "user"
        assert "TL 的分析结果" in out[1]["content"]
        assert "历史助手产出" in out[1]["content"]

    def test_assistant_with_reasoning_content_kept(self):
        from llm.client import _sanitize_messages_for_replay
        msgs = [{"role": "assistant", "content": "hi", "reasoning_content": "think..."}]
        out = _sanitize_messages_for_replay(msgs)
        assert out == msgs

    def test_assistant_with_tool_calls_kept(self):
        from llm.client import _sanitize_messages_for_replay
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]}]
        out = _sanitize_messages_for_replay(msgs)
        assert out == msgs

    def test_consecutive_user_merged(self):
        from llm.client import _sanitize_messages_for_replay
        msgs = [
            {"role": "user", "content": "需求 A"},
            {"role": "user", "content": "已确认计划"},
            {"role": "assistant", "content": "产出"},
        ]
        out = _sanitize_messages_for_replay(msgs)
        # user 合并 + assistant 降级为 user → 全部并为一条
        assert len(out) == 1
        assert "需求 A" in out[0]["content"]
        assert "历史助手产出" in out[0]["content"]

    def test_non_list_passthrough(self):
        from llm.client import _sanitize_messages_for_replay
        assert _sanitize_messages_for_replay("not a list") == "not a list"

    def test_empty_list(self):
        from llm.client import _sanitize_messages_for_replay
        assert _sanitize_messages_for_replay([]) == []


class TestLogAndRaise:
    """非 2xx 响应体写入流量日志后再抛出"""

    def test_error_body_logged_before_raise(self):
        from llm.client import _log_and_raise, _llm_logger
        resp = Mock()
        resp.status_code = 400
        resp.text = '{"error":{"message":"The reasoning_content..."}}'
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("400")
        with patch.object(_llm_logger, 'info') as mock_log:
            with pytest.raises(requests.exceptions.HTTPError):
                _log_and_raise(resp, call_id="test1234", t0=0.0)
            # 错误体必须先于异常写入流量日志
            logged = mock_log.call_args[0][0]
            assert "reasoning_content" in logged
            assert '"status": 400' in logged

    def test_2xx_passes_through(self):
        from llm.client import _log_and_raise
        resp = Mock()
        resp.status_code = 200
        resp.raise_for_status = Mock()
        _log_and_raise(resp)
        resp.raise_for_status.assert_called_once()


class TestToolOnlyResponse:
    """thinking 模型常态输出：content 空 + tool_calls 非空，不得误判为空响应（需求 119 事故）"""

    def test_tool_only_response_is_not_error(self):
        client = LLMClient(api_key='test_key')
        tc = [{"id": "c1", "type": "function",
               "function": {"name": "write_file", "arguments": "{\"filename\":\"a.html\",\"content\":\"x\"}"}}]
        with patch.object(client, '_chat_with_tools_request_loop',
                          return_value=("", None, tc, {"total_tokens": 10}, False)):
            resp = client.chat_with_tools([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
        assert resp.is_error is False
        assert resp.tool_calls == tc

    def test_genuinely_empty_response_still_error(self):
        client = LLMClient(api_key='test_key')
        with patch.object(client, '_chat_with_tools_request_loop',
                          return_value=("", None, None, None, False)):
            resp = client.chat_with_tools([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
        assert resp.is_error is True
        assert "空响应" in (resp.error or "")
