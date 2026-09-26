# -*- coding: utf-8 -*-
"""LLM 响应 JSON 提取的鲁棒性回归。

需求 187 事故：agnes-3.0-flash 返回的 plan 完全正常（HTTP 200、plan 内容完整），
只在 `how_to_verify` 的值里夹了未转义的裸双引号（`"分数栏"最高分"显示10"`），
导致四层提取全部失败 → TeamLeader 抛「无法从 LLM 响应中提取 JSON」→ 需求判废。
"""

from harness.instructions.nodes import (
    _extract_json_from_llm_response,
    _rescue_unescaped_quotes,
)


def test_plain_json():
    assert _extract_json_from_llm_response('{"a": 1}') == {"a": 1}


def test_fenced_json():
    raw = '好的，计划如下：\n\n```json\n{"features": ["移动"]}\n```\n'
    assert _extract_json_from_llm_response(raw) == {"features": ["移动"]}


def test_unfenced_trailing_text():
    raw = '说明文字\n{"features": ["移动"]}\n以上。'
    assert _extract_json_from_llm_response(raw) == {"features": ["移动"]}


def test_unescaped_inner_quotes_rescued():
    """187 真实形态：字符串值内部夹裸双引号"""
    raw = (
        '```json\n'
        '{"acceptance_criteria": ['
        '{"id": "AC-4", "how_to_verify": "关闭页面重开，分数栏"最高分"显示10"}'
        ']}\n```'
    )
    got = _extract_json_from_llm_response(raw)
    assert got is not None, "裸引号不得导致整份 plan 报废"
    ac = got["acceptance_criteria"][0]
    assert ac["id"] == "AC-4"
    # 裸引号应被换成中文引号而非直接丢弃（保留原文语义）
    assert "\u201c" in ac["how_to_verify"] or "\u201d" in ac["how_to_verify"]
    assert "最高分" in ac["how_to_verify"]


def test_rescue_keeps_structural_quotes():
    """修复只作用于字符串值内部，不得破坏 JSON 结构引号"""
    src = '{"a": "x", "b": "y"}'
    assert _rescue_unescaped_quotes(src) == src
    import json
    assert json.loads(_rescue_unescaped_quotes(src)) == {"a": "x", "b": "y"}


def test_rescue_pairs_inner_quotes():
    out = _rescue_unescaped_quotes('{"v": "他说"你好"然后走了"}')
    assert out == '{"v": "他说\u201c你好\u201d然后走了"}'


def test_nested_object_and_array_unaffected():
    raw = '{"a": {"b": [1, 2]}, "c": "d"}'
    assert _extract_json_from_llm_response(raw) == {"a": {"b": [1, 2]}, "c": "d"}


def test_escaped_quote_preserved():
    raw = '{"v": "he said \\"hi\\""}'
    assert _extract_json_from_llm_response(raw) == {"v": 'he said "hi"'}


def test_garbage_returns_none():
    assert _extract_json_from_llm_response("我无法完成这个任务") is None
