# -*- coding: utf-8 -*-
"""
消息总线 —— 解耦多角色间通信

借鉴 MetaGPT 的 MGXEnv.publish_message() 四分支路由设计：
- 分支A: 用户直接 @角色 → 绕过 TeamLeader，直接投递
- 分支B: direct_chat 角色回复 → 仅在角色和用户之间
- 分支C: TeamLeader 发出 → 直接投递给目标角色
- 分支D: 其他角色发出 → 强制路由给 TeamLeader
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@dataclass
class Message:
    """统一消息格式"""
    role: str                         # 发送者角色名 ("TeamLeader" / "user" / ...)
    content: str                      # 消息内容
    send_to: str = ""                 # 目标接收者 (空 = 广播)
    msg_type: str = "task"            # "task" | "reply" | "report" | "question" | "system"
    timestamp: str = field(default_factory=_now)
    metadata: dict = field(default_factory=dict)


class AgentBus:
    """Agent 消息总线 —— 解耦角色间通信，统一 SSE 推送

    四分支路由：
    - 分支A: 用户 @角色 → 直接投递给目标角色（绕过 TeamLeader）
    - 分支B: direct_chat 角色 → 消息仅在角色和用户间，TL 和其他角色不参与
    - 分支C: TeamLeader 发出 → 直接发布给 send_to 指定的角色
    - 分支D: 其他角色发出 → 强制路由给 TeamLeader（汇报机制）
    """

    def __init__(self, sse_reporter=None):
        self._messages: list[Message] = []
        self._direct_chat_roles: set[str] = set()  # 用户直接对话的角色
        self.sse = sse_reporter

    # ---- 发布 ----

    def publish(self, content: str, role: str,
                send_to: str = "", msg_type: str = "task",
                metadata: dict = None) -> Message:
        """
        发布消息 —— 四分支路由。

        Args:
            content: 消息内容
            role: 发送者 ("user" / "TeamLeader" / "ProductManager" / ...)
            send_to: 目标接收者。空字符串表示按路由规则自动决定
            msg_type: 消息类型
            metadata: 附加元数据

        Returns:
            创建的消息对象
        """
        msg = Message(
            role=role,
            content=content,
            send_to=send_to,
            msg_type=msg_type,
            metadata=metadata or {},
        )

        # ---- 分支A: 用户直接 @某角色 ----
        if role == "user" and send_to and send_to != "TeamLeader":
            self._direct_chat_roles.add(send_to)
            msg.metadata["routed_by"] = "branch_a"

        # ---- 分支B: direct_chat 角色回复用户 ----
        elif role in self._direct_chat_roles:
            msg.send_to = "user"
            msg.metadata["routed_by"] = "branch_b"

        # ---- 分支C: TeamLeader 发出 ----
        elif role == "TeamLeader":
            if not send_to:
                send_to = "none"  # dummy 消息（TL 自言自语）
            msg.send_to = send_to
            msg.metadata["routed_by"] = "branch_c"

        # ---- 分支D: 其他角色发出 → 路由给 TeamLeader ----
        else:
            # 角色完成任务后汇报给 TL
            msg.send_to = "TeamLeader"
            msg.metadata["routed_by"] = "branch_d"

        self._messages.append(msg)
        return msg

    # ---- 订阅 ----

    def observe(self, role_name: str, since: int = 0) -> list[Message]:
        """
        拉取该角色关心的消息。

        Args:
            role_name: 角色名
            since: 从第几条消息开始拉取（分页）

        Returns:
            该角色关心的消息列表
        """
        relevant = []
        for msg in self._messages[since:]:
            # 目标明确指向该角色
            if msg.send_to == role_name:
                relevant.append(msg)
            # TeamLeader 看到所有非用户消息
            elif role_name == "TeamLeader" and msg.role != "user":
                relevant.append(msg)
            # 用户看到 direct_chat 角色的回复
            elif role_name == "user" and msg.send_to == "user":
                relevant.append(msg)
        return relevant

    def observe_all(self, role_name: str = "") -> list[Message]:
        """获取所有消息（调试用）"""
        if not role_name:
            return list(self._messages)
        return self.observe(role_name)

    # ---- 历史 ----

    def history(self) -> list[Message]:
        """完整消息历史"""
        return list(self._messages)

    def recent(self, n: int = 10) -> list[Message]:
        """最近 n 条消息"""
        return self._messages[-n:] if self._messages else []

    def last_from(self, role_name: str) -> Optional[Message]:
        """该角色最后一条消息"""
        for msg in reversed(self._messages):
            if msg.role == role_name:
                return msg
        return None

    # ---- SSE 推送 ----

    def publish_with_sse(self, content: str, role: str,
                         requirement_id: int,
                         send_to: str = "", msg_type: str = "task",
                         metadata: dict = None) -> Message:
        """发布消息并同步推送到 SSE"""
        msg = self.publish(content, role, send_to, msg_type, metadata)

        if self.sse:
            display_name = metadata.get("display_name", role) if metadata else role
            self.sse.dialogue(
                requirement_id, "agent", display_name,
                content[:1500], msg_type
            )

        return msg

    # ---- 状态 ----

    def clear(self):
        """清空消息历史"""
        self._messages.clear()
        self._direct_chat_roles.clear()

    @property
    def message_count(self) -> int:
        return len(self._messages)
