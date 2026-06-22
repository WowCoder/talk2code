# -*- coding: utf-8 -*-
"""
CheckpointManager —— 持久化检查点，支持断点恢复

持久化：注入 db_session 时落 agent_checkpoints 表（每个 requirement 最近一条），跨重启可恢复。
       不注入时退化为内存字典（保持与现有无参构造测试兼容）。
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


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
        self._memory: dict[int, Checkpoint] = {}  # requirement_id → Checkpoint（无 db 时回退）

    @property
    def _persisted(self) -> bool:
        return self._db is not None

    def save(self, requirement_id: int, node_name: str, state: dict) -> str:
        checkpoint_id = f"cp_{requirement_id}_{int(time.time() * 1000)}"
        state_json = json.dumps(state, default=str, ensure_ascii=False)
        cp = Checkpoint(
            id=checkpoint_id,
            requirement_id=requirement_id,
            node_name=node_name,
            state_json=state_json,
            created_at=time.time(),
        )

        if self._persisted:
            try:
                from models.models import CheckpointRecord
                # 每个 requirement 只保留最近一条：先删旧
                self._db.query(CheckpointRecord).filter_by(
                    requirement_id=requirement_id
                ).delete()
                self._db.add(CheckpointRecord(
                    checkpoint_id=checkpoint_id,
                    requirement_id=requirement_id,
                    node_name=node_name,
                    state_json=state_json,
                ))
                self._db.commit()
            except Exception as e:
                logger.warning("持久化检查点失败，回退内存：%s", e)
                self._db.rollback()
        self._memory[requirement_id] = cp
        return checkpoint_id

    def load(self, requirement_id: int) -> Optional[Checkpoint]:
        """加载最近的检查点"""
        if self._persisted:
            cp = self._load_db(requirement_id)
            if cp:
                return cp
        return self._memory.get(requirement_id)

    def resume(self, requirement_id: int) -> Optional[dict]:
        """从检查点恢复状态"""
        cp = self.load(requirement_id)
        if cp and cp.node_name != 'end':
            return json.loads(cp.state_json)
        return None

    def clear(self, requirement_id: int):
        """清除检查点（任务成功完成后调用）"""
        if self._persisted:
            try:
                from models.models import CheckpointRecord
                self._db.query(CheckpointRecord).filter_by(
                    requirement_id=requirement_id
                ).delete()
                self._db.commit()
            except Exception as e:
                logger.warning("清除持久化检查点失败：%s", e)
                self._db.rollback()
        self._memory.pop(requirement_id, None)

    def _load_db(self, requirement_id: int) -> Optional[Checkpoint]:
        try:
            from models.models import CheckpointRecord
            row = self._db.query(CheckpointRecord).filter_by(
                requirement_id=requirement_id
            ).order_by(CheckpointRecord.created_at.desc()).first()
            if not row:
                return None
            return Checkpoint(
                id=row.checkpoint_id,
                requirement_id=row.requirement_id,
                node_name=row.node_name,
                state_json=row.state_json,
                created_at=row.created_at.timestamp() if row.created_at else time.time(),
            )
        except Exception as e:
            logger.warning("加载持久化检查点失败，回退内存：%s", e)
            return None
