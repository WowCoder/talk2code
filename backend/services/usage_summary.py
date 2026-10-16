# -*- coding: utf-8 -*-
"""Shared event accounting for a requirement, execution, or reporting window.

The SQL expressions also serve grouped admin queries, so list pages do not need
one extra accounting query per requirement. Only actual ``llm_turn`` events count.
"""
from sqlalchemy import Numeric, and_, case, cast, func, or_

from models.models import AgentEvent


def usage_scope_condition(scope="requirement"):
    """Unassociated legacy LLM calls are independent; other legacy history stays."""
    origin = AgentEvent.meta["origin"].as_string()
    legacy_independent = and_(origin.is_(None), AgentEvent.requirement_id == 0,
                              AgentEvent.kind == "llm_turn")
    if scope is None:
        return True
    if scope == "requirement":
        return and_(or_(origin.is_(None), origin == "requirement"), ~legacy_independent)
    if scope == "independent":
        return or_(origin == scope, legacy_independent)
    if scope == "evaluation":
        return origin == scope
    raise ValueError("Unknown usage scope")


def build_usage_aggregate_columns():
    """Return labelled aggregate expressions usable in existing GROUP BY queries."""
    ev = AgentEvent
    llm = ev.kind == "llm_turn"
    has_usage = ev.meta["has_usage"].as_boolean()
    # Old events lack has_usage. Their positive token counts or explicit cache
    # report are evidence of usage; an all-zero legacy row remains unknown.
    reported = and_(llm, case(
        (has_usage.isnot(None), has_usage),
        else_=or_(ev.tokens_in > 0, ev.tokens_out > 0,
                  ev.cached_tokens.isnot(None)),
    ))
    cache_reported = and_(reported, ev.cached_tokens.isnot(None))
    precise_cost = func.coalesce(
        cast(ev.meta["cost_decimal"].as_string(), Numeric(38, 18)), ev.cost, 0)

    def total(value, label, condition=llm):
        return func.coalesce(func.sum(case((condition, value), else_=0)), 0).label(label)

    return [
        total(1, "llm_calls"),
        total(ev.tokens_in, "tokens_in", reported),
        total(ev.tokens_out, "tokens_out", reported),
        total(ev.cached_tokens, "cached_tokens", cache_reported),
        total(ev.tokens_in, "cache_reported_tokens_in", cache_reported),
        total(1, "cache_reported_calls", cache_reported),
        total(1, "usage_reported_calls", reported),
        total(1, "cost_fallback_calls", and_(reported, ev.meta["cost_fallback"].as_boolean().is_(True))),
        total(precise_cost, "cost"),
        total(cast(ev.meta["cache_savings"].as_string(), Numeric(38, 18)), "cache_savings"),
        total(1, "cache_savings_reported_calls", and_(
            reported, ev.meta["cache_savings"].as_string().isnot(None))),
    ]


USAGE_AGGREGATE_KEYS = (
    "llm_calls", "tokens_in", "tokens_out", "cached_tokens",
    "cache_reported_tokens_in", "cache_reported_calls", "usage_reported_calls",
    "cost_fallback_calls", "cost", "cache_savings", "cache_savings_reported_calls",
)


def usage_summary_from_aggregate(row):
    """Serialize a labelled SQL row with consistent coverage and unknown states."""
    values = {key: getattr(row, key, 0) or 0 for key in USAGE_AGGREGATE_KEYS}
    result = {key: int(value) for key, value in values.items()
              if key not in ("cost", "cache_savings")}
    result.update(
        tokens=result["tokens_in"] + result["tokens_out"],
        cost=float(values["cost"]),
        cache_savings=(float(values["cache_savings"])
                       if values["cache_savings_reported_calls"] else None),
        cost_currency="USD",
        cost_is_estimate=True,
    )
    result["cache_hit_rate"] = (
        round(result["cached_tokens"] / result["cache_reported_tokens_in"], 4)
        if result["cache_reported_tokens_in"] else None)
    result["cache_coverage"] = (
        round(result["cache_reported_tokens_in"] / result["tokens_in"], 4)
        if result["tokens_in"] else None)
    result["usage_coverage"] = (
        round(result["usage_reported_calls"] / result["llm_calls"], 4)
        if result["llm_calls"] else None)
    return result


def summarize_usage(db, requirement_id=None, trace_id=None, since=None, *, scope="requirement"):
    """Sum known usage from every attempt in the requested intersection of scopes."""
    query = db.query(*build_usage_aggregate_columns()).filter(usage_scope_condition(scope))
    if requirement_id is not None:
        query = query.filter(AgentEvent.requirement_id == requirement_id)
    if trace_id is not None:
        query = query.filter(AgentEvent.trace_id == trace_id)
    if since is not None:
        query = query.filter(AgentEvent.ts >= since)
    return usage_summary_from_aggregate(query.one())
