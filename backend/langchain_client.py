# -*- coding: utf-8 -*-
"""
阿里云百炼大模型客户端 (LangChain 版本)
使用 LangChain 框架调用通义千问模型，支持会话记忆
"""

import os
import json
from typing import Generator, Optional, List, Dict
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_MODEL

# 尝试导入 LangChain，如果未安装则使用基础版本
try:
    from langchain_openai import ChatOpenAI
    from langchain.memory import ConversationBufferMemory
    from langchain.schema import HumanMessage, AIMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False


class BailianLLM:
    """阿里云百炼大模型客户端（LangChain 版本）"""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or DASHSCOPE_MODEL

        if not self.api_key:
            raise ValueError("请配置 DASHSCOPE_API_KEY 环境变量或在 config.py 中设置")

        self._llm = None
        self._memory = None

    @property
    def llm(self):
        """懒加载 LLM 实例"""
        if self._llm is None:
            if LANGCHAIN_AVAILABLE:
                self._llm = ChatOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.model,
                    temperature=0.7,
                    max_tokens=4000,
                    streaming=False,
                    timeout=60,  # 超时时间（秒）
                    max_retries=2  # 最大重试次数
                )
            else:
                # 回退到基础客户端
                from llm_client import get_client
                self._llm = get_client()
        return self._llm

    def load_memory_from_db(self, dialogue_history: list):
        """从数据库加载对话历史到 memory"""
        if not LANGCHAIN_AVAILABLE:
            return

        # 清空现有记忆
        self.clear_memory()

        # 从对话历史中提取用户和 AI 的消息
        for msg in dialogue_history:
            if msg.get('role') == 'user':
                self.memory.chat_memory.add_user_message(msg.get('content', ''))
            elif msg.get('role') == 'agent' and msg.get('name') == 'AI 助手':
                self.memory.chat_memory.add_ai_message(msg.get('content', ''))

    @property
    def memory(self) -> ConversationBufferMemory:
        """获取会话记忆"""
        if self._memory is None:
            if LANGCHAIN_AVAILABLE:
                self._memory = ConversationBufferMemory(
                    memory_key="chat_history",
                    return_messages=True
                )
            else:
                self._memory = SimpleMemory()
        return self._memory

    def clear_memory(self):
        """清空会话记忆"""
        if self._memory:
            self._memory.clear()

    def chat(self, prompt: str, system_prompt: str = None, use_memory: bool = True) -> str:
        """
        聊天（非流式）

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆

        Returns:
            LLM 响应文本
        """
        if LANGCHAIN_AVAILABLE:
            from langchain.schema import HumanMessage, AIMessage, SystemMessage
            import signal

            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))

            # 添加历史记忆
            if use_memory:
                chat_history = self.memory.chat_memory.messages
                messages.extend(chat_history)

            messages.append(HumanMessage(content=prompt))

            try:
                # 使用超时保护
                def handler(signum, frame):
                    raise TimeoutError("LLM 调用超时（60 秒）")

                # 注册信号处理器（仅在 Unix 上有效）
                old_handler = None
                try:
                    old_handler = signal.signal(signal.SIGALRM, handler)
                    signal.alarm(60)  # 60 秒超时
                except (ValueError, OSError):
                    # 如果在非主线程或 Windows 上，跳过超时保护
                    pass

                try:
                    response = self.llm.invoke(messages)
                    ai_response = response.content
                finally:
                    # 取消闹钟
                    try:
                        signal.alarm(0)
                        if old_handler:
                            signal.signal(signal.SIGALRM, old_handler)
                    except:
                        pass

                # 保存到记忆
                if use_memory:
                    self.memory.chat_memory.add_user_message(prompt)
                    self.memory.chat_memory.add_ai_message(ai_response)

                return ai_response
            except TimeoutError as e:
                print(f"LangChain 调用超时：{e}，回退到基础客户端")
                from llm_client import chat_with_llm
                return chat_with_llm(prompt, system_prompt)
            except Exception as e:
                print(f"LangChain 调用失败：{e}，回退到基础客户端")
                # 回退到基础客户端
                from llm_client import chat_with_llm
                return chat_with_llm(prompt, system_prompt)
        else:
            # 回退到基础客户端
            from llm_client import chat_with_llm
            return chat_with_llm(prompt, system_prompt)

    def chat_stream(self, prompt: str, system_prompt: str = None, use_memory: bool = True) -> Generator:
        """
        流式聊天

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词
            use_memory: 是否使用会话记忆

        Yields:
            文本片段
        """
        if LANGCHAIN_AVAILABLE:
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))

            if use_memory:
                chat_history = self.memory.chat_memory.messages
                messages.extend(chat_history)

            messages.append(HumanMessage(content=prompt))

            try:
                for chunk in self.llm.stream(messages):
                    if chunk.content:
                        yield chunk.content

                # 保存到记忆
                if use_memory:
                    self.memory.chat_memory.add_user_message(prompt)
                    # 注意：流式输出需要收集完整响应后再保存
            except Exception as e:
                yield f"[错误] API 请求失败：{str(e)}"
        else:
            from llm_client import chat_with_llm_stream
            for chunk in chat_with_llm_stream(prompt, system_prompt):
                yield chunk

    def get_chat_history(self) -> List[Dict]:
        """获取会话历史"""
        if LANGCHAIN_AVAILABLE and self._memory:
            messages = self.memory.chat_memory.messages
            history = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    history.append({'role': 'user', 'content': msg.content})
                elif isinstance(msg, AIMessage):
                    history.append({'role': 'assistant', 'content': msg.content})
            return history
        return []


# 全局实例字典（每个需求一个实例）
_llm_instances: Dict[str, BailianLLM] = {}


def get_llm(instance_id: str = "default") -> BailianLLM:
    """获取 LLM 实例"""
    if instance_id not in _llm_instances:
        _llm_instances[instance_id] = BailianLLM()
    return _llm_instances[instance_id]


def clear_llm_memory(instance_id: str = "default"):
    """清空指定实例的会话记忆"""
    if instance_id in _llm_instances:
        _llm_instances[instance_id].clear_memory()
