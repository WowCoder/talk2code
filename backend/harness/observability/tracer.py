# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Tracer —— 链路追踪管理器
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


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
            "total_duration_ms": round((self.end_time - self.start_time) * 1000, 1) if self.end_time else None,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 4),
        }


class Tracer:
    """链路追踪管理器"""

    def __init__(self):
        self._traces: dict[str, Trace] = {}
        self._recent: list[Trace] = []

    def start_trace(self, requirement_id: int, user_id: int) -> Trace:
        trace = Trace(
            trace_id=str(uuid.uuid4())[:8],
            requirement_id=requirement_id,
            user_id=user_id,
            start_time=time.time(),
        )
        self._traces[trace.trace_id] = trace
        return trace

    def start_span(self, trace_id: str, name: str, parent_id: str = None, metadata: dict = None) -> Span:
        span = Span(
            span_id=str(uuid.uuid4())[:8],
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
        if trace:
            trace.end_time = time.time()
            trace.total_tokens = sum(
                s.metadata.get("tokens", 0) for s in trace.spans
            )

    def get_trace(self, trace_id: str) -> Optional[Trace]:
        return self._traces.get(trace_id)

    def recent_traces(self, limit: int = 20) -> list[dict]:
        traces = sorted(
            self._traces.values(),
            key=lambda t: t.start_time,
            reverse=True
        )[:limit]
        return [t.to_dict() for t in traces]
