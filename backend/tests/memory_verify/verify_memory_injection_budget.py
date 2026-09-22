# -*- coding: utf-8 -*-
"""记忆注入预算 / 用户隔离 / 空内容过滤 / 相关性门禁 的针对性验证。

现有 pytest 不覆盖本次新增的行为（token 预算裁剪、access_count 落库、
全局索引 + 用户过滤、空内容过滤、相关性门禁），所以这里补一组独立断言。

由 `tests/unit/test_memory_verification.py` 以子进程方式纳管（脚本内会
monkeypatch `harness.state.memory` 的模块级单例，子进程隔离避免污染其它测试）。
也可单独运行：cd backend && PYTHONPATH=. python tests/memory_verify/verify_memory_injection_budget.py
"""
import os
import sys
from pathlib import Path

FAILS = []


def _find_backend(start: str) -> str:
    """从脚本位置向上找到含 harness/ 的 backend 目录（迁移位置后仍稳健）。"""
    for cand in [Path(start).resolve(), *Path(start).resolve().parents]:
        if (cand / "harness").is_dir():
            return str(cand)
    raise RuntimeError("找不到 backend 目录（需含 harness/）")


sys.path.insert(0, _find_backend(__file__))

from harness.state.memory import (  # noqa: E402
    Memory, MemoryManager, _estimate_tokens, _truncate_to_tokens,
    INJECT_MAX_ITEMS, INJECT_MAX_TOKENS, INJECT_ITEM_MAX_TOKENS,
)


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_mem(i, lesson_len=1500, pattern_len=600):
    """造一条 lesson 明显超预算的记忆"""
    return Memory(
        id=i, user_id=1,
        requirement=f"需求样例 {i}：做一个带本地存储的待办清单应用",
        complexity="M", rating=8.5,
        lesson="关键教训内容" * lesson_len,
        reusable_pattern="可复用模式代码" * pattern_len,
        tags=["localStorage"],
    )


print("== 1. 截断工具 ==")
long_text = "中文测试内容" * 200
truncated = _truncate_to_tokens(long_text, 50)
check("超长文本被截断到预算内", _estimate_tokens(truncated) <= 50,
      f"{_estimate_tokens(truncated)} token")
check("截断后带省略号标记", truncated.endswith("…"))
check("未超预算的文本原样返回", _truncate_to_tokens("短文本", 100) == "短文本")
check("空串安全", _truncate_to_tokens("", 100) == "")
check("预算 <=0 返回空串", _truncate_to_tokens("abc", 0) == "")

print("\n== 2. 注入预算（核心）==")
mems = [make_mem(i) for i in range(1, 11)]
block = MemoryManager._format_few_shot(mems)
cost = _estimate_tokens(block)
n_items = block.count("### 案例")

check("总 token 不超预算", cost <= INJECT_MAX_TOKENS, f"{cost} <= {INJECT_MAX_TOKENS}")
check("注入条数不超上限", n_items <= INJECT_MAX_ITEMS, f"{n_items} <= {INJECT_MAX_ITEMS}")
check("确有内容注入（未误伤为空）", n_items > 0, f"注入 {n_items} 条 / 候选 {len(mems)} 条")

single = MemoryManager._format_few_shot([make_mem(1)])
single_cost = _estimate_tokens(single)
check("单条不超单条预算（含标题开销）",
      single_cost <= INJECT_ITEM_MAX_TOKENS + 40,
      f"{single_cost} <= {INJECT_ITEM_MAX_TOKENS + 40}")

# 回归对照：改造前 lesson 完全不截断，这里确认长 lesson 确实被压下来了
raw_lesson_cost = _estimate_tokens(mems[0].lesson)
check("超长 lesson 已被压缩", raw_lesson_cost > INJECT_ITEM_MAX_TOKENS * 2,
      f"原始 lesson {raw_lesson_cost} token，远超单条预算")

print("\n== 3. 边界与降级 ==")
check("空列表返回空串", MemoryManager._format_few_shot([]) == "")
check("无 lesson / 模式的记忆可正常渲染",
      bool(MemoryManager._format_few_shot([Memory(id=1, user_id=1, requirement="x", rating=9)])))

