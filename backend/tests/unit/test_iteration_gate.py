# -*- coding: utf-8 -*-
"""迭代耗尽时的交付门禁（ToolCallLoop._gate_extended）

背景（t16 实测，2026-09-28）：
    交付门禁此前只挂在「模型主动声明完成」这条路径上（无 tool_calls →
    task_complete）。而真实失败大多发生在「迭代耗尽」这条路：t16 连续 4 次运行
    全部在第 6 轮耗尽退出，其中一次已经写出了 index.html 却引用了从未创建的
    js 文件 —— 门禁一次都没执行，坏代码被静默交付给 verify。

    修复后：迭代耗尽时先兜一次「必需文件缺失 + 语法/引用硬伤」，命中则扩容
    SELF_REPAIR_EXTRA_ITERATIONS 轮（只扩容一次，避免迭代上限退化成无上限）。

验证目标（这三条是本文件的全部价值）：
    1. 有硬伤 + 迭代耗尽 → 扩容并注入可执行的修复指令
    2. 无硬伤 + 迭代耗尽 → 不扩容（门禁不能把上限变成无上限）
    3. 扩容只发生一次（连续硬伤时仍在第二次边界终止）
"""

import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from harness.runtime import ToolCallLoop
from harness.state.workspace import WorkspaceFS
from harness.tools.registry import ToolResult


BROKEN_INDEX = (
    "<!doctype html><html><head>"
    '<link rel="stylesheet" href="css/style.css">'
    "</head><body><h1>排序可视化</h1>"
    '<script src="js/app.js"></script>'
    "</body></html>"
)
GOOD_INDEX = "<!doctype html><html><body><h1>ok</h1></body></html>"


class _GateLoop(ToolCallLoop):
    """把工具执行替换成「每轮新建一个文件」，让文件集合持续变化。

    目的：把测试聚焦在门禁本身，避免被 no_progress / 重复调用检测提前终止。
    同时不改动被测代码路径（工具执行仍走 _execute_tool 的调用点）。
    """

    def _execute_tool(self, state, tool_call):
        n = state.get("_filler", 0) + 1
        state["_filler"] = n
        self.workspace.write(f"filler/f{n}.txt", f"round {n}\n")
        return ToolResult(content="ok")


def _make_loop(index_html: str, tmp: Path):
    workspace = WorkspaceFS(user_id=999, requirement_id=1, base_dir=tmp)
    workspace.init([])
    workspace.write("index.html", index_html)

    loop = _GateLoop(workspace=workspace, git=None, tools=Mock())
    # 测试目标是门禁，不是无进展检测 → 显式关闭，保证终止条件唯一
    loop._check_no_progress = lambda state: False
    return loop, workspace


def _state():
    return {
        "requirement_id": 1,
        "requirement_content": "做一个排序可视化页面",
        "user_id": 1,
        "dialogue_history": [],
        "code_files": [],
        "tool_call_count": 0,
        "no_progress_count": 0,
        "last_file_list": [],
        "hook_failures": {},
        "metadata": {},
        "implementation_order": [],
    }


def _client_that_never_finishes(counter):
    """每轮返回一个「无害的工具调用」，参数各不相同（避开重复调用检测）。

    关键：永远不返回「无 tool_calls」，因此循环只能从「迭代耗尽」退出 ——
    这正是本次修复针对的那条路径。
    """
    client = Mock()

    def _respond(*args, **kwargs):
        counter["n"] += 1
        tc = Mock()
        tc.name = "write_file"
        tc.arguments = {
            "filename": f"filler/deep/d{counter['n']}.txt",
            "content": f"payload {counter['n']}",
        }
        resp = Mock()
        resp.tool_calls = [tc]
        resp.content = "继续推进"
        resp.reasoning_content = ""
        resp.usage = None
        resp.error = None
        resp.is_error = False
        resp.finish_reason = "tool_calls"
        return resp

    client.chat_with_tools.side_effect = _respond
    return client


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # 门禁会用 node --check 检查 .js；本测试不涉及 JS，但显式钉住常量让断言可读
    monkeypatch.setattr(ToolCallLoop, "MAX_ITERATIONS", 15, raising=False)
    yield


def test_gate_extends_when_hard_problems_at_exhaustion():
    """有硬伤且在迭代耗尽处退出 → 扩容 + 注入修复指令"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, ws = _make_loop(BROKEN_INDEX, tmp)
        counter = {"n": 0}
        client = _client_that_never_finishes(counter)

        base_max = 6  # plan 为空 → file_count=max(0,3)=3 → 3+3=6
        with patch("harness.runtime.get_client", return_value=client):
            final = loop.run(_state())

        # 迭代耗尽 → 命中门禁 → 再跑 SELF_REPAIR_EXTRA_ITERATIONS 轮
        assert final.get("_gate_extended") is True, "门禁未在迭代耗尽处触发"
        assert counter["n"] == base_max + ToolCallLoop.SELF_REPAIR_EXTRA_ITERATIONS, (
            f"扩容轮数不符：实际 {counter['n']} 次 LLM 调用，"
            f"期望 {base_max + ToolCallLoop.SELF_REPAIR_EXTRA_ITERATIONS}"
        )

        # 注入的消息必须可执行：同时点出「引用了不存在的资源」与「该怎么做」
        injected = [
            m for m in final["dialogue_history"]
            if m.get("role") == "system" and "迭代即将用完" in str(m.get("content", ""))
        ]
        assert injected, "未注入修复指令"
        text = injected[0]["content"]
        assert "js/app.js" in text, f"未点出缺失引用：{text[:200]}"
        assert "edit_file" in text and "write_file" in text, "修复指令缺少可执行动作"
        # preserve 必须为真：context_pipeline 只保护 preserve 的 system 消息，
        # 否则这条提示会在压缩时被丢掉，模型看不到
        assert injected[0].get("preserve") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate_does_not_extend_when_clean():
    """无硬伤 → 不扩容，行为与改动前一致（上限不能退化成无上限）"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, ws = _make_loop(GOOD_INDEX, tmp)
        counter = {"n": 0}
        client = _client_that_never_finishes(counter)

        with patch("harness.runtime.get_client", return_value=client):
            final = loop.run(_state())

        assert final.get("_gate_extended") in (None, False)
        assert counter["n"] == 6, f"无硬伤却扩容了：{counter['n']} 次调用"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate_extends_only_once():
    """硬伤一直修不好 → 只在第一次边界扩容，第二次边界必须终止"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, ws = _make_loop(BROKEN_INDEX, tmp)
        # 让门禁永远认为有硬伤：模型始终没修 index.html
        counter = {"n": 0}
        client = _client_that_never_finishes(counter)

        with patch("harness.runtime.get_client", return_value=client):
            final = loop.run(_state())

        # 6 + 3 = 9，而不是 6 + 3 + 3
        assert counter["n"] == 9, f"扩容发生了多次：{counter['n']} 次调用"
        assert final["current_step"] == "max_iterations"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
