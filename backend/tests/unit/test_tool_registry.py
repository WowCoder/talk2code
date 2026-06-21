# -*- coding: utf-8 -*-
"""
测试 ToolRegistry 注册/查询/执行，每个 tool handler 的入参/出参
对应 tasks.md 2.6
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.tools.registry import (
    ToolRegistry, ToolDefinition, ToolResult, create_tool_registry
)


class TestToolDefinition:
    """ToolDefinition 数据类测试"""

    def test_tool_definition_create(self):
        """测试 ToolDefinition 创建"""
        def handler():
            return "ok"
        td = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object"},
            handler=handler,
            permission="read",
            max_retries=2
        )
        assert td.name == "test_tool"
        assert td.permission == "read"
        assert td.max_retries == 2
        assert td.handler() == "ok"

    def test_tool_definition_defaults(self):
        """测试 ToolDefinition 默认值"""
        def handler():
            pass
        td = ToolDefinition(
            name="default_tool",
            description="defaults",
            parameters={},
            handler=handler,
        )
        assert td.permission == "read"
        assert td.max_retries == 1


class TestToolResult:
    """ToolResult 数据类测试"""

    def test_tool_result_success(self):
        """测试成功结果"""
        result = ToolResult(content="文件已创建")
        assert result.success is True
        assert result.content == "文件已创建"
        assert result.error == ""

    def test_tool_result_error(self):
        """测试错误结果"""
        result = ToolResult(error="文件不存在")
        assert result.success is False
        assert result.error == "文件不存在"

    def test_tool_result_metadata_default(self):
        """测试默认 metadata"""
        result = ToolResult(content="ok")
        assert result.metadata == {}

    def test_tool_result_metadata_custom(self):
        """测试自定义 metadata"""
        result = ToolResult(content="ok", metadata={"size": 1024})
        assert result.metadata["size"] == 1024


class TestToolRegistryCore:
    """ToolRegistry 核心功能测试"""

    def test_register_and_get(self):
        """测试注册和获取工具"""
        registry = ToolRegistry()

        def handler(filename, content):
            return ToolResult(content=f"写入 {filename}")

        registry.register(ToolDefinition(
            name="write_file",
            description="写入文件",
            parameters={"type": "object", "properties": {"filename": {"type": "string"}}},
            handler=handler,
        ))

        tool = registry.get("write_file")
        assert tool is not None
        assert tool.name == "write_file"
        assert tool.permission == "read"

    def test_get_unknown_tool(self):
        """测试获取不存在的工具"""
        registry = ToolRegistry()
        assert registry.get("non_existent") is None

    def test_list_tools(self):
        """测试列出所有工具"""
        registry = ToolRegistry()

        registry.register(ToolDefinition(
            name="tool_a", description="a", parameters={}, handler=lambda: "a"
        ))
        registry.register(ToolDefinition(
            name="tool_b", description="b", parameters={}, handler=lambda: "b"
        ))

        tools = registry.list_tools()
        assert len(tools) == 2
        assert "tool_a" in tools
        assert "tool_b" in tools

    def test_get_permission(self):
        """测试获取工具权限"""
        registry = ToolRegistry()

        registry.register(ToolDefinition(
            name="read_tool", description="read", parameters={},
            handler=lambda: "ok", permission="read"
        ))
        registry.register(ToolDefinition(
            name="write_tool", description="write", parameters={},
            handler=lambda: "ok", permission="write"
        ))

        assert registry.get_permission("read_tool") == "read"
        assert registry.get_permission("write_tool") == "write"
        assert registry.get_permission("unknown") == "read"


class TestToolRegistryExecute:
    """ToolRegistry 执行测试"""

    def test_execute_success(self):
        """测试成功执行工具"""
        registry = ToolRegistry()

        def handler(a, b):
            return a + b

        registry.register(ToolDefinition(
            name="add",
            description="加法",
            parameters={"properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            handler=handler,
        ))

        result = registry.execute("add", {"a": 1, "b": 2})
        assert result.success
        assert result.content == "3"

    def test_execute_with_tool_result_return(self):
        """测试 handler 返回 ToolResult"""
        registry = ToolRegistry()

        def handler(filename):
            return ToolResult(content=f"已写入 {filename}", metadata={"bytes": 100})

        registry.register(ToolDefinition(
            name="write", description="写入",
            parameters={}, handler=handler,
        ))

        result = registry.execute("write", {"filename": "test.html"})
        assert result.success
        assert result.content == "已写入 test.html"
        assert result.metadata["bytes"] == 100

    def test_execute_unknown_tool(self):
        """测试执行未知工具"""
        registry = ToolRegistry()
        result = registry.execute("unknown_tool", {})
        assert result.success is False
        assert "未知工具" in result.error

    def test_execute_handler_exception(self):
        """测试 handler 抛异常"""
        registry = ToolRegistry()

        registry.register(ToolDefinition(
            name="fail_tool",
            description="总是失败",
            parameters={},
            handler=lambda: 1 / 0,
        ))

        result = registry.execute("fail_tool", {})
        assert result.success is False
        assert "division by zero" in result.error


class TestToolRegistrySchemas:
    """工具 Schema 生成测试"""

    def test_get_schemas_openai_format(self):
        """测试生成 OpenAI function calling 格式的 schema"""
        registry = ToolRegistry()

        registry.register(ToolDefinition(
            name="read_file",
            description="读取文件内容",
            parameters={"type": "object", "properties": {"filename": {"type": "string"}}},
            handler=lambda filename: "content",
        ))

        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "read_file"
        assert schemas[0]["function"]["description"] == "读取文件内容"
        assert "parameters" in schemas[0]["function"]

    def test_get_schemas_empty(self):
        """测试空注册表返回空 schemas"""
        registry = ToolRegistry()
        assert registry.get_schemas() == []


class TestCreateToolRegistry:
    """create_tool_registry 工厂函数测试"""

    def test_create_default_registry(self):
        """测试创建默认注册表"""
        registry = create_tool_registry()
        tools = registry.list_tools()
        # 应该至少包含 file_tools 和 code_tools
        assert "read_file" in tools
        assert "write_file" in tools
        assert "list_files" in tools
        assert "delete_file" in tools
        # code_tools
        assert "validate_html" in tools
        assert "lint_css" in tools
        assert "lint_js" in tools

    def test_default_permissions(self):
        """测试默认工具的权限"""
        registry = create_tool_registry()
        assert registry.get_permission("read_file") == "read"
        assert registry.get_permission("write_file") == "write"
        assert registry.get_permission("delete_file") == "write"