tiny = MemoryManager._format_few_shot(mems, max_tokens=60)
check("极小预算下优雅降级（不抛异常且有输出）", bool(tiny))
check("极小预算仍在预算内", _estimate_tokens(tiny) <= 60,
      f"{_estimate_tokens(tiny)} token")

print("\n== 4. 正负例区分 ==")
fail_mem = Memory(id=2, user_id=1, requirement="失败需求", rating=3.0,
                  lesson="踩坑", reusable_pattern="别这么写", tags=["failure"])
fail_block = MemoryManager._format_few_shot([fail_mem])
check("低分 / failure 标签 → 失败案例", "⚠️" in fail_block)
check("失败案例不展示可复用模式（避免被当范例模仿）", "可复用模式" not in fail_block)

ok_block = MemoryManager._format_few_shot(
    [Memory(id=3, user_id=1, requirement="成功需求", rating=9.0, lesson="顺利")])
check("高分 → 成功案例", "✅" in ok_block)

print("\n== 5. access_count 真正落库 ==")
try:
    from sqlalchemy import create_engine, Column, Integer, Text, Float, Boolean, DateTime, JSON
    from sqlalchemy.orm import sessionmaker, declarative_base

    TBase = declarative_base()

    class TMem(TBase):
        __tablename__ = "agent_memories_v2"
        id = Column(Integer, primary_key=True)
        user_id = Column(Integer, index=True)
        requirement = Column(Text, default="")
        complexity = Column(Text, default="S")
        code_summary = Column(Text, default="")
        rating = Column(Float, default=7.0)
        reflection = Column(Text, default="")
        lesson = Column(Text, default="")
        reusable_pattern = Column(Text, default="")
        tags = Column(JSON, default=list)
        importance = Column(Float, default=0.5)
        access_count = Column(Integer, default=0)
        created_at = Column(DateTime, nullable=True)
        merged_from = Column(JSON, default=list)
        superseded = Column(Boolean, default=False)

    tengine = create_engine("sqlite:///:memory:")
    TBase.metadata.create_all(tengine)
    TSession = sessionmaker(bind=tengine)

    import harness.state.memory as mem_mod
    orig_session, orig_model = mem_mod.SessionLocal, mem_mod.AgentMemoryV2
    mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = TSession, TMem
    try:
        db = TSession()
        db.add_all([TMem(id=1, user_id=1, access_count=0),
                    TMem(id=2, user_id=1, access_count=0),
                    TMem(id=3, user_id=2, access_count=0)])
        db.commit()

        # 不触发 __init__（避免加载检索模型）
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._touch_memories([1, 2])

        db.expire_all()
        rows = {r.id: r.access_count for r in db.query(TMem).all()}
        check("被注入的记忆 access_count +1", rows.get(1) == 1 and rows.get(2) == 1,
              f"实际 {rows}")
        check("未被注入的记忆保持 0", rows.get(3) == 0, f"实际 {rows.get(3)}")

        mgr._touch_memories([1])
        db.expire_all()
        rows = {r.id: r.access_count for r in db.query(TMem).all()}
        check("重复命中可累加", rows.get(1) == 2, f"实际 {rows.get(1)}")
        check("空 id 列表不报错", mgr._touch_memories([]) is None)
        db.close()
    finally:
        mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = orig_session, orig_model
except Exception as e:
    check("access_count 落库验证", False, f"异常: {e}")

print("\n== 6. 跨用户索引隔离（改造中新发现的 bug）==")
try:
    import threading
    import harness.state.memory as mem_mod

    db = TSession()
    db.query(TMem).delete()
    db.add_all([
        TMem(id=11, user_id=1, requirement="用户1的贪吃蛇需求", lesson="教训A"),
        TMem(id=12, user_id=1, requirement="用户1的待办清单需求", lesson="教训B"),
        TMem(id=13, user_id=2, requirement="用户2的私密需求", lesson="教训C"),
    ])
    db.commit()
    db.close()

    class FakeRetriever:
        """记录被索引的文档；search 假装全部命中，以放大隔离问题"""
        def __init__(self):
            self.docs = []

        def index(self, documents, memory_ids=None):
            self.docs = list(documents)

        def search(self, query, top_k=5):
            return [(i, 1.0) for i in range(min(top_k, len(self.docs)))]

    fake = FakeRetriever()
    orig_s, orig_m = mem_mod.SessionLocal, mem_mod.AgentMemoryV2
    mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = TSession, TMem
    try:
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._llm = None
        mgr._retriever = fake
        mgr._lock = threading.Lock()
        mgr._indexed_memories = None
        mgr._new_since_consolidate = 0

        block = mgr.build_memory_block("贪吃蛇", user_id=1)

        check("索引是全局视图（覆盖全部用户）", len(fake.docs) == 3,
              f"索引 {len(fake.docs)} 条 / 库中共 3 条")
        check("注入内容不含其他用户的记忆", "用户2的私密需求" not in block)
        check("本用户记忆确实被注入", "用户1的" in block)
    finally:
        mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = orig_s, orig_m
