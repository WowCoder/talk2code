# -*- coding: utf-8 -*-
"""节点级里程碑事件 —— 时间线的可读性由这些事件保证。

背景：契约里定义了 13 类事件，但此前运行时只写 llm_turn / tool_call，
时间线是一串流水账。排查「验收为什么没过」得逐条点开 LLM 原文猜。
这里锁定「节点执行一次 = 时间线一条带结论的事件」。
"""

import inspect

import pytest


def _capture(monkeypatch):
    """捕获 record_event 调用（nodes 在函数内 import，改模块属性即可生效）。"""
    calls = []

    def fake(requirement_id, kind, label, **kw):
        calls.append({"requirement_id": requirement_id, "kind": kind,
                      "label": label, **kw})
        return 1

    import harness.observability.trace_writer as tw
    monkeypatch.setattr(tw, "record_event", fake)
    return calls


class TestVerifyMilestone:

    def test_failure_reports_failed_acs(self, monkeypatch):
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        state = {
            "requirement_id": 1,
            "verify_verdict": {
                "verdict": "NEEDS_WORK", "score": 3.0, "findings": 4,
                "critical_count": 1, "failed_ac_ids": ["ac1", "ac2"],
            },
        }
        _emit_node_milestone("verify", state,
                             {"verify_passed": False,
                              "current_step": "verify_done"})

        assert len(calls) == 1
        c = calls[0]
        assert c["kind"] == "verify"
        assert "未达成 AC 2 条" in c["label"]
        assert c["meta"]["failed_ac_ids"] == ["ac1", "ac2"]
        assert c["meta"]["critical_count"] == 1
        # 信息通道键不该被 checkpoint / 状态快照带走
        assert "verify_verdict" not in state

    def test_pass_label_carries_score(self, monkeypatch):
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        state = {"requirement_id": 2,
                 "verify_verdict": {"verdict": "PASS", "score": 8.6}}
        _emit_node_milestone("verify", state, {"verify_passed": True})

        assert "验收通过" in calls[0]["label"]
        assert calls[0]["meta"]["verdict"] == "PASS"

    def test_early_return_still_emits(self, monkeypatch):
        """没有任何 verdict 信息的早退（缺 workspace / 评估异常）也必须留痕。

        这些路径恰恰是「验收根本没跑起来」的情况，最需要被看到。
        """
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        _emit_node_milestone("verify", {"requirement_id": 3},
                             {"verify_passed": False,
                              "current_step": "verify_done",
                              "error": "评估异常: boom"})

        assert len(calls) == 1
        assert calls[0]["status"] == "error"
        assert calls[0]["meta"]["verdict"] == "NEEDS_WORK"


class TestRepairMilestone:

    def test_round_and_files(self, monkeypatch):
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        state = {"requirement_id": 4,
                 "repair_verdict": {"round": 2,
                                    "target_defects": ["no_interaction"],
                                    "written_files": ["a.js", "b.css"]}}
        _emit_node_milestone("defect_repair", state,
                             {"current_step": "defect_repair_done"})

        c = calls[0]
        assert c["kind"] == "repair"
        assert "第 2 轮修复完成" in c["label"]
        assert c["meta"]["written_files"] == ["a.js", "b.css"]
        assert "repair_verdict" not in state

    def test_skipped_is_not_an_error(self, monkeypatch):
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        _emit_node_milestone("defect_repair", {"requirement_id": 5},
                             {"current_step": "defect_repair_skipped"})

        assert calls[0]["status"] == "ok"
        assert "跳过修复" in calls[0]["label"]

    def test_failed_marks_error(self, monkeypatch):
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        _emit_node_milestone("defect_repair", {"requirement_id": 6},
                             {"current_step": "defect_repair_failed"})

        assert calls[0]["status"] == "error"


class TestMilestoneTraceability:

    def test_turn_index_and_trace_id_propagated(self, monkeypatch):
        """里程碑事件必须带上轮次与 trace_id。

        不带的话，多轮对话里第 N 轮的结论会落到「初次生成」下面，
        且与 agent_traces 断链 —— 这正是轮次切换器数据错乱的原因。
        """
        from harness.instructions.nodes import _record_milestone
        calls = _capture(monkeypatch)
        state = {"requirement_id": 7,
                 "metadata": {"turn_index": 3, "trace_id": "t-abc"}}
        _record_milestone(state, "plan", "规划完成")

        assert calls[0]["turn_index"] == 3
        assert calls[0]["trace_id"] == "t-abc"


