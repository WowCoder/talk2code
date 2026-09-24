# -*- coding: utf-8 -*-
"""本地文件系统存储（ObjectStore v1 实现）。

落盘布局：{PUBLISH_STORE_DIR}/{hash[:2]}/{hash}/{relpath}
设计文档 §3.3 / plan B3。路径穿越防护：所有访问必须落在 hash 目录内。
"""
import os
from pathlib import Path
from typing import Dict

from config import settings


class StoreError(Exception):
    """存储层错误（路径穿越 / IO 失败等）。"""


class LocalFSStore:
    def __init__(self, root: str | None = None):
        root_path = Path(root) if root else settings.PUBLISH_STORE_PATH
        self.root = root_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 内部：安全路径解析 ----
    def _bundle_dir(self, content_hash: str) -> Path:
        # hash 目录：{root}/{hash[:2]}/{hash}
        d = (self.root / content_hash[:2] / content_hash).resolve()
        # 防止 hash 本身含 ".." 逃出 root
        if not str(d).startswith(str(self.root) + os.sep):
            raise StoreError("invalid content_hash")
        return d

    def _safe_file(self, content_hash: str, relpath: str) -> Path:
        d = self._bundle_dir(content_hash)
        target = (d / relpath).resolve()
        if target != d and not str(target).startswith(str(d) + os.sep):
            raise StoreError(f"path escape: {relpath}")
        return target

    # ---- ObjectStore 接口 ----
    def put_bundle(self, content_hash: str, files: Dict[str, bytes]) -> str:
        """写入整个 bundle。幂等：目录已存在则跳过（零写入）。返回 store_key。"""
        d = self._bundle_dir(content_hash)
        if d.exists():
            return self._store_key(content_hash)
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            p = self._safe_file(content_hash, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return self._store_key(content_hash)

    def exists(self, content_hash: str) -> bool:
        return self._bundle_dir(content_hash).exists()

    def get(self, content_hash: str, relpath: str = "index.html") -> bytes:
        """取单文件内容；不存在抛 KeyError。"""
        p = self._safe_file(content_hash, relpath)
        if not p.exists():
            raise KeyError(relpath)
        return p.read_bytes()

    def _store_key(self, content_hash: str) -> str:
        return f"{content_hash[:2]}/{content_hash}"
