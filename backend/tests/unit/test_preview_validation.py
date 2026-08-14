# -*- coding: utf-8 -*-
"""
真实运行验证闭环（run_preview）测试

覆盖：
1. PreviewToolHandler：文件缺失 / 验证通过 / 验证失败 / 浏览器不可用降级
2. ToolRegistry：run_preview 已注册
3. ToolCallLoop 验证回灌：生成完成后发现错误 → 回灌为修复 prompt → 重新进入 loop

PreviewToolHandler 的 run_preview 依赖真实浏览器，单测里用 monkeypatch
替换 run_preview_in_browser 返回固定报告，保证 CI 不依赖浏览器二进制。
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from harness.tools.preview_tools import PreviewToolHandler, register_preview_tools
from harness.tools.registry import create_tool_registry
from harness.state.agent_state import AgentState


# ---------- PreviewToolHandler ----------

class _FakeWorkspace:
    """最小化的 workspace 替身"""
    def __init__(self, files=None, base="/tmp/fake_ws"):
        from pathlib import Path
        self.path = Path(base)
        self._files = files or {}

    def exists(self, name):
        return name in self._files

    def list(self):
        return list(self._files.keys())


class TestPreviewToolHandler:

    def test_missing_file_returns_error(self):
        ws = _FakeWorkspace(files={})
        handler = PreviewToolHandler(ws)
        result = handler.run_preview("index.html")
        assert not result.success
        assert "index.html" in (result.error or "")

    def test_clean_page_passes(self):
        ws = _FakeWorkspace(files={"index.html": "x"})
        handler = PreviewToolHandler(ws)
        clean_report = {"available": True, "errors": [], "logs": [], "network": [], "url": "file://index.html"}
        with patch("harness.tools.preview_runner.run_preview_in_browser", return_value=clean_report):
            result = handler.run_preview("index.html")
        assert result.success
        assert result.metadata["errors"] == []

    def test_page_with_errors_returns_error_with_summary(self):
        ws = _FakeWorkspace(files={"index.html": "x"})
        handler = PreviewToolHandler(ws)
        bad_report = {
            "available": True,
            "errors": [
                {"type": "pageerror", "message": "undefinedFunc is not defined"},
                {"type": "console_error", "message": "boom"},
            ],
            "logs": [], "network": [], "url": "file://index.html",
        }
        with patch("harness.tools.preview_runner.run_preview_in_browser", return_value=bad_report):
            result = handler.run_preview("index.html")
        assert not result.success
        assert "undefinedFunc" in result.error
        assert "boom" in result.error
        assert len(result.metadata["errors"]) == 2

    def test_browser_unavailable_degrades_gracefully(self):
        """浏览器二进制缺失时不应抛异常，应降级为成功+skip"""
        ws = _FakeWorkspace(files={"index.html": "x"})
        handler = PreviewToolHandler(ws)
        with patch("harness.tools.preview_runner.run_preview_in_browser",
                   side_effect=RuntimeError("chromium 未安装")):
            result = handler.run_preview("index.html")
        # 降级：不阻断流程（success=True，metadata 标记 unavailable）
        assert result.success
        assert result.metadata.get("available") is False


# ---------- 注册 ----------

class TestPreviewRegistration:

    def test_run_preview_in_default_registry(self):
        registry = create_tool_registry()
        names = [t["function"]["name"] for t in registry.get_schemas()]
        assert "run_preview" in names

    def test_run_preview_description_mentions_validation(self):
        registry = create_tool_registry()
        schema = next(t["function"] for t in registry.get_schemas() if t["function"]["name"] == "run_preview")
        assert "验证" in schema["description"] or "运行" in schema["description"]


# ---------- ToolCallLoop 验证回灌 ----------

class TestToolCallLoopValidationFeedBack:
    """
    生成完成后：run_preview 发现错误 → 应把错误回灌为修复 prompt 并重新跑 loop。
    """

    def _make_state(self):
        return AgentState({
            "requirement_id": 1,
            "requirement_content": "做一个待办清单",
            "plan": {"action": "build", "features": ["add todo"]},
            "current_step": "starting",
            "dialogue_history": [],
            "code_files": [],
            "metadata": {},
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
        })

    def test_validation_failure_feeds_back_repair_prompt(self):
        from harness.runtime import ToolCallLoop

        ws = _FakeWorkspace(files={"index.html": "<html></html>", "style.css": "", "script.js": ""})
        loop = ToolCallLoop(workspace=ws)

        # run_preview 第一次返回错误，第二次（修复后）通过
        bad = {"available": True, "errors": [{"type": "pageerror", "message": "foo is not defined"}],
               "logs": [], "network": [], "url": "x"}
        good = {"available": True, "errors": [], "logs": [], "network": [], "url": "x"}
        with patch.object(PreviewToolHandler, "run_preview",
                          side_effect=[_to_result(bad), _to_result(good)]):
            errors = loop._run_preview_validation(self._make_state())

        assert errors == ["[pageerror] foo is not defined"]

    def test_no_index_html_skips_validation(self):
        """没有 index.html 时不触发验证（返回空，不阻断）"""
        from harness.runtime import ToolCallLoop
        ws = _FakeWorkspace(files={"style.css": ""})  # 无 index.html
        loop = ToolCallLoop(workspace=ws)
        errors = loop._run_preview_validation(self._make_state())
        assert errors == []

    def test_plan_appears_in_system_prompt(self):
        """P1: plan 应被注入 Coder system prompt（之前被丢弃）"""
        from harness.runtime import ToolCallLoop
        ws = _FakeWorkspace(files={})
        loop = ToolCallLoop(workspace=ws)
        state = self._make_state()
        prompt = loop._build_system_prompt(state)
        assert "实现计划" in prompt
        assert "build" in prompt  # plan.action 值


def _to_result(report):
    """把 dict report 包成 ToolResult-like"""
    r = MagicMock()
    r.success = len(report.get("errors", [])) == 0 and report.get("available", True)
    r.metadata = report
    r.error = "errors found" if report.get("errors") else ""
    r.content = "ok"
    return r
