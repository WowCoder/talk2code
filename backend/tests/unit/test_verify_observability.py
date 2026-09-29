# -*- coding: utf-8 -*-
"""验证阶段可观测性：进度分配表、实时卡片、落库摘要的防回归守卫。

背景（req 207 验收反馈）
-----------------------
1. 进度百分比散在四处各写各的，拼起来**非单调**：编码 `20 + 75*ratio` 能冲到 95，
   验证却从 80 重来（每次需求都倒退 15 个点）；修复节点 85 又低于验证 90。
2. 验证阶段 90%（通用冒烟）之后还有 类名契约 / DoD / 视觉证据 / 深度评估 四步，
   **零进度推送** —— 实测约 2 分钟界面停在「正在做通用交互冒烟测试」不动。
3. 验证结论只有一条**不落库**的 evaluator_result 事件：刷新页面后
   "质量工程师做过什么"完全查不到。

这些用例盯的就是上面三件事不再回归。
"""

import re
from pathlib import Path

import pytest

from harness.observability import progress_plan as pp
from harness.observability.verify_trace import (
    VerifyTrace, build_summary_message, publish_summary,
)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (_backend_root() / rel_path).read_text(encoding="utf-8")


def _is_non_decreasing(seq) -> bool:
    return all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))


# ==================== 进度分配表 ====================


def test_single_pass_progress_is_monotonic():
    """一轮之内（编码 → 验证 → 修复）进度绝不能回退。"""
    for r in range(pp._MAX_ROUND + 1):
        seq = pp.single_pass_sequence(r)
        assert _is_non_decreasing(seq), f"第 {r} 轮进度回退: {seq}"


def test_full_sequence_is_monotonic_after_frontend_clamp():
    """多轮跑批：后端原始序列可能因 coder 重入而回落，前端钳制后必须单调。"""
    seq = pp.full_sequence(clamp=True)
    assert _is_non_decreasing(seq), f"钳制后仍回退: {seq}"
    assert seq[-1] == pp.DONE


def test_raw_full_sequence_documented_as_non_monotonic():
    """原始序列的回落是**已知且有意**的（coder 重入）。

    这条用例把"回落确实只发生在编码带"钉住：一旦有人在别处也引入了回退，
    这里的断言会失败，提醒他去看前端钳制是不是唯一的兜底。
    """
    raw = pp.full_sequence(clamp=False)
    drops = [i for i in range(len(raw) - 1) if raw[i] > raw[i + 1]]
    assert drops, "原始序列居然单调了，说明轮次右移逻辑被改掉，请复核本用例前提"
    band_starts = {pp.coding_percent(0.0, r) for r in range(pp._MAX_ROUND + 1)}
    for i in drops:
        # 回落的下一个值必须是**某一轮**编码带的起点（coder 重入），不是别处
        assert raw[i + 1] in band_starts, (
            f"下标 {i} 的回落不是编码带重入: {raw[i]} → {raw[i + 1]}"
        )


def test_coding_band_never_reaches_verify_band():
    """编码带上界必须低于同轮验证带起点 —— 这是"验证一亮相就回退"的直接病根。"""
    for r in range(pp._MAX_ROUND + 1):
        assert pp.coding_percent(1.0, r) < pp.verify_percent("preview", r), (
            f"第 {r} 轮编码上界已越过验证起点"
        )


def test_verify_step_offsets_strictly_increase():
    """7 个子步骤的轮内偏移必须严格递增，否则进度条会原地不动一段时间。"""
    offsets = [pp.STEP_OFFSET[key] for key in pp.STEP_ORDER]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets), f"存在重复偏移: {offsets}"


def test_repair_never_below_this_round_verify():
    """修复灯亮起时不能比验证收尾值低（历史 bug：repair=85 < verify=90）。"""
    for r in range(pp._MAX_ROUND + 1):
        assert pp.repair_percent(r) >= pp.verify_percent(pp.STEP_ORDER[-1], r)


def test_node_percent_coder_is_band_start():
    """coder 节点级进度取带**起点**：进节点后 _push_activity 会把它推到上界，
    这里若直接给上界，第一步动作就把进度条拉回去了。"""
    for r in range(pp._MAX_ROUND + 1):
        assert pp.node_percent("coder", r) == pp.coding_percent(0.0, r)


def test_round_index_reads_repair_count():
    assert pp.round_index({"metadata": {"repair_count": 1}}) == 1
    # 越界收敛，不无限逼近 100
    assert pp.round_index({"metadata": {"repair_count": 99}}) == pp._MAX_ROUND
    assert pp.round_index({}) == 0
    assert pp.round_index(None) == 0
    assert pp.round_index({"metadata": {"repair_count": "bad"}}) == 0


# ==================== 验证阶段埋点（90% 之后不再静默） ====================


@pytest.mark.parametrize("step", ["contract", "dod", "vision", "evaluate"])
def test_verify_node_instruments_every_late_step(step):
    """冒烟之后的四个步骤必须各有进度推送（此前全静默，实测卡 2 分钟）。"""
    src = _read("harness/instructions/nodes.py")
    assert f'_verify_progress("{step}")' in src, f"验证阶段缺少 {step} 的进度埋点"


