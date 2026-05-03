# -*- coding: utf-8 -*-
"""
LLM 客户端单元测试
使用 mock 避免实际 API 调用
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

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
        assert client.model == 'qwen-plus'  # 默认模型

    def test_client_init_default_params(self):
        """测试默认参数"""
        client = LLMClient(api_key='test_key')
        assert client.temperature == 0.7
        assert client.max_tokens == 4000
        assert client.timeout == 60
        assert client.max_retries == 2

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
        # 第一次失败，第二次成功
        mock_post.side_effect = [
            Exception("First error"),
            Mock(json=lambda: {'choices': [{'message': {'content': 'Success'}}]}, raise_for_status=Mock())
        ]

        client = LLMClient(api_key='test_key', max_retries=1)
        response = client.chat('Test', use_memory=False)

        assert mock_post.call_count == 2
        assert response.content == 'Success'

    @patch('llm.client.requests.post')
    @patch('llm.client.time.sleep')
    def test_max_retries_exceeded(self, mock_sleep, mock_post):
        """测试超过最大重试次数"""
        mock_post.side_effect = Exception("Always fails")

        client = LLMClient(api_key='test_key', max_retries=2)
        response = client.chat('Test', use_memory=False)

        assert mock_post.call_count == 3  # 1 + 2 retries
        assert response.is_error is True