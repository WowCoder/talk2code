#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可观测性指标报表（第一批）

产出两类起步指标（对应《可观测性设计说明》指标体系，质量层留待第二批）：

1. **KV-cache 命中率** —— 从 `llm_traffic.log` 离线解析。
   数据已经在磁盘上流过去了（此前没人接），所以**不需要跑新需求就能看到数字**，
   这也是验证 L1「稳定前缀 + 可变尾段」优化是否真的生效的唯一手段。

2. **节点耗时分解** —— 从 `agent_traces` 表读取线上真实 trace，
   按 span 归一化分组后给出次数 / P50 / P95 / Max 与端到端占比。

用法（在 backend 目录下）：
    PYTHONPATH=. python -m harness.observability.metrics_report
    PYTHONPATH=. python -m harness.observability.metrics_report --traffic-only
    PYTHONPATH=. python -m harness.observability.metrics_report --traces-only --limit 200

设计原则：**宁可低估也不高估**。日志体被截断（8000 字符）的行会被单独计为
"截断跳过"并显式打印，绝不静默混入分子分母。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
from collections import defaultdict

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)

# llm_traffic 的落盘路径在历史上分裂过（相对路径受 CWD 影响 + client.py 曾指向项目根），
# 同时搜索两个候选目录以兼容历史数据；新写入统一收敛到 backend/logs。
_TRAFFIC_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, "logs", "llm_traffic.log"),
    os.path.join(_BACKEND_DIR, "logs", "llm_traffic.log"),
]

_ITER_RE = re.compile(r"^(tool_coder_iter)_\d+$")


def _traffic_files(extra=()) -> list:
    if extra:
        files = []
        for pattern in extra:
            files.extend(sorted(glob.glob(pattern)))
        return [f for f in files if os.path.isfile(f)]

    files = []
    for base in _TRAFFIC_CANDIDATES:
        # 含按天轮转的备份：llm_traffic.log.2026-09-01
        files.extend(sorted(glob.glob(base + "*")))
    return [f for f in files if os.path.isfile(f)]


def _parse_line(line: str):
    """日志行格式：<时间> | <JSON>"""
    _, _, payload = line.partition(" | ")
    payload = payload.strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _usage_of(rec):
    """返回 usage dict；'truncated' 表示 body 被截断无法解析；None 表示不适用"""
    if not isinstance(rec, dict) or rec.get("dir") != "response":
        return None
    body = rec.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return "truncated"
    if not isinstance(body, dict):
        return None
    return body.get("usage") or None


