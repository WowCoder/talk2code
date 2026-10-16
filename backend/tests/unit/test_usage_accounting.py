# -*- coding: utf-8 -*-
"""Protocol totals, cache prices, and per-attempt accounting contracts."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module, util

import pytest

from harness.observability import cost as cost_module
from harness.observability.cost import CostTracker


def normalize(usage, provider=""):
    assert util.find_spec("harness.observability.usage") is not None, "usage normalizer is missing"
    return import_module("harness.observability.usage").normalize_usage(usage, provider)


def quote(model, usage, provider=""):
    fn = getattr(cost_module, "quote_cost", None)
    assert callable(fn), "precise quote API is missing"
    return fn(model, usage, provider)


@pytest.mark.parametrize("usage, expected", [
    (None, (0, 0, None, False)),
    ({}, (0, 0, None, False)),
    ({"other": 100}, (0, 0, None, False)),
    ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0, None, True)),
    ({"prompt_tokens": 100, "completion_tokens": 7}, (100, 7, None, True)),
    ({"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 0}}, (100, 7, 0, True)),
    ({"prompt_tokens": 100, "completion_tokens": 7, "prompt_cache_hit_tokens": 40}, (100, 7, 40, True)),
    ({"input_tokens": 100, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 40}}, (100, 7, 40, True)),
])
def test_normalization_preserves_unknown_zero_and_inclusive_openai_input(usage, expected):
    result = normalize(usage, "openai_compatible")
    assert (result["input_tokens"], result["output_tokens"], result["cached_tokens"], result["has_usage"]) == expected


def test_anthropic_adds_cache_read_and_creation_once_with_ttl_breakdown():
    result = normalize({"input_tokens": 100, "output_tokens": 20,
                        "cache_read_input_tokens": 400,
                        "cache_creation_input_tokens": 300,
                        "cache_creation": {"ephemeral_5m_input_tokens": 200,
                                           "ephemeral_1h_input_tokens": 100}}, "anthropic_compatible")
    assert result == {"input_tokens": 800, "output_tokens": 20, "cached_tokens": 400,
                      "cache_creation_tokens": 300, "cache_creation_5m_tokens": 200,
                      "cache_creation_1h_tokens": 100, "has_usage": True}


def test_anthropic_cache_creation_breakdown_without_total_is_not_lost():
    result = normalize({"input_tokens": 10, "cache_creation": {"ephemeral_1h_input_tokens": 30}}, "anthropic")
    assert result["input_tokens"] == 40
    assert result["cache_creation_tokens"] == 30
    assert result["cached_tokens"] is None


def test_openai_cache_reads_are_capped_at_inclusive_input_total():
    result = normalize({"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 100}}, "openai_compatible")
    assert result["input_tokens"] == 10
    assert result["cached_tokens"] == 10


@pytest.mark.parametrize("input_key, details_key", [
    ("prompt_tokens", "prompt_tokens_details"),
    ("input_tokens", "input_tokens_details"),
])
def test_openai_cache_write_details_do_not_duplicate_inclusive_input(input_key, details_key):
    result = normalize({input_key: 100, details_key: {"cached_tokens": 20, "cache_write_tokens": 30}}, "openai_compatible")
    assert result["input_tokens"] == 100
    assert result["cached_tokens"] == 20
    assert result["cache_creation_tokens"] == 30


def test_explicit_attempt_does_not_consume_legacy_pending_cache():
    from harness.observability.log_context import bind_trace_id
    tracker = CostTracker()
    bind_trace_id("t_stale")
    try:
        tracker.extract_usage({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 40}}, "openai_compatible")
        tracker.record("t_stale", 100, 0, "gpt-4o", attempt_id="new")
        report = tracker.get_report("t_stale")
        assert report.cached_tokens == 0
        assert report.reported_calls == 0
    finally:
        bind_trace_id(None)


def test_namespace_exact_price_and_version_alias_use_mini_before_parent():
    exact = quote("openai/gpt-4o-mini", {"input_tokens": 1_000_000, "cached_tokens": 0})
    version = quote("openai/gpt-4o-mini-2024-07-18", {"input_tokens": 1_000_000, "cached_tokens": 0})
    assert exact["cost"] == 0.15
    assert exact["cost_source"] == "configured"
    assert version["cost"] == 0.15
    assert version["cost_source"] == "alias"
    assert quote("gpt-4o-mini-unknown", {"input_tokens": 1_000_000})["cost"] == 0.15


@pytest.mark.parametrize("model", ["deepseek/deepseek-flash", "claude-future", "unknown-model"])
def test_family_and_unknown_prices_remain_explicit_fallbacks(model):
    result = quote(model, {"input_tokens": 100, "cached_tokens": 40})
    assert result["cost_source"] == "fallback"
    assert result["cache_savings"] is None
    assert CostTracker.uses_fallback(model)


def test_gpt4o_cache_read_is_half_price_and_snapshot_uses_decimal_strings():
    result = quote("gpt-4o", {"input_tokens": 1_000_000, "output_tokens": 100_000, "cached_tokens": 800_000})
    assert result["cost"] == 2.5
    assert Decimal(result["cost_decimal"]) == Decimal("2.5")
    assert result["cache_savings"] == 1.0
    snapshot = result["price_snapshot"]
    assert snapshot["currency"] == "USD"
    assert snapshot["version"]
    assert snapshot["source"]
    assert Decimal(snapshot["rates"]["cached"]) == Decimal("1.25")
    assert all(isinstance(rate, str) for rate in snapshot["rates"].values())


def test_anthropic_read_and_both_write_ttls_have_separate_rates_and_net_savings():
    result = quote("claude-opus-4-5", {"input_tokens": 800, "output_tokens": 20, "cached_tokens": 400,
                                      "cache_creation_tokens": 300, "cache_creation_5m_tokens": 200,
                                      "cache_creation_1h_tokens": 100}, "anthropic_compatible")
    # 100*5 + 400*.5 + 200*6.25 + 100*10 + 20*25, per million.
    assert Decimal(result["cost_decimal"]) == Decimal("0.00345")
    assert result["cache_savings"] == pytest.approx(0.00105)


def test_default_anthropic_write_ttl_is_five_minutes_and_can_cost_more_than_no_cache():
    result = quote("claude-opus-4-5", {"input_tokens": 100, "cached_tokens": 0,
                                      "cache_creation_tokens": 100}, "anthropic")
    assert Decimal(result["cost_decimal"]) == Decimal("0.000625")
    assert result["cache_savings"] == pytest.approx(-0.000125)


def test_unconfirmed_cache_price_does_not_invent_ten_percent_discount():
    result = quote("qwen-plus", {"input_tokens": 1_000_000, "cached_tokens": 1_000_000})
    assert result["cost"] == 0.5
    assert result["cost_source"] == "fallback"
    assert result["cache_savings"] is None


def test_missing_cache_usage_does_not_claim_zero_savings():
    assert quote("gpt-4o", {"input_tokens": 10})["cache_savings"] is None


def test_cost_report_counts_unknown_attempts_but_only_known_usage_coverage():
    tracker = CostTracker()
    tracker.record("t", 0, 0, "gpt-4o", usage_reported=False, attempt_id="failed")
    tracker.record("t", 100, 20, "gpt-4o", 40, attempt_id="ok")
    tracker.record("t", 10, 0, "unknown-model", attempt_id="fallback")
    report = tracker.get_report("t")
    assert report.calls == 3
    assert report.usage_reported_calls == 2
    assert report.cost_fallback_calls == 1
    assert report.reported_calls == 1
    assert report.total_tokens == 130
    assert report.cache_savings == pytest.approx(0.00005)


def test_duplicate_attempts_are_atomic_and_clear_allows_reuse():
    tracker = CostTracker()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: tracker.record("t", 100, 20, "gpt-4o", 40, attempt_id="same"), range(100)))
    assert tracker.get_report("t").calls == 1
    assert tracker.get_report("t").total_tokens == 120
    tracker.clear("t")
    tracker.record("t", 100, 20, "gpt-4o", 40, attempt_id="same")
    assert tracker.get_report("t").calls == 1


def test_decimal_aggregation_does_not_accumulate_float_rounding():
    tracker = CostTracker()
    for _ in range(1000):
        tracker.record("t", 1, 0, "agnes-3.0-flash", 0)
    assert tracker.get_report("t").total_cost == 0.00005


def test_anthropic_record_preserves_inclusive_input_and_write_cost():
    tracker = CostTracker()
    tracker.record("t", 800, 20, "claude-opus-4-5", 400, provider="anthropic_compatible",
                   cache_creation_tokens=300, cache_creation_5m_tokens=200, cache_creation_1h_tokens=100)
    report = tracker.get_report("t")
    assert report.total_tokens == 820
    assert report.total_cost == 0.00345
    assert report.cache_savings == pytest.approx(0.00105)
