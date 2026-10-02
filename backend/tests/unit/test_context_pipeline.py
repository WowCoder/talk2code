# -*- coding: utf-8 -*-
"""ContextPipeline 单元测试（TDD：对应 docs/design/context-pipeline-v2.md 测试矩阵 P1–P8/P14）。"""

import pytest

from harness.state.context_pipeline import ContextPipeline, estimate_tokens, SINGLE_RESULT_LIMIT


def make_read(filename, start, end, total, body):
    header = f"[文件: {filename} (行 {start}-{end} / 共 {total} 行)]\n\n"
    return {"role": "tool_call", "name": "read_file", "content": header + body}


def make_tool(name, content, preserve=False):
    return {"role": "tool_call", "name": name, "content": content, "preserve": preserve}


def make_chat(role, content, preserve=False):
    return {"role": role, "content": content, "preserve": preserve}


def make_system_hidden(content):
    return {"role": "system", "content": content}


def fake_ref_store(records):
    def _store(name, content):
        records.append((name, content))
        return f".task/refs/{name}"
    return _store


def fake_summary(text):
    return "SUMMARY(" + str(len(text)) + ")"


# ---------------- T0 基线 ----------------

def test_import_and_instantiate():
    p = ContextPipeline()
    assert p.budget == 24000
    assert p.keep_recent == 3


# ---------------- P1 / P2 角色通道 ----------------

def test_system_hidden_enters_messages():
    history = [make_system_hidden("你是 TASK_STATE.md 的维护者")]
    msgs, _ = ContextPipeline(budget=10000).build("sys", history)
    assert any("[系统提示]" in m["content"] for m in msgs)


def test_preserve_message_passed_through():
    history = [make_chat("user", "重要约束：不要删数据库", preserve=True)]
    msgs, _ = ContextPipeline(budget=10000).build("sys", history)
    assert any(m["content"] == "重要约束：不要删数据库" for m in msgs)


def test_thinking_and_iteration_batch_dropped():
    history = [
        {"role": "thinking", "content": "内部推理"},
        {"role": "iteration_batch", "content": "batch-meta"},
        make_chat("user", "真实需求"),
    ]
    msgs, _ = ContextPipeline(budget=10000).build("sys", history)
    contents = [m["content"] for m in msgs]
    assert not any("内部推理" in c for c in contents)
    assert not any("batch-meta" in c for c in contents)
    assert any("真实需求" in c for c in contents)


# ---------------- P3 预算内零遮蔽 ----------------

def test_no_mask_within_budget():
    history = [make_read("a.js", 1, 10, 100, "x" * 200) for _ in range(3)]
    msgs, stats = ContextPipeline(budget=24000).build("sys", history)
    assert not any("已省略" in m["content"] for m in msgs)
    assert stats["masked_read"] == 0


# ---------------- P4 时间序从最旧遮蔽 ----------------

def test_mask_oldest_first_when_over_budget():
    reads = [make_read(f"f{i}.js", 1, 100, 100, "y" * 4000) for i in range(6)]
    p = ContextPipeline(budget=3000)
    msgs, stats = p.build("", reads)
    assert stats["masked_read"] == 3
    # 最旧 3 条被遮蔽，最新 3 条保留
    masked = [m for m in msgs if "已省略" in m["content"]]
    kept = [m for m in msgs if "已省略" not in m["content"] and "[工具 read_file" in m["content"]]
    assert len(masked) == 3 and len(kept) == 3


# ---------------- P4a 最近 3 条 read_file 永不遮蔽 ----------------

def test_keep_recent_three_reads():
    reads = [make_read(f"f{i}.js", 1, 100, 100, "y" * 4000) for i in range(8)]
    p = ContextPipeline(budget=3000)
    msgs, stats = p.build("", reads)
    # 最近 3 条必须完整保留
    kept = [m for m in msgs if "[工具 read_file" in m["content"] and "已省略" not in m["content"]]
    assert len(kept) == 3
    assert stats["masked_read"] == 5


# ---------------- P4b write/edit/验证 永不遮蔽 ----------------

def test_excluded_tools_never_masked():
    history = [make_tool("write_file", "created app.js\n" + "z" * 4000)]
    history += [make_read("big.js", 1, 999, 999, "y" * 4000) for _ in range(4)]
    p = ContextPipeline(budget=3000)
    msgs, stats = p.build("", history)
    assert any("created app.js" in m["content"] for m in msgs)
    assert stats["masked_read"] >= 1  # read 被遮，write 不遮


