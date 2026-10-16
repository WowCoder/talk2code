# -*- coding: utf-8 -*-
"""Decimal USD estimates and thread-safe per-attempt usage accounting."""

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Optional

from harness.observability.log_context import current_trace_id
from harness.observability.usage import normalize_usage, _tokens

logger = logging.getLogger(__name__)
_WARNED_MODELS: set = set()
_PRICE_LOCK = RLock()
_MILLION = Decimal("1000000")


@dataclass
class CostReport:
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reported_input_tokens: int = 0
    reported_calls: int = 0
    total_cost: float = 0.0
    by_model: dict = field(default_factory=dict)
    calls: int = 0
    usage_reported_calls: int = 0
    cache_savings: Optional[float] = None
    cost_fallback_calls: int = 0
    _cost_decimal: Decimal = field(default=Decimal("0"), repr=False)
    _savings_decimal: Decimal = field(default=Decimal("0"), repr=False)
    _model_costs: dict = field(default_factory=dict, repr=False)

    @property
    def cache_hit_rate(self):
        if not self.reported_input_tokens:
            return None
        return round(self.cached_tokens / self.reported_input_tokens, 4)

    @property
    def cache_coverage(self):
        return round(self.reported_input_tokens / self.input_tokens, 4) if self.input_tokens else None

    @property
    def usage_coverage(self):
        return round(self.usage_reported_calls / self.calls, 4) if self.calls else None


