# -*- coding: utf-8 -*-
"""
工具类
包含密码加密、内容分块、SSE 推送等辅助功能
"""

import bcrypt
import time
import json
from typing import Callable, Optional
from flask import Response


def hash_password(password: str) -> str:
    """
    密码加密（使用 bcrypt）

    Args:
        password: 原始密码

    Returns:
        加密后的密码哈希
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """
    验证密码

    Args:
        password: 原始密码
        password_hash: 存储的密码哈希

    Returns:
        验证是否通过
    """
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


def chunk_content(content: str, chunk_size: int = 50) -> list:
    """
    将内容分块，用于流式推送

    Args:
        content: 完整内容
        chunk_size: 每块大小（字符数）

    Returns:
        分块后的内容列表
    """
    return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]


class SSEMessage:
    """
    SSE 消息格式化
    """

    @staticmethod
    def format_event(event_type: str, data: dict) -> str:
        """
        格式化 SSE 消息

        Args:
            event_type: 事件类型（dialogue/code/progress）
            data: 数据字典

        Returns:
            SSE 格式化的字符串
        """
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @staticmethod
    def dialogue_message(role: str, name: str, content: str, timestamp: str) -> str:
        """
        对话消息

        Args:
            role: 角色（user/agent）
            name: 名称（用户/智能体名称）
            content: 消息内容
            timestamp: 时间戳

        Returns:
            SSE 消息字符串
        """
        return SSEMessage.format_event('dialogue', {
            'role': role,
            'name': name,
            'content': content,
            'timestamp': timestamp
        })

    @staticmethod
    def code_message(filename: str, content: str, line_number: int, is_complete: bool = False) -> str:
        """
        代码消息

        Args:
            filename: 文件名
            content: 代码内容
            line_number: 行号
            is_complete: 是否完成

        Returns:
            SSE 消息字符串
        """
        return SSEMessage.format_event('code', {
            'filename': filename,
            'content': content,
            'line_number': line_number,
            'is_complete': is_complete
        })

    @staticmethod
    def progress_message(current_agent: str, progress: int, status: str = 'processing') -> str:
        """
        进度消息

        Args:
            current_agent: 当前智能体
            progress: 进度百分比
            status: 状态

        Returns:
            SSE 消息字符串
        """
        return SSEMessage.format_event('progress', {
            'current_agent': current_agent,
            'progress': progress,
            'status': status
        })

    @staticmethod
    def complete_message(requirement_id: int) -> str:
        """
        完成消息

        Args:
            requirement_id: 需求 ID

        Returns:
            SSE 消息字符串
        """
        return SSEMessage.format_event('complete', {
            'requirement_id': requirement_id
        })


def stream_generator(chunks: list, event_type: str = 'message') -> Callable:
    """
    创建流式生成器

    Args:
        chunks: 内容分块列表
        event_type: 事件类型

    Yields:
        SSE 格式的消息
    """
    def generate():
        for chunk in chunks:
            yield SSEMessage.format_event(event_type, {'content': chunk})
            time.sleep(0.1)
    return generate


def get_current_timestamp() -> str:
    """
    获取当前时间戳（ISO 格式）

    Returns:
        时间戳字符串
    """
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
