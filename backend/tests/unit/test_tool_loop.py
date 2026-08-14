# -*- coding: utf-8 -*-
"""
测试 ToolCallLoop 正常完成/最大迭代/连续无进展终止
对应 tasks.md 3.5
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.runtime import ToolCallLoop
from harness.tools.registry import ToolRegistry, ToolDefinition, ToolResult


class TestToolCallLoopBasic:
    """ToolCallLoop 基本流程测试"""

    @patch("harness.runtime.get_client")
    def test_completes_when_no_tool_calls(self, mock_get_client):
        """测试 LLM 返回无工具调用时正常完成"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "任务完成，所有文件已创建"
        mock_response.usage = None
        mock_response.error = None
        mock_response.is_error = False  # Mock doesn't use dataclass property
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = ["index.html", "style.css", "script.js"]
        workspace.path = None  # required by git

        git = Mock()
        git.commit.return_value = "abc123"

        loop = ToolCallLoop(workspace=workspace, git=git, tools=None)

        state = {
            "requirement_id": 1,
            "requirement_content": "创建登录页",
            "user_id": 1,
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "metadata": {},
        }

        final_state = loop.run(state)
        assert final_state["current_step"] == "task_complete"
        assert len(final_state["dialogue_history"]) >= 1

    @patch("harness.runtime.get_client")
    def test_max_iterations_limit(self, mock_get_client):
        """测试达到最大迭代次数后终止"""
        from harness.tools.registry import create_tool_registry
        mock_client = Mock()
        mock_response = Mock()
        _tc = Mock()
        _tc.name = "list_files"
        _tc.arguments = {}
        mock_response.tool_calls = [_tc]
        mock_response.content = "继续..."
        mock_response.reasoning_content = ""
        mock_response.usage = None
        mock_response.error = None
        mock_response.is_error = False  # Mock doesn't use dataclass property
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = []
        workspace.path = None

        tools = create_tool_registry()
        loop = ToolCallLoop(workspace=workspace, tools=tools)

        state = {
            "requirement_id": 1,
            "requirement_content": "测试",
            "user_id": 1,
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "metadata": {},
        }

        loop.MAX_ITERATIONS = 2
        final_state = loop.run(state)
        # 达到 max_iterations 或 no_progress
        assert final_state["current_step"] in ("max_iterations", "no_progress")

    @patch("harness.runtime.get_client")
    def test_no_progress_termination(self, mock_get_client):
        """测试连续无进展后终止（前3轮豁免）"""
        from harness.tools.registry import create_tool_registry
        mock_client = Mock()
        mock_response = Mock()
        _tc = Mock()
        _tc.name = "list_files"
        _tc.arguments = {}
        mock_response.tool_calls = [_tc]
        mock_response.content = "检查中..."
        mock_response.reasoning_content = ""
        mock_response.usage = None
        mock_response.error = None
        mock_response.is_error = False  # Mock doesn't use dataclass property
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = ["index.html"]
        workspace.path = None

        tools = create_tool_registry()
        loop = ToolCallLoop(workspace=workspace, tools=tools)

        state = {
            "requirement_id": 1,
            "requirement_content": "测试",
            "user_id": 1,
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 15,  # 已经超过 MAX_ITERATIONS
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "metadata": {},
        }

        final_state = loop.run(state)
        assert final_state["current_step"] in ("max_iterations", "no_progress")

    def test_check_no_progress_early_rounds_immune(self):
        """测试前3轮无进展不终止"""
        workspace = Mock()
        workspace.list.return_value = ["a.html"]
        loop = ToolCallLoop(workspace=workspace)

        state = {
            "tool_call_count": 2,
            "no_progress_count": 0,
            "last_file_list": ["a.html"],
        }

        # tool_call_count <= 3 豁免
        assert loop._check_no_progress(state) is False

    def test_check_no_progress_detects_stall(self):
        """测试检测到连续无进展"""
        workspace = Mock()
        workspace.list.return_value = ["a.html"]
        loop = ToolCallLoop(workspace=workspace)

        state = {
            "tool_call_count": 5,
            "no_progress_count": 4,  # 上一轮已经 4 次
            "last_file_list": ["a.html"],
        }

        # 文件列表没变，no_progress_count 会 +1 -> 5 >= NO_PROGRESS_LIMIT(5)
        result = loop._check_no_progress(state)
        assert result is True  # 达到限制
        assert state["no_progress_count"] == 5

    def test_check_no_progress_resets_on_change(self):
        """测试文件列表变化时重置无进展计数"""
        workspace = Mock()
        workspace.list.return_value = ["a.html", "b.html"]  # 新增了 b.html
        loop = ToolCallLoop(workspace=workspace)

        state = {
            "tool_call_count": 5,
            "no_progress_count": 4,
            "last_file_list": ["a.html"],  # 旧列表
        }

        assert loop._check_no_progress(state) is False
        assert state["no_progress_count"] == 0  # 重置了

    def test_build_system_prompt(self):
        """测试系统提示词构建"""
        workspace = Mock()
        workspace.list.return_value = []
        loop = ToolCallLoop(workspace=workspace)

        state = {
            "requirement_content": "创建待办事项应用",
        }

        prompt = loop._build_system_prompt(state)
        assert "待办事项应用" in prompt
        assert "write_file" in prompt
        assert "index.html" in prompt.lower()

    def test_build_messages(self):
        """测试消息列表构建"""
        workspace = Mock()
        workspace.list.return_value = []
        loop = ToolCallLoop(workspace=workspace)

        state = {
            "requirement_content": "测试",
            "dialogue_history": [
                {"role": "user", "content": "创建应用"},
                {"role": "agent", "content": "好的，开始创建"},
            ]
        }

        messages = loop._build_messages(state)
        assert len(messages) >= 2
        # 第一个应该是 system
        assert messages[0]["role"] == "system"
        # 后面是用户消息
        assert any(m["role"] == "user" and "创建应用" in m["content"] for m in messages)


class TestToolCallLoopToolExecution:
    """工具执行测试"""

    def test_execute_tool_success(self):
        """测试工具执行成功"""
        workspace = Mock()
        workspace.list.return_value = []
        workspace.path = None

        from harness.tools.registry import create_tool_registry
        tools = create_tool_registry()
        loop = ToolCallLoop(workspace=workspace, tools=tools)

        state = {"requirement_id": 1}
        tc = Mock()
        tc.name = "list_files"
        tc.arguments = {}

        result = loop._execute_tool(state, tc)
        assert result.success is True

    def test_execute_unknown_tool(self):
        """测试执行未知工具"""
        workspace = Mock()
        workspace.path = None

        registry = ToolRegistry()
        loop = ToolCallLoop(workspace=workspace, tools=registry)

        state = {"requirement_id": 1}
        tc = Mock()
        tc.name = "non_existent_tool"
        tc.arguments = {}

        result = loop._execute_tool(state, tc)
        assert result.success is False
        assert "未知工具" in result.error

    def test_execute_tool_with_missing_handler(self):
        """测试未知工具调用返回错误"""
        workspace = Mock()
        workspace.path = None

        from harness.tools.registry import create_tool_registry

        tools = create_tool_registry()
        loop = ToolCallLoop(workspace=workspace, tools=tools)

        state = {"requirement_id": 1}
        tc = Mock()
        tc.name = "unknown_tool_xyz"
        tc.arguments = {}

        result = loop._execute_tool(state, tc)
        assert result.success is False
        assert "未知工具" in result.error
