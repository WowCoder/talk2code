# -*- coding: utf-8 -*-
"""
Tracer —— 链路追踪管理器

持久化：注入 db_session 时，start_trace() 立刻落一条 running 占位，
       end_span() 增量刷新，end_trace() 更新为终态。
       这样崩溃 / 中断 / 进行中的需求也留有可查的痕迹 —— 此前只在
       end_trace() 一次性写入，中断任务的 trace 全部丢失。
       不注入时退化为内存字典（保持与现有无参构造测试兼容）。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from harness.observability.log_context import bind_trace_id

logger = logging.getLogger(__name__)


@dataclass
class Span:
    span_id: str
    parent_id: Optional[str]
    name: str
    start_time: float
    trace_id: Optional[str] = None   # 反查所属 Trace，用于增量落库
    end_time: Optional[float] = None
    status: str = "running"
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "trace_id": self.trace_id,
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
    status: str = "running"     # running / finished / error
    spans: list = field(default_factory=list)
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    # 「上报过缓存信息」的 input token 与调用数 —— 命中率的分母只含这些。
    # 供应商不回缓存字段的调用混进分母会把命中率系统性算低（实测约 29%
    # 的有用例调用没带该字段），且看起来依然像那么回事。
    cache_reported_tokens_in: int = 0
    cache_reported_calls: int = 0
    total_cost: float = 0.0
    calls: int = 0
    usage_reported_calls: int = 0
    cost_fallback_calls: int = 0
    cache_savings: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "requirement_id": self.requirement_id,
            "user_id": self.user_id,
            "status": self.status,
            "started_at": self.start_time,
            "ended_at": self.end_time,
            "total_duration_ms": round((self.end_time - self.start_time) * 1000, 1) if self.end_time else None,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens": self.total_tokens,
            "tokens_in": self.input_tokens,
            "tokens_out": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_reported_tokens_in": self.cache_reported_tokens_in,
            "cache_reported_calls": self.cache_reported_calls,
            # 分母是「上报过缓存信息的 input」，与后台 _hit_rate 同口径；
            # 一条都没上报时给 None（没数据），不是 0%（查过、没命中）
            "cache_hit_rate": (
                round(self.cached_tokens / self.cache_reported_tokens_in, 4)
                if self.cache_reported_tokens_in else None
            ),
            "total_cost": self.total_cost,
            "cost": self.total_cost,
            "calls": self.calls,
            "llm_calls": self.calls,
            "usage_reported_calls": self.usage_reported_calls,
            "usage_coverage": (round(self.usage_reported_calls / self.calls, 4)
                               if self.calls else None),
            "cache_coverage": (round(self.cache_reported_tokens_in / self.input_tokens, 4)
                               if self.input_tokens else None),
            "cost_fallback_calls": self.cost_fallback_calls,
            "cost_currency": "USD",
            "cost_is_estimate": True,
            "cache_savings": self.cache_savings,
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
        # trace_id 生成后立刻绑定日志上下文 —— 这是「日志 ↔ agent_traces 表」
        # 唯一能互查的钥匙。此前 trace_id 从不落日志，两边永远对不上。
        bind_trace_id(trace.trace_id)
        # ★ 立刻落库（status=running）：此前只在 end_trace() 一次性写，
        #   导致崩溃 / 中断 / 进行中的需求完全没有 trace（覆盖率仅约 1/4）。
        #   先占位，后续 end_span() 增量刷新，异常结束也留下痕迹。
        if self._persisted:
            self._persist_trace(trace)
        return trace

    def start_span(self, trace_id: str, name: str, parent_id: str = None, metadata: dict = None) -> Span:
        span = Span(
            span_id=uuid.uuid4().hex,
            parent_id=parent_id,
            name=name,
            start_time=time.time(),
            trace_id=trace_id,
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
        # ★ 增量刷新：每个 span 结束时把当前进度写回 DB，
        #   长任务中途也能看到已完成的阶段。
        if self._persisted and span.trace_id:
            trace = self._traces.get(span.trace_id)
            if trace:
                self._persist_trace(trace)

    def end_trace(self, trace_id: str, status: str = "finished"):
        trace = self._traces.get(trace_id)
        if not trace:
            return
        trace.end_time = time.time()
        # 有未正常关闭的 span 说明流程在此处被打断（异常 / kill / 重启）。
        # 显式标记 interrupted，避免与正常完成混在一起，也留给出问题的定位线索。
        still_open = [s for s in trace.spans if s.end_time is None]
        if still_open:
            if status == "finished":
                status = "interrupted"
            for s in still_open:
                s.end_time = time.time()
                s.status = status
        trace.status = status
        # span 元数据里的 tokens 只有编码循环写（runtime.py:303）—— 规划 /
        # 验收 / 修复 / AC 翻译的 span 不带这个键，只按 span 求和会少计
        # 25%~41%（实测 req233：span 和 153,364 vs 事件聚合 247,777）。
        # 先按 span 求和作为无 cost_tracker 时的退化路径。
        trace.total_tokens = sum(
            s.metadata.get("tokens", 0) for s in trace.spans
        )
        if self._cost_tracker:
            report = self._cost_tracker.get_report(trace_id)
            trace.total_cost = report.total_cost
            trace.input_tokens = report.input_tokens
            trace.output_tokens = report.output_tokens
            trace.cached_tokens = report.cached_tokens
            trace.cache_reported_tokens_in = report.reported_input_tokens
            trace.cache_reported_calls = report.reported_calls
            trace.calls = report.calls
            trace.usage_reported_calls = report.usage_reported_calls
            trace.cost_fallback_calls = report.cost_fallback_calls
            trace.cache_savings = report.cache_savings
            # total_tokens 与 total_cost **同源**：累计报告覆盖全部 LLM 调用
            # （客户端按实际请求尝试记账），input+output 即本 trace 的
            # 完整用量。此前 tokens 按 span 求和、cost 按累计报告，
            # 同一个页面上两个数字口径不同，tokens 系统性偏小。
            if report.total_tokens:
                trace.total_tokens = report.total_tokens
            # trace 已落库/入最近列表，cost 明细无需再按 trace 保留，
            # 联动清理避免 _usage 无界增长
            self._cost_tracker.clear(trace_id)

        # Persisted attempts are authoritative, including old events and calls
        # whose usage was never reported. The tracker remains the in-memory path
        # for evaluations and tests without a database.
        if self._persisted:
            try:
                from services.usage_summary import summarize_usage
                # Trace IDs already identify one execution, including evaluation
                # and independent calls excluded from requirement dashboards.
                usage = summarize_usage(self._db, trace_id=trace_id, scope=None)
                if usage["llm_calls"]:
                    trace.total_tokens = usage["tokens"]
                    trace.input_tokens = usage["tokens_in"]
                    trace.output_tokens = usage["tokens_out"]
                    trace.cached_tokens = usage["cached_tokens"]
                    trace.cache_reported_tokens_in = usage["cache_reported_tokens_in"]
                    trace.cache_reported_calls = usage["cache_reported_calls"]
                    trace.calls = usage["llm_calls"]
                    trace.usage_reported_calls = usage["usage_reported_calls"]
                    trace.cost_fallback_calls = usage["cost_fallback_calls"]
                    trace.cache_savings = usage["cache_savings"]
                    trace.total_cost = usage["cost"]
            except Exception as exc:
                logger.warning("汇总 trace 用量失败，保留内存报告：%s", exc)

        # 持久化整条 trace（跨重启可查）
        if self._persisted:
            self._persist_trace(trace)

        # 从活动字典移入最近列表（_traces 不再无界增长；_recent 是唯一内存回放源）
        self._traces.pop(trace_id, None)
        # trace 已结束：解绑，避免该线程后续日志挂着已结束的 trace_id
        bind_trace_id(None)
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
                start_time=d.get("started_at", d.get("start_time", time.time())),
                end_time=d.get("ended_at", d.get("end_time")),
                status=d.get("status", "finished"),
                spans=spans,
                total_tokens=row.total_tokens or 0,
                total_cost=row.total_cost or 0.0,
                input_tokens=d.get("input_tokens", 0),
                output_tokens=d.get("output_tokens", 0),
                cached_tokens=d.get("cached_tokens", 0),
                cache_reported_tokens_in=d.get("cache_reported_tokens_in", 0),
                cache_reported_calls=d.get("cache_reported_calls", 0),
                calls=d.get("calls", d.get("llm_calls", 0)),
                usage_reported_calls=d.get("usage_reported_calls", 0),
                cost_fallback_calls=d.get("cost_fallback_calls", 0),
                cache_savings=d.get("cache_savings"),
            )
        except Exception as e:
            logger.warning("加载持久化 trace 失败：%s", e)
            return None
