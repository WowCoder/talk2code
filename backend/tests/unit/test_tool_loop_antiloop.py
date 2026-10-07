# -*- coding: utf-8 -*-
"""
空转/反复回读防护测试（需求 182 回归）

背景：Coder 写完 2 个文件后连续 8 轮只读不写，四道防线一道都没拦住，
最终 4 个文件未生成、交付门禁拦截。本文件锁定修复后的行为。
"""

from unittest.mock import Mock, patch

from harness.runtime import ToolCallLoop


def _make_loop(file_list):
    workspace = Mock()
    workspace.list.return_value = list(file_list)
    workspace.path = None
    return ToolCallLoop(workspace=workspace, git=None, tools=None)


class TestDeliverableFiles:
    """无进展判定必须只看交付文件"""

    def test_excludes_task_metadata(self):
        loop = _make_loop([
            ".task/contract.json",
            ".task/TASK_STATE.md",
            ".task/evaluator/result.json",
            "css/style.css",
            "js/utils.js",
        ])
        deliverable = loop._deliverable_files()
        assert deliverable == {"css/style.css", "js/utils.js"}
        assert not any(f.startswith(".task") for f in deliverable)


class TestNoProgressIgnoresMetadata:
    """.task/** 的持续重写不得重置无进展计数"""

    def test_metadata_churn_does_not_reset_counter(self):
        loop = _make_loop(["css/style.css", ".task/contract.json"])
        state = {"tool_call_count": 9, "metadata": {}}

        flags = []
        # 每轮 .task 下都多一个新文件（模拟 hook / update_task_notes / verify 写入），
        # 但交付文件始终是同一个 → 修复前会被这些元数据写入反复重置，永不触发。
        for i in range(8):
            loop.workspace.list.return_value = [
                "css/style.css",
                ".task/contract.json",
                f".task/round_{i}.json",
            ]
            flags.append(loop._check_no_progress(state))

        assert any(flags), (
            "交付文件未变化但无进展从未触发：判定仍被 .task/** 元数据污染"
        )


class TestPendingPlanFiles:
    """按实现计划比对文件系统得出缺失清单"""

    def test_reports_files_not_yet_created(self):
        loop = _make_loop(["css/style.css", "js/utils.js", ".task/contract.json"])
        state = {
            "implementation_order": [
                "index.html", "css/style.css", "js/utils.js", "js/game.js",
            ],
            "metadata": {},
        }
        assert loop._pending_plan_files(state) == ["index.html", "js/game.js"]

    def test_empty_in_chat_mode(self):
        loop = _make_loop(["css/style.css"])
        state = {"implementation_order": ["index.html"], "metadata": {"is_chat": True}}
        assert loop._pending_plan_files(state) == []

    def test_per_file_mode_only_checks_target(self):
        loop = _make_loop(["css/style.css"])
        state = {
            "implementation_order": ["index.html", "css/style.css"],
            "metadata": {},
            "_per_file_mode": True,
            "_current_target_file": "index.html",
        }
        assert loop._pending_plan_files(state) == ["index.html"]


class TestMissingReminderDelivery:
    """缺文件提醒必须每轮投递（此前只在零工具调用轮投递）"""

    def test_reminder_sent_when_files_missing(self):
        loop = _make_loop(["css/style.css"])
        state = {
            "implementation_order": ["index.html", "css/style.css", "js/main.js"],
            "metadata": {},
            "dialogue_history": [],
        }
        loop._maybe_remind_missing_files(state, 0)
        assert len(state["dialogue_history"]) == 1
        assert "js/main.js" in state["dialogue_history"][0]["content"]

    def test_no_reminder_when_all_created(self):
        loop = _make_loop(["index.html", "css/style.css"])
        state = {
            "implementation_order": ["index.html", "css/style.css"],
            "metadata": {},
            "dialogue_history": [],
        }
        loop._maybe_remind_missing_files(state, 0)
        assert state["dialogue_history"] == []


