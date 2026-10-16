# -*- coding: utf-8 -*-
"""历史用量修复。默认只预览，--apply 才写入；不会恢复未记录的请求。

在应用完成新增列迁移后执行：
    PYTHONPATH=. python scripts/rebuild_usage.py --requirement-id 123
    PYTHONPATH=. python scripts/rebuild_usage.py --requirement-id 123 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _saved_quote(snapshot, usage, original):
    """重跑时沿用首次修复保存的价格，不跟着当前配置漂移。"""
    rates = {k: Decimal(v) for k, v in snapshot['rates'].items()}
    cached = min(usage['cached_tokens'] or 0, usage['input_tokens'])
    creation = min(usage['cache_creation_tokens'], usage['input_tokens'] - cached)
    one_hour = min(creation, usage['cache_creation_1h_tokens'])
    cost = (Decimal(usage['input_tokens'] - cached - creation) * rates['input']
            + Decimal(cached) * rates['cached']
            + Decimal(creation - one_hour) * rates['cache_creation_5m']
            + Decimal(one_hour) * rates['cache_creation_1h']
            + Decimal(usage['output_tokens']) * rates['output']) / Decimal(1000000)
    return {'cost': float(cost), 'cost_decimal': format(cost, 'f'),
            'cost_source': original.get('cost_source', 'fallback'),
            'cache_savings': original.get('cache_savings'),
            'price_snapshot': snapshot}


def rebuild_usage(db, *, apply=False, requirement_id=None):
    """预览/幂等重建有原始 usage 的历史事件及其 trace 汇总。"""
    from harness.observability.cost import quote_cost
    from harness.observability.usage import normalize_usage
    from models.models import AgentEvent, AgentPayload, AgentTrace
    from services.usage_summary import summarize_usage

    query = db.query(AgentEvent, AgentPayload.response).outerjoin(
        AgentPayload, AgentPayload.id == AgentEvent.payload_id).filter(
            AgentEvent.kind == 'llm_turn')
    if requirement_id is not None:
        query = query.filter(AgentEvent.requirement_id == requirement_id)
    report = {'mode': 'apply' if apply else 'preview', 'scanned_events': 0,
              'changed_events': 0, 'unknown_events': 0, 'current_events': 0,
              'updated_traces': 0, 'samples': [],
              'pricing_note': '历史费用按首次修复时的配置价估算，原始缺失请求不可恢复'}
    touched_traces = set()
    for event, response in query.yield_per(200):
        report['scanned_events'] += 1
        meta = dict(event.meta or {})
        if meta.get('accounting_source') == 'client_attempt':
            report['current_events'] += 1
            continue
        response = _dict(response)
        provider = meta.get('provider', '')
        usage = normalize_usage(response.get('usage'), provider)
        if not usage['has_usage']:
            report['unknown_events'] += 1
            continue
        snapshot = meta.get('price_snapshot')
        if meta.get('historical_estimate') and isinstance(snapshot, dict) and snapshot.get('rates'):
            quote = _saved_quote(snapshot, usage, meta)
        else:
            quote = quote_cost(event.model or '', usage, provider)
        desired_meta = {**meta, 'has_usage': True, 'historical_estimate': True,
                        'cost_source': quote['cost_source'],
                        'cost_fallback': quote['cost_source'] == 'fallback',
                        'cost_decimal': quote['cost_decimal'],
                        'price_snapshot': quote['price_snapshot'],
                        'cache_savings': quote['cache_savings'],
                        'cache_creation_tokens': usage['cache_creation_tokens']}
        changed = (event.tokens_in != usage['input_tokens']
                   or event.tokens_out != usage['output_tokens']
                   or event.cached_tokens != usage['cached_tokens']
                   or event.cost != quote['cost'] or meta != desired_meta)
        if not changed:
            continue
        report['changed_events'] += 1
        if len(report['samples']) < 20:
            report['samples'].append({
                'event_id': event.id, 'requirement_id': event.requirement_id,
                'model': event.model,
                'before': {'tokens_in': event.tokens_in, 'tokens_out': event.tokens_out,
                           'cached_tokens': event.cached_tokens, 'cost': event.cost},
                'after': {'tokens_in': usage['input_tokens'], 'tokens_out': usage['output_tokens'],
                          'cached_tokens': usage['cached_tokens'], 'cost': quote['cost'],
                          'cost_source': quote['cost_source']},
            })
        if apply:
            event.tokens_in = usage['input_tokens']
            event.tokens_out = usage['output_tokens']
            event.cached_tokens = usage['cached_tokens']
            event.cost = quote['cost']
            event.meta = desired_meta
            if event.trace_id:
                touched_traces.add(event.trace_id)
    if apply:
        db.flush()
        for trace in db.query(AgentTrace).filter(AgentTrace.trace_id.in_(touched_traces)).all():
            summary = summarize_usage(db, trace_id=trace.trace_id, scope=None)
            data = dict(trace.data or {})
            data.update(summary)
            data.update(total_tokens=summary['tokens'], total_cost=summary['cost'],
                        input_tokens=summary['tokens_in'], output_tokens=summary['tokens_out'],
                        calls=summary['llm_calls'])
            trace.data = data
            trace.total_tokens = summary['tokens']
            trace.total_cost = summary['cost']
            report['updated_traces'] += 1
        db.commit()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='显式写入，默认只读预览')
    mode.add_argument('--dry-run', action='store_true', help='只读预览（默认）')
    parser.add_argument('--requirement-id', type=int)
    args = parser.parse_args()
    from utils.db import get_db
    with get_db() as db:
        print(json.dumps(rebuild_usage(db, apply=args.apply,
                                      requirement_id=args.requirement_id), ensure_ascii=False))


if __name__ == '__main__':
    main()
