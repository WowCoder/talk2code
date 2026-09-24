# -*- coding: utf-8 -*-
"""本地存储测试（plan B3）。

注：沙箱拦截 /tmp/pytest-of-* 的 mkdir，故用 backend 下的临时目录而非 tmp_path fixture。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from services.publish.store import LocalFSStore, StoreError


def _make_store():
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    return LocalFSStore(d), d


def test_put_idempotent():
    store, d = _make_store()
    try:
        files = {"index.html": b"a", "app.js": b"b"}
        h = "abc123def456"
        key1 = store.put_bundle(h, files)
        key2 = store.put_bundle(h, files)
        assert key1 == key2
        assert store.exists(h)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_get_missing_raises_keyerror():
    store, d = _make_store()
    try:
        with pytest.raises(KeyError):
            store.get("nope", "index.html")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_path_traversal_blocked():
    store, d = _make_store()
    try:
        with pytest.raises(StoreError):
            store._safe_file("hash", "../../etc/passwd")
    finally:
        shutil.rmtree(d, ignore_errors=True)
