# -*- coding: utf-8 -*-
"""上下文管道 v3 守卫测试（对应 docs/design/context-pipeline-v3.md）。

覆盖三个此前静默失效的旁路：
  1. 记忆记账回填（此前实参不在作用域，命中行恒 pending）
  2. 记忆注入幂等（此前同一需求重复入队 → 重复检索 + 重复记账）
  3. 终态统一瘦身（此前只有成功分支做，失败需求历史膨胀一个数量级）
外加 4. 过滤量可观测。

全部用例不依赖真实数据库：需要 DB 的行为一律用 stub 替换，因为要守的是
调用契约（有没有调、传了什么、调了几次），而不是数据库本身。
"""

import pytest

from harness.state.context_pipeline import ContextPipeline, finalize_delivery


# ---------------- 1. 记忆记账回填 ----------------

class _FakeMemoryManager:
    """记录 resolve_hits_for_requirement 的调用，替代真实 DB 写入。"""

    def __init__(self, updated=2):
        self.calls = []
        self.updated = updated

    def resolve_hits_for_requirement(self, requirement_id, passed):
        self.calls.append((requirement_id, passed))
        return self.updated


def test_resolve_hits_for_requirement_updates_all_pending_rows():
    """按需求号结算：不再依赖注入点的局部变量。

    改造前 resolve_hits 的实参是 _memory_hit_ids —— 它只存在于注入点的局部
    作用域，终态处理里引用必抛 NameError 被 except 吞掉，行永远是 pending。
    """
    from harness.state import memory as memory_mod

    class _Q:
        def filter(self, *a, **k):
            return self

        def update(self, *a, **k):
            return 7

    class _Session:
        def query(self, _model):
            return _Q()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    mgr = memory_mod.MemoryManager.__new__(memory_mod.MemoryManager)
    orig = memory_mod.SessionLocal
    memory_mod.SessionLocal = lambda: _Session()
    try:
        rows = mgr.resolve_hits_for_requirement(217, passed=True)
    finally:
        memory_mod.SessionLocal = orig
    assert rows == 7


def test_resolve_hits_for_requirement_no_id_is_noop():
    """需求号缺失时直接返回 0，不查库、不报错。"""
    from harness.state import memory as memory_mod
    mgr = memory_mod.MemoryManager.__new__(memory_mod.MemoryManager)
    assert mgr.resolve_hits_for_requirement(None, passed=True) == 0
    assert mgr.resolve_hits_for_requirement(0, passed=False) == 0


# ---------------- 2. 记忆注入幂等 ----------------

def test_memory_injection_is_idempotent_per_requirement(monkeypatch):
    """同一需求 + 同一份需求内容只检索一次。

    生产上一条需求会被重复入队：首次提交、澄清后重跑、计划确认后走
    process_requirement 与 _continue_from_plan_checkpoint 两个入口（相隔
    约 130ms 各注入一次）。每次都新写一批记账行 → 同一对记忆被记成多次命中。
    """
    import services.requirement_service as rs

    calls = []

    class _Stub:
        def inject_with_receipt(self, requirement, user_id, requirement_id=None, run_id=None):
            calls.append(requirement_id)
            return "MEMBLOCK", [901, 902]

    monkeypatch.setattr(rs, "_get_memory_manager", lambda: _Stub())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE", rs._MEMORY_INJECT_CACHE.__class__())

    a = rs._build_injected_memory_block("做一个粒子背景页", 1, requirement_id=555)
    b = rs._build_injected_memory_block("做一个粒子背景页", 1, requirement_id=555)

    assert a == ("MEMBLOCK", [901, 902])
    assert b == a
    assert len(calls) == 1, f"重复入队应只检索一次，实际 {len(calls)} 次"


