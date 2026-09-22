# -*- coding: utf-8 -*-
"""memory_hits 记账（"注入即记账"）的针对性验证。

现有 pytest 不覆盖本行为，故补独立断言。核心要验的是第 2 组：
预算裁剪会丢弃尾部候选，记账若按"候选列表"记就会凭空抬高命中率，
所以回执必须与真正进入 prompt 的条目严格一致。

由 `tests/unit/test_memory_verification.py` 以子进程方式纳管。
也可单独运行：cd backend && PYTHONPATH=. python tests/memory_verify/verify_memory_hits_accounting.py
"""
import os
import sys
from pathlib import Path


def _find_backend(start: str) -> str:
    """从脚本位置向上找到含 harness/ 的 backend 目录（迁移位置后仍稳健）。"""
    for cand in [Path(start).resolve(), *Path(start).resolve().parents]:
        if (cand / "harness").is_dir():
            return str(cand)
    raise RuntimeError("找不到 backend 目录（需含 harness/）")


sys.path.insert(0, _find_backend(__file__))

from sqlalchemy import create_engine, Column, Integer, Text, Float, JSON, Boolean, DateTime  # noqa: E402
from sqlalchemy.orm import sessionmaker, declarative_base  # noqa: E402

from harness.state.memory import Memory, MemoryManager  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_mem(i, lesson_len=20):
    return Memory(
        id=i, user_id=1,
        requirement=f"需求样例 {i}",
        complexity="M", rating=8.5,
        lesson="关键教训内容" * lesson_len,
        reusable_pattern="可复用模式代码" * 5,
        tags=["localStorage"],
    )


# ---------- 内存库 + 测试用 MemoryHit 模型 ----------
TBase = declarative_base()


class THit(TBase):
    __tablename__ = "memory_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, index=True)
    user_id = Column(Integer, index=True)
    requirement_id = Column(Integer, index=True)
    run_id = Column(Text, index=True)
    inject_position = Column(Integer, default=0)
    inject_tokens = Column(Integer, default=0)
    injected_at = Column(DateTime, nullable=True)
    outcome = Column(Text, default="pending", index=True)
    resolved_at = Column(DateTime, nullable=True)


tengine = create_engine("sqlite:///:memory:")
TBase.metadata.create_all(tengine)
TSession = sessionmaker(bind=tengine)

import harness.state.memory as mem_mod  # noqa: E402
orig_session, orig_hit = mem_mod.SessionLocal, mem_mod.MemoryHit
mem_mod.SessionLocal, mem_mod.MemoryHit = TSession, THit


def fresh_db():
    db = TSession()
    db.query(THit).delete()
    db.commit()
    return db


def new_mgr(block, items):
    """构造一个跳过检索、直接返回指定结果的 MemoryManager（避免加载检索模型）"""
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._select_and_render = lambda req, uid: (block, items)
    return mgr


print("== 1. 回执与实际注入严格一致（核心）==")
mems = [make_mem(i) for i in (1, 2, 3)]
block, items = MemoryManager._render_few_shot(mems)
n_in_block = block.count("### 案例")
check("全部注入时条数一致", len(items) == 3 and n_in_block == 3,
      f"回执 {len(items)} / block 内 {n_in_block}")
check("回执带位置与 token", all(p > 0 and t > 0 for _, p, t in items),
      f"实际 {[(p, t) for _, p, t in items]}")
check("位置连续递增", [p for _, p, _ in items] == [1, 2, 3],
      f"实际 {[p for _, p, _ in items]}")

print("\n== 2. 预算裁剪时只记真正注入的（防命中率虚高）==")
big = [make_mem(i, lesson_len=400) for i in range(1, 8)]  # 7 条，必然超预算
block2, items2 = MemoryManager._render_few_shot(big)
n_in_block2 = block2.count("### 案例")
check("裁剪后回执数 == block 实际条数", len(items2) == n_in_block2,
      f"回执 {len(items2)} / block 内 {n_in_block2}")