class TestBrokenReferenceReminder:
    """悬空引用提醒必须每轮投递（此前只在迭代耗尽时由门禁兜一次、扩容 3 轮）

    2026-10-05 评测集实测：t02/t05/t17/t18/t21 五个任务全挂在
    「index.html 引用了从未创建的 js 资源」，门禁把这条事实告诉模型时已经第 7 轮。
    """

    @staticmethod
    def _loop(files: dict):
        workspace = Mock()
        workspace.path = None
        workspace.list.return_value = list(files)
        workspace.read.side_effect = lambda name: files[name]
        return ToolCallLoop(workspace=workspace, git=None, tools=None)

    def test_reminder_reports_dangling_ref(self):
        loop = self._loop({
            "index.html": '<link href="css/style.css" rel="stylesheet">'
                          '<script src="js/main.js"></script>',
            "css/style.css": "body{}",
        })
        state = {"metadata": {}, "dialogue_history": []}
        loop._maybe_remind_broken_references(state, 2)
        assert len(state["dialogue_history"]) == 1
        content = state["dialogue_history"][0]["content"]
        assert "js/main.js" in content
        assert "引用闭合检查" in content

    def test_no_reminder_when_references_closed(self):
        loop = self._loop({
            "index.html": '<script src="js/app.js"></script>',
            "js/app.js": "var a=1;",
        })
        state = {"metadata": {}, "dialogue_history": []}
        loop._maybe_remind_broken_references(state, 2)
        assert state["dialogue_history"] == []

    def test_skips_external_and_anchor_refs(self):
        """CDN / data: / #锚点 不算悬空引用"""
        loop = self._loop({
            "index.html": (
                '<script src="https://cdn.example.com/x.js"></script>'
                '<a href="#top">top</a><img src="data:image/png;base64,AAA">'
            ),
        })
        state = {"metadata": {}, "dialogue_history": []}
        loop._maybe_remind_broken_references(state, 2)
        assert state["dialogue_history"] == []

    def test_catches_wrong_name_even_when_plan_complete(self):
        """引用名写错时只有这条线索能报警：

        HTML 里写 js/main.js，而实现计划里叫 js/app.js 且**已创建** ——
        缺文件提醒此时完全静默（"计划里的文件都建了"），只有引用闭合检查
        能发现交付物自相矛盾。t02 实测就是这个形态。
        """
        loop = self._loop({
            "index.html": '<script src="js/main.js"></script>',
            "css/style.css": "body{}",
            "js/app.js": "var a=1;",
        })
        state = {
            "implementation_order": ["index.html", "css/style.css", "js/app.js"],
            "metadata": {},
            "dialogue_history": [],
        }
        loop._maybe_remind_missing_files(state, 3)
        assert state["dialogue_history"] == [], "计划文件已齐，缺文件提醒本应静默"
        loop._maybe_remind_broken_references(state, 3)
        assert "js/main.js" in state["dialogue_history"][0]["content"]

    def test_silent_in_chat_mode(self):
        loop = self._loop({"index.html": '<script src="js/main.js"></script>'})
        state = {"metadata": {"is_chat": True}, "dialogue_history": []}
        loop._maybe_remind_broken_references(state, 2)
        assert state["dialogue_history"] == []

    def test_same_list_throttled_after_two_rounds(self):
        """同清单连续 2 轮后改隔轮，避免重复句淹没上下文"""
        loop = self._loop({
            "index.html": '<script src="js/main.js"></script>',
        })
        state = {"metadata": {}, "dialogue_history": []}
        for i in range(6):
            loop._maybe_remind_broken_references(state, i)
        # 计数 1..6：前 2 轮每轮投递（1、2），之后隔轮（3、5），偶数轮静音（4、6）
        # → 6 轮共投递 4 次。与缺文件提醒同一套降频纪律。
        assert len(state["dialogue_history"]) == 4

    def test_gate_failure_never_breaks_loop(self):
        """门禁自身故障（读文件抛异常）必须静默降级，不得打断编码循环"""
        loop = self._loop({"index.html": '<script src="js/main.js"></script>'})
        loop.workspace.read.side_effect = RuntimeError("boom")
        state = {"metadata": {}, "dialogue_history": []}
        loop._maybe_remind_broken_references(state, 2)
        assert state["dialogue_history"] == []

    def test_wired_into_loop_body(self):
        """方法写好了还必须真的接线：漏了调用点等于没修"""
        import inspect
        src = inspect.getsource(ToolCallLoop._run_impl)
        assert "_maybe_remind_broken_references(state, iteration)" in src


class TestReadbackGuard:
    """写入后回读同一文件必须收到"内容未变"提示"""

    class _Result:
        def __init__(self):
            self.content = "[文件: css/style.css — 全文共 435 行，未截断]\n\nbody{}"

    class _Call:
        name = "read_file"

        def __init__(self, filename="css/style.css"):
            self.arguments = {"filename": filename}

    def test_annotates_recently_written_file(self):
        loop = _make_loop(["css/style.css"])
        state = {"_recent_writes": {"css/style.css": 1}, "tool_call_count": 3}
        result = self._Result()
        loop._annotate_readback(state, self._Call(), result)
        assert result.content.startswith("[提示]")
        assert "css/style.css" in result.content

    def test_no_annotation_after_guard_window(self):
        loop = _make_loop(["css/style.css"])
        state = {"_recent_writes": {"css/style.css": 1}, "tool_call_count": 30}
        result = self._Result()
        loop._annotate_readback(state, self._Call(), result)
        assert not result.content.startswith("[提示]")

    def test_no_annotation_for_file_never_written(self):
        loop = _make_loop(["js/utils.js"])
        state = {"_recent_writes": {"css/style.css": 1}, "tool_call_count": 2}
        result = self._Result()
        # 读的是别人写的文件（js/utils.js），不在 _recent_writes 里
        loop._annotate_readback(state, self._Call("js/utils.js"), result)
        assert not result.content.startswith("[提示]")