except Exception as e:
    import traceback
    traceback.print_exc()
    check("跨用户隔离验证", False, f"异常: {e}")

print("\n== 7. 空内容记忆不注入（P0 止血）==")
try:
    import harness.state.memory as mem_mod

    # 7a 单元级：内容判定
    check("双空记忆 → 不注入",
          not mem_mod._has_injectable_content(
              Memory(user_id=1, lesson="", reusable_pattern="")))
    check("lesson=无 且 pattern 空 → 不注入",
          not mem_mod._has_injectable_content(
              Memory(user_id=1, lesson="无", reusable_pattern="")))
    check("有 lesson 缺 pattern → 仍注入（宽松保留）",
          mem_mod._has_injectable_content(
              Memory(user_id=1, lesson="踩坑教训", reusable_pattern="")))
    check("有 pattern 缺 lesson → 注入",
          mem_mod._has_injectable_content(
              Memory(user_id=1, lesson="", reusable_pattern="可复用模块")))

    # 7b 集成级：经 build_memory_block 的真实过滤路径
    db = TSession()
    db.query(TMem).delete()
    db.add_all([
        TMem(id=21, user_id=1, requirement="贪吃蛇需求",
             lesson="先确认视觉风格再编码", reusable_pattern="状态机管理游戏循环"),
        TMem(id=34, user_id=1, requirement="空记忆需求",
             lesson="", reusable_pattern=""),  # 幽灵记忆（rating 可能很高但无内容）
        TMem(id=35, user_id=1, requirement="多文件项目",
             lesson="确保文件完整性", reusable_pattern="无"),
    ])
    db.commit(); db.close()

    class FakeRetriever7:
        def __init__(self): self.docs = []
        def index(self, documents, memory_ids=None): self.docs = list(documents)
        def search(self, query, top_k=5):
            return [(i, 1.0) for i in range(min(top_k, len(self.docs)))]

    fake = FakeRetriever7()
    orig_s, orig_m = mem_mod.SessionLocal, mem_mod.AgentMemoryV2
    mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = TSession, TMem
    try:
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._llm = None
        mgr._retriever = fake
        mgr._lock = threading.Lock()
        mgr._indexed_memories = None
        mgr._new_since_consolidate = 0

        block, items = mgr._select_and_render("贪吃蛇", user_id=1)
        injected_ids = [m.id for m, _, _ in items]
        check("实际注入 2 条（id=34 幽灵被过滤）", len(injected_ids) == 2,
              f"注入 {injected_ids}")
        check("id=34 幽灵不在注入列表", 34 not in injected_ids)
        check("有内容的 id=21 / id=35 被注入",
              21 in injected_ids and 35 in injected_ids)
        check("注入块非空（确有内容）", bool(block))
    finally:
        mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = orig_s, orig_m
except Exception as e:
    import traceback; traceback.print_exc()
    check("空内容过滤验证", False, f"异常: {e}")

