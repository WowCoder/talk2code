# -*- coding: utf-8 -*-
"""
测试 Tracer span 嵌套/序列化、CostTracker 计算
对应 tasks.md 10.7
"""

import pytest
from harness.observability.tracer import Tracer, Trace, Span
from harness.observability.cost import CostTracker, CostReport


class TestSpan:
    """Span 测试"""

    def test_span_creation(self):
        """测试 Span 创建"""
        import time
        span = Span(
            span_id="span_001",
            parent_id=None,
            name="tool_coder_iter_0",
            start_time=time.time(),
            metadata={"tokens": 100},
        )
        assert span.span_id == "span_001"
        assert span.name == "tool_coder_iter_0"
        assert span.status == "running"
        assert span.metadata["tokens"] == 100

    def test_span_to_dict_in_progress(self):
        """测试运行中 Span 序列化"""
        import time
        span = Span(span_id="s1", parent_id=None, name="test", start_time=time.time())

        d = span.to_dict()
        assert d["span_id"] == "s1"
        assert d["name"] == "test"
        assert d["status"] == "running"
        assert d["duration_ms"] is None  # 尚未结束

    def test_span_to_dict_completed(self):
        """测试完成的 Span 序列化"""
        import time
        t0 = time.time() - 1.0  # 1 秒前
        span = Span(span_id="s2", parent_id=None, name="test", start_time=t0)
        span.end_time = time.time()
        span.status = "success"

        d = span.to_dict()
        assert d["status"] == "success"
        assert d["duration_ms"] is not None
        assert d["duration_ms"] > 0

    def test_span_with_error(self):
        """测试带错误的 Span"""
        import time
        span = Span(span_id="s3", parent_id=None, name="test", start_time=time.time())
        span.error = "LLM timeout"

        d = span.to_dict()
        assert d["error"] == "LLM timeout"


class TestTrace:
    """Trace 测试"""

    def test_trace_creation(self):
        """测试 Trace 创建"""
        import time
        trace = Trace(
            trace_id="tr_001",
            requirement_id=1,
            user_id=100,
            start_time=time.time(),
        )
        assert trace.trace_id == "tr_001"
        assert trace.requirement_id == 1
        assert trace.user_id == 100
        assert trace.spans == []

    def test_trace_to_dict(self):
        """测试 Trace 序列化"""
        import time
        t0 = time.time() - 2.0
        trace = Trace(
            trace_id="tr_002",
            requirement_id=1,
            user_id=100,
            start_time=t0,
        )
        span = Span(span_id="s1", parent_id=None, name="planner", start_time=t0)
        span.end_time = time.time()
        span.status = "success"
        span.metadata["tokens"] = 500
        trace.spans.append(span)
        trace.end_time = time.time()
        trace.total_tokens = 500

        d = trace.to_dict()
        assert d["trace_id"] == "tr_002"
        assert d["total_duration_ms"] is not None
        assert d["span_count"] == 1
        assert len(d["spans"]) == 1
        assert d["total_tokens"] == 500