class TestCodingMilestone:
    """编码结论必须在**两条路径**上都出现，且只出现一条。

    第一次端到端跑完，13 类事件只缺 coding —— 原因就是埋点只写在了
    `_execute_delegated_tasks`（委派分支）里，而普通需求全是 `code` 类型任务，
    走的是 coder_node 的批量分支，那条路径一个埋点都没有。
    """

    def test_batch_path_emits(self, monkeypatch):
        """批量编码正常收尾：报出产出文件数。"""
        from harness.instructions.nodes import _emit_coding_milestone
        calls = _capture(monkeypatch)
        _emit_coding_milestone(
            {"requirement_id": 11},
            {"current_step": "coding_done",
             "code_files": [{"filename": "index.html"}, {"filename": "app.js"}]})

        assert len(calls) == 1
        c = calls[0]
        assert c["kind"] == "coding"
        assert "编码完成 · 2 个文件" in c["label"]
        assert c["meta"]["files"] == ["index.html", "app.js"]
        assert c["status"] == "ok"

    def test_error_path_marks_error(self, monkeypatch):
        """编码抛异常也要留痕，否则时间线上「编码段」是一片空白。"""
        from harness.instructions.nodes import _emit_coding_milestone
        calls = _capture(monkeypatch)
        _emit_coding_milestone({"requirement_id": 12},
                               {"current_step": "coding_error",
                                "error": "ToolCallLoop 未注入到 state"})

        assert calls[0]["status"] == "error"
        assert "编码失败" in calls[0]["label"]

    def test_unfinished_step_is_not_dressed_as_success(self, monkeypatch):
        """跑满轮次提前收尾（既没完成也没报错）不许显示成「编码完成」。"""
        from harness.instructions.nodes import _emit_coding_milestone
        calls = _capture(monkeypatch)
        _emit_coding_milestone({"requirement_id": 13},
                               {"current_step": "max_iterations",
                                "code_files": [{"filename": "a.js"}]})

        assert "编码完成" not in calls[0]["label"]
        assert "max_iterations" in calls[0]["label"]

    def test_file_count_comes_from_workspace(self, monkeypatch):
        """文件数以磁盘上的实际产物为准，不取可能为空的 `result["code_files"]`。

        实测需求 215：工作区里躺着 4 个交付文件，事件却写 file_count=0 ——
        日志说谎比没有日志更糟，排查时会顺着错误方向走。
        """
        from harness.instructions import nodes
        calls = _capture(monkeypatch)
        monkeypatch.setattr(nodes, "_delivered_files",
                            lambda state: ["index.html", "js/app.js"])
        nodes._emit_coding_milestone(
            {"requirement_id": 14},
            {"current_step": "max_iterations", "code_files": []})

        assert calls[0]["meta"]["file_count"] == 2
        assert "2 个文件" in calls[0]["label"]

    def test_delegated_path_does_not_double_emit(self):
        """委派分支内部不得再单独埋点，否则一次编码会被记成两条。"""
        from harness.instructions.nodes import _execute_delegated_tasks
        src = inspect.getsource(_execute_delegated_tasks)
        assert "KIND_CODING" not in src
        assert "_record_milestone" not in src

    def test_wrapper_covers_every_return(self):
        """结论埋在包装层：入口函数只做转发，实现体里不再各自埋点。"""
        from harness.instructions import nodes
        src = inspect.getsource(nodes.coder_node)
        assert "_coder_node_impl(state)" in src
        assert "_emit_coding_milestone(state, result)" in src


class TestTracedNodePlacement:
    """`@_traced_node("X")` 必须挂在真正的节点上，且切面不能被非法输入打挂。

    HEAD 里这个装饰器被挂在了辅助函数 `_build_vision_images` 上（它的第一个
    参数是截图路径而不是 state）。后果有两层：
    1. 真正的 defect_repair 节点没被包到 → repair 事件永远不出现；
    2. 辅助函数每次调用都在 `state.pop(...)` 上抛 AttributeError，
       把验收的视觉模式整个打挂 —— 而且是在「加可观测性」的名义下。
    """

    def test_nodes_are_traced(self):
        from harness.instructions import nodes
        for fn in (nodes.verify_node, nodes.defect_repair_node):
            assert hasattr(fn, "__wrapped__"), f"{fn.__name__} 缺少 trace 切面"

    def test_helper_is_not_traced(self):
        """非节点函数不得被 trace 切面包住：它们的签名不是 (state, ...)。"""
        from harness.instructions import nodes
        assert not hasattr(nodes._build_vision_images, "__wrapped__")
        # 直接调用必须能正常返回，而不是抛 AttributeError
        assert nodes._build_vision_images("/nonexistent-shot.png", "on") == []

    def test_milestone_ignores_foreign_input(self, monkeypatch):
        """state 不是 dict / 节点名不认识时静默跳过，不抛异常。"""
        from harness.instructions.nodes import _emit_node_milestone
        calls = _capture(monkeypatch)
        _emit_node_milestone("defect_repair", "/tmp/not-a-state.png", ["x"])
        _emit_node_milestone("some_unknown_node", {"requirement_id": 1}, {})
        assert calls == []


class TestWriterReuseWiring:
    """run 级 writer 复用是性能设计的一部分，掉了会静默退化成每事件一次查询。"""

    def test_tool_call_loop_passes_shared_writer(self):
        from harness.runtime import ToolCallLoop
        src = inspect.getsource(ToolCallLoop)
        assert src.count("writer=self._trace_writer") >= 2, \
            "llm_turn / tool_call 两处埋点都必须复用 run 级 writer"

    def test_no_recursive_run(self):
        """run 拆成 run + _run_impl 后，内部不得再调 self.run（会重复建 writer）。"""
        from harness.runtime import ToolCallLoop
        src = inspect.getsource(ToolCallLoop._run_impl)
        assert "self.run(" not in src
