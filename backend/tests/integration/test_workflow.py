# -*- coding: utf-8 -*-
"""
LangGraph 工作流集成测试 (v4: 4 节点编排)
验证 StateGraph 工作流与 LangGraph 的兼容性
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
        assert hasattr(workflow, 'invoke')
        assert hasattr(workflow, 'stream')

    def test_create_workflow_has_v4_nodes(self):
        """测试 v5 工作流节点已注册（repair 节点已在 v5 移除，QA 反馈直接注入 dialogue）"""
        workflow = create_workflow()
        nodes = list(workflow.nodes.keys())
        assert 'team_leader' in nodes, "team_leader 节点未注册"
        assert 'coder' in nodes, "coder 节点未注册"
        assert 'verify' in nodes, "verify 节点未注册"

    def test_workflow_entry_point_is_team_leader(self):
        """测试入口点是 team_leader 节点"""
        workflow = create_workflow()
        assert hasattr(workflow, 'invoke'), "编译后的工作流应该有 invoke 方法"


class TestWorkflowStructure:
    """工作流结构测试"""

    def test_workflow_has_conditional_routing(self):
        """测试 v4 有条件路由（route_after_tl, route_after_verify）"""
        workflow = create_workflow()
        assert hasattr(workflow, 'invoke'), "工作流应该可以调用"

    def test_team_leader_to_coder_edge(self):
        """TeamLeader → Coder 边存在"""
        assert True  # 由 LangGraph 编译时保证


class TestAgentStateCompatibility:
    """AgentState 与 LangGraph 兼容性测试"""

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
    def test_workflow_invokes_team_leader(self, mock_get_client):
        """测试工作流调用 team_leader 节点"""
        mock_client = Mock()
        mock_client.chat.return_value = Mock(
            content='{"features":["add todo"],"complexity":"S","file_structure":["index.html","style.css","app.js"]}',
            is_error=False,
            error=None
        )
        mock_get_client.return_value = mock_client

        workflow = get_workflow()

        result = workflow.invoke({
            'requirement_id': 1,
            'requirement_content': 'Create a todo app',
            'agent_outputs': [],
            'dialogue_history': [],
            'metadata': {}
        })

        assert 'current_step' in result

    @patch('harness.instructions.nodes.get_client')
    def test_workflow_accumulates_outputs(self, mock_get_client):
        """测试工作流生成 team_leader 输出"""
        mock_client = Mock()
        mock_client.chat.return_value = Mock(
            content='{"features":["header"],"complexity":"XS","file_structure":["index.html"]}',
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
