# -*- coding: utf-8 -*-
"""
测试 GitVersioning commit/log/rollback
对应 tasks.md 4.7
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.state.versioning import GitVersioning
from harness.state.workspace import WorkspaceFS


class TestGitVersioningInit:
    """Git 初始化测试"""

    @patch("pathlib.Path.exists", return_value=False)
    @patch("subprocess.run")
    def test_init_runs_git_commands(self, mock_run, mock_exists):
        """测试 __init__ 自动执行 git init 和 config"""
        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)

        calls = mock_run.call_args_list
        assert len(calls) == 3
        # git init
        assert calls[0][0][0] == ["git", "init"]
        assert calls[0][1]["cwd"] == "/tmp/test_ws"
        # git config user.name
        assert calls[1][0][0] == ["git", "config", "user.name", "Talk2Code Agent"]
        # git config user.email
        assert calls[2][0][0] == ["git", "config", "user.email", "agent@talk2code.local"]


class TestGitVersioningCommit:
    """提交测试"""

    @patch("pathlib.Path.exists", return_value=True)  # 跳过自动 init
    @patch("subprocess.run")
    def test_commit_stages_and_commits(self, mock_run, mock_exists):
        """测试 commit 暂存并提交"""
        mock_head = Mock()
        mock_head.stdout = "abc123def\n"
        mock_run.return_value = mock_head

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        result = git.commit("test commit")

        calls = mock_run.call_args_list
        # git add -A
        assert calls[0][0][0] == ["git", "add", "-A"]
        # git commit -m
        assert calls[1][0][0] == ["git", "commit", "-m", "test commit"]
        # git rev-parse HEAD
        assert result == "abc123def"


class TestGitVersioningLog:
    """日志测试"""

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_log_returns_commit_list(self, mock_run, mock_exists):
        """测试 log 返回提交列表"""
        mock_result = Mock()
        mock_result.stdout = "hash1|first commit|2024-01-01 10:00:00\nhash2|second commit|2024-01-01 11:00:00\n"
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        log = git.log(max_count=10)

        assert len(log) == 2
        assert log[0]["hash"] == "hash1"
        assert log[0]["message"] == "first commit"
        assert log[0]["time"] == "2024-01-01 10:00:00"
        assert log[1]["hash"] == "hash2"

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_log_empty_repo(self, mock_run, mock_exists):
        """测试空仓库返回空日志"""
        mock_result = Mock()
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        log = git.log()

        assert log == []


class TestGitVersioningRollback:
    """回滚测试"""

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_rollback_success(self, mock_run, mock_exists):
        """测试回滚成功"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        result = git.rollback("abc123")

        mock_run.assert_called_with(
            ["git", "reset", "--hard", "abc123"],
            cwd="/tmp/test_ws", capture_output=True
        )
        assert result is True

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_rollback_failure(self, mock_run, mock_exists):
        """测试回滚失败"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        result = git.rollback("invalid_hash")

        assert result is False


class TestGitVersioningHasChanges:
    """变更检测测试"""

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_has_changes_true(self, mock_run, mock_exists):
        """测试有未提交变更"""
        mock_result = Mock()
        mock_result.stdout = "M index.html\n"
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        assert git.has_changes() is True

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_has_changes_false(self, mock_run, mock_exists):
        """测试无未提交变更"""
        mock_result = Mock()
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        workspace = Mock()
        workspace.path = "/tmp/test_ws"

        git = GitVersioning(workspace)
        assert git.has_changes() is False
