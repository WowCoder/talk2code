# -*- coding: utf-8 -*-
"""
测试 Tracer span 嵌套/序列化、CostTracker 计算
对应 tasks.md 10.7
"""

import pytest
from harness.observability.tracer import Tracer, Trace, Span
from harness.observability.cost import CostTracker, CostReport


def test_trace_report_keeps_unknown_attempts_and_coverage():
    tracker = CostTracker()
    tracer = Tracer(cost_tracker=tracker)
    trace = tracer.start_trace(42, 1)
    tracker.record(trace.trace_id, 100, 10, "gpt-4o", cached_tokens=40)
    tracker.record(trace.trace_id, 0, 0, "gpt-4o", usage_reported=False)
    tracer.end_trace(trace.trace_id, status="paused")
    report = tracer.get_trace(trace.trace_id).to_dict()
    assert report["status"] == "paused"
    assert report["llm_calls"] == report["calls"] == 2
    assert report["usage_reported_calls"] == 1
    assert report["usage_coverage"] == .5
    assert report["cache_coverage"] == 1
    assert report["cost_currency"] == "USD"
    assert report["cost_is_estimate"] is True
    assert report["cache_savings"] is not None
    assert report["cache_savings"] >= 0


def test_error_and_paused_trace_close_all_open_spans():
    for status in ("error", "paused", "awaiting_confirmation"):
        tracer = Tracer()
        trace = tracer.start_trace(42, 1)
        span = tracer.start_span(trace.trace_id, "planning")
        tracer.end_trace(trace.trace_id, status=status)
        assert span.end_time is not None
        assert span.status != "running"


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
        assert "claude-opus-4-5" in tracker.PRICING
        assert "deepseek-v3" in tracker.PRICING