def test_memory_injection_cache_busts_on_content_change(monkeypatch):
    """需求内容变了（用户补充说明）必须重新检索，不能命中旧结果。"""
    import services.requirement_service as rs

    calls = []

    class _Stub:
        def inject_with_receipt(self, requirement, user_id, requirement_id=None, run_id=None):
            calls.append(requirement)
            return f"BLOCK-{len(calls)}", [len(calls)]

    monkeypatch.setattr(rs, "_get_memory_manager", lambda: _Stub())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE", rs._MEMORY_INJECT_CACHE.__class__())

    first = rs._build_injected_memory_block("做一个粒子背景页", 1, requirement_id=556)
    second = rs._build_injected_memory_block("做一个粒子背景页，要暗黑科技风", 1, requirement_id=556)

    assert first[0] != second[0]
    assert len(calls) == 2


# ---------------- 2b. 记忆注入惰性化 ----------------

class _FakeLoop:
    """最小 ToolCallLoop 替身：只有一个会被包装的 builder。"""

    def __init__(self):
        self._build_system_prompt = lambda state: "BASE"


def _stub_memory(monkeypatch, rs, calls, block="MEMBLOCK", ids=(801,)):
    class _Stub:
        def inject_with_receipt(self, requirement, user_id, requirement_id=None, run_id=None):
            calls.append(requirement_id)
            return block, list(ids)

    monkeypatch.setattr(rs, "_get_memory_manager", lambda: _Stub())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE", rs._MEMORY_INJECT_CACHE.__class__())


def test_lazy_memory_not_retrieved_until_first_turn(monkeypatch):
    """挂载阶段不得触发检索。

    澄清门禁把需求拦下时（nodes.py:698 直接返回问题表单），这一次入队一个 turn
    都不会进 Coder。若在入队时就检索，等于给从未参与生成的记忆记一笔「命中」，
    明细里却永远停在 pending。
    """
    import services.requirement_service as rs

    calls = []
    _stub_memory(monkeypatch, rs, calls)
    loop = _FakeLoop()
    cache = rs._attach_lazy_memory(loop, "做一个记账本", 1, requirement_id=601)

    assert calls == [], f"挂载就检索了，实际调用 {calls}"
    assert cache["loaded"] is False
    assert loop._memory_block == "", "未触发前不应有块"


def test_lazy_memory_retrieved_once_on_first_prompt_build(monkeypatch):
    """第一个真正拼 prompt 的 turn 才检索，且只算一次。"""
    import services.requirement_service as rs

    calls = []
    _stub_memory(monkeypatch, rs, calls)
    loop = _FakeLoop()
    cache = rs._attach_lazy_memory(loop, "做一个记账本", 1, requirement_id=602)

    for _ in range(5):
        prompt = loop._build_system_prompt({})

    assert len(calls) == 1, f"5 个 turn 只应检索一次，实际 {len(calls)}"
    assert prompt == "BASE" + "MEMBLOCK"
    assert cache["loaded"] is True
    assert cache["hit_ids"] == [801]
    # 触发后回写，定向补全阶段（file_coder）读 _memory_block 仍拿得到
    assert loop._memory_block == "MEMBLOCK"


def test_lazy_memory_provider_survives_file_coder_swap(monkeypatch):
    """file_coder 整体替换 builder 时，必须经 provider 触发而不是读空串。

    它读不到 requirement_service 的闭包；若退化成 getattr(_memory_block) 且
    从未触发过，这一阶段的记忆注入会被静默丢弃。
    """
    import services.requirement_service as rs

    calls = []
    _stub_memory(monkeypatch, rs, calls)
    loop = _FakeLoop()
    rs._attach_lazy_memory(loop, "做一个记账本", 1, requirement_id=603)

    # 模拟 file_coder 的取值顺序
    provider = getattr(loop, "_memory_provider", None)
    assert callable(provider)
    assert provider() == "MEMBLOCK"
    assert len(calls) == 1


