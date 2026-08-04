# -*- coding: utf-8 -*-
"""
统一 LLM 客户端模块
支持 OpenAI 兼容接口 和 Anthropic 兼容接口 两种协议
通过 LLM_PROVIDER 配置切换，支持流式输出、会话记忆、自动重试
"""

import os
import json
import time
from typing import Generator, List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

import requests
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, settings

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
    # 方法2: 去掉最后不完整的字段（查找最后一个 ",
    last_quote = fixed.rfind('",')
    if last_quote > 0:
        try:
            return json.loads(fixed[:last_quote + 1] + '}')
        except json.JSONDecodeError:
            pass
    # 方法3: 从末尾去掉不完整 token，找到最后一个顶层逗号截断
    # 适用于截断位置在 key 开始处的情况（如 ..., "tech_stack": {...}, "）
    last_top_comma = -1
    depth = 0
    in_str3 = False
    escaped3 = False
    for i, ch in enumerate(raw):
        if escaped3:
            escaped3 = False
            continue
        if ch == '\\':
            escaped3 = True
            continue
        if ch == '"':
            in_str3 = not in_str3
        elif not in_str3:
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
            elif ch == ',' and depth == 1:
                last_top_comma = i
    if last_top_comma > 0:
        truncated = raw[:last_top_comma].rstrip()
        # 补齐未闭合的括号
        truncated_depth = 0
        in_ts = False
        esc_ts = False
        for ch in truncated:
            if esc_ts:
                esc_ts = False
                continue
            if ch == '\\':
                esc_ts = True
                continue
            if ch == '"':
                in_ts = not in_ts
            elif not in_ts:
                if ch in '{[':
                    truncated_depth += 1
                elif ch in '}]':
                    truncated_depth -= 1
        for _ in range(truncated_depth):
            truncated += '}'
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass
    # 方法4: 从右向左扫描，尝试在每个 '}' 位置截断并解析
    # 适用于 JSON 之后有额外非 JSON 文本的 LLM 响应
    for end_try in range(len(raw) - 1, max(0, len(raw) - 500), -1):
        if raw[end_try] == '}':
            try:
                return json.loads(raw[:end_try + 1])
            except json.JSONDecodeError:
                pass
    # 方法5: 截断在 key: 后面没有 value（如 {"key":, {"a": 1, "b":）
    # 移除最后一个不完整的 key-value 对，然后补全括号
    last_colon = raw.rfind('":')
    if last_colon > 0:
        # 往前找到这个 key 之前的最后一个分隔符
        # 可能是 ','（同层前一个字段之后）或 '{'（对象开头/嵌套对象开头）
        prev_comma = raw.rfind(',', 0, last_colon)
        prev_brace = raw.rfind('{', 0, last_colon)
        key_start = max(prev_comma, prev_brace)
        if key_start >= 0:
            if raw[key_start] == ',':
                # 截掉逗号及之后的不完整 key-value
                prefix = raw[:key_start]
            else:
                # key_start 指向 {，保留 { 并截掉其后的不完整 key
                prefix = raw[:key_start + 1]
            # 补全未闭合的括号
            depth = 0
            in_s = False
            esc = False
            for ch in prefix:
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"':
                    in_s = not in_s
                elif not in_s:
                    if ch in '{[':
                        depth += 1
                    elif ch in '}]':
                        depth -= 1
            prefix += '}' * depth
            try:
                return json.loads(prefix)
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


class CircuitBreakerOpenError(Exception):
    """熔断器打开时抛出，调用方应捕获并做降级处理"""
    pass


