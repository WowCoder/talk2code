# -*- coding: utf-8 -*-
"""
WorkspaceFS —— 每个需求独立工作目录，三层文件隔离

工作区根目录通过 config.WORKSPACE_DIR 配置，默认 BACKEND_DIR/workspaces，
持久化存储，重启不丢失（替代原 /tmp 方案）。
"""

from __future__ import annotations

import shutil
from pathlib import Path


class WorkspaceFS:
    """
    运行时文件系统，每个需求一个独立工作目录

    三层隔离：
    Layer 1: user_id 路径物理隔离
    Layer 2: _validate() 拒绝路径穿越
    Layer 3: TaskQueue 同一 requirement_id 仅一个线程执行
    """

    @staticmethod
    def _get_base_dir() -> Path:
        """从配置获取工作区根目录（持久化，重启不丢失）"""
        from config import settings
        if settings.WORKSPACE_DIR:
            return Path(settings.WORKSPACE_DIR)
        return settings.BACKEND_DIR / "workspaces"

    def __init__(self, user_id: int, requirement_id: int, base_dir: Path = None):
        self.user_id = user_id
        self.req_id = requirement_id
        root = base_dir if base_dir is not None else self._get_base_dir()
        self.path = root / str(user_id) / str(requirement_id)

    def _validate(self, filename: str):
        """防止路径穿越：拒绝上级目录（按路径段判断）与绝对路径。

        说明：
        - `'..' in filename` 是子串匹配，会误杀 foo..bar.js 这类合法文件名，
          改为按路径分段判断"是否存在 '..' 段"；
        - startswith 前缀比较无分隔符边界（/ws/u1/1234 会误命中 /ws/u1/12），
          改用 Python 3.9+ 的 Path.is_relative_to() 做组件级包含判断。
        """
        filename = filename.replace('\\', '/')  # 归一化反斜杠，覆盖 Windows 风格穿越
        if any(part == '..' for part in filename.split('/')):
            raise PermissionError(f"非法文件路径: {filename}")
        if filename.startswith('/'):
            raise PermissionError(f"非法文件路径: {filename}")
        resolved_root = self.path.resolve()
        full_path = (self.path / filename).resolve()
        if not full_path.is_relative_to(resolved_root):
            raise PermissionError(f"禁止访问工作目录外的文件: {filename}")

    def init(self, code_files: list[dict] = None):
        """初始化工作目录"""
        self.path.mkdir(parents=True, exist_ok=True)
        if code_files:
            for f in code_files:
                self._validate(f["filename"])
                filepath = self.path / f["filename"]
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(f["content"], encoding="utf-8")

    def read(self, filename: str) -> str:
        self._validate(filename)
        filepath = self.path / filename
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filename}")
        return filepath.read_text(encoding="utf-8")

    def write(self, filename: str, content: str):
        self._validate(filename)
        filepath = self.path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    def list(self) -> list[str]:
        """列出工作区所有文件（相对路径），排除 .git"""
        files = []
        if not self.path.exists():
            return files
        for f in self.path.rglob("*"):
            if f.is_file() and '.git' not in f.parts:
                files.append(str(f.relative_to(self.path)))
        return files

    def delete(self, filename: str):
        self._validate(filename)
        filepath = self.path / filename
        if filepath.exists():
            filepath.unlink()

    def exists(self, filename: str) -> bool:
        self._validate(filename)
        return (self.path / filename).exists()

    def snapshot(self) -> list[dict]:
        """获取当前所有文件的快照 [{filename, content}]"""
        if not self.path.exists():
            return []
        return [
            {
                "filename": str(f.relative_to(self.path)),
                "content": f.read_text(encoding="utf-8"),
            }
            for f in self.path.rglob("*")
            if f.is_file() and '.git' not in f.parts
        ]

    def cleanup(self):
        """清理工作目录"""
        shutil.rmtree(self.path, ignore_errors=True)
