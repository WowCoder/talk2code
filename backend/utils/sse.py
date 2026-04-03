# -*- coding: utf-8 -*-
"""
SSE 工具函数
包含 SSE 消息格式化等功能
"""

import json


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