def _cache_of(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens"):
        return int(details["cached_tokens"])
    if usage.get("prompt_cache_hit_tokens"):
        return int(usage["prompt_cache_hit_tokens"])
    if usage.get("cache_read_input_tokens"):
        return int(usage["cache_read_input_tokens"])
    return 0


def _pct(values: list, p: float) -> float:
    """线性插值分位数"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = (len(ordered) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return float(ordered[lo] * (1 - frac) + ordered[hi] * frac)


def report_traffic(extra_paths=()) -> None:
    files = _traffic_files(extra_paths)
    print("=== KV-cache 命中率（来源：llm_traffic.log）===")
    if not files:
        print("未找到 llm_traffic.log（已查：%s）" % "、".join(_TRAFFIC_CANDIDATES))
        return

    calls = skipped = 0
    prompt_total = cached_total = completion_total = 0
    per_model = defaultdict(lambda: {"calls": 0, "prompt": 0, "cached": 0})
    buckets = {"<10%": 0, "10-50%": 0, "50-90%": 0, ">=90%": 0}

    # 两阶段收集：model 只出现在 request 行，必须按 call_id 关联到 response 行，
    # 否则多模型场景下根本分不清命中率归属（实测首版就全落在 "-" 上）。
    pending = []
    model_by_call = {}
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                rec = _parse_line(line)
                if not isinstance(rec, dict):
                    continue
                if rec.get("dir") == "request":
                    if rec.get("model"):
                        model_by_call[rec.get("call_id")] = rec["model"]
                    continue

                usage = _usage_of(rec)
                if usage == "truncated":
                    skipped += 1
                    continue
                if not usage:
                    continue

                pending.append((
                    rec.get("call_id"),
                    int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                    int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                    _cache_of(usage),
                ))

    for call_id, prompt, completion, cached in pending:
        calls += 1
        prompt_total += prompt
        completion_total += completion
        cached_total += cached

        model = model_by_call.get(call_id) or "-"
        per_model[model]["calls"] += 1
        per_model[model]["prompt"] += prompt
        per_model[model]["cached"] += cached

        rate = (cached / prompt) if prompt else 0.0
        if rate < 0.10:
            buckets["<10%"] += 1
        elif rate < 0.50:
            buckets["10-50%"] += 1
        elif rate < 0.90:
            buckets["50-90%"] += 1
        else:
            buckets[">=90%"] += 1

    if not calls:
        print("解析到 0 条有效响应（可能日志未记录 usage）")
        return

    print("日志文件            %d 个" % len(files))
    print("有效响应            %d 条" % calls)
    print("body 截断跳过       %d 条（不计入分子分母）" % skipped)
    if skipped:
        print("                    注：被截断的多为大 prompt，而大 prompt 命中率通常偏高")
        print("                    → 下列命中率是**偏保守的下限**，不是精确值")
    print("-" * 52)
    print("总输入 token        %s" % f"{prompt_total:,}")
    print("总缓存命中 token    %s" % f"{cached_total:,}")
    print("总输出 token        %s" % f"{completion_total:,}")
    print("全局缓存命中率      %.1f%%" % (100.0 * cached_total / prompt_total if prompt_total else 0.0))
    print("-" * 52)
    print("按模型：")
    for model, agg in sorted(per_model.items(), key=lambda kv: -kv[1]["prompt"]):
        rate = 100.0 * agg["cached"] / agg["prompt"] if agg["prompt"] else 0.0
        print("  %-22s 调用 %-5d 输入 %-12s 命中 %-12s 命中率 %.1f%%"
              % (model, agg["calls"], f"{agg['prompt']:,}", f"{agg['cached']:,}", rate))
    print("-" * 52)
    print("单次命中率分布（反映前缀稳定性，越集中在高桶越好）：")
    for name in ("<10%", "10-50%", "50-90%", ">=90%"):
        print("  %-8s %d 次" % (name, buckets[name]))


def report_traces(limit: int = 100) -> None:
    print()
    print("=== 节点耗时分解（来源：agent_traces）===")
    try:
        from utils.db import get_db
        from models.models import AgentTrace
    except Exception as e:
        print("无法导入数据层：%s" % e)
        return

    try:
        with get_db() as db:
            rows = db.query(AgentTrace).order_by(AgentTrace.id.desc()).limit(limit).all()
            payload = [
                {"duration_ms": r.duration_ms, "data": r.data or {},
                 "tokens": r.total_tokens or 0}
                for r in rows
            ]
    except Exception as e:
        print("查询 agent_traces 失败：%s" % e)
        return

    if not payload:
        print("agent_traces 表为空（还没有线上 trace）")
        return

    groups = defaultdict(list)
    for row in payload:
        for span in (row["data"].get("spans") or []):
            dur = span.get("duration_ms")
            if dur is None:
                continue
            name = span.get("name") or "-"
            match = _ITER_RE.match(name)
            if match:
                name = match.group(1)  # 编码轮次归一化：iter_0/1/2 合并观察
            groups[name].append(float(dur))

    all_spans_ms = sum(sum(v) for v in groups.values())
    e2e = [float(r["duration_ms"]) for r in payload if r["duration_ms"]]

    print("trace 条数          %d" % len(payload))
    if e2e:
        print("端到端  P50 %s ms   P95 %s ms   Max %s ms"
              % (f"{_pct(e2e, 0.5):,.0f}", f"{_pct(e2e, 0.95):,.0f}", f"{max(e2e):,.0f}"))
    print("-" * 72)
    print("%-24s %6s %10s %10s %10s %8s" % ("节点（span 归一化）", "次数", "P50(ms)", "P95(ms)", "Max(ms)", "占比"))
    for name, vals in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        share = 100.0 * sum(vals) / all_spans_ms if all_spans_ms else 0.0
        print("%-24s %6d %10s %10s %10s %7.1f%%"
              % (name, len(vals),
                 f"{_pct(vals, 0.5):,.0f}", f"{_pct(vals, 0.95):,.0f}",
                 f"{max(vals):,.0f}", share))

    rates = [r["data"].get("cache_hit_rate") for r in payload]
    rates = [float(x) for x in rates if isinstance(x, (int, float))]
    if rates:
        print("-" * 72)
        print("trace 级缓存命中率  P50 %.1f%%   均值 %.1f%%   （样本 %d 条）"
              % (100 * _pct(rates, 0.5), 100 * statistics.fmean(rates), len(rates)))


def main() -> None:
    parser = argparse.ArgumentParser(description="可观测性指标报表（第一批）")
    parser.add_argument("--traffic-only", action="store_true", help="只输出 KV-cache 命中率")
    parser.add_argument("--traces-only", action="store_true", help="只输出节点耗时分解")
    parser.add_argument("--limit", type=int, default=100, help="读取最近多少条 trace")
    parser.add_argument("--traffic-path", action="append", default=[],
                        help="显式指定 llm_traffic 日志路径（可重复，支持通配符）")
    args = parser.parse_args()

    if not args.traces_only:
        report_traffic(args.traffic_path)
    if not args.traffic_only:
        report_traces(limit=args.limit)


if __name__ == "__main__":
    main()