class TestCostAccounting:
    """成本口径守卫（2026-10-08 修三处偏差）

    三处都会让后台/详情页的数字失真：
      ① 主力模型未登记 → 按兜底价算（贵 21 倍）
      ② 缓存命中部分没折价 → 输入成本系统性偏高
      ③ 只有编码阶段进累计 → 同一个「本次成本」在两处是两个数
    """

    # ---- ① 定价登记 ----

    def test_main_model_is_registered_not_fallback(self):
        """主力模型必须显式登记，不能落到兜底价"""
        p = CostTracker.price_for("agnes-3.0-flash")
        assert p == CostTracker.PRICING["agnes-3.0-flash"], p
        assert p["input"] < 1.0, "走到兜底价（$1/$4）了"
        assert "cached" in p and p["cached"] < p["input"]

    def test_family_prefix_fallback(self):
        """同族新版本号按前缀回退，不必逐个登记"""
        assert CostTracker.price_for("agnes-9-turbo")["input"] == \
            CostTracker.PRICING["agnes-3.0-flash"]["input"]
        assert CostTracker.price_for("deepseek-v9")["input"] == \
            CostTracker.PRICING["deepseek-v3"]["input"]

    def test_unknown_model_warns_and_uses_fallback(self, caplog):
        """未登记模型可用兜底价，但必须留下可发现的告警（不能静默）"""
        import logging
        with caplog.at_level(logging.WARNING):
            p = CostTracker.price_for("nobody-has-this-model")
        assert p["input"] == CostTracker.FALLBACK_PRICING["input"]
        assert any("未登记模型" in r.message for r in caplog.records)
        # 同一个名字只告警一次，避免每次调用刷屏
        n = len(caplog.records)
        CostTracker.price_for("nobody-has-this-model")
        assert len(caplog.records) == n

    def test_price_for_always_has_cached_key(self):
        """调用方不该各自判断缺键：price_for 一定补齐 cached"""
        for m in ("gpt-4o", "deepseek-v3", "agnes-3.0-flash", "whatever-x"):
            assert "cached" in CostTracker.price_for(m), m

    # ---- ② 缓存折价 ----

    def test_cached_tokens_are_discounted(self):
        """命中部分必须按缓存价计，而不是把命中量也按全价收一遍"""
        tin, tout, cached = 1_000_000, 0, 1_000_000
        full = CostTracker._cost_of(
            CostTracker.price_for("agnes-3.0-flash"), tin, tout, 0)
        hit = CostTracker._cost_of(
            CostTracker.price_for("agnes-3.0-flash"), tin, tout, cached)
        assert hit < full
        # 全部命中时成本 = 缓存价，正好是全价的 10%
        assert hit == pytest.approx(full * CostTracker.CACHED_INPUT_RATIO)

    def test_record_matches_estimate(self):
        """累计成本（record）与单次成本（estimate_cost_usd）必须逐位一致"""
        from harness.observability.cost import estimate_cost_usd
        t = CostTracker()
        t.record("t1", 12_345, 678, "agnes-3.0-flash", 9_000)
        assert t.get_report("t1").total_cost == pytest.approx(
            estimate_cost_usd("agnes-3.0-flash", 12_345, 678, 9_000), abs=1e-12)

    def test_cached_above_input_is_clamped(self):
        """供应商上报异常（命中量 > 输入量）不能让成本算成负数"""
        c = CostTracker._cost_of(
            CostTracker.price_for("agnes-3.0-flash"), 100, 10, 99_999)
        assert c > 0

    # ---- ③ 记账覆盖面 ----

    def test_shared_cost_tracker_is_singleton(self):
        from harness.observability.cost import shared_cost_tracker
        assert shared_cost_tracker() is shared_cost_tracker()

    def test_non_coding_llm_call_is_counted(self):
        """规划/验收/修复这类非编码调用也必须进累计成本

        判据：绑好 trace → 调一次 record_llm_turn（不带 requirement_id，
        证明记账按 trace_id 而不是需求号）→ 累计里出现这笔用量。
        """
        from types import SimpleNamespace
        from harness.observability import trace_writer as tw
        from harness.observability.cost import shared_cost_tracker, estimate_cost_usd
        from harness.observability.log_context import bind_trace_id

        shared_cost_tracker().clear()
        bind_trace_id("trace_cost_guard")
        try:
            resp = SimpleNamespace(
                usage={"prompt_tokens": 1000, "completion_tokens": 100,
                       "prompt_tokens_details": {"cached_tokens": 400}},
                content="ok", is_error=False, error=None,
                finish_reason="stop", reasoning_content=None, tool_calls=None,
            )
            ret = tw.record_llm_turn(
                None, stage="planning", model="agnes-3.0-flash",
                system_prompt="s", prompt="p", response=resp,
            )
            # 没有 requirement_id → 不落库，但不该影响记账
            assert ret is None
            rep = shared_cost_tracker().get_report("trace_cost_guard")
            assert (rep.input_tokens, rep.output_tokens, rep.cached_tokens) == (1000, 100, 400)
            assert rep.total_cost == pytest.approx(
                estimate_cost_usd("agnes-3.0-flash", 1000, 100, 400), abs=1e-12)
        finally:
            bind_trace_id(None)
            shared_cost_tracker().clear()

    def test_event_row_carries_discounted_cost(self):
        """落库的单条事件也要带折价后的成本（否则列表页与详情页又对不上）"""
        from types import SimpleNamespace
        from harness.observability import trace_writer as tw
        from harness.observability.cost import estimate_cost_usd, shared_cost_tracker

        class _W:
            def __init__(self):
                self.kw = None

            def llm_call(self, **kw):
                self.kw = kw
                return "ok"

        w = _W()
        resp = SimpleNamespace(
            usage={"prompt_tokens": 1000, "completion_tokens": 100,
                   "prompt_tokens_details": {"cached_tokens": 400}},
            content="ok", is_error=False, error=None,
            finish_reason="stop", reasoning_content=None, tool_calls=None,
        )
        try:
            tw.record_llm_turn(1, stage="verifying", model="agnes-3.0-flash",
                               system_prompt="s", prompt="p", response=resp,
                               writer=w)
            assert w.kw["cached_tokens"] == 400
            assert w.kw["cost"] == pytest.approx(
                estimate_cost_usd("agnes-3.0-flash", 1000, 100, 400), abs=1e-12)
        finally:
            shared_cost_tracker().clear()

    def test_runtime_does_not_double_record(self):
        """编码路径不许再自己记账 —— 两处都记会让成本翻倍

        `record_llm_turn` 是唯一记账点；runtime 里若还留着
        `cost_tracker.record(...)`，同一轮就会被计两次。
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2]
               / "harness" / "runtime.py").read_text(encoding="utf-8")
        assert "cost_tracker.record(" not in src, "编码路径自己记账 → 成本翻倍"
        # 但「本轮用了多少 token」仍要写进 span（进度展示依赖它）
        assert 'span.metadata["tokens"]' in src

    def test_wiring_uses_shared_tracker(self):
        """各入口必须注入共享实例，否则 tracer 读不到非编码阶段的用量"""
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for rel in ("harness/__init__.py", "services/requirement_service.py",
                    "routes/requirements.py"):
            src = (root / rel).read_text(encoding="utf-8")
            assert "shared_cost_tracker()" in src, rel
            assert "CostTracker()" not in src, f"{rel} 里还有各建各的 CostTracker()"

    # ---- ④ total_tokens 与成本同源（2026-10-15 修少计 25%~41%） ----

    def test_end_trace_tokens_match_cost_report(self):
        """total_tokens 必须与 total_cost 同源，不能按 span 求和。

        此前 tokens 按 `span.metadata["tokens"]` 求和，而那个键**只有编码循环写**
        （runtime.py:303）—— 规划 / 验收 / 修复 / AC 翻译的用量一分不计。
        实测 req233：span 和 153,364 vs 事件聚合 247,777，少计 38%。
        """
        tracker = CostTracker()
        tracer = Tracer(cost_tracker=tracker)
        trace = tracer.start_trace(1, 100)

        # 编码 span：这个键只有编码循环会写，且与 record_llm_turn 记账的是
        # **同一批调用**（同一次 response.usage）—— 被报告覆盖不会丢
        span = tracer.start_span(trace.trace_id, "tool_coder_iter_0")
        span.metadata["tokens"] = 1000
        tracer.end_span(span)
        tracker.record(trace.trace_id, 800, 200, "agnes-3.0-flash")

        # 规划阶段的调用只进累计报告，span 不带 tokens 键
        tracker.record(trace.trace_id, 5000, 500, "agnes-3.0-flash")

        tracer.end_trace(trace.trace_id)
        # 6500 = (800+200) + (5000+500)；按 span 求和只会得到 1000，少计 84%
        assert trace.total_tokens == 6500
        assert trace.input_tokens == 5800
        # 与成本同源：tokens 与 cost 必须描述同一批调用
        assert trace.total_cost > 0

    def test_end_trace_without_tracker_falls_back_to_spans(self):
        """无 cost_tracker 时仍按 span 求和（无参构造的旧路径不能坏）"""
        tracer = Tracer()
        trace = tracer.start_trace(1, 100)
        span = tracer.start_span(trace.trace_id, "tool_coder_iter_0")
        span.metadata["tokens"] = 1000
        tracer.end_span(span)
        tracer.end_trace(trace.trace_id)
        assert trace.total_tokens == 1000

    def test_trace_to_dict_cache_fields(self):
        """to_dict 要带缓存三件套；一条都没上报时命中率是 None 不是 0%"""
        import time
        trace = Trace(trace_id="tr_cache", requirement_id=1, user_id=1,
                      start_time=time.time())
        d = trace.to_dict()
        assert d["cached_tokens"] == 0
        assert d["cache_reported_tokens_in"] == 0
        assert d["cache_hit_rate"] is None, "没数据必须给 None，0% 会被读成「缓存失效」"

        trace.cached_tokens = 400
        trace.cache_reported_tokens_in = 1000
        d = trace.to_dict()
        assert d["cache_hit_rate"] == 0.4

    # ---- ⑤ 命中率分母只含上报过的调用（三态语义） ----

    def test_hit_rate_denominator_excludes_unreported(self):
        """没上报缓存字段的调用不进分母 —— 否则命中率被系统性算低。"""
        t = CostTracker()
        t.record("t_denom", 1000, 0, "agnes-3.0-flash", 400)   # 上报·命中
        t.record("t_denom", 1000, 0, "agnes-3.0-flash", 0)     # 上报·未命中
        t.record("t_denom", 8000, 0, "agnes-3.0-flash")        # 没上报（无挂账）
        rep = t.get_report("t_denom")
        assert rep.reported_calls == 2, "没上报的调用不该进样本量"
        assert rep.reported_input_tokens == 2000, "分母只含上报过的两次"
        assert rep.input_tokens == 10000, "但总输入要如实记账，不能为分母好看就藏起来"
        assert rep.cache_hit_rate == 0.2

    def test_hit_rate_none_when_nothing_reported(self):
        """一次都没上报 → None，与「查过、一次没命中」（0.0）分开"""
        t = CostTracker()
        t.record("t_none", 5000, 100, "agnes-3.0-flash")
        rep = t.get_report("t_none")
        assert rep.cache_hit_rate is None
        assert rep.reported_calls == 0

    def test_extract_usage_only_registers_reported_calls(self):
        """extract_usage 只在 usage 真带缓存字段时挂账。

        挂 0 会让随后到来的 record() 把这笔算进「上报过」，分母被污染。
        """
        from harness.observability.log_context import bind_trace_id
        t = CostTracker()
        bind_trace_id("tr_pending")
        try:
            t.extract_usage({"prompt_tokens": 10, "completion_tokens": 1},
                            "openai_compatible")
            assert "tr_pending" not in t._pending_cache, "没带缓存字段不该留痕"

            t.extract_usage({"prompt_tokens": 10, "completion_tokens": 1,
                             "prompt_tokens_details": {"cached_tokens": 4}},
                            "openai_compatible")
            assert t._pending_cache["tr_pending"] == 4
            t.record("tr_pending", 10, 1, "agnes-3.0-flash")
            rep = t.get_report("tr_pending")
            assert (rep.reported_calls, rep.cached_tokens) == (1, 4)
        finally:
            bind_trace_id(None)
            t.clear()

    def test_explicit_cached_none_still_counts_as_reported(self):
        """显式传 0 = 上报了没命中，必须进分母（与不传是两件事）"""
        t = CostTracker()
        t.record("t_zero", 1000, 0, "agnes-3.0-flash", 0)
        rep = t.get_report("t_zero")
        assert rep.reported_calls == 1
        assert rep.cache_hit_rate == 0.0

    # ---- ⑥ vendor 命名空间前缀 + 兜底标记 ----

    def test_vendor_namespace_matches_family_price(self):
        """"deepseek/deepseek-flash" 这类带厂商前缀的名字要走族价。

        直接按前缀匹配会全部落空 → 静默按 $1/$4 兜底价计。实测 80 次调用、
        143 万 input token 因此记了 $2.68，按 v3 刊价只值约 $0.73。
        """
        # 同族回退仍是估算，不能伪装成该具体模型的已登记价格。
        assert CostTracker.uses_fallback("deepseek/deepseek-flash") is True
        assert CostTracker.price_for("deepseek/deepseek-flash")["input"] == \
            CostTracker.PRICING["deepseek-v3"]["input"]
        assert CostTracker.uses_fallback("qwen/qwen-plus") is False

    def test_uses_fallback_flags_unregistered(self):
        """未登记模型要能被标记出来（兜底价是量级参考，不是账单）"""
        assert CostTracker.uses_fallback("gpt-5.6") is True
        assert CostTracker.uses_fallback("minimax-m3") is True
        assert CostTracker.uses_fallback("agnes-3.0-flash") is False
        assert CostTracker.uses_fallback("") is False
        assert CostTracker.uses_fallback(None) is False

    def test_record_llm_turn_marks_cost_fallback_in_meta(self):
        """兜底计价的事件必须带 cost_fallback，精确价的不带（meta 不变脏）"""
        from types import SimpleNamespace
        from harness.observability import trace_writer as tw

        class _W:
            def __init__(self):
                self.kw = None

            def llm_call(self, **kw):
                self.kw = kw
                return "ok"

        resp = SimpleNamespace(
            usage={"prompt_tokens": 100, "completion_tokens": 10},
            content="ok", is_error=False, error=None,
            finish_reason="stop", reasoning_content=None, tool_calls=None,
        )
        w_fallback = _W()
        tw.record_llm_turn(1, stage="coding", model="gpt-5.6",
                           system_prompt="s", prompt="p", response=resp,
                           writer=w_fallback)
        assert w_fallback.kw["meta"].get("cost_fallback") is True

        w_exact = _W()
        tw.record_llm_turn(1, stage="coding", model="agnes-3.0-flash",
                           system_prompt="s", prompt="p", response=resp,
                           writer=w_exact)
        assert "cost_fallback" not in w_exact.kw["meta"]