def test_verify_progress_takes_step_key_not_bare_number():
    """`_verify_progress` 必须只接受步骤 key：旧的 `_verify_progress(80, "...")`
    写法就是"百分比散落各处"的来源，不允许再出现。"""
    src = _read("harness/instructions/nodes.py")
    assert not re.search(r"_verify_progress\(\s*\d", src), (
        "仍有 _verify_progress(裸数字, ...) 调用，应改为 _verify_progress(\"<step>\")"
    )
    assert "def _verify_progress(step: str)" in src


def test_old_progress_tables_are_gone():
    """旧的三处硬编码进度必须已收口到 progress_plan。"""
    assert "_progress_map" not in _read("services/requirement_service.py")
    hooks = _read("harness/constraints/progress_hooks.py")
    assert "75 * completed" not in hooks
    assert "coding_percent" in hooks
    runtime = _read("harness/runtime.py")
    assert "75 * (iteration + 1)" not in runtime
    assert "coding_percent" in runtime


# ==================== 实时卡片 + 落库摘要 ====================


class FakeSSE:
    """记录 verify_start / verify_step / qa_summary 的假 reporter。"""

    def __init__(self):
        self.calls = []

    def verify_start(self, req_id, payload):
        self.calls.append(("verify_start", req_id, payload))

    def verify_step(self, req_id, payload):
        self.calls.append(("verify_step", req_id, payload))

    def qa_summary(self, req_id, payload):
        self.calls.append(("qa_summary", req_id, payload))

    def events(self, name):
        return [c for c in self.calls if c[0] == name]


def _state(**meta):
    return {"requirement_id": 42, "metadata": dict(meta)}


def test_trace_records_before_begin_without_emitting():
    """建卡前的步骤只记账不推送 —— 否则前端会收到指向"还不存在的卡"的事件。"""
    sse = FakeSSE()
    trace = VerifyTrace(_state(), sse=sse)
    trace.done("preview", "0 个运行时错误")
    trace.done("ac", "2/3 通过")
    assert sse.calls == []

    trace.begin()
    starts = sse.events("verify_start")
    assert len(starts) == 1
    steps = {s["key"]: s for s in starts[0][2]["steps"]}
    # 已发生的两步随卡一起给，卡片一出现就有内容
    assert steps["preview"]["status"] == "done"
    assert steps["ac"]["detail"] == "2/3 通过"
    assert steps["smoke"]["status"] == "pending"


def test_trace_emits_steps_after_begin():
    sse = FakeSSE()
    trace = VerifyTrace(_state(), sse=sse)
    trace.begin()
    trace.done("contract", "0 处断裂 · 1 条警告")
    steps = sse.events("verify_step")
    assert len(steps) == 1
    assert steps[0][2]["key"] == "contract"
    assert steps[0][2]["status"] == "done"


def test_trace_round_follows_repair_count():
    sse = FakeSSE()
    trace = VerifyTrace(_state(repair_count=1), sse=sse)
    assert trace.round == 1
    trace.begin()
    assert sse.events("verify_start")[0][2]["round"] == 1
    # 轮次右移必须真的作用到百分比上，否则"修复 → 再验证"仍会回退：
    # 下一轮验证的**收尾值**要严格高于上一轮修复值（起点持平是允许的）。
    assert pp.verify_percent(pp.STEP_ORDER[-1], 1) > pp.repair_percent(0)


def test_trace_round_unclamped_beyond_max_round():
    """封顶值只用于进度计算，卡片标签必须显示真实轮次。

    req 208 复盘：第 3、4 轮验证的卡片都显示「第 3 轮」（封顶到 _MAX_ROUND=2
    后 round+1=3），用户以为修复卡住。改后 VerifyTrace.round 用真实轮次，
    而 verify_percent/coding_percent/repair_percent 仍用封顶值——突破 100
    没意义，把语义和算法分开。
    """
    over = pp._MAX_ROUND + 2  # 故意超 _MAX_ROUND
    state = _state(repair_count=over)
    # 进度计算封顶（防止突破 100）
    assert pp.round_index(state) == pp._MAX_ROUND
    # 卡标签用真实值
    assert pp.actual_round(state) == over
    trace = VerifyTrace(state, sse=FakeSSE())
    assert trace.round == over
    card = trace.to_card(verdict="NEEDS_WORK")
    assert card["round"] == over  # 卡片头部就是真实轮次


