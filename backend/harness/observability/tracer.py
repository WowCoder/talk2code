# -*- coding: utf-8 -*-
"""
Tracer —— 链路追踪管理器

持久化：注入 db_session 时，end_trace() 把整条 trace（含 spans/tokens/cost）落 agent_traces 表。
       不注入时退化为内存字典（保持与现有无参构造测试兼容）。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Span:
    span_id: str
    parent_id: Optional[str]
    name: str
    start_time: float
    end_time: Optional[float] = None
    status: str = "running"
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "metadata": self.metadata,
            "error": self.error,
            "duration_ms": round((self.end_time - self.start_time) * 1000, 1) if self.end_time else None,
        }


@dataclass
class Trace:
    trace_id: str
    requirement_id: int
    user_id: int
    start_time: float
    end_time: Optional[float] = None
    spans: list = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "requirement_id": self.requirement_id,
            "user_id": self.user_id,
            "total_duration_ms": round((self.end_time - self.start_time) * 1000, 1) if self.end_time else None,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 4),
        }


class Tracer:
    """链路追踪管理器"""

    def __init__(self, db_session=None, cost_tracker=None):
        self._db = db_session
        self._cost_tracker = cost_tracker
        self._traces: dict[str, Trace] = {}
        self._recent: list[Trace] = []

    @property
    def _persisted(self) -> bool:
        return self._db is not None

    def start_trace(self, requirement_id: int, user_id: int) -> Trace:
        trace = Trace(
            trace_id=uuid.uuid4().hex,
            requirement_id=requirement_id,
            user_id=user_id,
            start_time=time.time(),
        )
        self._traces[trace.trace_id] = trace
        return trace

    def start_span(self, trace_id: str, name: str, parent_id: str = None, metadata: dict = None) -> Span:
        span = Span(
            span_id=uuid.uuid4().hex,
            parent_id=parent_id,
            name=name,
            start_time=time.time(),
            metadata=metadata or {},
        )
        trace = self._traces.get(trace_id)
        if trace:
            trace.spans.append(span)
        return span

    def end_span(self, span: Span, status: str = "success", error: str = None):
        span.end_time = time.time()
        span.status = status
        if error:
            span.error = error

    def end_trace(self, trace_id: str):
        trace = self._traces.get(trace_id)
        if not trace:
            return
        trace.end_time = time.time()
        trace.total_tokens = sum(
            s.metadata.get("tokens", 0) for s in trace.spans
        )
        if self._cost_tracker:
            report = self._cost_tracker.get_report(trace_id)
            trace.total_cost = report.total_cost
            # trace 已落库/入最近列表，cost 明细无需再按 trace 保留，
            # 联动清理避免 _usage 无界增长
            self._cost_tracker.clear(trace_id)

        # 持久化整条 trace（跨重启可查）
        if self._persisted:
            self._persist_trace(trace)

        # 从活动字典移入最近列表（_traces 不再无界增长；_recent 是唯一内存回放源）
        self._traces.pop(trace_id, None)
        self._recent.append(trace)
        if len(self._recent) > 100:
            self._recent = self._recent[-100:]

    def get_trace(self, trace_id: str) -> Optional[Trace]:
        # 内存命中（活动 trace 或最近列表）
        trace = self._traces.get(trace_id)
        if trace:
            return trace
        for recent in self._recent:
            if recent.trace_id == trace_id:
                return recent
        # 持久化回查
        if self._persisted:
            return self._load_db_trace(trace_id)
        return None

    def recent_traces(self, limit: int = 20) -> list[dict]:
        # 已结束（_recent，有界 100）+ 进行中（_traces，仅活跃 trace）
        pool = list(self._recent) + list(self._traces.values())
        traces = sorted(
            pool,
            key=lambda t: t.start_time,
            reverse=True
        )[:limit]
        return [t.to_dict() for t in traces]

    # ---------- 持久化实现 ----------

    def _persist_trace(self, trace: Trace):
        try:
            from models.models import AgentTrace
            duration_ms = int((trace.end_time - trace.start_time) * 1000) if trace.end_time else 0
            # upsert：同 trace_id 覆盖
            existing = self._db.query(AgentTrace).filter_by(trace_id=trace.trace_id).first()
            if existing:
                row = existing
            else:
                row = AgentTrace(trace_id=trace.trace_id,
                                 requirement_id=trace.requirement_id,
                                 user_id=trace.user_id)
                self._db.add(row)
            row.data = trace.to_dict()
            row.total_tokens = trace.total_tokens
            row.total_cost = trace.total_cost
            row.duration_ms = duration_ms
            self._db.commit()
        except Exception as e:
            logger.warning("持久化 trace 失败：%s", e)
            self._db.rollback()

    def _load_db_trace(self, trace_id: str) -> Optional[Trace]:
        try:
            from models.models import AgentTrace
            row = self._db.query(AgentTrace).filter_by(trace_id=trace_id).first()
            if not row or not row.data:
                return None
            d = row.data
            span_fields = Span.__dataclass_fields__.keys()
            spans = [
                Span(**{k: v for k, v in s.items() if k in span_fields})
                for s in d.get("spans", [])
            ]
            return Trace(
                trace_id=row.trace_id,
                requirement_id=row.requirement_id,
                user_id=row.user_id,
                start_time=d.get("start_time", time.time()),
                end_time=d.get("end_time"),
                spans=spans,
                total_tokens=row.total_tokens or 0,
                total_cost=row.total_cost or 0.0,
            )
        except Exception as e:
            logger.warning("加载持久化 trace 失败：%s", e)
            return None