class CostTracker:
    """Inclusive input totals and explicit model/cache rates; all costs are estimates."""

    # Legacy public constant, retained for clients using Agnes' configured ratio.
    # Prices below never apply this ratio to another provider automatically.
    CACHED_INPUT_RATIO = 0.1
    PRICE_VERSION = "2026-10-16"
    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00, "cached": 1.25},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached": 0.075},
        "qwen-plus": {"input": 0.50, "output": 2.00},
        "qwen-max": {"input": 2.00, "output": 8.00},
        "claude-opus-4-5": {"input": 5.00, "output": 25.00, "cached": 0.50,
                             "cache_creation": 6.25, "cache_creation_5m": 6.25,
                             "cache_creation_1h": 10.00},
        "deepseek-v3": {"input": 0.27, "output": 1.10},
        "deepseek-r1": {"input": 0.55, "output": 2.19},
        "agnes-3.0-flash": {"input": 0.05, "output": 0.15, "cached": 0.005},
        "agnes-3.0-pro": {"input": 0.30, "output": 0.90, "cached": 0.03},
    }
    FALLBACK_PRICING = {"input": 1.0, "output": 4.0}
    _ALIASES = {
        "gpt-4o-2024-08-06": "gpt-4o",
        "gpt-4o-2024-11-20": "gpt-4o",
        "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
        "claude-opus-4-5-20251101": "claude-opus-4-5",
    }
    _PREFIX_FALLBACKS = (
        ("deepseek-", "deepseek-v3"), ("qwen-", "qwen-plus"),
        ("gpt-4o-", "gpt-4o"), ("agnes-", "agnes-3.0-flash"),
        ("claude-", "claude-opus-4-5"),
    )
    _RATE_SOURCES = {
        "gpt-4o": "https://openai.com/index/api-prompt-caching/",
        "gpt-4o-mini": "https://openai.com/index/api-prompt-caching/",
        "claude-opus-4-5": "https://platform.claude.com/docs/en/build-with-claude/prompt-caching",
    }

    def __init__(self):
        self._usage: dict[str, CostReport] = {}
        self._pending_cache: dict[str, int] = {}
        self._seen_attempts: dict[str, set[str]] = {}
        self._lock = RLock()

    @classmethod
    def _bare_model(cls, model: str) -> str:
        return (model or "").rsplit("/", 1)[-1]

    @classmethod
    def _resolve_price(cls, model: str):
        bare = cls._bare_model(model)
        if bare in cls.PRICING:
            return cls.PRICING[bare], "configured", bare
        if bare in cls._ALIASES:
            key = cls._ALIASES[bare]
            return cls.PRICING[key], "alias", key
        # Select the most specific registered model before a broad family.
        candidates = [(key + "-", key) for key in cls.PRICING] + list(cls._PREFIX_FALLBACKS)
        for prefix, key in sorted(candidates, key=lambda pair: len(pair[0]), reverse=True):
            if bare.startswith(prefix):
                return cls.PRICING[key], "fallback", key
        with _PRICE_LOCK:
            if model and model not in _WARNED_MODELS:
                _WARNED_MODELS.add(model)
                logger.warning("[CostTracker] 未登记模型 %r，按兜底价 %s 计；成本仅供估算", model, cls.FALLBACK_PRICING)
        return cls.FALLBACK_PRICING, "fallback", ""

    @classmethod
    def uses_fallback(cls, model: str) -> bool:
        return bool(model) and cls._resolve_price(model)[1] == "fallback"

    @classmethod
    def price_for(cls, model: str) -> dict:
        """Return legacy numeric rates; unconfirmed cache reads use full input price."""
        pricing = dict(cls._resolve_price(model)[0])
        pricing.setdefault("cached", pricing["input"])
        return pricing

    @staticmethod
    def _cost_of(pricing: dict, input_tokens: int, output_tokens: int,
                 cached_tokens: int = 0) -> float:
        total = _tokens(input_tokens)
        cached = min(_tokens(cached_tokens), total)
        fresh = total - cached
        cost = (Decimal(fresh) * Decimal(str(pricing["input"]))
                + Decimal(cached) * Decimal(str(pricing.get("cached", pricing["input"])))
                + Decimal(_tokens(output_tokens)) * Decimal(str(pricing["output"]))) / _MILLION
        return float(cost)

    def record(self, trace_id: str, input_tokens: int, output_tokens: int,
               model: str = "", cached_tokens: Optional[int] = None, *,
               provider: str = "", cache_creation_tokens: int = 0,
               cache_creation_5m_tokens: int = 0, cache_creation_1h_tokens: int = 0,
               usage_reported: bool = True, attempt_id: Optional[str] = None):
        with self._lock:
            if attempt_id is not None and attempt_id in self._seen_attempts.get(trace_id, set()):
                return
            pending = self._pending_cache.pop(trace_id, None)
            # HTTP attempts carry their own telemetry; only legacy record calls
            # consume the extract_usage bridge, avoiding stale runtime telemetry.
            if cached_tokens is None and attempt_id is None:
                cached_tokens = pending
            tin, tout = _tokens(input_tokens), _tokens(output_tokens)
            if not usage_reported:
                tin = tout = 0
                cached_tokens = None
                cache_creation_tokens = cache_creation_5m_tokens = cache_creation_1h_tokens = 0
            cached = min(_tokens(cached_tokens), tin)
            normalized = {"input_tokens": tin, "output_tokens": tout,
                          "cached_tokens": None if cached_tokens is None else cached,
                          "cache_creation_tokens": cache_creation_tokens,
                          "cache_creation_5m_tokens": cache_creation_5m_tokens,
                          "cache_creation_1h_tokens": cache_creation_1h_tokens}
            quote = quote_cost(model, normalized, provider)
            report = self._usage.setdefault(trace_id, CostReport())
            report.calls += 1
            report.usage_reported_calls += int(usage_reported)
            report.total_tokens += tin + tout
            report.input_tokens += tin
            report.output_tokens += tout
            report.cached_tokens += cached
            report._cost_decimal += Decimal(quote["cost_decimal"])
            report.total_cost = float(report._cost_decimal)
            if cached_tokens is not None:
                report.reported_input_tokens += tin
                report.reported_calls += 1
            if usage_reported and quote["cost_source"] == "fallback":
                report.cost_fallback_calls += 1
            if quote["cache_savings"] is not None:
                report._savings_decimal += Decimal(quote["cache_savings_decimal"])
                report.cache_savings = float(report._savings_decimal)
            if model:
                entry = report.by_model.setdefault(model, {"tokens": 0, "cost": 0.0, "cached": 0})
                entry["tokens"] += tin + tout
                entry["cached"] += cached
                report._model_costs[model] = report._model_costs.get(model, Decimal("0")) + Decimal(quote["cost_decimal"])
                entry["cost"] = float(report._model_costs[model])
            if attempt_id is not None:
                self._seen_attempts.setdefault(trace_id, set()).add(attempt_id)

    def get_report(self, trace_id: str) -> CostReport:
        with self._lock:
            return deepcopy(self._usage.get(trace_id, CostReport()))

    def clear(self, trace_id: str = None):
        with self._lock:
            for mapping in (self._usage, self._pending_cache, self._seen_attempts):
                if trace_id is None:
                    mapping.clear()
                else:
                    mapping.pop(trace_id, None)

    def extract_usage(self, response_usage: dict, provider: str) -> tuple:
        """Legacy tuple API and cache bridge; new attempts pass telemetry directly."""
        usage = normalize_usage(response_usage, provider)
        trace_id = current_trace_id()
        if trace_id != "-":
            with self._lock:
                if usage["cached_tokens"] is None:
                    self._pending_cache.pop(trace_id, None)
                else:
                    self._pending_cache[trace_id] = usage["cached_tokens"]
        return usage["input_tokens"], usage["output_tokens"]

    @staticmethod
    def cache_hit_or_none(response_usage: dict):
        return normalize_usage(response_usage)["cached_tokens"]

    @staticmethod
    def extract_cache_hit(response_usage: dict) -> int:
        return int(CostTracker.cache_hit_or_none(response_usage) or 0)