def test_trace_card_shape():
    trace = VerifyTrace(_state(), sse=FakeSSE())
    trace.begin()
    trace.done("preview", "无错误")
    trace.done("ac", "1/3 通过")
    trace.done("smoke", "4/5 项通过")
    trace.failed("evaluate", "2 条待修")
    card = trace.to_card(
        verdict="NEEDS_WORK",
        score=5.5,
        findings=[
            {"severity": "critical"}, {"severity": "critical"}, {"severity": "major"},
        ],
        ac_results=[
            {"ac_id": "AC-1", "passed": True},
            {"ac_id": "AC-2", "passed": False, "failures": ["x"]},
            {"ac_id": "AC-3", "passed": False, "harness_errors": ["timeout"], "failures": ["y"]},
        ],
        smoke_result={"available": True, "checks": {"page_loads": True}, "defects": [1, 2]},
        browser_result={"errors": [{"message": "boom"}]},
    )
    assert card["verdict"] == "NEEDS_WORK"
    assert card["ac"] == {"passed": 1, "total": 3}
    assert card["smoke"]["defect_count"] == 2
    assert card["browser_errors"] == 1
    assert card["severity"] == {"critical": 2, "major": 1}
    assert card["defect_count"] == 3
    assert card["start_ts"] and card["end_ts"]
    assert [s["key"] for s in card["steps"]] == list(pp.STEP_ORDER)


def test_trace_ignores_unknown_step():
    trace = VerifyTrace(_state(), sse=FakeSSE())
    trace.begin()
    trace.done("not-a-step", "x")  # 不应抛异常
    assert all(s["key"] in pp.STEP_ORDER for s in trace.to_card()["steps"])


def test_summary_message_is_visible_and_preserved():
    """摘要必须是**可见**消息：它就是"质量工程师做过什么"的唯一持久记录。"""
    trace = VerifyTrace(_state(), sse=FakeSSE())
    trace.done("smoke", "5/5 项通过")
    card = trace.to_card(verdict="PASS", score=8.0, ac_results=[{"ac_id": "AC-1", "passed": True}])
    msg = build_summary_message(card, "Catherine（质量工程师）")
    assert msg["role"] == "qa_summary"
    assert msg["preserve"] is True
    assert not msg.get("hidden"), "摘要卡被标了 hidden，用户又看不到质量工程师做了什么"
    assert msg["qa_summary"]["steps"], "摘要卡必须内嵌完整步骤"
    assert "PASS" in msg["content"]


def test_publish_summary_is_silent_when_sender_missing():
    """老 reporter / 无 reporter 时不能抛异常打断验证流程。"""
    publish_summary(None, 1, {"qa_summary": {}})
    publish_summary(object(), 1, {"qa_summary": {}})
    sse = FakeSSE()
    publish_summary(sse, 7, {"role": "qa_summary"})
    assert len(sse.events("qa_summary")) == 1


def test_sse_reporter_exposes_new_events():
    """reporter 必须真的有这三个方法，且事件名与前端监听一致。"""
    from harness.observability.sse_reporter import SSEReporter

    class Recorder:
        def __init__(self):
            self.sent = []

        def broadcast(self, req_id, msg):
            self.sent.append((req_id, msg))

    reporter = SSEReporter(Recorder())
    reporter.verify_start(1, {"steps": []})
    reporter.verify_step(1, {"key": "smoke"})
    reporter.qa_summary(1, {"role": "qa_summary"})
    blob = " ".join(m for _r, m in reporter.sse.sent)
    for evt in ("verify_start", "verify_step", "qa_summary"):
        assert f"event: {evt}" in blob, f"缺少 SSE 事件 {evt}: {blob[:200]}"


# ==================== 前端侧守卫 ====================


def _frontend(rel_path: str) -> str:
    root = _backend_root().parent / "frontend-vue" / "src"
    return (root / rel_path).read_text(encoding="utf-8")


def test_frontend_listens_to_new_events():
    sse = _frontend("composables/useSSE.ts")
    for evt in ("verify_start", "verify_step", "qa_summary"):
        assert f"addEventListener('{evt}'" in sse, f"前端未监听 {evt}"


def test_frontend_clamps_progress_never_decreasing():
    """后端可能按轮内位置推更小的值（有意），前端必须取历史最大值。"""
    sse = _frontend("composables/useSSE.ts")
    assert "Math.max(prevPercent" in sse


def test_frontend_renders_summary_and_live_card_with_one_component():
    msg = _frontend("components/detail/DialogueMessage.vue")
    assert "VerifyStepsCard" in msg
    assert "msg.role === 'qa_summary' || msg.role === 'verify_steps'" in msg
    # 实时卡与落库卡共用组件 —— 两条通道的渲染分叉正是"刷新后长得不一样"的来源
    card = _frontend("components/detail/VerifyStepsCard.vue")
    assert "qa_summary" in card and "verify_steps" in card


def test_store_drops_live_card_when_summary_arrives():
    store = _frontend("stores/requirement.ts")
    assert "dropLiveVerifyCard" in store
    assert "startVerifyTrace" in store and "updateVerifyStep" in store
    # 入列 qa_summary 时必须撤掉临时 live 卡，否则同一件事显示两遍
    assert re.search(r"role === 'qa_summary'\)\s*dropLiveVerifyCard\(\)", store), (
        "qa_summary 入列时没有撤掉 live 验证卡"
    )