# ---------------- P4c 分段读同文件预算内全保留 ----------------

def test_segmented_same_file_all_kept_within_budget():
    history = [
        make_read("game.js", 1, 200, 648, "a" * 800),
        make_read("game.js", 201, 400, 648, "b" * 800),
        make_read("game.js", 401, 648, 648, "c" * 800),
    ]
    msgs, stats = ContextPipeline(budget=24000).build("sys", history)
    assert stats["masked_read"] == 0
    assert all(f"行 {s}-{e}" in "\n".join(m["content"] for m in msgs)
               for s, e in [(1, 200), (201, 400), (401, 648)])


# ---------------- P4e 占位符带一句话摘要 ----------------

def test_placeholder_carries_one_line_summary():
    body = "export function move() {}\nexport class Snake {}\nconst X = 1\n" + "z" * 4000
    reads = [make_read("game.js", 1, 100, 100, body) for _ in range(4)]
    p = ContextPipeline(budget=3000)
    msgs, _ = p.build("", reads)
    masked = [m for m in msgs if "已省略" in m["content"]]
    assert masked
    assert any("摘要：导出 move/Snake" in m["content"] for m in masked)


# ---------------- P5a 单条 >2000 tok 截断 ----------------

def test_single_result_truncated_at_gate():
    big = "def fn():\n" + ("x = 1\n" * 3000)  # 远超 2000 tok
    reads = [make_read("huge.py", 1, 3000, 3000, big)]
    p = ContextPipeline()
    msgs, _ = p.build("", reads)
    content = msgs[0]["content"]
    assert "已截断" in content
    assert "start_line/end_line" in content
    assert estimate_tokens(content) <= SINGLE_RESULT_LIMIT + 50


# ---------------- P5b 非文件大结果落盘 refs ----------------

def test_nonfile_large_result_offloaded_to_refs():
    # 多个非文件结果各自 < 单条上限（L3a 不截断），但合计超预算 → 最旧被遮蔽并落盘
    records = []
    store = fake_ref_store(records)
    history = [make_tool("run_command", "pytest\n" + "FAILED\n" * 600) for _ in range(4)]
    p = ContextPipeline(budget=3000, ref_store=store)
    msgs, stats = p.build("", history)
    assert stats["offloaded"] >= 1
    assert records
    assert any(".task/refs/" in m["content"] for m in msgs)


# ---------------- P6 全历史预算内零丢弃 ----------------

def test_full_history_no_drop_within_budget():
    history = [make_chat("user", f"msg{i}") for i in range(10)]
    history += [make_read("a.js", 1, 10, 10, "x" * 100) for _ in range(3)]
    msgs, stats = ContextPipeline(budget=24000).build("sys", history)
    assert all(f"msg{i}" in "\n".join(m["content"] for m in msgs) for i in range(10))
    assert stats["dropped"] == 0


# ---------------- P7 L5 摘要兜底 ----------------

def test_l5_summary_when_still_over_budget():
    history = [make_chat("user", "x" * 3000) for _ in range(10)]  # 无工具可遮
    p = ContextPipeline(budget=4000, llm_summary=fake_summary)
    msgs, stats = p.build("", history)
    assert stats["compacted"] >= 1
    assert any("[历史摘要]" in m["content"] for m in msgs)


# ---------------- P8 L5 异常回退 ----------------

def test_l5_fallback_on_summary_error():
    history = [make_chat("user", "x" * 3000) for _ in range(10)]

    def boom(text):
        raise RuntimeError("llm down")

    p = ContextPipeline(budget=4000, llm_summary=boom)
    # 不应抛异常
    msgs, stats = p.build("", history)
    assert stats["dropped"] >= 1
    assert len(msgs) > 0


# ---------------- P14 交付边界：新轮不含旧工具轨迹 ----------------

def test_delivery_boundary_next_run_has_no_tool_trail():
    # 新轮初始上下文只含 handoff 笔记 + 新需求，不含上一轮工具结果
    handoff = make_chat("user", "DELIVERY.md: 贪吃蛇已完成，Canvas 渲染，localStorage 存分")
    new_req = make_chat("user", "再加一个分享按钮")
    msgs, stats = ContextPipeline(budget=24000).build("sys", [handoff, new_req])
    assert not any("[工具" in m["content"] and "已省略" in m["content"] for m in msgs)
    assert any("再加一个分享按钮" in m["content"] for m in msgs)
    assert stats["masked_read"] == 0


