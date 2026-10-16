# -*- coding: utf-8 -*-
"""在实际请求边界记账；业务阶段埋点只负责补充语义。"""
from __future__ import annotations

import logging
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from harness.observability.log_context import (
    capture_accounting_context,
)

logger = logging.getLogger(__name__)
_pending_lock = threading.RLock()
_replay_stop = threading.Event()
_replay_thread = None


def _spool_directory():
    """Partition by destination without storing database credentials on disk."""
    from config import settings
    from harness.observability.trace_db import trace_url
    from sqlalchemy.engine import make_url
    url = trace_url() or settings.DATABASE_URL
    if url:
        # Password rotation changes credentials, not the accounting destination.
        destination = str(make_url(url)._replace(password=None))
    else:
        destination = f'main:{Path(settings.BACKEND_DIR) / settings.DATABASE_NAME}'
    partition = hashlib.sha256(destination.encode()).hexdigest()[:24]
    return Path(settings.BACKEND_DIR) / settings.LOG_DIR / 'llm-accounting-pending' / partition


def _pending_path(attempt_id):
    key = hashlib.sha256(str(attempt_id).encode()).hexdigest()
    return _spool_directory() / f'{key}.json'


def _write_pending(payload):
    path = _pending_path(payload['attempt_id'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _report_failure(message):
    try:
        from harness.observability.trace_writer import _count_failure
        _count_failure()
    except Exception:
        pass
    try:
        logger.warning(message, exc_info=True)
    except Exception:
        pass


def _persist_attempt(payload):
    from harness.observability.trace_db import trace_session
    from harness.observability.trace_writer import TraceWriter
    context, usage, quote = payload['context'], payload['usage'], payload['quote']
    with trace_session() as db:
        writer = TraceWriter(db, context.get('requirement_id') or 0,
                             trace_id=context.get('trace_id'),
                             turn_index=context.get('turn_index', 0))
        seq = writer.llm_call(
            call_id=payload['attempt_id'], attempt_id=payload['attempt_id'],
            request=payload['request'], response=payload['response'], model=payload['model'],
            duration_ms=payload['duration_ms'], tokens_in=usage['input_tokens'],
            tokens_out=usage['output_tokens'], cached_tokens=usage['cached_tokens'],
            cost=quote['cost'], status=payload['status'], meta=payload['meta'],
            stage=context.get('stage'), iteration=context.get('iteration'),
            label=payload['label'], ts=datetime.fromisoformat(payload['ts']),
        )
        if not seq:
            raise RuntimeError('LLM attempt event was not committed')
        return seq


def pending_attempt_count():
    """Pending files for the current database, including unreadable records."""
    return sum(1 for _ in _spool_directory().glob('*.json'))


def replay_pending_attempts(limit=100):
    """Replay frozen accounting only; never calls an LLM or recalculates prices."""
    restored = 0
    with _pending_lock:
        for path in sorted(_spool_directory().glob('*.json'))[:limit]:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                _report_failure('LLM 请求补记文件不可读，保留记录并检查下一条')
                continue
            try:
                _persist_attempt(payload)
            except Exception:
                _report_failure('LLM 请求补记失败，记录保留待重试')
                # One outage should not hold the writer lock for 100 DB timeouts.
                break
            try:
                path.unlink()
                restored += 1
            except Exception:
                _report_failure('LLM 请求补记成功但文件清理失败，后续幂等重试')
    return restored


def start_accounting_replay():
    """Application-owned recovery worker; pending files also survive a restart."""
    from config import settings
    global _replay_thread
    interval = settings.LLM_ACCOUNTING_REPLAY_INTERVAL_SECONDS
    if interval <= 0 or (_replay_thread is not None and _replay_thread.is_alive()):
        return
    _replay_stop.clear()
    def run():
        while not _replay_stop.is_set():
            try:
                replay_pending_attempts()
            except Exception:
                _report_failure('LLM 补记队列检查失败')
            _replay_stop.wait(interval)
    _replay_thread = threading.Thread(target=run, name='LLM-Accounting-Replay', daemon=True)
    _replay_thread.start()


def stop_accounting_replay():
    _replay_stop.set()
    if _replay_thread is not None:
        _replay_thread.join(timeout=1)


def _response_payload(body, error):
    body = body if isinstance(body, dict) else {}
    choices = body.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get('message')
    message = message if isinstance(message, dict) else {}
    blocks = body.get('content') or []
    content = message.get('content')
    if content is None and isinstance(blocks, list):
        content = ''.join(str(b.get('text') or '') for b in blocks if isinstance(b, dict))
    return {
        'content': content,
        'reasoning_content': message.get('reasoning_content'),
        'tool_calls': message.get('tool_calls'),
        'usage': body.get('usage'),
        'finish_reason': choice.get('finish_reason') or body.get('stop_reason'),
        'is_error': bool(error), 'error': error,
    }


def record_attempt(*, attempt_id, provider, model, request, response,
                   duration_ms, error=None, http_status=None, accounting_context=None):
    """每个 attempt 仅计一次；失败/未上报保持未知，不影响生成请求。"""
    from harness.observability.usage import normalize_usage
    from harness.observability.cost import quote_cost, shared_cost_tracker

    body = response if isinstance(response, dict) else {}
    usage = normalize_usage(body.get('usage'), provider)
    quote = quote_cost(model, usage, provider)
    context = accounting_context if accounting_context is not None else capture_accounting_context()
    trace_id = context.get('trace_id')
    if trace_id:
        shared_cost_tracker().record(
            trace_id, usage['input_tokens'], usage['output_tokens'], model,
            usage['cached_tokens'], provider=provider,
            cache_creation_tokens=usage['cache_creation_tokens'],
            cache_creation_5m_tokens=usage['cache_creation_5m_tokens'],
            cache_creation_1h_tokens=usage['cache_creation_1h_tokens'],
            usage_reported=usage['has_usage'], attempt_id=attempt_id,
        )
    requirement_id = context.get('requirement_id') or 0
    meta = {
        'accounting_source': 'client_attempt', 'provider': provider,
        'requested_model': request.get('model') if isinstance(request, dict) else None,
        'has_usage': usage['has_usage'], 'http_status': http_status,
        'cost_fallback': quote['cost_source'] == 'fallback',
        'cost_source': quote['cost_source'],
        'cost_decimal': quote['cost_decimal'],
        'price_snapshot': quote['price_snapshot'],
        'cache_savings': quote['cache_savings'],
        'cache_creation_tokens': usage['cache_creation_tokens'],
        'cache_creation_5m_tokens': usage['cache_creation_5m_tokens'],
        'cache_creation_1h_tokens': usage['cache_creation_1h_tokens'],
        'usage_unknown': not usage['has_usage'],
        'origin': context.get('origin') or ('requirement' if requirement_id else 'independent'),
        'purpose': context.get('purpose'),
    }
    if error:
        meta['error'] = str(error)[:500]
    payload = {
        'version': 1, 'attempt_id': attempt_id, 'context': dict(context),
        'request': request, 'response': _response_payload(body, error),
        'model': model, 'duration_ms': int(duration_ms), 'usage': usage,
        'quote': quote, 'meta': meta, 'status': 'error' if error else 'ok',
        'label': 'LLM 请求' if usage['has_usage'] else 'LLM 请求 · 用量未上报',
        'ts': datetime.utcnow().isoformat(),
    }
    # Durable before DB; a crash after commit leaves an idempotent replay record.
    with _pending_lock:
        pending = None
        try:
            path = _pending_path(attempt_id)
            if path.exists():
                payload = json.loads(path.read_text(encoding='utf-8'))
                pending = path
            else:
                pending = _write_pending(payload)
        except Exception:
            _report_failure('LLM 请求补记文件写入失败，继续尝试数据库落账')
        try:
            seq = _persist_attempt(payload)
        except Exception:
            _report_failure('LLM 请求落账失败，等待持久队列补记')
            return 0
        if pending is not None:
            try:
                pending.unlink()
            except OSError:
                _report_failure('LLM 已落账但补记文件清理失败，后续按请求 ID 去重')
        return seq


def enrich_attempts(requirement_id, attempt_ids, *, stage=None, iteration=None,
                    turn_index=0, trace_id=None, label=None):
    """给已记账尝试补阶段，绝不再生成一笔费用。"""
    if not requirement_id or not attempt_ids:
        return
    from harness.observability.trace_db import trace_session
    from models.models import AgentEvent
    # Update pending records before touching DB, so a later replay retains phase
    # and iteration even when this semantic callback also encounters an outage.
    with _pending_lock:
        for attempt_id in attempt_ids:
            path = _pending_path(attempt_id)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if payload['context'].get('requirement_id') != requirement_id:
                    continue
                context = payload['context']
                context.update(stage=context.get('stage') or stage, iteration=iteration,
                               turn_index=turn_index, trace_id=context.get('trace_id') or trace_id)
                if label:
                    payload['label'] = label
                _write_pending(payload)
            except Exception:
                _report_failure('LLM 待补记记录语义更新失败，继续更新已落账事件')
    with trace_session() as db:
        rows = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.attempt_id.in_(attempt_ids),
        ).all()
        for row in rows:
            row.stage = row.stage or stage
            row.iteration = iteration
            row.turn_index = turn_index
            row.trace_id = row.trace_id or trace_id
            if label:
                row.label = label
        db.commit()
