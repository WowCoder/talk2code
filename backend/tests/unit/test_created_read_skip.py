# -*- coding: utf-8 -*-
"""「创建后免回读 + 上下文卸载」测试（需求 198 验收反馈）。

背景：Coder 用 write_file 创建文件后立刻 read_file 回读同一文件，
每次白付一整轮 LLM 往返（弱模型尤其爱"先读确认"）。修复后：
  1. write_file 成功 → 登记磁盘内容指纹；
  2. 未被改过就 read_file → 直接拦截（blocked），不执行；
  3. edit_file 成功 → 撤销登记（模型副本过期，必须允许重新读）；
  4. 允许的读取成功后 → 把创建时留在上下文里的正文替换成占位说明（卸载）。
"""

from unittest.mock import Mock

from harness.runtime import ToolCallLoop


class _TC:
    """最小化的工具调用桩（只需要 arguments）"""

    def __init__(self, **arguments):
        self.arguments = arguments


def _make_loop(files: dict):
    workspace = Mock()
    workspace.list.return_value = list(files)
    workspace.path = None
    workspace.read.side_effect = lambda f: files[f]
    return ToolCallLoop(workspace=workspace, git=None, tools=None), files


class TestTrackKnownContent:
    """write_file 成功后登记磁盘内容指纹"""

    def test_registers_hash_and_lines(self):
        loop, files = _make_loop({"js/app.js": "hello\nworld\n"})
        state = {"tool_call_count": 3, "dialogue_history": []}
        loop._track_known_content(state, _TC(filename="js/app.js"))
        info = state["_known_content_files"]["js/app.js"]
        assert info["hash"] == loop._content_hash("hello\nworld\n")
        assert info["lines"] == 3
        assert info["round"] == 3

    def test_unreadable_file_not_registered(self):
        loop, files = _make_loop({})
        state = {"tool_call_count": 1, "dialogue_history": []}
        loop._track_known_content(state, _TC(filename="js/app.js"))
        assert state.get("_known_content_files", {}) == {}


class TestCreatedReadBlock:
    """未被改过的创建文件 → read_file 直接拦截"""

    def test_blocks_read_after_create(self):
        loop, files = _make_loop({"js/app.js": "a\nb\n"})
        state = {"tool_call_count": 2, "dialogue_history": []}
        loop._track_known_content(state, _TC(filename="js/app.js"))

        block = loop._created_read_block(state, "js/app.js")
        assert block is not None
        assert block.blocked is True
        assert "已跳过" in block.content and "js/app.js" in block.content

    def test_allows_read_after_content_changed(self):
        loop, files = _make_loop({"js/app.js": "a\nb\n"})
        state = {"tool_call_count": 2, "dialogue_history": []}
        loop._track_known_content(state, _TC(filename="js/app.js"))

        files["js/app.js"] = "a\nb\nc\n"  # 被改过
        assert loop._created_read_block(state, "js/app.js") is None

    def test_allows_untracked_file(self):
        loop, _ = _make_loop({"js/other.js": "x\n"})
        state = {"_known_content_files": {}}
        assert loop._created_read_block(state, "js/other.js") is None

    def test_allows_when_file_missing(self):
        loop, _ = _make_loop({})
        state = {
            "_known_content_files": {"js/app.js": {"hash": "x", "round": 1, "lines": 1}}
        }
        assert loop._created_read_block(state, "js/app.js") is None

    def test_untrack_after_edit_reenables_read(self):
        loop, files = _make_loop({"js/app.js": "a\n"})
        state = {"tool_call_count": 2, "dialogue_history": []}
        loop._track_known_content(state, _TC(filename="js/app.js"))
        loop._untrack_known_content(state, _TC(filename="js/app.js"))
        assert loop._created_read_block(state, "js/app.js") is None


class TestOffloadCreatedContext:
    """允许的读取成功后，创建时的正文副本要卸载"""

    def test_replaces_write_result_with_placeholder(self):
        loop, _ = _make_loop({})
        state = {
            "tool_call_count": 5,
            "_known_content_files": {
                "js/app.js": {"hash": "h", "round": 1, "lines": 10}
            },
            "dialogue_history": [
                {"role": "tool_call", "name": "write_file",
                 "arguments": {"filename": "js/app.js"},
                 "content": "已创建 js/app.js (10 行, 100 字符)\n\n" + "x" * 5000},
                {"role": "tool_call", "name": "read_file",
                 "arguments": {"filename": "js/other.js"}, "content": "..."},
            ],
        }
        loop._offload_created_context(state, _TC(filename="js/app.js"))

        assert "已卸载" in state["dialogue_history"][0]["content"]
        assert len(state["dialogue_history"][0]["content"]) < 200
        assert "js/app.js" not in state["_known_content_files"]
        # 其它条目不受影响
        assert state["dialogue_history"][1]["content"] == "..."

    def test_noop_without_tracking(self):
        loop, _ = _make_loop({})
        entry = {"role": "tool_call", "name": "write_file",
                 "arguments": {"filename": "js/app.js"}, "content": "正文"}
        state = {"dialogue_history": [entry]}
        loop._offload_created_context(state, _TC(filename="js/app.js"))
        assert entry["content"] == "正文"