# =====================================================================
# T4 / T5 / T9 新增测试：L2 TASK_STATE.md、staleness、L1 索引、交付折叠
# =====================================================================

import json

from harness.state.context_pipeline import (
    note_task_activity, evaluate_staleness, finalize_delivery,
)
from harness.tools.task_state_tools import _merge_section, _is_valid_section
from harness.tools.registry import create_tool_registry


# ---------------- T4 / P9：update_task_notes 工具与 TASK_STATE.md ----------------

def test_merge_section_appends_when_missing():
    out = _merge_section("", "目标", "做一个贪吃蛇")
    assert "## 目标" in out
    assert "做一个贪吃蛇" in out


def test_merge_section_overwrites_existing():
    existing = "## 目标\n旧目标\n## 文件状态\n- a.js"
    out = _merge_section(existing, "目标", "新目标")
    assert out.count("## 目标") == 1
    assert "新目标" in out
    assert "旧目标" not in out
    assert "## 文件状态" in out  # 其他小节保留


def test_is_valid_section_rejects_empty():
    assert _is_valid_section("") is False
    assert _is_valid_section("   ") is False
    assert _is_valid_section("下一步") is True


def test_update_task_notes_registered():
    registry = create_tool_registry()
    assert "update_task_notes" in registry.list_tools()
    assert registry.get_permission("update_task_notes") == "write"


class _FakeWorkspace:
    """内存版 WorkspaceFS，供工具/交付测试用。"""

    def __init__(self, files=None):
        self._files = dict(files or {})

    def list(self):
        return list(self._files.keys())

    def read(self, name):
        if name not in self._files:
            raise FileNotFoundError(name)
        return self._files[name]

    def write(self, name, content):
        self._files[name] = content


def test_update_task_notes_handler_writes_file():
    from harness.tools.task_state_tools import UpdateTaskNotesHandler
    ws = _FakeWorkspace()
    handler = UpdateTaskNotesHandler(workspace=ws)
    res = handler.execute({"section": "目标", "content": "贪吃蛇小游戏"})
    assert res.success
    assert ".task/TASK_STATE.md" in ws.list()
    assert "贪吃蛇小游戏" in ws.read(".task/TASK_STATE.md")


# ---------------- T4 / P10：staleness 检测 ----------------

def test_staleness_reminder_after_two_rounds():
    state = {}
    # 第 1 轮：文件变更，无 notes
    note_task_activity(state, "write_file", True)
    r1 = evaluate_staleness(state)
    assert r1 == ""  # 第 1 轮不提醒
    # 第 2 轮：再次文件变更，仍无 notes
    note_task_activity(state, "edit_file", True)
    r2 = evaluate_staleness(state)
    assert r2 != ""  # 连续 2 轮 → 提醒
    assert "update_task_notes" in r2


def test_staleness_triggers_on_alternating_write_read_rounds():
    """req 147 修正：写/读交替时，旧判据（连续轮）会被只读轮重置为 0 → 7 轮 0 触发。

    新判据是「累计未记录的文件变更次数」，中间夹只读轮不该重置。
    """
    state = {}
    note_task_activity(state, "write_file", True)   # 轮1：写
    assert evaluate_staleness(state) == ""          # 累计 1，不提醒
    assert evaluate_staleness(state) == ""          # 轮2：只读（无变更）→ 累计仍 1
    note_task_activity(state, "write_file", True)   # 轮3：又写 → 累计 2
    r = evaluate_staleness(state)
    assert r != "", "累计 2 次未记录变更必须提醒，不能因中间只读轮漏掉"
    assert "update_task_notes" in r
    # 提醒过一次后清零，避免每轮刷屏
    assert evaluate_staleness(state) == ""


def test_staleness_reset_when_notes_updated():
    state = {}
    note_task_activity(state, "write_file", True)
    evaluate_staleness(state)
    note_task_activity(state, "write_file", True)
    note_task_activity(state, "update_task_notes", True)  # 本轮更新了 notes
    r = evaluate_staleness(state)
    assert r == ""  # 更新 notes → 不提醒


# ---------------- T5 / P11：head 稳定前缀（相同输入确定性） ----------------

def test_head_stable_across_rounds():
    from harness.runtime import ToolCallLoop
    from unittest.mock import Mock
    ws = Mock()
    ws.list.return_value = ["index.html", "js/game.js", "css/style.css"]
    loop = ToolCallLoop(workspace=ws)
    state = {"requirement_content": "做一个贪吃蛇小游戏"}
    p1 = loop._build_system_prompt(state)
    p2 = loop._build_system_prompt(state)
    assert p1 == p2  # 相同输入 → 字节一致（稳定前缀命中 KV-cache）
    assert "js/game.js" in p1  # 文件索引已注入