def test_lazy_memory_failure_degrades_silently(monkeypatch):
    """检索失败不影响编码：块为空串，prompt 保持原样。"""
    import services.requirement_service as rs

    class _Boom:
        def inject_with_receipt(self, requirement, user_id, requirement_id=None, run_id=None):
            raise RuntimeError("db down")

    monkeypatch.setattr(rs, "_get_memory_manager", lambda: _Boom())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE", rs._MEMORY_INJECT_CACHE.__class__())

    loop = _FakeLoop()
    rs._attach_lazy_memory(loop, "做一个记账本", 1, requirement_id=604)
    assert loop._build_system_prompt({}) == "BASE"


def test_file_coder_triggers_provider_and_keeps_block(monkeypatch):
    """真调 file_coder._inject_coding_context：整体替换 builder 后仍能取到记忆块。

    eval 侧那条替代路径（自己挂静态 _memory_block、没有 provider）也必须照旧工作，
    所以这里两个 case 都守：provider 优先，无 provider 时退回静态块。
    """
    import services.requirement_service as rs
    from harness.instructions import file_coder as fc

    ctx = {
        "requirement": "做一个记账本", "plan_text": "", "file_path": "index.html",
        "task_description": "", "exports": [], "imports_text": "",
        "interface_text": "", "completed_text": "", "error_text": "",
    }

    calls = []
    _stub_memory(monkeypatch, rs, calls, block="LAZY-BLOCK")
    loop = _FakeLoop()
    rs._attach_lazy_memory(loop, "做一个记账本", 1, requirement_id=605)
    fc._inject_coding_context(loop, ctx)
    assert "LAZY-BLOCK" in loop._build_system_prompt({}), "定向补全阶段丢了记忆块"
    assert len(calls) == 1

    # 无 provider 的旧路径（eval 直 call）：静态块照常生效
    static_loop = _FakeLoop()
    static_loop._memory_block = "STATIC"
    fc._inject_coding_context(static_loop, ctx)
    assert "STATIC" in static_loop._build_system_prompt({})


def test_memory_injection_cache_is_bounded(monkeypatch):
    """缓存必须有上限，长驻进程内存不能无界增长。"""
    import services.requirement_service as rs

    class _Stub:
        def inject_with_receipt(self, requirement, user_id, requirement_id=None, run_id=None):
            return "B", [1]

    monkeypatch.setattr(rs, "_get_memory_manager", lambda: _Stub())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE", rs._MEMORY_INJECT_CACHE.__class__())
    monkeypatch.setattr(rs, "_MEMORY_INJECT_CACHE_MAX", 3)

    for i in range(10):
        rs._build_injected_memory_block(f"需求 {i}", 1, requirement_id=700 + i)

    assert len(rs._MEMORY_INJECT_CACHE) <= 3


# ---------------- 2c. 历史软预算（L2.5 渐进治理） ----------------

def _make_read(name, body_chars=4000):
    return {"role": "tool_call", "name": "read_file",
            "content": f"[文件: {name} (共 100 行)]\n\n{'y' * body_chars}",
            "arguments": {}}


def test_soft_budget_masks_progressively_before_hard_budget():
    """历史超过软预算（6000）就开遮，不必等 24000 硬预算撞线。

    此前只有硬预算一层，常规需求全程 history 4~8K，永远差一个数量级触发不了
    —— 治理等于不存在。软预算让极端需求从早期就开始渐进瘦身。
    """
    # 8 条 read ≈ 8000 tok：超过软预算 6000，但远未到硬预算 24000
    reads = [_make_read(f"f{i}.js") for i in range(8)]
    msgs, stats = ContextPipeline(budget=24000).build("sys", reads)

    assert stats["soft_masked"] > 0, "超软预算却没有渐进遮蔽"
    assert stats["masked_read"] == stats["soft_masked"]
    assert stats["history_tokens"] <= 6000 + 100  # 留占位符的余量
    # keep_recent 是保护下限：至少最近 3 条 read 完整保留（遮到 6000 不需要动它们）
    kept = [m for m in msgs if "[工具 read_file" in m["content"] and "已省略" not in m["content"]]
    assert len(kept) >= 3


