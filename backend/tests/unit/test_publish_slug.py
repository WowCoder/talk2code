# -*- coding: utf-8 -*-
"""slug 生成测试（plan B4）。"""
from services.publish.slug import new_slug, is_valid_slug, _ALPHABET


def test_slug_length_20():
    assert len(new_slug()) == 20


def test_slug_charset_subset_crockford():
    s = new_slug()
    assert all(c in _ALPHABET for c in s)
    # Crockford 排除 I L O U
    assert not any(c in "ILOU" for c in s)


def test_slug_unique():
    assert new_slug() != new_slug()


def test_is_valid_slug():
    assert is_valid_slug(new_slug())
    assert not is_valid_slug("short")
