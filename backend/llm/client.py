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

# 注意：llm.client 是底层模块，不能在模块顶层 import harness（harness 依赖 llm.client，
# 会形成循环导入）。日志用标准 logging，harness 的日志系统会在应用启动时统一配置 root logger。
import logging as _logging
logger = _logging.getLogger(__name__)

# LLM 请求/响应专用日志（独立于应用日志，便于排查问题）
import logging as _logging
_llm_logger = _logging.getLogger("llm.traffic")
_llm_logger.setLevel(_logging.DEBUG)
if not _llm_logger.handlers:
    import os as _os
    _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "..", "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    _fh = _logging.FileHandler(_os.path.join(_log_dir, "llm_traffic.log"), encoding="utf-8")
    _fh.setLevel(_logging.DEBUG)
    _fh.setFormatter(_logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _llm_logger.addHandler(_fh)
    _llm_logger.propagate = False


def _log_llm_request(call_id: str, provider: str, model: str, url: str, payload: dict):
    """记录 LLM 请求"""
    _llm_logger.info(
        f"[{call_id}] REQUEST | provider={provider} model={model} url={url}\n"
        f"[{call_id}] PAYLOAD | {json.dumps(payload, ensure_ascii=False, default=str)[:8000]}"
    )


def _log_llm_response(call_id: str, status: int, body: dict, duration_ms: float):
    """记录 LLM 响应"""
    _llm_logger.info(
        f"[{call_id}] RESPONSE | status={status} duration={duration_ms:.0f}ms\n"
        f"[{call_id}] BODY | {json.dumps(body, ensure_ascii=False, default=str)[:8000]}"
    )


def _try_fix_json(raw: str) -> dict | None:
    """尝试修复 LLM 返回的不完整 JSON"""
    # 方法1: 补齐末尾的 } 和 "
    stack = []
    in_str = False
    escaped = False
    for ch in raw:
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in '{[':
            stack.append(ch)
        elif not in_str and ch in '}]':
            if stack and ((ch == '}' and stack[-1] == '{') or (ch == ']' and stack[-1] == '[')):
                stack.pop()
    # 补齐
    fixed = raw.rstrip()
    if in_str:
        fixed += '"'
    for opener in reversed(stack):
        fixed += '}' if opener == '{' else ']'
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 方法2: 去掉最后不完整的字段
    last_quote = fixed.rfind('",')
    if last_quote > 0:
        try:
            return json.loads(fixed[:last_quote + 1] + '}')
        except json.JSONDecodeError:
            pass
    return None


@dataclass
class Message:
    """消息对象"""
    role: str  # "system", "user", "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@dataclass
class ToolCall:
    """LLM 工具调用"""
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """LLM 响应对象"""
    content: str
    reasoning_content: Optional[str] = None  # DeepSeek 等模型的思考链
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

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
        stream: bool = False,
        max_tokens: Optional[int] = None
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
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens
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
                    import uuid
                    call_id = uuid.uuid4().hex[:8]
                    t0 = time.time()
                    _log_llm_request(call_id, self.provider, self.model, url, data)
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=self.timeout
                    )
                    response.raise_for_status()
                    result = response.json()
                    _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)
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
        stream: bool = False,
        max_tokens: Optional[int] = None
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
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens
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
                    import uuid
                    call_id = uuid.uuid4().hex[:8]
                    t0 = time.time()
                    _log_llm_request(call_id, self.provider, self.model, url, data)
                    response = requests.post(
                        url, headers=headers, json=data,
                        timeout=self.timeout
                    )
                    response.raise_for_status()
                    result = response.json()
                    _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)
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
        stream: bool = False,
        max_tokens: Optional[int] = None
    ) -> Generator[str, None, None]:
        """根据 provider 分发到对应的请求方法"""
        if self.provider == 'anthropic_compatible':
            yield from self._request_anthropic(messages, stream, max_tokens=max_tokens)
        else:
            yield from self._request_openai(messages, stream, max_tokens=max_tokens)

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_memory: bool = False,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> LLMResponse:
        """
        非流式聊天

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆（默认 False：单例客户端被多线程共享，
                开启记忆会导致跨请求串扰，仅在单线程场景显式开启）
            max_tokens: 最大生成 token 数（覆盖默认值）
            timeout: 超时时间（覆盖默认值）

        Returns:
            LLMResponse 对象
        """
        # 解析本次调用的有效参数（不修改实例状态，保证线程安全）
        effective_max_tokens = max_tokens or self.max_tokens
        effective_timeout = timeout or self.timeout

        messages = self._build_messages(prompt, system_prompt, use_memory)
        logger.debug(f"LLM 请求：messages_count={len(messages)}, max_tokens={effective_max_tokens}")

        # 带重试的请求
        content = ""
        error = None
        for attempt in range(self.max_retries + 1):
            try:
                # 使用超时保护（仅 Unix 主线程）
                def handler(signum, frame):
                    raise TimeoutError(f"LLM 调用超时（{effective_timeout}秒）")

                old_handler = None
                try:
                    old_handler = signal.signal(signal.SIGALRM, handler)
                    signal.alarm(effective_timeout)
                except (ValueError, OSError):
                    pass  # 非主线程或 Windows

                try:
                    for chunk in self._do_request(
                        messages, stream=False,
                        max_tokens=effective_max_tokens,
                    ):
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


    def chat_with_tools(
        self,
        messages: list,
        tools: list,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        支持 function calling 的聊天接口

        Args:
            messages: 消息列表 [{"role": "...", "content": "..."}]
            tools: 工具描述列表（OpenAI function calling 格式）
            tool_choice: "auto" / "none" / "required"
            max_tokens: 最大 token 数

        Returns:
            LLMResponse 含 tool_calls 字段
        """
        # 线程安全：不修改 self.max_tokens，按调用解析有效值
        effective_max_tokens = max_tokens or self.max_tokens

        content = ""
        tool_calls = None
        usage = None
        finish_reason = None
        error = None

        reasoning_content = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == 'anthropic_compatible':
                    content, reasoning_content, tool_calls, usage = self._request_anthropic_with_tools(
                        messages, tools, max_tokens=effective_max_tokens)
                else:
                    content, reasoning_content, tool_calls, usage = self._request_openai_with_tools(
                        messages, tools, tool_choice, max_tokens=effective_max_tokens)
                break
            except Exception as e:
                error = str(e)
                logger.error(f"chat_with_tools 失败：{error}")
                if attempt >= self.max_retries:
                    content = f"[错误] 工具调用失败：{error}"

        return LLMResponse(content=content, reasoning_content=reasoning_content,
                           usage=usage, finish_reason=finish_reason,
                           error=error, tool_calls=tool_calls)

    def _request_openai_with_tools(self, messages: list, tools: list, tool_choice: str,
                                    max_tokens: Optional[int] = None):
        """OpenAI function calling 协议"""
        import uuid
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        data = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens,
            'tools': tools,
            'tool_choice': tool_choice,
        }
        url = f'{self.base_url}/chat/completions'

        call_id = uuid.uuid4().hex[:8]
        t0 = time.time()
        _log_llm_request(call_id, self.provider, self.model, url, data)

        response = requests.post(url, headers=headers, json=data, timeout=self.timeout)
        response.raise_for_status()
        result = response.json()

        _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)

        choice = result.get('choices', [{}])[0]
        msg = choice.get('message', {})
        content = msg.get('content', '') or ''
        reasoning_content = msg.get('reasoning_content', '') or None
        usage_data = result.get('usage')

        # 解析 tool_calls
        raw_tool_calls = msg.get('tool_calls', [])
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get('function', {})
            raw_args = fn.get('arguments', '{}')
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as e:
                # 尝试修复：补齐截断的 JSON
                if isinstance(raw_args, str):
                    fixed = _try_fix_json(raw_args)
                    if fixed:
                        parsed_args = fixed
                        logger.warning(f"LLM tool call 参数已自动修复: {tc.get('function', {}).get('name', '?')}")
                    else:
                        logger.warning(f"LLM 返回了无法修复的 tool call 参数: {e}，跳过")
                        continue
                else:
                    logger.warning(f"LLM 返回了无法解析的 tool call 参数: {e}，跳过")
                    continue
            tool_calls.append(ToolCall(
                name=fn.get('name', ''),
                arguments=parsed_args
            ))

        return content, reasoning_content, (tool_calls or None), usage_data

    def _request_anthropic_with_tools(self, messages: list, tools: list,
                                       max_tokens: Optional[int] = None):
        """Anthropic tool use 协议"""
        headers = {
            'x-api-key': self.api_key,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Anthropic: system 是顶层字段, tools 格式不同
        system_prompt = None
        api_messages = []
        for m in messages:
            if m['role'] == 'system':
                system_prompt = m['content']
            else:
                api_messages.append(m)

        anthropic_tools = []
        for t in tools:
            fn = t.get('function', t)
            anthropic_tools.append({
                'name': fn.get('name', ''),
                'description': fn.get('description', ''),
                'input_schema': fn.get('parameters', {}),
            })

        data = {
            'model': self.model,
            'messages': api_messages,
            'max_tokens': max_tokens if max_tokens is not None else self.max_tokens,
            'tools': anthropic_tools,
        }
        if system_prompt:
            data['system'] = system_prompt

        url = f'{self.base_url}/messages'

        import uuid
        call_id = uuid.uuid4().hex[:8]
        t0 = time.time()
        _log_llm_request(call_id, self.provider, self.model, url, data)

        response = requests.post(url, headers=headers, json=data, timeout=self.timeout)
        response.raise_for_status()
        result = response.json()

        _log_llm_response(call_id, response.status_code, result, (time.time() - t0) * 1000)

        content = ""
        tool_calls = []
        usage_data = result.get('usage')

        for block in result.get('content', []):
            if block.get('type') == 'text':
                content += block.get('text', '')
            elif block.get('type') == 'tool_use':
                tool_calls.append(ToolCall(
                    name=block.get('name', ''),
                    arguments=block.get('input', {})
                ))

        return content, None, (tool_calls or None), usage_data


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
