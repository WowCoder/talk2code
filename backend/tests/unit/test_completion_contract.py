# -*- coding: utf-8 -*-
"""
测试 CompletionContract 和 progress_hooks
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch

from harness.constraints.completion_contract import CompletionContract
from harness.constraints.progress_hooks import (
    block_unnecessary_read,
    block_premature_completion,
    track_write_success,
    READ_BLOCK_WINDOW,
)
from harness.constraints.hooks import HookContext


# ==================== CompletionContract ====================

class TestCompletionContract:
    """CompletionContract 单元测试"""

    def _mock_workspace(self, files=None):
        """创建模拟 workspace"""
        ws = Mock()
        storage = dict(files or {})

        def _read(path):
            if path in storage:
                return storage[path]
            raise FileNotFoundError(path)

        def _write(path, content):
            storage[path] = content

        def _delete(path):
            storage.pop(path, None)

        ws.read = _read
        ws.write = _write
        ws.delete_file = _delete
        ws._storage = storage  # 暴露内部存储便于断言
        return ws

    # ---- initialize ----

    def test_initialize_creates_contract(self):
        """正常初始化：创建 contract.json 包含所有文件"""
        ws = self._mock_workspace()
        contract = CompletionContract(ws)
        result = contract.initialize(["js/app.js", "css/style.css", "index.html"])

        assert result is True
        data = json.loads(ws._storage[".task/contract.json"])
        assert data["js/app.js"] == {"created": False, "validated": False}
        assert data["css/style.css"] == {"created": False, "validated": False}
        assert data["index.html"] == {"created": False, "validated": False}

    def test_initialize_empty_order(self):
        """空 implementation_order 不创建 contract"""
        ws = self._mock_workspace()
        contract = CompletionContract(ws)
        result = contract.initialize([])

        assert result is False
        assert ".task/contract.json" not in ws._storage

    # ---- mark_created ----

    def test_mark_created_updates_status(self):
        """标记已创建的文件"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/app.js": {"created": False, "validated": False},
            })
        })
        contract = CompletionContract(ws)

        result = contract.mark_created("js/app.js")
        assert result is True

        data = json.loads(ws._storage[".task/contract.json"])
        assert data["js/app.js"]["created"] is True

    def test_mark_created_idempotent(self):
        """重复标记不会出错"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/app.js": {"created": True, "validated": False},
            })
        })
        contract = CompletionContract(ws)

        result = contract.mark_created("js/app.js")
        assert result is True  # 幂等

    def test_mark_created_non_target_file(self):
        """标记不在 contract 中的文件返回 False"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/app.js": {"created": False, "validated": False},
            })
        })
        contract = CompletionContract(ws)

        result = contract.mark_created("package.json")
        assert result is False  # 不在 contract 中

    # ---- mark_validated ----

    def test_mark_validated(self):
        """标记已验证"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/app.js": {"created": True, "validated": False},
            })
        })
        contract = CompletionContract(ws)

        contract.mark_validated("js/app.js")
        data = json.loads(ws._storage[".task/contract.json"])
        assert data["js/app.js"]["validated"] is True

    # ---- all_completed ----

    def test_all_completed_true(self):
        """所有文件创建完成"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/a.js": {"created": True, "validated": False},
                "js/b.js": {"created": True, "validated": False},
            })
        })
        contract = CompletionContract(ws)
        assert contract.all_completed() is True

    def test_all_completed_false(self):
        """还有文件未创建"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/a.js": {"created": True, "validated": False},
                "js/b.js": {"created": False, "validated": False},
            })
        })
        contract = CompletionContract(ws)
        assert contract.all_completed() is False

    def test_all_completed_no_contract(self):
        """无 contract 时默认通过"""
        ws = self._mock_workspace()
        contract = CompletionContract(ws)
        assert contract.all_completed() is True

    # ---- pending_files ----

    def test_pending_files_returns_uncompleted(self):
        """返回未完成的文件列表"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/a.js": {"created": True, "validated": False},
                "js/b.js": {"created": False, "validated": False},
                "js/c.js": {"created": False, "validated": False},
            })
        })
        contract = CompletionContract(ws)
        pending = contract.pending_files()
        assert pending == ["js/b.js", "js/c.js"]

    def test_pending_files_all_done(self):
        """全部完成时返回空列表"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/a.js": {"created": True, "validated": False},
            })
        })
        contract = CompletionContract(ws)
        assert contract.pending_files() == []

    # ---- progress ----

    def test_get_progress(self):
        """进度摘要正确"""
        ws = self._mock_workspace({
            ".task/contract.json": json.dumps({
                "js/a.js": {"created": True, "validated": False},
                "js/b.js": {"created": False, "validated": False},
            })
        })
        contract = CompletionContract(ws)
        progress = contract.get_progress()
        assert progress["total"] == 2
        assert progress["completed"] == 1
        assert progress["all_done"] is False

    # ---- exists / clear ----

    def test_exists(self):
        """检查 contract 是否存在"""
        ws = self._mock_workspace({
            ".task/contract.json": "{}"
        })
        contract = CompletionContract(ws)
        assert contract.exists() is True

    def test_not_exists(self):
        ws = self._mock_workspace()
        contract = CompletionContract(ws)
        assert contract.exists() is False

    def test_clear(self):
        """清除 contract"""
        ws = self._mock_workspace({
            ".task/contract.json": "{}"
        })
        contract = CompletionContract(ws)
        contract.clear()
        assert ".task/contract.json" not in ws._storage


# ==================== Progress Hooks ====================

class TestBlockUnnecessaryRead:
    """block_unnecessary_read Hook 测试"""

    def test_blocks_read_within_window(self):
        """写入后 1 轮内阻断 read_file"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="read_file",
            tool_args={"filename": "js/app.js"},
            state={
                "tool_call_count": 3,
                "_recent_writes": {"js/app.js": 2},  # 上一轮写入
            }
        )
        result = block_unnecessary_read(ctx)
        assert result is not None
        assert "js/app.js" in result
        assert "禁止 read_file" in result

    def test_allows_read_after_window(self):
        """写入后超过 2 轮允许 read_file"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="read_file",
            tool_args={"filename": "js/app.js"},
            state={
                "tool_call_count": 6,
                "_recent_writes": {"js/app.js": 2},  # 间隔 4 轮
            }
        )
        result = block_unnecessary_read(ctx)
        assert result is None  # 允许通过

    def test_allows_read_unrelated_file(self):
        """读取非刚写入的文件允许通过"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="read_file",
            tool_args={"filename": "js/game.js"},
            state={
                "tool_call_count": 5,
                "_recent_writes": {"js/app.js": 4},  # 不同的文件
            }
        )
        result = block_unnecessary_read(ctx)
        assert result is None

    def test_no_recent_writes_allows_pass(self):
        """没有最近写入记录时允许通过"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="read_file",
            tool_args={"filename": "js/app.js"},
            state={}
        )
        result = block_unnecessary_read(ctx)
        assert result is None

    def test_non_read_tool_ignored(self):
        """非 read_file 工具直接忽略"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"filename": "js/app.js"},
            state={"_recent_writes": {"js/app.js": 1}}
        )
        result = block_unnecessary_read(ctx)
        assert result is None


