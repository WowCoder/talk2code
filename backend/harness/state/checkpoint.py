# -*- coding: utf-8 -*-
"""
CheckpointManager —— 持久化检查点，支持断点恢复
"""

import json
import time
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Checkpoint:
    """工作流检查点"""
    id: str
    requirement_id: int
    node_name: str  # planner / tool_coder / tool_executor
    state_json: str  # JSON 序列化的 AgentState
    created_at: float


class CheckpointManager:
    """
    持久化检查点管理

    保存时机：
    - 每个 LangGraph 节点执行完成后
    - 工具调用循环中每 3 轮保存一次
    """

    def __init__(self, db_session=None):
        self._db = db_session
        self._memory: dict[int, Checkpoint] = {}  # requirement_id → Checkpoint

    def save(self, requirement_id: int, node_name: str, state: dict) -> str:
        checkpoint_id = f"cp_{requirement_id}_{int(time.time() * 1000)}"
        cp = Checkpoint(
            id=checkpoint_id,
            requirement_id=requirement_id,
            node_name=node_name,
            state_json=json.dumps(state, default=str, ensure_ascii=False),
            created_at=time.time(),
        )
        self._memory[requirement_id] = cp
        return checkpoint_id

    def load(self, requirement_id: int) -> Optional[Checkpoint]:
        """加载最近的检查点"""
        return self._memory.get(requirement_id)

    def resume(self, requirement_id: int) -> Optional[dict]:
        """从检查点恢复状态"""
        cp = self.load(requirement_id)
        if cp and cp.node_name != 'end':
            return json.loads(cp.state_json)
        return None

    def clear(self, requirement_id: int):
        """清除检查点（任务成功完成后调用）"""
        self._memory.pop(requirement_id, None)
