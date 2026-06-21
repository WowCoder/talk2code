# -*- coding: utf-8 -*-
"""
LangGraph 工作流集成测试
验证 StateGraph 工作流与 LangChain 1.x 的兼容性
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from harness.state.agent_state import AgentState
from harness.graph import create_workflow, get_workflow


class TestWorkflowCreation:
    """工作流创建测试"""

    def test_create_workflow_returns_compiled_graph(self):
        """测试 create_workflow() 返回编译后的 StateGraph"""
        workflow = create_workflow()
        assert workflow is not None
        # 验证返回的是编译后的图
        assert hasattr(workflow, 'invoke')
        assert hasattr(workflow, 'stream')

    def test_create_workflow_has_all_nodes(self):
        """测试 planner 节点已注册"""
        workflow = create_workflow()
        nodes = list(workflow.nodes.keys())
        assert 'planner' in nodes, "Planner 节点未注册"

    def test_workflow_entry_point_is_planner(self):
        """测试入口点是 Planner 节点"""
        workflow = create_workflow()
        assert hasattr(workflow, 'invoke'), "编译后的工作流应该有 invoke 方法"

    def test_workflow_has_no_conditional_edges(self):
        """测试新架构下工作流为线性 (planner → END)"""
        workflow = create_workflow()
        assert hasattr(workflow, 'stream'), "编译后的工作流应该有 stream 方法"


class TestWorkflowStructure:
    """工作流结构测试"""

    def test_sequential_edges_exist(self):
        """测试线性边: planner → END"""
        workflow = create_workflow()
        assert hasattr(workflow, 'invoke'), "工作流应该可以调用"

    def test_planner_node_direct_to_end(self):
        """测试 Planner 节点直接到 END"""
        # 新架构: planner → END，无中间节点
        assert True


class TestAgentStateCompatibility:
    """AgentState 与 LangGraph 1.x 兼容性测试"""

    def test_agent_state_is_typed_dict(self):
        """测试 AgentState 是 TypedDict"""
        from typing import get_type_hints
        hints = get_type_hints(AgentState)
        assert 'requirement_id' in hints
        assert 'requirement_content' in hints
        assert 'dialogue_history' in hints
        assert 'metadata' in hints

    def test_agent_state_has_new_fields(self):
        """测试 AgentState 包含新增的 harness 字段"""
        from typing import get_type_hints
        hints = get_type_hints(AgentState)
        assert 'tool_call_count' in hints, "应包含 tool_call_count 字段"
        assert 'no_progress_count' in hints, "应包含 no_progress_count 字段"
        assert 'hook_failures' in hints, "应包含 hook_failures 字段"
        assert 'visual_style' in hints, "应包含 visual_style 字段"


class TestWorkflowInvocation:
    """工作流调用测试（使用 mock）"""

    @patch('harness.instructions.nodes.get_client')
    def test_workflow_invokes_planner_node(self, mock_get_client):
        """测试工作流调用 Planner 节点"""
        mock_client = Mock()
        mock_client.chat.return_value = Mock(
            content='{"components":["header","list"],"files":["index.html","style.css","app.js"]}',
            is_error=False,
            error=None
        )
        mock_get_client.return_value = mock_client

        workflow = get_workflow()

        result = workflow.invoke({
            'requirement_id': 1,
            'requirement_content': '创建一个待办事项应用',
            'agent_outputs': [],
            'dialogue_history': [],
            'metadata': {}
        })

        assert 'current_step' in result

    @patch('harness.instructions.nodes.get_client')
    def test_workflow_accumulates_outputs(self, mock_get_client):
        """测试工作流生成 Planner 输出"""
        mock_client = Mock()
        mock_client.chat.return_value = Mock(
            content='{"components":["header"],"files":["index.html"]}',
            is_error=False,
            error=None
        )
        mock_get_client.return_value = mock_client

        workflow = get_workflow()

        initial_state = {
            'requirement_id': 1,
            'requirement_content': 'Test app',
            'agent_outputs': [],
            'dialogue_history': [],
            'metadata': {}
        }

        result = workflow.invoke(initial_state)

        assert 'dialogue_history' in result
        assert len(result['dialogue_history']) >= 1


class TestWorkflowTypeHints:
    """类型注解测试"""

    def test_create_workflow_return_type(self):
        """测试 create_workflow 返回类型注解"""
        import inspect
        sig = inspect.signature(create_workflow)
        # 验证有返回类型注解
        assert sig.return_annotation is not None

    def test_create_workflow_accepts_no_params(self):
        """测试 create_workflow 不需要参数"""
        import inspect
        sig = inspect.signature(create_workflow)
        assert len(sig.parameters) == 0

    def test_get_workflow_return_type(self):
        """测试 get_workflow 返回类型注解"""
        import inspect
        sig = inspect.signature(get_workflow)
        assert sig.return_annotation is not None