class TestBlockPrematureCompletion:
    """block_premature_completion Hook 测试"""

    def _make_ws_with_contract(self, files_status):
        """创建带 contract 的 mock workspace"""
        ws = Mock()
        contract_data = {}
        for fname, created in files_status.items():
            contract_data[fname] = {"created": created, "validated": False}

        def _read(path):
            if path == ".task/contract.json":
                return json.dumps(contract_data)
            raise FileNotFoundError(path)

        ws.read = _read
        return ws

    def test_blocks_when_pending(self):
        """contract 未全部完成时阻断"""
        ws = self._make_ws_with_contract({
            "js/a.js": True,
            "js/b.js": False,
        })
        ctx = HookContext(
            requirement_id=1,
            tool_name="task_complete",
            state={
                "_workspace": ws,
            }
        )
        result = block_premature_completion(ctx)
        assert result is not None
        assert "尚未创建" in result
        assert "js/b.js" in result

    def test_allows_when_all_done(self):
        """全部完成时放行"""
        ws = self._make_ws_with_contract({
            "js/a.js": True,
            "js/b.js": True,
        })
        ctx = HookContext(
            requirement_id=1,
            tool_name="task_complete",
            state={"_workspace": ws}
        )
        result = block_premature_completion(ctx)
        assert result is None

    def test_no_contract_passes(self):
        """无 contract 时不阻断"""
        ws = Mock()
        ws.read = lambda path: (_ for _ in ()).throw(FileNotFoundError(path))

        ctx = HookContext(
            requirement_id=1,
            tool_name="task_complete",
            state={"_workspace": ws}
        )
        result = block_premature_completion(ctx)
        assert result is None

    def test_non_complete_tool_ignored(self):
        """非 task_complete 工具不触发"""
        ws = self._make_ws_with_contract({"js/a.js": False})
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            state={"_workspace": ws}
        )
        result = block_premature_completion(ctx)
        assert result is None


class TestTrackWriteSuccess:
    """track_write_success Hook 测试"""

    def test_tracks_write_to_recent_writes(self):
        """write_file 成功后追踪到 _recent_writes"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"filename": "js/app.js"},
            tool_result="已创建 js/app.js (150 行, 4500 字符)",
            state={"tool_call_count": 3}
        )
        result = track_write_success(ctx)
        assert result is None  # 静默通过

        assert ctx.state["_recent_writes"]["js/app.js"] == 3

    def test_non_write_tool_ignored(self):
        """非 write_file 工具跳过"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="read_file",
            tool_args={"filename": "js/app.js"},
            state={}
        )
        result = track_write_success(ctx)
        assert result is None
        assert "_recent_writes" not in ctx.state

    def test_no_tool_result_skipped(self):
        """写入失败（无 tool_result）时跳过"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"filename": "js/app.js"},
            tool_result=None,
            state={}
        )
        result = track_write_success(ctx)
        assert result is None