print("\n== 8. 相关性门禁（P1 检索治理）==")
try:
    import harness.state.memory as mem_mod
    db = TSession()
    db.query(TMem).delete()
    db.add_all([
        TMem(id=21, user_id=1, requirement="贪吃蛇", lesson="先确认视觉风格", reusable_pattern="状态机"),
        TMem(id=30, user_id=1, requirement="blog", lesson="确保文件完整", reusable_pattern="无"),
        TMem(id=35, user_id=1, requirement="多文件", lesson="架构分离", reusable_pattern="无"),
    ])
    db.commit(); db.close()

    class FakeRetriever8:
        def __init__(self, scored): self.docs = []; self.scored = list(scored)
        def index(self, documents, memory_ids=None): self.docs = list(documents)
        def search(self, query, top_k=5): return list(self.scored)

    orig_s, orig_m = mem_mod.SessionLocal, mem_mod.AgentMemoryV2
    mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = TSession, TMem
    try:
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._llm = None
        mgr._lock = threading.Lock()
        mgr._indexed_memories = None
        mgr._new_since_consolidate = 0
        mgr._llm_verify = lambda req, cands: cands  # 绕过 LLM，精确测门禁

        # 8a 绝对门禁：最相关 0.10 < 0.25 → 整体不注入（修复"名片页注入贪吃蛇"）
        mgr._retriever = FakeRetriever8([(0, 0.10), (1, 0.08), (2, 0.05)])
        block, items = mgr._select_and_render("名片页任务", user_id=1)
        check("绝对门禁：最相关<0.25 整体不注入", block == "" and not items)

        # 8b 相对门禁：top=0.9 高相关，其余远低于 → 只注入最相关 1 条
        mgr._retriever = FakeRetriever8([(0, 0.90), (1, 0.10), (2, 0.05)])
        _, items = mgr._select_and_render("贪吃蛇需求", user_id=1)
        check("相对门禁：只注入最相关 1 条（防同质化）", len(items) == 1, f"注入 {len(items)} 条")

        # 8c 相对门禁边界：top=0.5，次相关 0.30 >= 0.25(=0.5*0.5) → 注入 2 条
        mgr._retriever = FakeRetriever8([(0, 0.50), (1, 0.30), (2, 0.04)])
        _, items = mgr._select_and_render("贪吃蛇需求", user_id=1)
        check("相对门禁边界：0.5*0.5=0.25，0.30 入选 → 注入 2 条", len(items) == 2, f"注入 {len(items)} 条")

        # 8d 不相关任务：所有候选相似度都低 → 整体不注入
        mgr._retriever = FakeRetriever8([(0, 0.12), (1, 0.10), (2, 0.09)])
        block, items = mgr._select_and_render("名片页任务", user_id=1)
        check("不相关任务：低相似度整体不注入", block == "" and not items)

        # 8e 跨用户高相似度不应抬高本用户门槛（修复误杀回归）：
        # 索引含"其他用户的空记忆(top=1.0)"+ 本用户两条 0.60/0.55 的相关记忆。
        # 旧逻辑先取全局 top=1.0 → rel_floor=0.5 → 本用户两条可能被误杀(注入 0 条)。
        # 新逻辑用户隔离前置 → 本用户 top>=0.60 → rel_floor<=0.30 → 两条都注入。
        # 本用户两条得分都 >=0.5，断言与"1.0 落在哪个 idx"无关（顺序无关，确定可复现）。
        db = TSession(); db.query(TMem).delete()
        db.add_all([
            TMem(id=1, user_id=8, requirement="x", lesson="无", reusable_pattern="无"),  # 其他用户空记忆
            TMem(id=21, user_id=1, requirement="贪吃蛇", lesson="视觉风格", reusable_pattern="状态机"),
            TMem(id=30, user_id=1, requirement="blog", lesson="文件完整", reusable_pattern="无"),
        ])
        db.commit(); db.close()
        mgr._indexed_memories = None  # 强制从 DB 重建（得到正规 Memory 对象，非游离 ORM）
        mgr._retriever = FakeRetriever8([(0, 1.0), (1, 0.60), (2, 0.55)])
        _, items = mgr._select_and_render("贪吃蛇需求", user_id=1)
        check("跨用户 top 不抬高本用户门槛（应注入 2 条而非 0 条）", len(items) == 2, f"注入 {len(items)} 条")
    finally:
        mem_mod.SessionLocal, mem_mod.AgentMemoryV2 = orig_s, orig_m
except Exception as e:
    import traceback; traceback.print_exc()
    check("相关性门禁验证", False, f"异常: {e}")

print("\n" + ("=" * 46))
print("全部通过" if not FAILS else f"失败 {len(FAILS)} 项: {FAILS}")
sys.exit(1 if FAILS else 0)