class TestReadOnlySpinBreaker:
    """端到端复现需求 182：连续只读不写必须在预算内被熔断"""

    @patch("harness.runtime.get_client")
    def test_read_only_spin_is_stopped_early(self, mock_get_client):
        from harness.tools.registry import create_tool_registry

        mock_client = Mock()
        mock_response = Mock()
        _tc = Mock()
        _tc.name = "read_file"
        _tc.arguments = {"filename": "css/style.css"}
        mock_response.tool_calls = [_tc]
        mock_response.content = "先确认一下类名"
        mock_response.reasoning_content = ""
        mock_response.usage = None
        mock_response.error = None
        mock_response.is_error = False
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = [
            "css/style.css", "js/utils.js", ".task/contract.json",
        ]
        workspace.read.return_value = "body { color: red; }\n" * 20
        workspace.path = None

        loop = ToolCallLoop(workspace=workspace, tools=create_tool_registry())
        impl_order = ["index.html", "css/style.css", "js/utils.js",
                      "js/storage.js", "js/game.js", "js/main.js"]
        state = {
            "requirement_id": 1,
            "requirement_content": "贪吃龙小游戏",
            "user_id": 1,
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "implementation_order": impl_order,
            "metadata": {},
        }

        final_state = loop.run(state)

        # 预算 = min(6+3, 10) = 9 轮；修复前会跑满 9 轮只读
        budget = min(len(impl_order) + 3, 10)
        assert final_state["current_step"] == "no_progress", (
            f"只读空转未被熔断，结束于 {final_state['current_step']}"
        )
        assert final_state["tool_call_count"] < budget, (
            f"熔断太晚：第 {final_state['tool_call_count']} 轮才终止（预算 {budget}）"
        )

        # 缺文件提醒必须投递过（修复前只在零工具调用轮投递，此场景一次都不会有）
        reminders = [
            m for m in state["dialogue_history"]
            if m.get("role") == "system" and "还差" in (m.get("content") or "")
        ]
        assert reminders, "空转期间从未投递缺文件提醒"

    @patch("harness.runtime.get_client")
    def test_writing_resets_spin_detection(self, mock_get_client):
        """写入后立即恢复：不得把正常"读→写"流程误判为空转"""
        from harness.tools.registry import create_tool_registry

        mock_client = Mock()
        mock_response = Mock()
        _tc = Mock()
        _tc.name = "write_file"
        _tc.arguments = {"filename": "index.html", "content": "<html></html>"}
        mock_response.tool_calls = [_tc]
        mock_response.content = "创建 index.html"
        mock_response.reasoning_content = ""
        mock_response.usage = None
        mock_response.error = None
        mock_response.is_error = False
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = ["index.html"]
        workspace.path = None

        loop = ToolCallLoop(workspace=workspace, tools=create_tool_registry())
        state = {
            "requirement_id": 1,
            "requirement_content": "登录页",
            "user_id": 1,
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "implementation_order": ["index.html"],
            "metadata": {},
        }

        final_state = loop.run(state)
        assert final_state["current_step"] != "no_progress", (
            "持续写入被误判为无进展"
        )


class TestCircuitBreakerReachability:
    """熔断阈值必须在迭代预算内可达（此前是死代码）"""

    def test_read_heavy_abort_within_budget(self):
        # 迭代预算 = min(文件数 + 3, 10)，最坏情况 10 轮
        max_budget = ToolCallLoop.MAX_ITERATIONS if False else 10
        abort_round = (
            ToolCallLoop.READ_HEAVY_WINDOW + ToolCallLoop.READ_HEAVY_ABORT - 1
        )
        assert abort_round <= max_budget, (
            f"读写比熔断最早第 {abort_round} 轮才能终止，超出预算 {max_budget} 轮"
        )

    def test_repeat_read_breaker_within_budget(self):
        # 连续无写 3 轮 + 同一文件累计读 4 轮 → 最迟第 4 轮触发
        worst_case = max(
            ToolCallLoop.REPEAT_READ_LIMIT, ToolCallLoop.REPEAT_READ_NO_WRITE
        )
        assert worst_case <= 10
