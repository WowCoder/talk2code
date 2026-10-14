# -*- coding: utf-8 -*-
"""Chat 修改轮死锁回归（线上 req 74A9W0HSDEFM2EYDH58E，2026-10-14）。

死锁机制（生产实测，4 轮 / 12s / 15 次 read_file / 0 写入 → max_iterations）：
  1. L2.5 软预算遮蔽把 read_file 正文换成占位符，占位符还邀请「重新读取」；
  2. 同内容重读熔断又以「内容已在上方工具结果里」为由拦截重读 ——
     而上方其实只剩占位符，这句是假话；
  3. edit_file 的 SEARCH 块要求逐字符精确匹配 → 模型拿不到正文就改不了，
     chat 预算（MAX_ITERATIONS=4）烧尽 → failed。

回归覆盖：
  A. runtime._same_content_read_block：被遮蔽文件必须放行重读（恢复路径）；
  B. _build_messages：is_chat 轮放宽软预算（第 1 轮读到的正文活到第 2 轮），
     编码主流程仍用默认 6000；
  C. ContextPipeline.build：被遮蔽的 read 文件名回传（豁免判据的来源）；
  D. 占位符必须带真实文件名（破折号头部格式 + arguments 兜底），
     不得回落成工具名 "read_file"。
"""

from unittest.mock import Mock

from harness.runtime import ToolCallLoop
from harness.state.context_pipeline import (
    CHAT_HISTORY_SOFT_BUDGET,
    HISTORY_SOFT_BUDGET,
    ContextPipeline,
)


def _dash_read(name, body_chars=3200):
    """生产实际格式的 read_file 结果（file_tools.py:132 破折号头部）。

    3200 个 ASCII 字符 ≈ 800 tok，8 条 ≈ 6400 tok：超默认软预算 6000，
    低于 chat 软预算 20000 —— 正好卡在死锁触发区间。
    """
    return {
        "role": "tool_call", "name": "read_file",
        "content": f"[文件: {name} — 全文共 100 行，未截断]\n\n{'y' * body_chars}",
        "arguments": {"filename": name},
    }


def _eight_reads():
    return [_dash_read(f"f{i}.js") for i in range(8)]


def _make_loop(files: dict):
    workspace = Mock()
    workspace.list.return_value = list(files)
    workspace.path = None
    workspace.read.side_effect = lambda f: files[f]
    return ToolCallLoop(workspace=workspace, git=None, tools=None)


# ---------------- A. 同内容重读熔断的 masked 豁免 ----------------

class TestSameContentReadBlockMaskedExemption:

    def test_blocks_repeat_read_when_not_masked(self):
        """未遮蔽时的既有行为不变：同内容重读仍要拦。"""
        loop = _make_loop({"js/board.js": "a\nb\n"})
        state = {}
        assert loop._same_content_read_block(state, "js/board.js") is None
        block = loop._same_content_read_block(state, "js/board.js")
        assert block is not None and block.blocked is True
        assert "已跳过" in block.content and "js/board.js" in block.content

    def test_allows_repeat_read_when_masked(self):
        """★ 核心回归：文件已被遮出上下文时，重读必须放行。

        改前代码在此处返回 blocked，文案还谎称「已在上方工具结果里」——
        模型按占位符的邀请来恢复内容，正好撞在熔断上，死锁由此形成。
        """
        loop = _make_loop({"js/board.js": "a\nb\n"})
        state = {"_masked_read_files": {"js/board.js"}}
        # 首次读取登记指纹
        assert loop._same_content_read_block(state, "js/board.js") is None
        # 指纹相同 + 已遮蔽 → 放行（恢复路径），不得拦截
        assert loop._same_content_read_block(state, "js/board.js") is None
        assert "_read_file_hashes" in state  # 指纹仍正常维护

    def test_allows_when_content_changed(self):
        loop = _make_loop({"js/board.js": "a\nb\n"})
        files = {"js/board.js": "a\nb\n"}
        loop = _make_loop(files)
        state = {"_masked_read_files": {"js/board.js"}}
        loop._same_content_read_block(state, "js/board.js")
        files["js/board.js"] = "a\nb\nc\n"
        assert loop._same_content_read_block(state, "js/board.js") is None

    def test_allows_when_unreadable(self):
        workspace = Mock()
        workspace.read.side_effect = FileNotFoundError("不存在")
        loop = ToolCallLoop(workspace=workspace, git=None, tools=None)
        state = {"_read_file_hashes": {"x": "h"}, "_masked_read_files": {"x"}}
        assert loop._same_content_read_block(state, "x") is None

    def test_empty_filename_passes_through(self):
        loop = _make_loop({"a.js": "x"})
        assert loop._same_content_read_block({}, "") is None


# ---------------- B. chat 轮软预算放宽 ----------------

