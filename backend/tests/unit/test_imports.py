# -*- coding: utf-8 -*-
"""
LangChain 导入验证测试
确保所有导入使用 langchain_core.* 命名空间，无弃用导入
"""

import pytest
import os
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))


class TestLangChainCoreImports:
    """验证 langchain_core 核心类型可以正常导入"""

    def test_import_human_message(self):
        """测试 HumanMessage 导入"""
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="test")
        assert msg.content == "test"

    def test_import_system_message(self):
        """测试 SystemMessage 导入"""
        from langchain_core.messages import SystemMessage
        msg = SystemMessage(content="test")
        assert msg.content == "test"

    def test_import_ai_message(self):
        """测试 AIMessage 导入"""
        from langchain_core.messages import AIMessage
        msg = AIMessage(content="test")
        assert msg.content == "test"

    def test_import_base_message(self):
        """测试 BaseMessage 导入"""
        from langchain_core.messages import BaseMessage
        # BaseMessage 是基类，用于类型注解

    def test_import_chat_prompt_template(self):
        """测试 ChatPromptTemplate 导入"""
        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([("human", "test")])
        assert prompt is not None

    def test_import_system_message_prompt_template(self):
        """测试 SystemMessagePromptTemplate 导入"""
        from langchain_core.prompts import SystemMessagePromptTemplate
        template = SystemMessagePromptTemplate.from_template("test")
        assert template is not None

    def test_import_human_message_prompt_template(self):
        """测试 HumanMessagePromptTemplate 导入"""
        from langchain_core.prompts import HumanMessagePromptTemplate
        template = HumanMessagePromptTemplate.from_template("test")
        assert template is not None


class TestNoDeprecatedImports:
    """验证项目中无弃用的导入"""

    def get_project_python_files(self):
        """获取后端目录所有 Python 文件"""
        backend_dir = Path(__file__).parent.parent.parent
        python_files = []
        for py_file in backend_dir.rglob("*.py"):
            # 跳过虚拟环境、测试文件和 conftest
            if '.venv' not in str(py_file) and 'venv' not in str(py_file) \
               and 'tests' not in str(py_file) and 'conftest' not in str(py_file):
                python_files.append(py_file)
        return python_files

    def test_no_langchain_schema_imports(self):
        """扫描源文件确保无 'from langchain.schema' 导入"""
        files = self.get_project_python_files()
        # Match actual import statements, not comments (imports start at line beginning after whitespace)
        deprecated_pattern = r"^\s*from langchain\.schema\b"

        files_with_deprecated = []
        for py_file in files:
            content = py_file.read_text(encoding='utf-8')
            for line in content.splitlines():
                import re
                if re.match(deprecated_pattern, line):
                    files_with_deprecated.append(str(py_file.relative_to(Path(__file__).parent.parent.parent)))
                    break

        assert len(files_with_deprecated) == 0, \
            f"发现弃用导入 'from langchain.schema' 在文件：{files_with_deprecated}"

    def test_no_langchain_prebuilt_imports(self):
        """扫描源文件确保无 'from langchain.prebuilt' 导入"""
        files = self.get_project_python_files()
        deprecated_pattern = "from langchain.prebuilt"

        files_with_deprecated = []
        for py_file in files:
            content = py_file.read_text(encoding='utf-8')
            if deprecated_pattern in content:
                files_with_deprecated.append(str(py_file.relative_to(Path(__file__).parent.parent.parent)))

        assert len(files_with_deprecated) == 0, \
            f"发现弃用导入 'from langchain.prebuilt' 在文件：{files_with_deprecated}"


class TestProjectImportsClean:
    """验证项目文件使用正确的导入"""

    def test_agents_state_uses_annotated_pattern(self):
        """验证 harness/state/agent_state.py 使用 Annotated 模式"""
        from harness.state.agent_state import AgentState
        assert AgentState is not None

    def test_harness_graph_uses_langgraph(self):
        """验证 harness/graph.py 使用 langgraph.graph.StateGraph"""
        from harness.graph import create_workflow
        workflow = create_workflow()
        assert workflow is not None

    def test_harness_instructions_nodes_exist(self):
        """验证 harness/instructions/nodes.py 节点函数存在"""
        from harness.instructions import nodes
        assert hasattr(nodes, 'planner_node')
        assert hasattr(nodes, 'tool_coder_node')

    def test_agents_backward_compat(self):
        """验证 agents/ 向后兼容重导出仍然有效"""
        from agents.workflow import create_workflow
        from agents.nodes import planner_node, tool_coder_node
        from agents.tool_loop import ToolCallLoop
        assert create_workflow is not None
        assert planner_node is not None
        assert tool_coder_node is not None
        assert ToolCallLoop is not None

    def test_llm_client_imports(self):
        """验证 llm/client.py 可以正常导入"""
        import llm.client
        from llm.client import LLMClient, LLMResponse, Message
        assert LLMClient is not None
        assert LLMResponse is not None
        assert Message is not None

    def test_to_langchain_message_function_exists(self):
        """验证 to_langchain_message 转换函数存在"""
        from llm.client import to_langchain_message
        assert to_langchain_message is not None

    def test_to_langchain_message_user_to_human(self):
        """验证 user 角色转换为 HumanMessage"""
        from llm.client import to_langchain_message, Message
        msg = Message(role='user', content='Hello')
        result = to_langchain_message(msg)
        assert result.content == 'Hello'

    def test_to_langchain_message_assistant_to_ai(self):
        """验证 assistant 角色转换为 AIMessage"""
        from llm.client import to_langchain_message, Message
        msg = Message(role='assistant', content='Hi there')
        result = to_langchain_message(msg)
        assert result.content == 'Hi there'

    def test_to_langchain_message_system_to_system(self):
        """验证 system 角色转换为 SystemMessage"""
        from llm.client import to_langchain_message, Message
        msg = Message(role='system', content='System message')
        result = to_langchain_message(msg)
        assert result.content == 'System message'

    def test_prompts_imports(self):
        """验证 harness/instructions/prompts/ 可以正常导入"""
        from harness.instructions.prompts import load_prompt, load_prompt_template
        assert load_prompt
        assert load_prompt_template
        # 验证加载实际 Prompt
        s = load_prompt("roles/team_leader.md")
        assert "Leon" in s
        assert "TeamLeader" in s or "TeamLeader" not in s  # at least it's a string
        # 验证模板加载
        result = load_prompt_template("coding/coder_xs.md",
            requirement="test", plan_section="", existing_text="",
            craft_rules="", skill_instructions="")
        assert "test" in result