# ---------------- T5 / P11：文件索引 = 文件名 + 一行结构摘要（每组一行，不含正文） ----------------

def test_file_summaries_one_line_index():
    """对齐 §3.A L142：索引须给出**结构线索**，否则 LLM 只能回读文件找结构。"""
    from harness.runtime import ToolCallLoop
    ws = _FakeWorkspace({
        "index.html": ('<html><head><title>贪吃蛇</title></head>'
                       '<body><canvas id="game"></canvas></body></html>'),
        "js/game.js": ("function init() {}\n"
                       "function draw() {}\n"
                       "document.getElementById('game');\n"),
    })
    loop = ToolCallLoop(workspace=ws)
    text = loop._build_file_summaries(["index.html", "js/game.js"])
    # 每个文件恰好一行，且以「- 文件名:」开头
    lines = text.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("- index.html:")
    assert lines[1].startswith("- js/game.js:")
    # 结构线索进入索引（title / 元素 id / 函数名 / DOM 引用）
    assert "贪吃蛇" in text
    assert 'game' in text
    assert "init" in text and "draw" in text
    # 不含正文：完整源码不会出现在索引里
    assert "<canvas" not in text
    assert "function init() {}" not in text


# ---------------- T9 / P14：finalize_delivery 写 handoff + 归档（非删除）工具轨迹 ----------------

def test_finalize_delivery_writes_handoff_and_archives_trail():
    ws = _FakeWorkspace()
    state = {
        "requirement_content": "做一个贪吃蛇",
        "plan": {"file_structure": ["index.html", "js/game.js"]},
        "dialogue_history": [
            {"role": "user", "content": "做贪吃蛇"},
            {"role": "tool_call", "name": "read_file", "content": "[文件: a]..."},
            {"role": "system", "content": "隐藏系统提示", "hidden": True},
            {"role": "agent", "content": "已完成"},
        ],
    }
    path = finalize_delivery(state, ws)
    assert path == ".task/DELIVERY.md"
    assert ".task/DELIVERY.md" in ws.list()
    handoff = ws.read(".task/DELIVERY.md")
    assert "做一个贪吃蛇" in handoff
    assert "js/game.js" in handoff  # 计划文件结构带出
    # 工具轨迹被**归档**到 .task/EXECUTION.jsonl（而非当场删除），供事后排查
    assert ".task/EXECUTION.jsonl" in ws.list()
    archived = [json.loads(l) for l in ws.read(".task/EXECUTION.jsonl").strip().split("\n")]
    archived_roles = [m["role"] for m in archived]
    assert "tool_call" in archived_roles
    assert "system" in archived_roles
    assert any(m.get("name") == "read_file" for m in archived)
    # 但 dialogue_history 仍只保留人类可读对话（交付后上下文轻量）
    roles = [m["role"] for m in state["dialogue_history"]]
    assert "tool_call" not in roles
    assert "system" not in roles
    assert "user" in roles and "agent" in roles
    # handoff 起点消息已注入，且**以 TL 身份**注入（不是伪装成用户消息）
    from harness.agent_names import TL_NAME
    handoff_msgs = [
        m for m in state["dialogue_history"]
        if "上一轮已交付" in str(m.get("content", ""))
    ]
    assert len(handoff_msgs) == 1
    assert handoff_msgs[0]["role"] == "agent"
    assert handoff_msgs[0]["name"] == TL_NAME


# ---------------- L3b 遮蔽策略：失效的 read 优先遮蔽 ----------------

def test_superseded_reads_detects_stale_content():
    """read 之后同文件被 write/edit 改过 → 该 read 内容过期，应被识别出来。"""
    from harness.state.context_pipeline import ContextPipeline
    p = ContextPipeline()
    staged = [
        {"kind": "tool", "name": "read_file",
         "content": "[文件: a.js (共 10 行)]\n\nbody", "preserve": False, "arguments": {}},
        {"kind": "tool", "name": "write_file",
         "content": "已创建 a.js", "preserve": False, "arguments": {"filename": "a.js"}},
        {"kind": "tool", "name": "read_file",
         "content": "[文件: b.js (共 5 行)]\n\nbody", "preserve": False, "arguments": {}},
        {"kind": "tool", "name": "edit_file",
         "content": "已更新 b.js", "preserve": False, "arguments": {"filename": "b.js"}},
    ]
    sup = p._superseded_reads(staged)
    assert sup.get(0) == "write_file"   # a.js 读完被 write 覆盖
    assert sup.get(2) == "edit_file"    # b.js 读完被 edit 覆盖


