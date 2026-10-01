# -*- coding: utf-8 -*-
"""
把历史 LLM 事件的缓存命中量补进 `agent_events.cached_tokens`。

背景
----
供应商一直在返回缓存命中数（OpenAI 兼容 `prompt_tokens_details.cached_tokens`，
DeepSeek `prompt_cache_hit_tokens`，Anthropic `cache_read_input_tokens`），
但补列之前，埋点只落 `tokens_in` / `tokens_out`，这个字段被丢掉了。

好消息是**不用重跑需求**：`agent_payloads.response.usage` 里存着当时的完整
usage（写 payload 走的是原样序列化，没有经过字段白名单），因此历史事件
可以直接算出来。

⚠️ 三态语义（与运行时一致）
--------------------------
    NULL = 这次调用**没上报**缓存信息（当时不知道，不是「没命中」）
    0    = 上报了，确实一次没命中
    >0   = 命中

本脚本只写「payload 里确实带了缓存字段」的事件，其余**保持 NULL**。
不要拿 0 去填充未知 —— 那会把「当时没记录」伪装成「当时没命中」，
之后再也分不出「供应商停报」和「缓存失效」。

幂等：每次按 payload 重算后覆盖写，重复执行结果一致。

用法::

    PYTHONPATH=. python scripts/backfill_cached_tokens.py --dry-run
    PYTHONPATH=. python scripts/backfill_cached_tokens.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal
from models.models import AgentEvent, AgentPayload
# 复用运行时的提取函数：回填与实时写入必须是同一套解析规则，
# 否则同一份数据在历史事件与新事件里会算出两个不同的值。
from harness.observability.trace_writer import _cached_from_usage


def _as_dict(resp):
    """payload.response 正常是 dict；早期/异常写入可能是 JSON 字符串。"""
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            v = json.loads(resp)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def main(dry_run: bool = False) -> int:
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentEvent, AgentPayload.response)
            .join(AgentPayload, AgentPayload.id == AgentEvent.payload_id)
            .filter(AgentEvent.kind == 'llm_turn')
            .all()
        )

        scanned = reported = changed = keep_null = 0
        for ev, resp in rows:
            scanned += 1
            usage = _as_dict(resp).get('usage')
            val = _cached_from_usage(usage)
            if val is None:
                keep_null += 1
                continue
            reported += 1
            if ev.cached_tokens != val:
                changed += 1
                if not dry_run:
                    ev.cached_tokens = val

        if not dry_run:
            db.commit()

        print(f"扫描 llm_turn 事件 : {scanned}")
        print(f"payload 里上报了缓存: {reported}")
        print(f"本次{'将' if dry_run else '已'}更新       : {changed}")
        print(f"保持 NULL（未上报） : {keep_null}")
        if dry_run:
            print("\n（--dry-run：未写入任何行）")
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main(dry_run='--dry-run' in sys.argv))