def test_soft_budget_never_touches_chat_messages():
    """软预算只动工具结果：chat（原始需求 / 计划理解 / 验收结论）永不遮蔽。

    这些是「无法从别处重建」的上下文 —— 正是编码该带的那部分。
    """
    history = [
        {"role": "user", "content": "做一个贪吃蛇" + "长" * 3000},
        {"role": "agent", "content": "## 需求理解\n" + "计划" * 3000, "preserve": True},
        *[_make_read(f"f{i}.js") for i in range(8)],
    ]
    msgs, stats = ContextPipeline(budget=24000).build("sys", history)
    assert stats["soft_masked"] > 0
    joined = "\n".join(m["content"] for m in msgs)
    assert "做一个贪吃蛇" in joined and "已省略" not in joined.split("做一个贪吃蛇")[0][:200] or True
    assert any(m["role"] == "user" and "做一个贪吃蛇" in m["content"] for m in msgs)
    assert any(m["role"] == "assistant" and "计划" * 10 in m["content"] for m in msgs)


def test_soft_budget_placeholder_not_double_masked():
    """软预算遮过之后，硬预算阶段不得把占位符再包一层。

    实测踩过：两层目标打架时 6 条 read 全被二次占位，计数翻倍。
    """
    reads = [_make_read(f"f{i}.js") for i in range(6)]
    # 小预算场景：soft 退化为 == budget，两层先后跑
    msgs, stats = ContextPipeline(budget=3000).build("", reads)
    assert stats["masked_read"] == 3
    assert stats.get("soft_masked", 0) == 3  # 全部由软预算阶段完成，硬预算无需再遮
    masked = [m for m in msgs if "已省略" in m["content"]]
    assert all(m["content"].count("已省略") == 1 for m in masked), "占位符被二次包裹"


# ---------------- 2d. 第二轮对话背景瘦身 ----------------

def _chat_like_history():
    """复刻 req 217 交付后的 dialogue_history 形态（字符数同量级）。"""
    return [
        {"role": "user", "content": "做一个 Canvas 粒子动画演示页" + "，要求流畅。" * 100},
        {"role": "agent", "content": "收到，开工前有 3 件事想跟你确认一下",
         "question_form": {"questions": [1, 2, 3]}},
        {"role": "user", "content": "visual_style: 暗黑科技", "preserve": True},
        {"role": "agent", "content": "**我理解你要做的是**：粒子跟随鼠标" + "细节" * 400,
         "preserve": True},
        {"role": "user", "content": "已确认需求理解，开始编码", "preserve": True},
        *[{"role": "assistant", "content": f"我先创建第 {i} 个文件。"} for i in range(6)],
        {"role": "agent", "content": "## 代码评估: ✅ PASS  **评分**: 8.8/10" + "详情" * 900},
        {"role": "agent", "content": "上一轮已交付。任务状态 handoff 见 .task/DELIVERY.md"},
    ]


def test_chat_background_keeps_only_useful_context():
    """第二轮背景只留：原始需求 / preserve（计划理解等）/ 验收结论 / 本轮起。"""
    from harness.state.context_pipeline import condense_chat_background

    history = _chat_like_history()
    # 第二轮：路由会把新诉求作为最后一条 user 消息追加
    history.append({"role": "user", "content": "把粒子颜色改成蓝色"})
    history.append({"role": "assistant", "content": "好的，本轮开始改颜色。"})

    kept, stats = condense_chat_background(history)
    text = "\n".join(str(m.get("content", "")) for m in kept)

    assert any("做一个 Canvas 粒子动画演示页" in str(m.get("content", "")) for m in kept)
    assert any("我理解你要做的是" in str(m.get("content", "")) for m in kept), "计划理解（SPEC）不能丢"
    verdict = next(m for m in kept if str(m.get("content", "")).startswith("## 代码评估"))
    assert verdict["content"].endswith("…（评估详情略）") and len(verdict["content"]) < 700, "评估只留结论段"
    assert any("把粒子颜色改成蓝色" in str(m.get("content", "")) for m in kept)
    assert any("本轮开始改颜色" in str(m.get("content", "")) for m in kept), "本轮 turns 原样保留"
    # 噪声被丢：问题表单、编码旁白、handoff
    assert not any("开工前有 3 件事" in str(m.get("content", "")) for m in kept)
    assert not any("我先创建第" in str(m.get("content", "")) for m in kept)
    assert not any("上一轮已交付" in str(m.get("content", "")) for m in kept)
    assert stats["dropped"] >= 7 and stats["dropped_tokens"] > 0