def quote_cost(model: str, normalized_usage: dict, provider: str = "") -> dict:
    """Price inclusive usage in Decimal; snapshots describe estimates, not bills."""
    pricing, source, price_model = CostTracker._resolve_price(model)
    tin = _tokens(normalized_usage.get("input_tokens"))
    tout = _tokens(normalized_usage.get("output_tokens"))
    cached_raw = normalized_usage.get("cached_tokens")
    cached = min(_tokens(cached_raw), tin)
    creation_5m = _tokens(normalized_usage.get("cache_creation_5m_tokens"))
    creation_1h = _tokens(normalized_usage.get("cache_creation_1h_tokens"))
    creation = min(max(_tokens(normalized_usage.get("cache_creation_tokens")), creation_5m + creation_1h), tin - cached)
    creation_1h = min(creation_1h, creation)
    # No TTL breakdown means Anthropic's default 5m cache duration.
    creation_5m = creation - creation_1h
    numeric_rates = {"input": pricing["input"], "output": pricing["output"],
                     "cached": pricing.get("cached", pricing["input"]),
                     "cache_creation": pricing.get("cache_creation", pricing["input"]),
                     "cache_creation_5m": pricing.get("cache_creation_5m", pricing.get("cache_creation", pricing["input"])),
                     "cache_creation_1h": pricing.get("cache_creation_1h", pricing["input"])}
    rates = {key: Decimal(str(value)) for key, value in numeric_rates.items()}
    if ((cached and "cached" not in pricing)
            or (creation_5m and "cache_creation_5m" not in pricing)
            or (creation_1h and "cache_creation_1h" not in pricing)):
        source = "fallback"
    cost = (Decimal(tin - cached - creation) * rates["input"]
            + Decimal(cached) * rates["cached"]
            + Decimal(creation_5m) * rates["cache_creation_5m"]
            + Decimal(creation_1h) * rates["cache_creation_1h"]
            + Decimal(tout) * rates["output"]) / _MILLION
    savings = None
    if source != "fallback" and cached_raw is not None and "cached" in pricing:
        savings = (Decimal(tin) * rates["input"] + Decimal(tout) * rates["output"]) / _MILLION - cost
    return {
        "cost": float(cost), "cost_decimal": format(cost, "f"),
        "cache_savings": None if savings is None else float(savings),
        "cache_savings_decimal": None if savings is None else format(savings, "f"),
        "cost_source": source,
        "price_snapshot": {"currency": "USD", "version": CostTracker.PRICE_VERSION,
                           "source": source, "model": price_model, "provider": provider,
                           "is_estimate": True,
                           "rate_source": CostTracker._RATE_SOURCES.get(price_model, "existing_project_configuration"),
                           "rates": {key: format(value, "f") for key, value in rates.items()}},
    }


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int,
                      cached_tokens: int = 0) -> float:
    """Legacy numeric estimate API, sharing the Decimal quote calculation."""
    if not model or (not input_tokens and not output_tokens):
        return 0.0
    return quote_cost(model, {"input_tokens": input_tokens, "output_tokens": output_tokens,
                             "cached_tokens": cached_tokens})["cost"]


_SHARED: Optional[CostTracker] = None
_SHARED_LOCK = RLock()


def shared_cost_tracker() -> CostTracker:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = CostTracker()
        return _SHARED
