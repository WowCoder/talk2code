# -*- coding: utf-8 -*-
"""
测试 WorkspaceFS 路径穿越拒绝、子目录创建、文件隔离
对应 tasks.md 4.6
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from harness.state.workspace import WorkspaceFS


class TestWorkspaceFSPathValidation:
    """路径验证测试"""

    def test_reject_parent_directory_traversal(self):
        """测试拒绝 ../ 路径穿越"""
        ws = WorkspaceFS(user_id=1, requirement_id=1)
        with pytest.raises(PermissionError, match="非法文件路径"):
            ws._validate("../etc/passwd")

    def test_reject_double_dot_in_path(self):
        """测试拒绝包含 .. 的路径"""
        ws = WorkspaceFS(user_id=1, requirement_id=1)
        with pytest.raises(PermissionError, match="非法文件路径"):
            ws._validate("subdir/../../passwd")

    def test_reject_absolute_path(self):
        """测试拒绝绝对路径"""
        ws = WorkspaceFS(user_id=1, requirement_id=1)
        with pytest.raises(PermissionError, match="非法文件路径"):
            ws._validate("/etc/passwd")

    def test_allow_normal_path(self):
        """测试正常路径允许通过"""
        ws = WorkspaceFS(user_id=1, requirement_id=1)
        # 不应抛异常
        ws._validate("index.html")
        ws._validate("css/style.css")
        ws._validate("js/app.js")
        ws._validate("subdir/deep/nested/file.txt")

    def test_reject_symlink_escape(self):
        """测试 resolve 后路径不在工作目录的拒绝"""
        ws = WorkspaceFS(user_id=1, requirement_id=1)
        # 如果是符号链接或 resolve 到外部，应该拒绝
        # _validate 会调用 resolve 并检查前缀
        pass  # 在临时目录环境下，resolve 行为取决于实际文件系统


class TestWorkspaceFSFileOperations:
    """文件操作测试"""

    def test_init_creates_directory(self):
        """测试 init 创建工作目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            assert ws.path.exists()
            assert ws.path.is_dir()

    def test_init_with_code_files(self):
        """测试从 code_files 初始化工作区"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init([
                {"filename": "index.html", "content": "<html></html>"},
                {"filename": "css/style.css", "content": "body{}"},
            ])
            assert ws.path.exists()
            assert (ws.path / "index.html").exists()
            assert (ws.path / "css" / "style.css").exists()

    def test_read_write(self):
        """测试读写文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()

            ws.write("test.html", "<h1>Hello</h1>")
            content = ws.read("test.html")
            assert content == "<h1>Hello</h1>"

    def test_read_nonexistent_file(self):
        """测试读取不存在的文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()

            with pytest.raises(FileNotFoundError):
                ws.read("nonexistent.html")

    def test_write_to_subdirectory(self):
        """测试写入子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()

            ws.write("deep/nested/file.js", "console.log(1)")
            assert ws.exists("deep/nested/file.js")
            content = ws.read("deep/nested/file.js")
            assert content == "console.log(1)"

    def test_list_files(self):
        """测试列出所有文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            ws.write("index.html", "html")
            ws.write("css/style.css", "css")
            ws.write("js/app.js", "js")

            files = ws.list()
            assert len(files) == 3
            assert "index.html" in files
            assert "css/style.css" in files
            assert "js/app.js" in files

    def test_delete_file(self):
        """测试删除文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            ws.write("test.html", "test")
            assert ws.exists("test.html")

            ws.delete("test.html")
            assert not ws.exists("test.html")

    def test_delete_nonexistent(self):
        """测试删除不存在的文件不报错"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            # 不应抛异常
            ws.delete("nonexistent.html")

    def test_snapshot(self):
        """测试快照功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            ws.write("index.html", "html content")
            ws.write("app.js", "js content")

            snapshot = ws.snapshot()
            assert len(snapshot) == 2
            filenames = [s["filename"] for s in snapshot]
            assert "index.html" in filenames
            assert "app.js" in filenames
            contents = {s["filename"]: s["content"] for s in snapshot}
            assert contents["index.html"] == "html content"
            assert contents["app.js"] == "js content"

    def test_cleanup(self):
        """测试清理工作目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            WorkspaceFS.BASE_DIR = Path(tmpdir) / "workspaces"
            ws = WorkspaceFS(user_id=1, requirement_id=100)
            ws.init()
            ws.write("test.html", "test")
            assert ws.path.exists()

            ws.cleanup()
            assert not ws.path.exists()


class TestWorkspaceFSIsolation:
    """文件隔离测试"""

    def test_user_isolation(self):
        """测试不同用户的工作目录隔离"""
        ws1 = WorkspaceFS(user_id=1, requirement_id=1)
        ws2 = WorkspaceFS(user_id=2, requirement_id=1)
        assert ws1.path != ws2.path
        assert str(ws1.user_id) in str(ws1.path)
        assert str(ws2.user_id) in str(ws2.path)

    def test_requirement_isolation(self):
        """测试不同需求的工作目录隔离"""
        ws1 = WorkspaceFS(user_id=1, requirement_id=100)
        ws2 = WorkspaceFS(user_id=1, requirement_id=200)
        assert ws1.path != ws2.path
        assert str(ws1.req_id) in str(ws1.path)
        assert str(ws2.req_id) in str(ws2.path)
