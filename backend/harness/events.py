# -*- coding: utf-8 -*-
"""
类型化事件模型 —— Pydantic BaseModel 替代松散 dict

ToolCallLoop 内部使用这些模型表示工具事件，
序列化到 dialogue_history 时转为 dict 保持数据库兼容。
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, ValidationError


class ToolCallEvent(BaseModel):
    """单个工具调用事件"""
    name: str = Field(..., description="工具名称，如 read_file / write_file")
    arguments: dict = Field(default_factory=dict, description="工具调用参数")
    display_label: str = Field(default="", description="前端展示用简短标签")
    success: bool = Field(default=False, description="工具执行是否成功")
    blocked: bool = Field(default=False, description="是否被 PRE_TOOL_USE 硬约束跳过")

    def to_dict(self) -> dict:
        """序列化为 dict（兼容现有 dialogue_history 格式）"""
        return {
            "name": self.name,
            "readable": self.display_label,
            "success": self.success,
            "blocked": self.blocked,
            "arguments": self.arguments,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCallEvent":
        """从 dialogue_history 中的 dict 反序列化"""
        return cls(
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
            display_label=data.get("readable", ""),
            success=data.get("success", False),
            blocked=data.get("blocked", False),
        )


class IterationBatchEvent(BaseModel):
    """一轮迭代的批量事件（替代逐个 tool_call/tool_result/thinking SSE）"""
    iteration: int = Field(..., description="第几轮迭代")
    coder_name: str = Field(default="", description="角色名称")
    thinking_preview: str = Field(default="", description="thinking 前 100 字符预览")
    agent_text: str = Field(default="", description="LLM 回复文本截断")
    tools: List[ToolCallEvent] = Field(default_factory=list, description="本轮工具调用列表")
    content: str = Field(default="", description="迭代摘要文本")

    def to_dict(self) -> dict:
        """序列化为 dict（兼容现有 dialogue_history 格式和 SSE 推送）"""
        return {
            "iteration": self.iteration,
            "coder_name": self.coder_name,
            "thinking_preview": self.thinking_preview,
            "agent_text": self.agent_text,
            "tools": [t.to_dict() for t in self.tools],
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IterationBatchEvent":
        """从 dialogue_history 中的 dict 反序列化"""
        tools_data = data.get("tools", [])
        tools = [ToolCallEvent.from_dict(t) for t in tools_data] if tools_data else []
        return cls(
            iteration=data.get("iteration", 0),
            coder_name=data.get("coder_name", ""),
            thinking_preview=data.get("thinking_preview", ""),
            agent_text=data.get("agent_text", ""),
            tools=tools,
            content=data.get("content", ""),
        )


class ThinkingEvent(BaseModel):
    """LLM 思考过程事件（对应 reasoning_content）"""
    name: str = Field(default="", description="角色名称")
    content: str = Field(default="", description="思考内容")

    def to_dict(self) -> dict:
        return {
            "role": "thinking",
            "name": self.name,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ThinkingEvent":
        return cls(
            name=data.get("name", ""),
            content=data.get("content", ""),
        )
