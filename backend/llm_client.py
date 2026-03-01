# -*- coding: utf-8 -*-
"""
阿里云百炼大模型客户端
使用 OpenAI 兼容 API 格式调用通义千问模型
文档：https://help.aliyun.com/zh/model-studio/developer-reference/compatibility-of-openai-compatible-rest-apis
"""

import json
import requests
from typing import Generator, Optional
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_MODEL


class BailianClient:
    """阿里云百炼大模型客户端"""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or DASHSCOPE_MODEL

        if not self.api_key:
            raise ValueError("请配置 DASHSCOPE_API_KEY 环境变量或在 config.py 中设置")

    def chat(self, messages: list, stream: bool = False, max_tokens: int = 4000) -> Generator:
        """
        发送聊天请求

        Args:
            messages: 消息列表，格式：[{"role": "user|assistant|system", "content": "..."}]
            stream: 是否使用流式输出

        Yields:
            流式输出时，每次 yield 一个文本片段
            非流式时，yield 完整响应
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': self.model,
            'messages': messages,
            'stream': stream,
            'temperature': 0.7,
            'max_tokens': max_tokens
        }

        url = f'{self.base_url}/chat/completions'

        try:
            if stream:
                # 流式输出
                response = requests.post(url, headers=headers, json=data, stream=True, timeout=120)
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
                # 非流式输出
                response = requests.post(url, headers=headers, json=data, timeout=120)
                response.raise_for_status()
                result = response.json()
                content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                yield content

        except requests.exceptions.Timeout:
            yield f"[错误] API 请求超时（120 秒）"
        except requests.exceptions.RequestException as e:
            error_msg = f"API 请求失败：{str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_msg += f"\n响应内容：{e.response.text}"
            yield f"[错误] {error_msg}"


# 全局客户端实例（延迟初始化）
_client: Optional[BailianClient] = None


def get_client() -> BailianClient:
    """获取客户端实例"""
    global _client
    if _client is None:
        _client = BailianClient()
    return _client


def chat_with_llm(prompt: str, system_prompt: str = None, max_tokens: int = 4000) -> str:
    """
    简单的聊天接口（非流式）

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词
        max_tokens: 最大生成 token 数

    Returns:
        LLM 响应文本
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    response = client.chat(messages, stream=False, max_tokens=max_tokens)
    return ''.join(response)


def chat_with_llm_stream(prompt: str, system_prompt: str = None) -> Generator:
    """
    流式聊天接口

    Args:
        prompt: 用户输入
        system_prompt: 系统提示词

    Yields:
        文本片段
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    for chunk in client.chat(messages, stream=True):
        yield chunk