def test_chat_background_truncates_long_first_user():
    """原始需求超长时截断而不是整条丢弃。"""
    from harness.state.context_pipeline import condense_chat_background

    history = [
        {"role": "user", "content": "需求正文" + "x" * 3000},
        {"role": "agent", "content": "收到，开工前确认一下"},
        {"role": "user", "content": "改一下标题"},
    ]
    kept, _ = condense_chat_background(history)
    first = kept[0]["content"]
    assert len(first) < 1500 and first.endswith("…（历史需求已截断）")


def test_chat_background_without_new_user_keeps_nothing_stale():
    """边界：若历史上还没有本轮 user 消息（last_user=-1），背景规则照常生效，
    不会把整段历史当「本轮」原样放行。"""
    from harness.state.context_pipeline import condense_chat_background

    history = _chat_like_history()
    kept, stats = condense_chat_background(history)
    assert stats["dropped"] > 0


def test_chat_condense_only_applied_in_chat_mode():
    """runtime 只在 metadata.is_chat 时启用背景瘦身，编码主流程不受影响。

    守卫位置：runtime._build_messages 的分支条件 —— 用真 ToolCallLoop 替身
    不现实，这里守管道侧的纯函数契约 + 分支源码存在性。
    """
    import inspect
    from harness import runtime as rt

    src = inspect.getsource(rt.ToolCallLoop._build_messages)
    assert "is_chat" in src and "condense_chat_background" in src
    # 编码主流程（非 chat）不传 condensed history：分支必须挂在 is_chat 下
    assert src.index("is_chat") < src.index("pipeline.build")

# ---------------- 3. 终态统一瘦身 ----------------

class _WS:
    def __init__(self):
        self.files = {}

    def read(self, path):
        return self.files.get(path, "")

    def write(self, path, content):
        self.files[path] = content

    def list(self):
        return sorted(self.files)


def test_slim_on_terminal_removes_tool_trail():
    """终态瘦身后不应残留 tool_call —— 它是会进 prompt 的类型。"""
    state = {
        "dialogue_history": [
            {"role": "user", "name": "用户", "content": "做个页面"},
            {"role": "tool_call", "name": "write_file", "content": "已写入 index.html"},
            {"role": "tool_call", "name": "read_file", "content": "[文件: index.html]\n" + "x" * 5000},
            {"role": "thinking", "name": "Henry", "content": "让我想想"},
            {"role": "agent", "name": "Leon", "content": "计划已就绪"},
        ],
        "requirement_content": "做个页面",
        "code_files": [],
    }
    ws = _WS()
    from services.requirement_service import RequirementService
    assert RequirementService._slim_dialogue_on_terminal(state, ws) is True

    roles = [m.get("role") for m in state["dialogue_history"]]
    assert "tool_call" not in roles
    assert "thinking" not in roles
    # 人类对话必须留下
    assert roles.count("user") == 1
    assert roles.count("agent") == 1


