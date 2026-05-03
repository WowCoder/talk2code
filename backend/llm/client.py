# -*- coding: utf-8 -*-
"""
统一 LLM 客户端模块
支持 OpenAI 兼容接口 和 Anthropic 兼容接口 两种协议
通过 LLM_PROVIDER 配置切换，支持流式输出、会话记忆、自动重试
"""

import os
import json
import signal
import time
from typing import Generator, List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

import requests
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Message:
    """消息对象"""
    role: str  # "system", "user", "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@dataclass
class LLMResponse:
    """LLM 响应对象"""
    content: str
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


def to_langchain_message(msg: Message) -> BaseMessage:
    """
    Convert internal Message to langchain_core BaseMessage.

    Enables future integration with LangChain components while keeping
    the custom DashScope API client.

    Args:
        msg: Internal Message object with role and content

    Returns:
        langchain_core BaseMessage subclass (HumanMessage, AIMessage, or SystemMessage)
    """
    if msg.role == 'user':
        return HumanMessage(content=msg.content)
    elif msg.role == 'assistant':
        return AIMessage(content=msg.content)
    else:
        return SystemMessage(content=msg.content)


class LLMClient:
    """
    统一 LLM 客户端

    支持两种 API 协议，通过 provider 参数切换：
    - openai_compatible:    POST {base_url}/chat/completions
                            Header: Authorization: Bearer {api_key}
                            system prompt 放在 messages 数组中
    - anthropic_compatible: POST {base_url}/messages
                            Header: x-api-key: {api_key}
                            system prompt 作为顶层字段
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: int = 60,
        max_retries: int = 2
    ):
        self.api_key = api_key or LLM_API_KEY
        self.base_url = base_url or LLM_BASE_URL
        self.model = model or LLM_MODEL
        self.provider = provider or LLM_PROVIDER
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

        # 会话记忆
        self._messages: List[Message] = []

        if not self.api_key:
            raise ValueError("请配置 LLM_API_KEY 环境变量")

        if self.provider not in ('openai_compatible', 'anthropic_compatible'):
            raise ValueError(f"不支持的 LLM_PROVIDER: {self.provider}，可选值：openai_compatible, anthropic_compatible")

        logger.info(f"LLMClient 初始化：provider={self.provider}, model={self.model}, base_url={self.base_url}")

    def clear_memory(self):
        """清空会话记忆"""
        self._messages.clear()
        logger.debug("LLM 会话记忆已清空")

    def load_memory(self, dialogue_history: List[Dict[str, Any]]):
        """从数据库加载对话历史"""
        self.clear_memory()
        for msg in dialogue_history:
            role = msg.get('role', 'user')
            if role == 'user':
                self._messages.append(Message(role='user', content=msg.get('content', '')))
            elif role in ('agent', 'assistant'):
                self._messages.append(Message(role='assistant', content=msg.get('content', '')))
            elif role == 'system':
                self._messages.append(Message(role='system', content=msg.get('content', '')))
        logger.debug(f"从数据库加载了 {len(self._messages)} 条对话历史")

    def get_memory(self) -> List[Dict[str, str]]:
        """获取会话记忆（格式化为 API 请求格式）"""
        return [{'role': m.role, 'content': m.content} for m in self._messages]

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = True
    ) -> List[Dict[str, str]]:
        """构建消息列表"""
        messages = []

        # 系统提示
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        # 历史记忆
        if use_memory:
            messages.extend(self.get_memory())

        # 用户输入
        messages.append({'role': 'user', 'content': prompt})

        return messages

    def _request_openai(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> Generator[str, None, None]:
        """发送 OpenAI 兼容 API 请求（带重试）"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': self.model,
            'messages': messages,
            'stream': stream,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens
        }

        url = f'{self.base_url}/chat/completions'

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                if stream:
                    response = requests.post(
                        url, headers=headers, json=data,
                        stream=True, timeout=self.timeout
                    )
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if line:
                            line = line.decode('utf-8')
                            if line.startswith('data: '):
                                content = line[6:]
                                if content == '[DONE]':
                                    break
                                try:
                                    chunk = json.loads(content)
                                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                                    content_text = delta.get('content', '')
                                    if content_text:
                                        yield content_text
                                except json.JSONDecodeError:
                                    continue
                else:
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=self.timeout
                    )
                    response.raise_for_status()
                    result = response.json()
                    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    yield content
                return

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                last_error = e
                if attempt < self.max_retries:
                    import random
                    delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random() * 0.5)
                    logger.warning(f"LLM 请求失败：{str(e)}，{delay:.2f}秒后重试 ({attempt + 1}/{self.max_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"LLM 请求失败，已达最大重试次数：{str(e)}")
                    yield f"[错误] API 请求失败：{str(e)}"

    def _request_anthropic(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> Generator[str, None, None]:
        """发送 Anthropic 兼容 API 请求（带重试）"""
        headers = {
            'x-api-key': self.api_key,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Anthropic: system 是顶层字段，不在 messages 数组中
        system_prompt = None
        api_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_prompt = m['content']
            else:
                api_messages.append(m)

        data = {
            'model': self.model,
            'messages': api_messages,
            'stream': stream,
            'max_tokens': self.max_tokens
        }
        if system_prompt:
            data['system'] = system_prompt

        url = f'{self.base_url}/messages'

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                if stream:
                    response = requests.post(
                        url, headers=headers, json=data,
                        stream=True, timeout=self.timeout
                    )
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if line:
                            line = line.decode('utf-8')
                            if line.startswith('data: '):
                                content = line[6:]
                                try:
                                    chunk = json.loads(content)
                                    if chunk.get('type') == 'content_block_delta':
                                        delta = chunk.get('delta', {})
                                        text = delta.get('text', '')
                                        if text:
                                            yield text
                                    elif chunk.get('type') == 'message_stop':
                                        break
                                except json.JSONDecodeError:
                                    continue
                else:
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=self.timeout
                    )
                    response.raise_for_status()
                    result = response.json()
                    content_blocks = result.get('content', [])
                    text = ''.join(
                        block.get('text', '')
                        for block in content_blocks
                        if block.get('type') == 'text'
                    )
                    yield text
                return

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                last_error = e
                if attempt < self.max_retries:
                    import random
                    delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random() * 0.5)
                    logger.warning(f"LLM 请求失败：{str(e)}，{delay:.2f}秒后重试 ({attempt + 1}/{self.max_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"LLM 请求失败，已达最大重试次数：{str(e)}")
                    yield f"[错误] API 请求失败：{str(e)}"

    def _do_request(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> Generator[str, None, None]:
        """根据 provider 分发到对应的请求方法"""
        if self.provider == 'anthropic_compatible':
            yield from self._request_anthropic(messages, stream)
        else:
            yield from self._request_openai(messages, stream)

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = True,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> LLMResponse:
        """
        非流式聊天

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆
            max_tokens: 最大生成 token 数（覆盖默认值）
            timeout: 超时时间（覆盖默认值）

        Returns:
            LLMResponse 对象
        """
        # 临时覆盖参数
        old_max_tokens = self.max_tokens
        old_timeout = self.timeout
        if max_tokens:
            self.max_tokens = max_tokens
        if timeout:
            self.timeout = timeout

        messages = self._build_messages(prompt, system_prompt, use_memory)
        logger.debug(f"LLM 请求：messages_count={len(messages)}, max_tokens={self.max_tokens}")

        # 带重试的请求
        content = ""
        error = None
        for attempt in range(self.max_retries + 1):
            try:
                # 使用超时保护（仅 Unix）
                def handler(signum, frame):
                    raise TimeoutError(f"LLM 调用超时（{self.timeout}秒）")

                old_handler = None
                try:
                    old_handler = signal.signal(signal.SIGALRM, handler)
                    signal.alarm(self.timeout)
                except (ValueError, OSError):
                    pass  # 非主线程或 Windows

                try:
                    for chunk in self._do_request(messages, stream=False):
                        content = chunk
                finally:
                    try:
                        signal.alarm(0)
                        if old_handler:
                            signal.signal(signal.SIGALRM, old_handler)
                    except:
                        pass

                if content and not content.startswith('[错误]'):
                    break  # 成功，退出重试循环
                elif attempt < self.max_retries:
                    logger.warning(f"LLM 请求失败，重试 {attempt + 1}/{self.max_retries}")
            except Exception as e:
                error = str(e)
                logger.error(f"LLM 请求异常：{error}")
                if attempt >= self.max_retries:
                    content = f"[错误] 请求失败：{error}"

        # 恢复参数
        self.max_tokens = old_max_tokens
        self.timeout = old_timeout

        # 保存到记忆
        if use_memory and content:
            self._messages.append(Message(role='user', content=prompt))
            self._messages.append(Message(role='assistant', content=content))

        # 获取用量信息（如果有）
        usage = None
        finish_reason = None

        return LLMResponse(content=content, usage=usage, finish_reason=finish_reason, error=error)

    def chat_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = True
    ) -> Generator[str, None, None]:
        """
        流式聊天

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆

        Yields:
            文本片段
        """
        messages = self._build_messages(prompt, system_prompt, use_memory)
        logger.debug(f"LLM 流式请求：messages_count={len(messages)}")

        full_content = ""
        for chunk in self._do_request(messages, stream=True):
            if chunk:
                full_content += chunk
                yield chunk

        # 保存到记忆
        if use_memory and full_content and not full_content.startswith('[错误]'):
            self._messages.append(Message(role='user', content=prompt))
            self._messages.append(Message(role='assistant', content=full_content))


# 全局客户端实例（延迟初始化）
_client: Optional[LLMClient] = None
_instances: Dict[str, LLMClient] = {}


def get_client(instance_id: str = "default") -> LLMClient:
    """获取或创建 LLM 客户端实例"""
    global _client
    if instance_id == "default":
        if _client is None:
            _client = LLMClient()
        return _client
    else:
        if instance_id not in _instances:
            _instances[instance_id] = LLMClient()
        return _instances[instance_id]


def clear_client_memory(instance_id: str = "default"):
    """清空指定实例的会话记忆"""
    client = get_client(instance_id)
    client.clear_memory()


# 兼容旧接口的快捷函数
def chat_with_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 4000,
    timeout: int = 60
) -> str:
    """
    简单聊天接口（非流式）

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词
        max_tokens: 最大生成 token 数
        timeout: 超时时间（秒）

    Returns:
        LLM 响应文本
    """
    client = get_client()
    response = client.chat(prompt, system_prompt, use_memory=False, max_tokens=max_tokens, timeout=timeout)
    return response.content


def chat_with_llm_stream(
    prompt: str,
    system_prompt: Optional[str] = None
) -> Generator[str, None, None]:
    """
    流式聊天接口

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词

    Yields:
        文本片段
    """
    client = get_client()
    for chunk in client.chat_stream(prompt, system_prompt, use_memory=False):
        yield chunk
