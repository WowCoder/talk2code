# -*- coding: utf-8 -*-
"""bundle 打包测试（plan B1 / B2）。"""
import pytest

from services.publish.bundle import build_bundle, compute_content_hash, BundleError


def test_content_hash_stable_order():
    f1 = {"a.js": b"x", "b.js": b"y"}
    f2 = {"b.js": b"y", "a.js": b"x"}
    assert compute_content_hash(f1) == compute_content_hash(f2)
    assert build_bundle(f1)[0] == build_bundle(f2)[0]


def test_content_hash_changes_on_byte():
    assert compute_content_hash({"a.js": b"x"}) != compute_content_hash({"a.js": b"y"})


def test_content_hash_changes_on_path():
    assert compute_content_hash({"a.js": b"x"}) != compute_content_hash({"dir/a.js": b"x"})


def test_bundle_returns_files():
    files = {"index.html": b"<html></html>"}
    h, out = build_bundle(files)
    assert out == files
    assert len(h) == 64  # sha256 hex


def test_bundle_blocks_external_cdn():
    # index.html 引用外链 → ENV-2 抛 BundleError
    files = {"index.html": b'<script src="https://cdn.example.com/x.js"></script>'}
    with pytest.raises(BundleError):
        build_bundle(files)
