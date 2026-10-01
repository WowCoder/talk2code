# -*- coding: utf-8 -*-
"""
把历史执行日志回填进 agent_events / agent_payloads / trace_message_blobs。

用途：可观测性后台上线前，让存量需求也能被检索 —— 否则新表是空的，
      前端与接口都无从验证。

数据源：
  1. logs/agent_exec/<req_id>.jsonl  —— llm_turn / tool_call（ToolCallLoop 明细）
  2. logs/agent.log                  —— 长期记忆注入 marker（jsonl 里没有）

幂等：同一需求重复执行会先清空旧记录再写入。

用法::

    PYTHONPATH=. python scripts/backfill_traces.py              # 全部
    PYTHONPATH=. python scripts/backfill_traces.py 157 183      # 指定需求
    PYTHONPATH=. python scripts/backfill_traces.py --dry-run    # 只统计不写入
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.observability.cost import estimate_cost_usd
# 缓存命中的解析必须与运行时同源：各写一份的话，重跑回填（或换台机器重放）
# 算出来的命中量会和实时写入的对不上。
from harness.observability.trace_writer import _cached_from_usage
from harness.observability.event_contract import (
    STAGE_CODING, STAGE_PLANNING, infer_stage_from_legacy,
)

# 大体积工具参数（write_file 的整个文件内容）由 TraceWriter 走 content 寻址归档，
# 这里**不再自行截断** —— 运行时与回填必须是同一套规则，否则同一份数据在
# 新旧需求里是两种形态，按参数检索历史数据会对不上。
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_MEM_RE = re.compile(
    r"req=(\d+).*?\[MemoryManager\]\s*注入\s*(\d+)\s*条记忆[^(]*\(用户\s*\d+\s*候选\s*(\d+)/(\d+)\s*条\)"
)


def _parse_ts(value):
    if not value:
        return datetime.utcnow()
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.utcnow()


def _clear_requirement(db, rid: int):
    """清空该需求的既有记录，支持重复执行。"""
    from models.models import AgentEvent, AgentPayload, TraceMessageBlob
    for model in (AgentEvent, AgentPayload, TraceMessageBlob):
        db.query(model).filter(model.requirement_id == rid).delete(
            synchronize_session=False)


def _collect_memory_events(log_dir: str) -> dict:
    """从 agent.log 抽取「长期记忆注入」事件 —— jsonl 里完全没有这部分。"""
    events = defaultdict(list)
    pattern = os.path.join(log_dir, "agent.log*")
    for path in glob.glob(pattern):
        try:
            fh = open(path, errors="ignore")
        except OSError:
            continue
        for line in fh:
            if "MemoryManager" not in line or "注入" not in line:
                continue
            m = _MEM_RE.search(line)
            if not m:
                continue
            rid = int(m.group(1))
            ts = _TS_RE.search(line)
            events[rid].append({
                "ts": _parse_ts(ts.group(1) if ts else None),
                "injected": int(m.group(2)),
                "candidates": int(m.group(4)),
            })
        fh.close()
    return events


def backfill_requirement(db, rid: int, jsonl_path: str,
                         memory_events: list, dry_run: bool = False) -> dict:
    """回填单个需求，返回统计。"""
    from harness.observability.trace_writer import KIND_MEMORY, TraceWriter

    records = []
    with open(jsonl_path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("kind") in ("llm_turn", "tool_call"):
                records.append(d)

    stats = {"requirement_id": rid, "llm_turn": 0, "tool_call": 0,
             "memory": len(memory_events), "total": 0}

    if dry_run:
        for r in records:
            stats[r["kind"]] = stats.get(r["kind"], 0) + 1
        stats["total"] = len(records) + len(memory_events)
        return stats

    # 清空与写入放在同一个事务里 —— 不能中途 commit：一旦 commit 后写失败，
    # 旧记录已经删掉、新的又没写全，这个需求的轨迹就断了。
    _clear_requirement(db, rid)

    writer = TraceWriter(db, requirement_id=rid, autocommit=False)

    # 长期记忆注入发生在 Coder 之前，先按时间混入
    # 标签与运行时保持一致（都是「长期记忆注入」），否则同一种事件在
    # 新旧数据里是两种文案，按 label 检索会对不上。
    for me in sorted(memory_events, key=lambda x: x["ts"]):
        writer.event(
            KIND_MEMORY, f"长期记忆注入 · {me['injected']} 条",
            stage=STAGE_PLANNING, ts=me["ts"],
            meta={"injected": me["injected"], "candidates": me["candidates"],
                  "source": "backfill"},
        )

    # jsonl 本身按写入顺序即时间顺序，ts 存在
    for i, rec in enumerate(records):
        ts = _parse_ts(rec.get("ts"))
        if rec.get("kind") == "llm_turn":
            # token 用量只有部分调用带 usage 字段（实测 15%），有则取，没有留 0。
            # 不能反过来把缺失当成 0 参与统计——那是"没记录"而不是"没消耗"。
            response = rec.get("response")
            usage = response.get("usage") if isinstance(response, dict) else None
            # 阶段：新日志自带 stage；旧日志没这个字段，交给契约里的推断函数
            # 按 iteration 反推（判据见 infer_stage_from_legacy 的注释）。
            # 此前一律标成 coding，运营后台的时间线上看不到验收与修复这两段。
            stage = rec.get("stage") or infer_stage_from_legacy(rec.get("iteration"))
            tin = int((usage or {}).get("prompt_tokens") or 0)
            tout = int((usage or {}).get("completion_tokens") or 0)
            # 缓存命中：取不到就写 NULL（= 当时没上报），**不要写 0** ——
            # 0 的语义是「上报了、确实没命中」，与「没记录」是两件事，
            # 混成一个值之后再也分不出「供应商停报」和「缓存失效」。
            cached = _cached_from_usage(usage)
            writer.llm_call(
                iteration=rec.get("iteration"),
                request=rec.get("request"),
                response=response,
                model=rec.get("model"),
                duration_ms=int(rec.get("latency_ms") or 0) or None,
                tokens_in=tin,
                tokens_out=tout,
                cached_tokens=cached,
                # jsonl 里只有部分调用带 usage（实测 15%），没有的那些就是 0 ——
                # 那是"没记录"而不是"没花钱"，meta.has_usage 会如实标出来。
                cost=estimate_cost_usd(rec.get("model"), tin, tout),
                stage=stage,
                ts=ts,
                meta={"thinking": bool(rec.get("thinking")),
                      "has_usage": bool(usage)},
            )
            stats["llm_turn"] += 1
        else:
            writer.tool_call(
                name=rec.get("tool", "?"),
                arguments=rec.get("arguments"),
                content=rec.get("content"),
                iteration=rec.get("iteration"),
                stage=STAGE_CODING,
                status="ok" if rec.get("success") else "error",
                ts=ts,
                meta={"blocked": bool(rec.get("blocked"))},
            )
            stats["tool_call"] += 1

        if (i + 1) % 50 == 0:
            writer.flush()

    writer.flush()
    stats["total"] = stats["llm_turn"] + stats["tool_call"] + stats["memory"]
    return stats


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(here, "logs")
    exec_dir = os.path.join(log_dir, "agent_exec")

    if args:
        targets = [(int(a), os.path.join(exec_dir, f"{a}.jsonl")) for a in args]
    else:
        targets = []
        for path in sorted(glob.glob(os.path.join(exec_dir, "*.jsonl"))):
            rid = os.path.basename(path).replace(".jsonl", "")
            if rid.isdigit():
                targets.append((int(rid), path))

    if not targets:
        print("没有找到可回填的日志文件")
        return

    print(f"待处理 {len(targets)} 个需求{('（dry-run）' if dry_run else '')}")

    memory_events = _collect_memory_events(log_dir)
    print(f"从 agent.log 提取到记忆事件的需求数：{len(memory_events)}")

    from models.models import SessionLocal
    db = SessionLocal()

    total = 0
    failed = []
    for i, (rid, path) in enumerate(targets, 1):
        if not os.path.exists(path):
            continue
        try:
            st = backfill_requirement(db, rid, path,
                                      memory_events.get(rid, []), dry_run)
            total += st["total"]
            if i % 10 == 0 or i == len(targets):
                print(f"  [{i}/{len(targets)}] req={rid}  "
                      f"llm={st['llm_turn']} tool={st['tool_call']} "
                      f"memory={st['memory']}")
        except Exception as e:
            failed.append((rid, str(e)[:120]))
            print(f"  [FAIL] req={rid}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
    db.close()

    print(f"\n{'预计' if dry_run else '实际'}写入 {total} 条事件")
    if failed:
        print(f"失败 {len(failed)} 个：{failed[:5]}")


if __name__ == "__main__":
    main()