check("裁剪确实发生了（注入数 < 候选数）", len(items2) < len(big),
      f"注入 {len(items2)} / 候选 {len(big)}")
check("回执的 memory_id 都在 block 里",
      all(f"需求样例 {m.id}" in block2 for m, _, _ in items2))

print("\n== 3. 记账写库 ==")
db = fresh_db()
block3, items3 = MemoryManager._render_few_shot(mems)
mgr = new_mgr(block3, items3)
blk, hit_ids = mgr.inject_with_receipt("需求X", user_id=1, requirement_id=42, run_id="run_test")
check("返回 block 正常", blk == block3)
check("返回 hit_ids 且数量对", len(hit_ids) == len(items3), f"实际 {len(hit_ids)}")

rows = db.query(THit).order_by(THit.inject_position).all()
check("库里写入了对应行数", len(rows) == len(items3), f"实际 {len(rows)}")
check("memory_id / user_id 正确",
      all(r.memory_id == m.id and r.user_id == 1 for r, (m, _, _) in zip(rows, items3)))
check("requirement_id / run_id 落库",
      all(r.requirement_id == 42 and r.run_id == "run_test" for r in rows))
check("position 与 token 落库",
      [r.inject_position for r in rows] == [1, 2, 3]
      and all(r.inject_tokens > 0 for r in rows))
check("初始 outcome 为 pending", all(r.outcome == "pending" for r in rows))
check("未回填时 resolved_at 为空", all(r.resolved_at is None for r in rows))

print("\n== 4. 结果回填 ==")
mgr.resolve_hits(hit_ids, passed=True)
db.expire_all()
rows = db.query(THit).all()
check("回填为 pass", all(r.outcome == "pass" for r in rows),
      f"实际 {[r.outcome for r in rows]}")
check("回填写入 resolved_at", all(r.resolved_at is not None for r in rows))

mgr.resolve_hits(hit_ids, passed=False)
db.expire_all()
rows = db.query(THit).all()
check("可改写为 fail", all(r.outcome == "fail" for r in rows))
check("空 hit_ids 不报错", mgr.resolve_hits([], True) is None)

print("\n== 5. build_memory_block 不记账（向后兼容）==")
db = fresh_db()
mgr5 = new_mgr(block3, items3)
out = mgr5.build_memory_block("需求Y", 1)
check("仍返回文本块", out == block3)
check("不产生记账行（缺归因上下文就该不记）", db.query(THit).count() == 0,
      f"实际 {db.query(THit).count()} 行")

print("\n== 6. 记账失败不阻断注入 ==")
db = fresh_db()
mgr6 = new_mgr(block3, items3)


class BoomHit:
    def __init__(self, **kw):
        raise RuntimeError("db write failed")


mem_mod.MemoryHit = BoomHit
try:
    blk6, ids6 = mgr6.inject_with_receipt("需求Z", 1, requirement_id=7)
    check("记账失败仍返回 block（不阻断）", blk6 == block3)
    check("记账失败返回空 hit_ids", ids6 == [], f"实际 {ids6}")
finally:
    mem_mod.MemoryHit = orig_hit

print("\n== 7. 边界 ==")
db = fresh_db()
mgr7 = new_mgr("", [])
blk7, ids7 = mgr7.inject_with_receipt("无记忆需求", 1)
check("无记忆时不记账", blk7 == "" and ids7 == [] and db.query(THit).count() == 0)


class NoIdMem:
    id = None
    user_id = 1
    requirement = "无 id"
    complexity = "M"
    rating = 8.0
    tags = []
    lesson = "x"
    reusable_pattern = "y"


mgr8 = new_mgr("block", [(NoIdMem(), 1, 10)])
_, ids8 = mgr8.inject_with_receipt("x", 1)
check("无 id 的记忆被跳过（不写脏行）", ids8 == [], f"实际 {ids8}")

mem_mod.SessionLocal, mem_mod.MemoryHit = orig_session, orig_hit

print("\n" + "=" * 46)
if FAILS:
    print(f"{len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("全部通过")