class CircuitBreaker:
    """LLM 调用熔断器

    连续失败 N 次 → 熔断器打开 → 后续调用直接抛出 CircuitBreakerOpenError
    熔断器打开后等待 M 秒 → 半开状态 → 允许 1 次探测调用
    探测成功 → 关闭熔断器；探测失败 → 重新打开

    设计原则：
    - 快速失败优于长时间等待（fail fast > wait long）
    - 避免在 LLM API 不可用时持续消耗系统资源（线程、SSE 连接、用户耐心）
    - 半开状态允许自动恢复，无需人工干预
    """

    def __init__(self, threshold: int = 5, recovery_timeout: float = 30.0):
        self.threshold = threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed | open | half_open

    @property
    def is_open(self) -> bool:
        if self._state == "closed":
            return False
        if self._state == "open":
            # 检查是否可以进入半开状态
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = "half_open"
                return False
            return True
        # half_open: 允许通过
        return False

    def record_success(self):
        """调用成功，重置熔断器"""
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self):
        """调用失败，递增计数"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.threshold:
            self._state = "open"
            logger.warning(
                f"[CircuitBreaker] 连续失败 {self._failure_count} 次，熔断器打开，"
                f"将在 {self.recovery_timeout:.0f}s 后进入半开状态"
            )

    def check(self):
        """检查熔断器状态，打开时抛出异常"""
        if self.is_open:
            remaining = self.recovery_timeout - (time.time() - self._last_failure_time)
            raise CircuitBreakerOpenError(
                f"LLM 熔断器已打开（连续失败 {self._failure_count} 次），"
                f"约 {remaining:.0f}s 后自动恢复"
            )


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
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None
    ):
        self.api_key = api_key or LLM_API_KEY
        self.base_url = base_url or LLM_BASE_URL
        self.model = model or LLM_MODEL
        self.provider = provider or LLM_PROVIDER
        self.temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
        self.max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else settings.LLM_MAX_RETRIES

        # 熔断器：防止 LLM API 不可用时持续无效重试
        self._circuit_breaker = CircuitBreaker(
            threshold=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
            recovery_timeout=settings.LLM_CIRCUIT_BREAKER_TIMEOUT,
        )

        # 备份模型参数（可选）：主模型不可用时自动切换
        self.backup_base_url = settings.LLM_BACKUP_BASE_URL or None
        self.backup_model = settings.LLM_BACKUP_MODEL or None
        self.backup_api_key = settings.LLM_BACKUP_API_KEY or None
        self.backup_provider = settings.LLM_BACKUP_PROVIDER or None
        self._has_backup = all([
            self.backup_base_url, self.backup_model, self.backup_api_key
        ])
        self._backup_circuit_breaker = CircuitBreaker(
            threshold=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
            recovery_timeout=settings.LLM_CIRCUIT_BREAKER_TIMEOUT,
        ) if self._has_backup else None

        # 会话记忆
        self._messages: List[Message] = []

        if not self.api_key:
            raise ValueError("请配置 LLM_API_KEY 环境变量")

        if self.provider not in ('openai_compatible', 'anthropic_compatible'):
            raise ValueError(f"不支持的 LLM_PROVIDER: {self.provider}，可选值：openai_compatible, anthropic_compatible")

        if self._has_backup:
            logger.info(
                f"LLMClient 初始化：provider={self.provider}, model={self.model}, base_url={self.base_url}"
                f" | 备用: provider={self.backup_provider}, model={self.backup_model}"
            )
        else:
            logger.info(f"LLMClient 初始化：provider={self.provider}, model={self.model}, base_url={self.base_url}")

    def _use_backup_params(self):
        """上下文管理器：临时切换到备份模型参数，退出时自动恢复

        用法:
            with self._use_backup_params():
                self._do_request(messages)  # 使用备份模型
        """
        from contextlib import contextmanager

        @contextmanager
        def _swap():
            # 保存主模型参数
            orig_base_url = self.base_url
            orig_model = self.model
            orig_api_key = self.api_key
            orig_provider = self.provider
            # 切换到备份模型
            self.base_url = self.backup_base_url
            self.model = self.backup_model
            self.api_key = self.backup_api_key
            self.provider = self.backup_provider
            logger.info(
                f"[Failover] 切换到备用模型: provider={self.provider}, "
                f"model={self.model}, base_url={self.base_url}"
            )
            try:
                yield
            finally:
                # 恢复主模型参数
                self.base_url = orig_base_url
                self.model = orig_model
                self.api_key = orig_api_key
                self.provider = orig_provider

        return _swap()

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
                                    # 注意：不 fallback 到 reasoning_content。
                                    # reasoning_content 是模型的内部思考链，不是最终回复。
                                    # reasoning 模型（如 DeepSeek-R1、agnes-2.0-flash）会同时
                                    # 流式输出 reasoning_content 和 content 两个 delta，
                                    # 我们只需要最终的 content。
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
                    msg = result.get('choices', [{}])[0].get('message', {})
                    content = msg.get('content', '')
                    reasoning = msg.get('reasoning_content', '')
                    # 如果 content 为空但有 reasoning_content，说明 max_tokens 不足，
                    # 所有 token 被 reasoning 消耗。此时以默认 max_tokens 重试一次。
                    if not content and reasoning:
                        req_tokens = data.get('max_tokens', 0)
                        fallback_tokens = self.max_tokens  # 默认 8000
                        logger.warning(
                            f"[LLM] 检测到 reasoning 模型 token 耗尽 "
                            f"(reasoning={len(reasoning)} chars, content为空, "
                            f"req_max_tokens={req_tokens})，"
                            f"以默认 max_tokens={fallback_tokens} 重试"
                        )
                        retry_data = dict(data)
                        retry_data['max_tokens'] = fallback_tokens
                        retry_response = requests.post(
                            url, headers=headers, json=retry_data,
                            timeout=self.timeout
                        )
                        retry_response.raise_for_status()
                        retry_result = retry_response.json()
                        retry_msg = retry_result.get('choices', [{}])[0].get('message', {})
                        content = retry_msg.get('content', '')
                        # 重试后仍然为空则放弃，让上层错误处理
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

    def _chat_request_loop(
        self, messages: list, effective_max_tokens: int, effective_timeout: int
    ) -> tuple:
        """核心请求+重试循环（不涉及熔断器，由调用方管理）

        超时保护由底层 requests.post(timeout=...) 提供，支持主线程和工作线程。

        Returns:
            (content: str, error: str | None, failed: bool)
        """
        content = ""
        error = None
        failed = False
        for attempt in range(self.max_retries + 1):
            try:
                for chunk in self._do_request(
                    messages, stream=False,
                    max_tokens=effective_max_tokens,
                ):
                    content = chunk

                if content and not content.startswith('[错误]'):
                    break  # 成功
                elif attempt < self.max_retries:
                    logger.warning(f"LLM 请求失败，重试 {attempt + 1}/{self.max_retries}")
                    failed = True
            except Exception as e:
                error = str(e)
                logger.error(f"LLM 请求异常：{error}")
                failed = True
                if attempt >= self.max_retries:
                    content = f"[错误] 请求失败：{error}"

        return content, error, failed

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

        支持主备模型自动切换：主模型不可用时（熔断器打开或请求失败），
        自动切换到备用模型重试。

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

        content = ""
        error = None
        tried_backup = False

        # ---- 主模型尝试 ----
        primary_available = True
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError as e:
            primary_available = False
            logger.warning(f"[Failover] 主模型熔断器已打开: {e}")

        if primary_available:
            content, error, failed = self._chat_request_loop(
                messages, effective_max_tokens, effective_timeout
            )
            if failed and (not content or content.startswith('[错误]')):
                self._circuit_breaker.record_failure()
            elif content and not content.startswith('[错误]'):
                self._circuit_breaker.record_success()

        # ---- 主模型失败 → 尝试备用模型 ----
        if (not content or content.startswith('[错误]')) and self._has_backup:
            logger.warning(
                f"[Failover] 主模型失败，切换到备用模型 "
                f"({self.backup_provider}/{self.backup_model}@{self.backup_base_url})"
            )
            try:
                self._backup_circuit_breaker.check()
            except CircuitBreakerOpenError as e:
                logger.error(f"[Failover] 备用模型熔断器也已打开: {e}")
            else:
                with self._use_backup_params():
                    content, error, failed = self._chat_request_loop(
                        messages, effective_max_tokens, effective_timeout
                    )
                    if failed and (not content or content.startswith('[错误]')):
                        self._backup_circuit_breaker.record_failure()
                    elif content and not content.startswith('[错误]'):
                        self._backup_circuit_breaker.record_success()
                        tried_backup = True
                        logger.info("[Failover] 备用模型请求成功")

        # 保存到记忆
        if use_memory and content and not content.startswith('[错误]'):
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
        流式聊天，含主备模型自动切换

        注意：由于流式特性，只能在请求开始前判断主备（熔断器级别切换）。
        如果流式传输中途失败，本次请求无法重试。

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆

        Yields:
            文本片段
        """
        messages = self._build_messages(prompt, system_prompt, use_memory)
        logger.debug(f"LLM 流式请求：messages_count={len(messages)}")

        # 选择模型：主模型可用 → 主；主不可用 + 有备份 → 备
        use_backup = False
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError:
            if self._has_backup:
                try:
                    self._backup_circuit_breaker.check()
                    use_backup = True
                    logger.warning("[Failover] 主模型熔断器打开，流式请求使用备用模型")
                except CircuitBreakerOpenError as e:
                    logger.error(f"[Failover] 主备模型熔断器均已打开: {e}")
                    yield f"[错误] LLM 服务不可用：{e}"
                    return
            else:
                yield "[错误] LLM 熔断器已打开，请稍后重试"
                return

        # 流式请求
        stream_ok = False
        if use_backup:
            with self._use_backup_params():
                full_content = ""
                for chunk in self._do_request(messages, stream=True):
                    if chunk:
                        full_content += chunk
                        stream_ok = True
                        yield chunk

                if stream_ok and not full_content.startswith('[错误]'):
                    self._backup_circuit_breaker.record_success()
                else:
                    self._backup_circuit_breaker.record_failure()
        else:
            full_content = ""
            for chunk in self._do_request(messages, stream=True):
                if chunk:
                    full_content += chunk
                    stream_ok = True
                    yield chunk

            if stream_ok and not full_content.startswith('[错误]'):
                self._circuit_breaker.record_success()
            elif not stream_ok or full_content.startswith('[错误]'):
                self._circuit_breaker.record_failure()

        # 保存到记忆
        if use_memory and full_content and not full_content.startswith('[错误]'):
            self._messages.append(Message(role='user', content=prompt))
            self._messages.append(Message(role='assistant', content=full_content))


    def _chat_with_tools_request_loop(
        self, messages: list, tools: list, tool_choice: str, effective_max_tokens: int
    ) -> tuple:
        """chat_with_tools 核心请求+重试循环

        Returns:
            (content, reasoning_content, tool_calls, usage, failed)
        """
        content = ""
        reasoning_content = None
        tool_calls = None
        usage = None
        failed = False

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
                logger.error(f"chat_with_tools 失败：{e}")
                failed = True
                if attempt >= self.max_retries:
                    content = f"[错误] 工具调用失败：{e}"

        return content, reasoning_content, tool_calls, usage, failed

    def chat_with_tools(
        self,
        messages: list,
        tools: list,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        支持 function calling 的聊天接口，含主备模型自动切换

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
        reasoning_content = None
        tool_calls = None
        usage = None
        finish_reason = None
        error = None

        # ---- 主模型尝试 ----
        primary_available = True
        try:
            self._circuit_breaker.check()
        except CircuitBreakerOpenError as e:
            primary_available = False
            logger.warning(f"[Failover] 主模型熔断器已打开: {e}")

        if primary_available:
            content, reasoning_content, tool_calls, usage, failed = \
                self._chat_with_tools_request_loop(
                    messages, tools, tool_choice, effective_max_tokens
                )
            if failed and (not content or content.startswith('[错误]')):
                self._circuit_breaker.record_failure()
            elif content and not content.startswith('[错误]'):
                self._circuit_breaker.record_success()

        # ---- 主模型失败 → 尝试备用模型 ----
        if (not content or content.startswith('[错误]')) and self._has_backup:
            logger.warning(
                f"[Failover] 主模型失败，切换到备用模型 "
                f"({self.backup_provider}/{self.backup_model}@{self.backup_base_url})"
            )
            try:
                self._backup_circuit_breaker.check()
            except CircuitBreakerOpenError as e:
                logger.error(f"[Failover] 备用模型熔断器也已打开: {e}")
            else:
                with self._use_backup_params():
                    content, reasoning_content, tool_calls, usage, failed = \
                        self._chat_with_tools_request_loop(
                            messages, tools, tool_choice, effective_max_tokens
                        )
                    if failed and (not content or content.startswith('[错误]')):
                        self._backup_circuit_breaker.record_failure()
                    elif content and not content.startswith('[错误]'):
                        self._backup_circuit_breaker.record_success()
                        logger.info("[Failover] 备用模型 chat_with_tools 请求成功")

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

        # 如果 content 和 tool_calls 都为空但有 reasoning_content，
        # 说明 max_tokens 不足，所有 token 被 reasoning 消耗。
        # 此时以默认 max_tokens 重试一次。
        raw_tool_calls = msg.get('tool_calls', [])
        if not content and not raw_tool_calls and reasoning_content:
            req_tokens = data.get('max_tokens', 0)
            fallback_tokens = self.max_tokens
            logger.warning(
                f"[LLM] 检测到 reasoning 模型 token 耗尽 "
                f"(reasoning={len(reasoning_content)} chars, content/tool_calls为空, "
                f"req_max_tokens={req_tokens})，"
                f"以默认 max_tokens={fallback_tokens} 重试"
            )
            retry_data = dict(data)
            retry_data['max_tokens'] = fallback_tokens
            retry_response = requests.post(
                url, headers=headers, json=retry_data,
                timeout=self.timeout
            )
            retry_response.raise_for_status()
            retry_result = retry_response.json()
            retry_choice = retry_result.get('choices', [{}])[0]
            retry_msg = retry_choice.get('message', {})
            content = retry_msg.get('content', '') or ''
            reasoning_content = retry_msg.get('reasoning_content', '') or None
            usage_data = retry_result.get('usage')
            raw_tool_calls = retry_msg.get('tool_calls', [])
            # 重试后仍然为空则放弃，让上层错误处理
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
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None
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