class TestTracer:
    """Tracer 测试"""

    def test_start_trace(self):
        """测试开始追踪"""
        tracer = Tracer()
        trace = tracer.start_trace(requirement_id=1, user_id=100)

        assert trace is not None
        assert trace.requirement_id == 1
        assert trace.user_id == 100
        assert len(trace.trace_id) > 0

    def test_start_span(self):
        """测试开始 Span"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)

        span = tracer.start_span(trace.trace_id, "planner_node")
        assert span.name == "planner_node"
        assert span.status == "running"

        # Span 应该添加到 trace 中
        assert len(trace.spans) == 1

    def test_start_span_with_metadata(self):
        """测试带 metadata 的 Span"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)

        span = tracer.start_span(trace.trace_id, "llm_call", metadata={"model": "gpt-4"})
        assert span.metadata["model"] == "gpt-4"

    def test_start_span_Nested(self):
        """测试嵌套 Span"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)

        root = tracer.start_span(trace.trace_id, "workflow")
        child = tracer.start_span(trace.trace_id, "planner", parent_id=root.span_id)

        assert child.parent_id == root.span_id
        assert len(trace.spans) == 2

    def test_end_span(self):
        """测试结束 Span"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)
        span = tracer.start_span(trace.trace_id, "test")

        tracer.end_span(span, status="success")
        assert span.status == "success"
        assert span.end_time is not None

    def test_end_span_with_error(self):
        """测试结束带错误的 Span"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)
        span = tracer.start_span(trace.trace_id, "test")

        tracer.end_span(span, status="failure", error="timeout")
        assert span.status == "failure"
        assert span.error == "timeout"

    def test_end_trace(self):
        """测试结束追踪"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)

        span = tracer.start_span(trace.trace_id, "llm_call")
        span.metadata["tokens"] = 1000
        tracer.end_span(span)

        tracer.end_trace(trace.trace_id)
        assert trace.end_time is not None
        assert trace.total_tokens == 1000

    def test_get_trace(self):
        """测试获取追踪"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)
        retrieved = tracer.get_trace(trace.trace_id)
        assert retrieved is trace

    def test_get_nonexistent_trace(self):
        """测试获取不存在的追踪"""
        tracer = Tracer()
        assert tracer.get_trace("nonexistent") is None

    def test_recent_traces(self):
        """测试获取最近的追踪"""
        tracer = Tracer()
        tracer.start_trace(1, 100)
        tracer.start_trace(2, 100)

        recent = tracer.recent_traces(limit=10)
        assert len(recent) == 2

    def test_span_to_dict_serialization(self):
        """测试 Span 序列化完整性"""
        import time
        t0 = time.time()
        span = Span(span_id="s1", parent_id="p1", name="test", start_time=t0)
        span.end_time = t0 + 1.5
        span.status = "success"
        span.metadata = {"tokens": 500, "model": "gpt-4"}

        d = span.to_dict()
        assert all(k in d for k in ["span_id", "parent_id", "name", "start_time",
                                      "end_time", "status", "metadata", "error", "duration_ms"])
        assert d["duration_ms"] == 1500.0


class TestCostTracker:
    """CostTracker 测试"""

    def test_record_single_call(self):
        """测试记录单次调用"""
        tracker = CostTracker()
        tracker.record("trace_1", input_tokens=100, output_tokens=50, model="gpt-4o")

        report = tracker.get_report("trace_1")
        assert report.total_tokens == 150
        assert report.input_tokens == 100
        assert report.output_tokens == 50
        assert report.total_cost > 0

    def test_record_multiple_calls(self):
        """测试记录多次调用"""
        tracker = CostTracker()

        tracker.record("trace_1", 100, 50, "gpt-4o")
        tracker.record("trace_1", 200, 100, "gpt-4o")

        report = tracker.get_report("trace_1")
        assert report.total_tokens == 450
        assert report.input_tokens == 300
        assert report.output_tokens == 150

    def test_record_unknown_model(self):
        """测试使用未知模型时的默认价格"""
        tracker = CostTracker()
        tracker.record("trace_2", 1000, 500, model="unknown-model")

        report = tracker.get_report("trace_2")
        assert report.total_tokens == 1500
        assert report.total_cost > 0  # 使用默认价格

    def test_get_nonexistent_report(self):
        """测试获取不存在的报告"""
        tracker = CostTracker()
        report = tracker.get_report("nonexistent")
        assert report.total_tokens == 0
        assert report.total_cost == 0.0

    def test_by_model_breakdown(self):
        """测试按模型分拆统计"""
        tracker = CostTracker()
        tracker.record("trace_3", 100, 50, "gpt-4o")
        tracker.record("trace_3", 200, 100, "gpt-4o-mini")

        report = tracker.get_report("trace_3")
        assert "gpt-4o" in report.by_model
        assert "gpt-4o-mini" in report.by_model

    def test_extract_usage_openai(self):
        """测试从 OpenAI 响应提取 usage"""
        tracker = CostTracker()
        input_tok, output_tok = tracker.extract_usage(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "openai_compatible"
        )
        assert input_tok == 100
        assert output_tok == 50

    def test_extract_usage_anthropic(self):
        """测试从 Anthropic 响应提取 usage"""
        tracker = CostTracker()
        input_tok, output_tok = tracker.extract_usage(
            {"input_tokens": 200, "output_tokens": 80},
            "anthropic_compatible"
        )
        assert input_tok == 200
        assert output_tok == 80

    def test_extract_usage_none(self):
        """测试无 usage 数据时"""
        tracker = CostTracker()
        input_tok, output_tok = tracker.extract_usage(None, "openai_compatible")
        assert input_tok == 0
        assert output_tok == 0

    def test_pricing_table_has_entries(self):
        """测试价格表包含已知模型"""
        tracker = CostTracker()
        assert "gpt-4o" in tracker.PRICING
        assert "claude-opus-4-7" in tracker.PRICING
        assert "deepseek-v3" in tracker.PRICING
