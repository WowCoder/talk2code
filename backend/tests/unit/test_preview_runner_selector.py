# -*- coding: utf-8 -*-
"""针对 preview_runner._resolve_selector 的选择器自适应单测（需求 126 事故）"""
import pytest
from unittest.mock import Mock


def doc():
    """模拟 Playwright page.frame 的 query_selector 行为：
    只认真实的 #startBtn，不认 AC 脚本的 #start-btn。"""
    d = Mock()
    real_ids = ["#startBtn", "#restartBtn", "#pauseBtn", "#close-btn"]
    d.query_selector.side_effect = lambda s: object() if s in real_ids else None
    d.evaluate.side_effect = lambda js, kw: \
        "#startBtn" if kw == "startbtn" else \
        ("#close-btn" if kw == "closebtn" else None)
    return d


def test_negative_passthrough():
    """原始选择器已存在 → 原样返回"""
    from harness.tools.preview_runner import _resolve_selector
    assert _resolve_selector(doc(), "#startBtn") == "#startBtn"


def test_hyphen_to_camel_resolved():
    """AC 脚本 #start-btn 应解析到真实 #startBtn（需求 126 事故）"""
    from harness.tools.preview_runner import _resolve_selector
    assert _resolve_selector(doc(), "#start-btn") == "#startBtn"


def test_underscore_variant():
    """#close-btn → #close-btn 本身已存在，应原样"""
    from harness.tools.preview_runner import _resolve_selector
    assert _resolve_selector(doc(), "#close-btn") == "#close-btn"


def test_returns_none_when_no_match():
    from harness.tools.preview_runner import _resolve_selector
    d = doc()
    d.query_selector.side_effect = lambda s: None
    d.evaluate.return_value = None
    assert _resolve_selector(d, "#nonexistent") is None
