# -*- coding: utf-8 -*-
"""
测试 PermissionManager 权限判定、SandboxExecutor 超时/清理
对应 tasks.md 6.8
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.environment.permissions import (
    PermissionManager, PermissionLevel, PermissionResult
)
from harness.environment.sandbox import SandboxExecutor, SandboxResult


class TestPermissionLevel:
    """PermissionLevel 枚举测试"""

    def test_level_values(self):
        """测试权限等级值"""
        assert PermissionLevel.READ.value == 0
        assert PermissionLevel.WRITE.value == 1
        assert PermissionLevel.EXECUTE.value == 2

    def test_permission_result_values(self):
        """测试权限结果常量"""
        assert PermissionResult.ALLOW == "allow"
        assert PermissionResult.NEEDS_APPROVAL == "needs_approval"
        assert PermissionResult.DENIED == "denied"


class TestPermissionManagerCheck:
    """PermissionManager.check() 测试"""

    def test_read_always_allowed(self):
        """测试只读操作总是放行"""
        pm = PermissionManager()
        result = pm.check(1, "read_file", "read")
        assert result == PermissionResult.ALLOW

    def test_write_needs_approval_by_default(self):
        """测试写入操作默认需要审批"""
        pm = PermissionManager()
        result = pm.check(1, "write_file", "write")
        assert result == PermissionResult.NEEDS_APPROVAL

    def test_write_allowed_after_grant(self):
        """测试授权后写入放行"""
        pm = PermissionManager()
        pm.grant(1, "write")
        result = pm.check(1, "write_file", "write")
        assert result == PermissionResult.ALLOW

    def test_execute_always_needs_approval(self):
        """测试执行操作每次都需要审批"""
        pm = PermissionManager()
        result = pm.check(1, "execute_code", "execute")
        assert result == PermissionResult.NEEDS_APPROVAL

    def test_grant_then_revoke(self):
        """测试授权后撤销"""
        pm = PermissionManager()
        pm.grant(1, "write")
        assert pm.check(1, "write_file", "write") == PermissionResult.ALLOW

        pm.revoke(1)
        assert pm.check(1, "write_file", "write") == PermissionResult.NEEDS_APPROVAL

    def test_different_requirements_independent(self):
        """测试不同需求的权限独立"""
        pm = PermissionManager()
        pm.grant(1, "write")

        # req 1 已授权
        assert pm.check(1, "write_file", "write") == PermissionResult.ALLOW
        # req 2 未授权
        assert pm.check(2, "write_file", "write") == PermissionResult.NEEDS_APPROVAL

    def test_unknown_permission_defaults_to_read(self):
        """测试未知权限字符串默认按只读处理"""
        pm = PermissionManager()
        result = pm.check(1, "unknown_tool", "unknown_level")
        assert result == PermissionResult.ALLOW

    def test_grant_with_level_string_1(self):
        """测试 grant 接受 '1' 字符串"""
        pm = PermissionManager()
        pm.grant(1, "1")
        assert pm.check(1, "write_file", "write") == PermissionResult.ALLOW


class TestSandboxExecutor:
    """SandboxExecutor 测试"""

    def test_sandbox_result_success(self):
        """测试沙箱成功结果"""
        result = SandboxResult(True, output="JS 语法检查通过")
        assert result.success is True
        assert result.output == "JS 语法检查通过"

    def test_sandbox_result_error(self):
        """测试沙箱错误结果"""
        result = SandboxResult(False, error="执行超时")
        assert result.success is False
        assert result.error == "执行超时"

    def test_execute_without_workspace(self):
        """测试无 workspace 时执行失败"""
        sandbox = SandboxExecutor(workspace=None)
        result = sandbox.execute("index.html")
        assert result.success is False
        assert "未绑定" in result.error

    def test_execute_js_file_calls_node(self):
        """测试 JS 文件调用 node --check"""
        workspace = Mock()
        workspace.read.return_value = "console.log(1)"
        sandbox = SandboxExecutor(workspace=workspace)

        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            result = sandbox.execute("app.js")
            # 检查调用了 node --check
            assert mock_run.called

    def test_execute_html_file_uses_html_parser(self):
        """测试 HTML 文件使用 HTMLParser 验证"""
        workspace = Mock()
        workspace.read.return_value = "<html><head></head><body><h1>Hello</h1></body></html>"
        sandbox = SandboxExecutor(workspace=workspace)

        result = sandbox.execute("index.html")
        assert result.success is True
        assert "有效" in result.output

    def test_execute_invalid_html(self):
        """测试无效 HTML"""
        workspace = Mock()
        workspace.read.return_value = "<html><unclosed_tag></html>"
        sandbox = SandboxExecutor(workspace=workspace)

        result = sandbox.execute("index.html")
        # HTMLParser 很宽容，大多数"无效" HTML 也能解析
        # 只检查不会崩溃
        assert result is not None

    def test_execute_unknown_extension(self):
        """测试未知扩展名跳过验证"""
        workspace = Mock()
        workspace.read.return_value = "some content"
        sandbox = SandboxExecutor(workspace=workspace)

        result = sandbox.execute("data.json")
        assert result.success is True
        assert "跳过" in result.output

    def test_execute_node_not_found(self):
        """测试 Node.js 未安装时的降级处理"""
        workspace = Mock()
        workspace.read.return_value = "console.log(1)"
        sandbox = SandboxExecutor(workspace=workspace)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = sandbox._check_js_syntax("console.log(1)")
            assert result.success is True
            assert "未安装" in result.output

    def test_execute_timeout(self):
        """测试执行超时"""
        import subprocess as sp
        workspace = Mock()
        workspace.read.return_value = "while(true){}"
        sandbox = SandboxExecutor(workspace=workspace)

        with patch("subprocess.run", side_effect=sp.TimeoutExpired("node", 30)):
            result = sandbox._check_js_syntax("while(true){}")
            assert result.success is False
            assert "超时" in result.error

    def test_cleanup(self):
        """测试清理不报错"""
        sandbox = SandboxExecutor()
        sandbox.cleanup()  # 不应抛异常
