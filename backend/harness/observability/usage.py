# -*- coding: utf-8 -*-
"""Normalize provider usage without turning missing telemetry into reported zero."""

from collections.abc import Mapping


def _tokens(value) -> int:
    try:
        return max(0, int(value)) if not isinstance(value, bool) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def normalize_usage(usage, provider: str = "") -> dict:
    """Return inclusive input totals and independent cache read/write categories.

    Anthropic's input_tokens excludes reads and writes; OpenAI's prompt/input
    total already includes cache reads. Creation total includes its TTL splits.
    """
    usage = usage if isinstance(usage, Mapping) else {}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    details = details if isinstance(details, Mapping) else {}
    creation = usage.get("cache_creation") or {}
    creation = creation if isinstance(creation, Mapping) else {}
    cached = next((value for value in (
        details.get("cached_tokens"), usage.get("prompt_cache_hit_tokens"),
        usage.get("cache_read_input_tokens"),
    ) if value is not None), None)
    write_5m = _tokens(creation.get("ephemeral_5m_input_tokens"))
    write_1h = _tokens(creation.get("ephemeral_1h_input_tokens"))
    writes = max(_tokens(usage.get("cache_creation_input_tokens")),
                 _tokens(details.get("cache_write_tokens")), write_5m + write_1h)
    tokens_in = _tokens(usage.get("prompt_tokens", usage.get("input_tokens")))
    tokens_out = _tokens(usage.get("completion_tokens", usage.get("output_tokens")))
    anthropic = (provider or "").startswith("anthropic") or (
        "prompt_tokens" not in usage and "input_tokens" in usage
        and any(key in usage for key in ("cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation"))
    )
    if anthropic:
        tokens_in += _tokens(cached) + writes
    else:
        # OpenAI's total already includes every input category. Normalize the
        # same upper bounds here and in pricing so event totals cannot exceed it.
        if cached is not None:
            cached = min(_tokens(cached), tokens_in)
        writes = min(writes, tokens_in - _tokens(cached))
        write_1h = min(write_1h, writes)
        write_5m = min(write_5m, writes - write_1h)
    reported_values = [usage.get(key) for key in (
        "prompt_tokens", "completion_tokens", "input_tokens", "output_tokens",
        "prompt_cache_hit_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    )] + [details.get("cached_tokens"), details.get("cache_write_tokens"),
         creation.get("ephemeral_5m_input_tokens"),
         creation.get("ephemeral_1h_input_tokens")]
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cached_tokens": None if cached is None else _tokens(cached),
        "cache_creation_tokens": writes,
        "cache_creation_5m_tokens": write_5m,
        "cache_creation_1h_tokens": write_1h,
        "has_usage": any(value is not None for value in reported_values),
    }
