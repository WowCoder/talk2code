#!/usr/bin/env python3
"""记忆有效性报表 CLI —— 回答"注入的记忆到底有没有用"。

用法（在 backend 目录下执行）：
    python scripts/memory_effectiveness.py
    python scripts/memory_effectiveness.py --min-hits 5 --json

为什么需要它：memory_hits 表把每次注入记成一行，过去**从不回填结果**，于是
"记忆有没有用"只能靠 eval 整体通过率这种粗糙指标猜测，无法归因到单条记忆。
现在生产侧在任务终态回填 outcome，这张表第一次能算出「某项记忆被注入 N 次、
其中任务通过 M 次」，也就有了淘汰劣质记忆的事实依据。

口径边界（写进输出，避免被当成因果结论）：
    - 通过率是**相关性**不是因果性 —— 一条记忆被注入到失败任务里，可能是它
      匹配到的任务本身就更难，而不是它导致失败。
    - 要证因果必须跑 on/off 对照（eval 侧带不带记忆各跑一轮）。
    - pending 占比过高时通过率不可信（说明大量任务没跑到终态）。

退出码：0 正常；1 数据库不可用。
"""
import argparse
import json
import os
import sys

# 允许从 backend/ 或仓库根目录执行
# 判定"优质记忆"所需的最少已回填样本数。设为 3 是因为样本更少时通过率没有
# 统计意义（1 次通过 = 100% 通过率，会误导）。
_MIN_RESOLVED_FOR_STRONG = 3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _print_human(rep: dict, top: int) -> None:
    o = rep["overall"]
    cov = rep["coverage"]
    line = "=" * 72
    print(line)
    print("记忆有效性报表")
    print(line)
    print(
        f"注入记录 {o['hit_rows']} 行 | 涉及记忆 {o['distinct_memories']} 条 | "
        f"涉及需求 {o['distinct_requirements']} 个"
    )
    print(
        f"  pass={o['pass']}  fail={o['fail']}  pending={o['pending']}"
        f"  → pending 占比 {o['pending_ratio']}"
    )
    if o["pending_ratio"] is not None and o["pending_ratio"] > 0.5:
        print("  ⚠️ pending 占比超过半数：这些是没跑到终态的任务，通过率不可信。")

    print(
        f"\n覆盖率：有记忆注入的需求 {cov['requirements_with_memory']} 个，"
        f"平均每需求 {cov['avg_memories_per_requirement']} 条"
    )
    if cov["requirements_with_memory"] == 0:
        print("  ⚠️ 一条都没注入过 —— 问题在检索层，不在记忆内容质量。")

    weak = rep["weak_memories"]
    print(f"\n弱记忆（≥min-hits 次注入且通过率 <50%，淘汰/重写候选）：{len(weak)} 条")
    for w in weak[:top]:
        print(
            f"  memory#{w['memory_id']:<5} 注入 {w['hits']:>2} 次 → "
            f"pass {w['pass']} / fail {w['fail']} / pending {w['pending']}"
            f"  通过率 {w['pass_rate']}"
        )

    # 分母必须用「已回填」的样本数（pass+fail），不能用 hits —— 否则一条被注入
    # 27 次但只回填了 1 次（1 pass / 0 fail）的记忆会被算成通过率 1.0 的"优质记忆"。
    strong = [
        x for x in rep["by_memory"]
        if x["pass_rate"] is not None
        and (x["pass"] + x["fail"]) >= _MIN_RESOLVED_FOR_STRONG
        and x["pass_rate"] >= 0.8
    ]
    print(f"\n优质记忆（≥{_MIN_RESOLVED_FOR_STRONG} 次已回填且通过率 ≥80%）：{len(strong)} 条")
    for w in strong[:top]:
        print(
            f"  memory#{w['memory_id']:<5} 注入 {w['hits']:>2} 次 → "
            f"pass {w['pass']} / fail {w['fail']}  通过率 {w['pass_rate']}"
        )

    print(f"\n按需求明细（前 {top}）：")
    for r in rep["by_requirement"][:top]:
        print(
            f"  req{r['requirement_id']:<6} 注入 {r['hits']:>2} 条 → "
            f"pass {r['pass']} / fail {r['fail']} / pending {r['pending']}"
        )

    print(f"\n口径说明：{rep['note']}")
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆有效性报表")
    ap.add_argument("--min-hits", type=int, default=3,
                    help="判定弱记忆所需的最少注入次数（默认 3）")
    ap.add_argument("--top", type=int, default=10, help="每节最多展示多少条（默认 10）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    from harness.state.memory import MemoryManager

    mgr = MemoryManager(llm_client=None)
    rep = mgr.effectiveness_report(min_hits=args.min_hits)
    if "error" in rep:
        print(f"查询失败：{rep['error']}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _print_human(rep, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