def test_stale_read_masked_before_older_valid_read():
    """已失效的 read 应优先于「更旧但仍有效」的 read 被遮蔽。

    传统 LRU 会先遮最旧的（b.js），但 b.js 的内容仍然有效；
    a.js 的内容已被覆盖，遮掉才是零信息损失。
    """
    from harness.state.context_pipeline import ContextPipeline
    big = "x" * 8000  # 每条约 2000 token

    def _read(name):
        return {"role": "tool_call", "name": "read_file",
                "content": f"[文件: {name} (共 100 行)]\n\nMARK-{name}-{big}"}

    history = [
        _read("b.js"),                                   # 最旧，但内容仍有效
        _read("a.js"),                                   # 随后被 write 覆盖 → 失效
        _read("c.js"), _read("d.js"), _read("e.js"),     # 最近 3 条，受 keep_recent 保护
        {"role": "tool_call", "name": "write_file",
         "content": f"已创建 a.js\n\n--- 文件内容预览 ---\n{big}",
         "arguments": {"filename": "a.js"}},
    ]
    # 预算只够遮 1 条（5 条 read + 1 条 write ≈ 12000 token，遮 1 条即达标）。
    # history_soft_budget 显式设为 budget：本测试守的是「失效 read 优先于更旧的
    # 有效 read」这个排序语义，软预算（6000）会额外把 b.js 也遮掉，稀释掉对照。
    p = ContextPipeline(budget=11000, single_result_limit=100000,
                        history_soft_budget=11000)
    messages, stats = p.build(head_content="", history=history)

    assert stats["masked_read"] == 1
    joined = " ".join(m["content"] for m in messages)
    # 被遮的是**失效**的 a.js（而非更旧但有效的 b.js）
    assert "MARK-a.js" not in joined
    assert "该内容已被后续 write_file 修改而过期" in joined
    # 更旧但内容仍有效的 b.js 保住正文
    assert "MARK-b.js" in joined



# ---------------- 交付折叠必须保留 iteration_batch（需求 183） ----------------

def test_finalize_delivery_keeps_iteration_batch():
    """coder 的 assistant 自述带 hidden=True，编码过程完全由 iteration_batch 承载。
    折叠时再把它丢掉，详情页「开发工程师」整段编码过程就会消失（req 183 实测）。"""
    from harness.state.context_pipeline import finalize_delivery

    class WS:
        def write(self, path, content):
            return True

    state = {
        "dialogue_history": [
            {"role": "user", "content": "做一个贪吃龙"},
            {"role": "assistant", "name": "Henry", "content": "我先写样式", "hidden": True},
            {"role": "iteration_batch", "name": "Henry", "content": "第 1 轮迭代 — 1 个操作",
             "tools": [{"name": "write_file", "readable": "写入 css/style.css",
                        "arguments": {"filename": "css/style.css", "content": "x" * 9000}}]},
            {"role": "tool_call", "name": "read_file", "content": "正文"},
        ]
    }
    finalize_delivery(state, WS())
    roles = [m.get("role") for m in state["dialogue_history"]]
    assert "iteration_batch" in roles, "迭代卡片必须保留"
    assert "tool_call" not in roles, "工具轨迹仍应被归档移除"


def test_finalize_delivery_slims_big_arguments():
    """迭代卡片的 arguments 带着 write_file 全文（实测 15,605 字符），
    原样落库会把 dialogue_history 撑到数 MB，必须瘦身。"""
    from harness.state.context_pipeline import finalize_delivery

    class WS:
        def write(self, path, content):
            return True

    big = "a" * 20000
    state = {
        "dialogue_history": [
            {"role": "iteration_batch", "name": "Henry", "content": "第 1 轮",
             "tools": [{"name": "write_file", "readable": "写入 js/game.js",
                        "arguments": {"filename": "js/game.js", "content": big}}]},
        ]
    }
    finalize_delivery(state, WS())
    batch = state["dialogue_history"][0]
    args = batch["tools"][0]["arguments"]
    assert "content" not in args, "正文不得落库"
    assert args.get("content_chars") == 20000
    assert args.get("filename") == "js/game.js"