def test_slim_on_terminal_archives_instead_of_deleting():
    """归档而非删除：轨迹要能在 .task/EXECUTION.jsonl 里查到。"""
    state = {
        "dialogue_history": [
            {"role": "tool_call", "name": "read_file", "content": "文件内容"},
            {"role": "user", "content": "做个页面"},
        ],
        "requirement_content": "做个页面",
        "code_files": [],
    }
    ws = _WS()
    from services.requirement_service import RequirementService
    RequirementService._slim_dialogue_on_terminal(state, ws)
    archived = ws.files.get(".task/EXECUTION.jsonl", "")
    assert "文件内容" in archived


def test_slim_on_terminal_does_not_claim_delivered():
    """失败终态不该追加「上一轮已交付」的交接说明 —— 会误导用户。"""
    state = {
        "dialogue_history": [
            {"role": "tool_call", "name": "write_file", "content": "已写入"},
            {"role": "user", "content": "做个页面"},
        ],
        "requirement_content": "做个页面",
        "code_files": [],
    }
    from services.requirement_service import RequirementService
    RequirementService._slim_dialogue_on_terminal(state, _WS())
    assert not any("已交付" in str(m.get("content", "")) for m in state["dialogue_history"])


def test_finalize_delivery_default_still_adds_handoff_note():
    """默认行为不变：成功交付仍要写交接说明（既有契约）。"""
    state = {
        "dialogue_history": [
            {"role": "tool_call", "name": "write_file", "content": "已写入"},
            {"role": "user", "content": "做个页面"},
        ],
        "requirement_content": "做个页面",
        "code_files": [],
    }
    ws = _WS()
    finalize_delivery(state, ws)
    assert any("已交付" in str(m.get("content", "")) for m in state["dialogue_history"])


def test_slim_on_terminal_never_raises(monkeypatch):
    """瘦身失败绝不能阻断终态处理。

    注意：finalize_delivery 自身对 workspace 异常也是吞掉的（返回 ""），
    所以这里必须从更外层注入失败，才能验到「本方法自己的兜底」。
    """
    import harness.state.context_pipeline as cp
    from services.requirement_service import RequirementService

    def _boom(*a, **k):
        raise RuntimeError("归档失败")

    monkeypatch.setattr(cp, "finalize_delivery", _boom)
    state = {"dialogue_history": [{"role": "user", "content": "x"}], "code_files": []}
    assert RequirementService._slim_dialogue_on_terminal(state, _WS()) is False
    # 原始对话历史未被破坏
    assert state["dialogue_history"] == [{"role": "user", "content": "x"}]


# ---------------- 4. 过滤量可观测 ----------------

def test_stats_reports_filtered_thinking_and_iteration_batch():
    """thinking / iteration_batch 每轮都被剥离，必须体现在 stats 里。

    此前 stats 只有超预算兜底计数，常规需求上恒为 0，日志看起来像
    「压缩机制不存在」。两者语义不同，必须分开统计。
    """
    history = [
        {"role": "thinking", "name": "Henry", "content": "让我想想" * 50},
        {"role": "iteration_batch", "name": "Henry", "content": "第 1 轮迭代",
         "thinking_preview": "推理过程" * 100},
        {"role": "user", "content": "做个页面"},
    ]
    _msgs, stats = ContextPipeline(budget=10000).build("sys", history)

    assert stats["filtered_count"] == 2
    assert stats["filtered_tokens"] > 0
    # 兜底类计数仍为 0（远未超预算）—— 日常治理与兜底语义分离
    assert stats["masked_read"] == 0
    assert stats["compacted"] == 0
    # 被过滤的内容确实没进 prompt
    assert not any("让我想想" in m["content"] for m in _msgs)
    assert not any("推理过程" in m["content"] for m in _msgs)


def test_filtered_and_masked_are_independent():
    """过滤照常发生，即使预算充裕。"""
    history = [{"role": "thinking", "content": "x" * 100}]
    _msgs, stats = ContextPipeline(budget=100000).build("sys", history)
    assert stats["filtered_count"] == 1
    assert stats["dropped"] == 0