class TestChatSoftBudget:

    def test_chat_pipeline_keeps_first_round_reads_alive(self):
        """chat 软预算下 8 条 read 一条不遮；默认 6000 下必遮（复现死锁前半段）。"""
        reads = _eight_reads()
        _, stats_default = ContextPipeline(budget=24000).build("sys", reads)
        assert stats_default["masked_read"] > 0, "默认软预算应触发遮蔽"

        msgs, stats_chat = ContextPipeline(
            budget=24000, history_soft_budget=CHAT_HISTORY_SOFT_BUDGET
        ).build("sys", reads)
        assert stats_chat["masked_read"] == 0
        assert stats_chat.get("masked_read_files") == []
        assert all("已省略" not in m["content"] for m in msgs)

    def test_build_messages_chat_round_uses_relaxed_budget(self):
        """is_chat 轮：_build_messages 走放宽后的软预算，正文完整进第 2 轮。"""
        workspace = Mock()
        workspace.list.return_value = []
        loop = ToolCallLoop(workspace=workspace)
        state = {
            "requirement_content": "做一个1024小游戏",
            "dialogue_history": [{"role": "user", "content": "改成2048"}] + _eight_reads(),
            "metadata": {"is_chat": True},
        }
        messages = loop._build_messages(state)
        assert not any("已省略" in m["content"] for m in messages), \
            "chat 轮不应遮蔽第 1 轮读到的正文"
        assert state["_masked_read_files"] == set()

    def test_build_messages_coder_round_keeps_default_budget(self):
        """编码主流程（非 chat）仍用默认 6000，且 masked 名单回传 state。"""
        workspace = Mock()
        workspace.list.return_value = []
        loop = ToolCallLoop(workspace=workspace)
        state = {
            "requirement_content": "做一个1024小游戏",
            "dialogue_history": [{"role": "user", "content": "做一个1024"}] + _eight_reads(),
        }
        messages = loop._build_messages(state)
        assert any("已省略" in m["content"] for m in messages), \
            "编码轮应保持默认软预算治理"
        assert "f0.js" in state["_masked_read_files"], \
            "被遮蔽的文件名必须回传（熔断豁免的判据）"


# ---------------- C. masked 名单回传 ----------------

class TestMaskedReadFilesFeedback:

    def test_build_reports_masked_filenames(self):
        reads = [_dash_read("js/board.js")] + [_dash_read(f"f{i}.js") for i in range(1, 8)]
        _, stats = ContextPipeline(budget=24000).build("sys", reads)
        assert stats["masked_read"] > 0
        masked = stats["masked_read_files"]
        assert "js/board.js" in masked, "最旧的 read 最先被遮，必须出现在名单里"
        # keep_recent=3：最近 3 条受保护，不在名单里
        assert not ({"f5.js", "f6.js", "f7.js"} & set(masked))

    def test_no_masking_means_empty_list(self):
        reads = [_dash_read("a.js", body_chars=100)]
        _, stats = ContextPipeline(budget=24000).build("sys", reads)
        assert stats["masked_read_files"] == []


# ---------------- D. 占位符文件名 ----------------

class TestMaskedPlaceholderFilename:

    def test_placeholder_carries_real_filename_dash_format(self):
        """破折号头部（生产格式）必须解析出文件名，不得显示「文件：read_file」。"""
        reads = [_dash_read("js/board.js")] + [_dash_read(f"f{i}.js") for i in range(1, 8)]
        msgs, stats = ContextPipeline(budget=24000).build("sys", reads)
        assert stats["masked_read"] > 0
        placeholders = [m for m in msgs if "已省略" in m["content"]]
        assert placeholders
        assert any("js/board.js" in m["content"] for m in placeholders)
        assert all("文件：read_file" not in m["content"] for m in placeholders)

    def test_placeholder_falls_back_to_arguments(self):
        """头部不可解析时回退 arguments.filename（dialogue_history 一直带着）。"""
        reads = [
            {"role": "tool_call", "name": "read_file",
             "content": "[文件: a]..." + "x" * 3200,
             "arguments": {"filename": "js/board.js"}},
        ] + [_dash_read(f"f{i}.js") for i in range(1, 8)]
        msgs, stats = ContextPipeline(budget=24000).build("sys", reads)
        placeholders = [m for m in msgs if "已省略" in m["content"]]
        assert any("js/board.js" in m["content"] for m in placeholders)

    def test_constants_sanity(self):
        """chat 软预算必须显著大于默认值、且不超过硬预算。"""
        assert CHAT_HISTORY_SOFT_BUDGET > HISTORY_SOFT_BUDGET
        assert CHAT_HISTORY_SOFT_BUDGET <= 24000


# ---------------- E. chat 路由终态判定 ----------------

class TestChatRunOutcome:
    """「耗尽但写入已落地」必须判 finished，否则用户看到失败而文件已改。"""

    def test_task_complete_finished(self):
        from routes.requirements import _chat_run_outcome
        assert _chat_run_outcome("task_complete", True) == "finished"
        assert _chat_run_outcome("task_complete", False) == "finished"

    def test_exhausted_with_writes_finished(self):
        """★ 核心回归：死锁修复后弱模型改完继续核对 → max_iterations 但活已干完。"""
        from routes.requirements import _chat_run_outcome
        assert _chat_run_outcome("max_iterations", True) == "finished"
        assert _chat_run_outcome("no_progress", True) == "finished"

    def test_exhausted_without_writes_failed(self):
        """真没干活（线上死锁原形态：4 轮全在读、0 写入）必须保持 failed。"""
        from routes.requirements import _chat_run_outcome
        assert _chat_run_outcome("max_iterations", False) == "failed"
        assert _chat_run_outcome("no_progress", False) == "failed"

    def test_infra_faults_always_failed(self):
        """基础设施故障不因写过东西就洗白。"""
        from routes.requirements import _chat_run_outcome
        assert _chat_run_outcome("llm_error", True) == "failed"
        assert _chat_run_outcome("coding_error", True) == "failed"
