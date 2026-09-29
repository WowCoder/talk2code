# -*- coding: utf-8 -*-
from __future__ import annotations
"""
GitVersioning —— 每次代码变更自动 commit，支持 diff 和回滚
"""

import subprocess
from pathlib import Path
from harness.state.workspace import WorkspaceFS


# 本地 git 命令的超时（秒）。
#
# git 在**本地仓库**上操作，正常都在毫秒级；给 30s 是兜「仓库被另一个进程
# lock 住 / 文件系统卡住」这类情况。此前这 9 处 subprocess.run 都不带 timeout，
# 一旦卡住就是**永久阻塞** —— 版本记录是旁路能力，却能把整个 ToolCallLoop
# 拖死在这一步上（对应「所有外部调用必须有显式超时」）。
# 超时不重试：上层每次代码变更都会重新 commit，重试没有额外收益。
_GIT_TIMEOUT_S = 30


def _run_git(args: list, cwd):
    """执行 git 命令，失败时返回 None 而不是抛异常。

    返回 None 表示"这一步没成功"。所有调用方都不把它当致命错误 —— 版本记录
    是旁路能力，不能因为 git 不可用就阻断代码生成。
    必须捕获 TimeoutExpired：默认它会上抛，一路冒泡直接打断编码循环。
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


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
        _run_git(["init"], self.workspace.path)
        _run_git(["config", "user.name", "Talk2Code Agent"], self.workspace.path)
        _run_git(["config", "user.email", "agent@talk2code.local"], self.workspace.path)

    def commit(self, message: str) -> str:
        """暂存所有变更并提交，返回 commit hash"""
        _run_git(["add", "-A"], self.workspace.path)
        _run_git(["commit", "-m", message], self.workspace.path)
        return self._get_head()

    def log(self, max_count: int = 20) -> list[dict]:
        """获取提交历史"""
        result = _run_git(
            ["log", f"-{max_count}", "--format=%H|%s|%ai"], self.workspace.path,
        )
        if result is None or not (result.stdout or "").strip():
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
        result = _run_git(["reset", "--hard", commit_hash], self.workspace.path)
        return result is not None and result.returncode == 0

    def has_changes(self) -> bool:
        """检查是否有未提交的变更"""
        result = _run_git(["status", "--porcelain"], self.workspace.path)
        return bool(result is not None and (result.stdout or "").strip())

    def _get_head(self) -> str:
        result = _run_git(["rev-parse", "HEAD"], self.workspace.path)
        return (result.stdout or "").strip() if result is not None else ""
