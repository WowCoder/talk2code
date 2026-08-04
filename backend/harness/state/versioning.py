# -*- coding: utf-8 -*-
from __future__ import annotations
"""
GitVersioning —— 每次代码变更自动 commit，支持 diff 和回滚
"""

import subprocess
from pathlib import Path
from harness.state.workspace import WorkspaceFS


class GitVersioning:
    """Git 版本控制，每次代码变更自动提交"""

    def __init__(self, workspace: WorkspaceFS):
        self.workspace = workspace
        self._ensure_initialized()

    def _ensure_initialized(self):
        """确保 git 仓库已初始化（幂等操作）"""
        git_dir = Path(self.workspace.path) / ".git"
        if git_dir.exists():
            return
        self.init()

    def init(self):
        """初始化 git 仓库（公开方法，允许显式重新初始化）"""
        subprocess.run(
            ["git", "init"],
            cwd=self.workspace.path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Talk2Code Agent"],
            cwd=self.workspace.path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "agent@talk2code.local"],
            cwd=self.workspace.path, capture_output=True
        )

    def commit(self, message: str) -> str:
        """暂存所有变更并提交，返回 commit hash"""
        subprocess.run(
            ["git", "add", "-A"],
            cwd=self.workspace.path, capture_output=True
        )
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        return self._get_head()

    def log(self, max_count: int = 20) -> list[dict]:
        """获取提交历史"""
        result = subprocess.run(
            ["git", "log", f"-{max_count}", "--format=%H|%s|%ai"],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        if not result.stdout.strip():
            return []
        commits = []
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|", 2)
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "time": parts[2] if len(parts) > 2 else "",
                })
        return commits

    def rollback(self, commit_hash: str) -> bool:
        """回滚到指定 commit"""
        result = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            cwd=self.workspace.path, capture_output=True
        )
        return result.returncode == 0

    def has_changes(self) -> bool:
        """检查是否有未提交的变更"""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        return bool(result.stdout.strip())

    def _get_head(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        return result.stdout.strip()
