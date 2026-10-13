# -*- coding: utf-8 -*-
"""需求名称摘要测试。

不可回退约束：
- 名称必须短（≤ TITLE_MAX_LEN），列表与导航栏要一眼扫完
- 名称永不为空（title 列 NOT NULL，空串会让详情页标题区空白）
- 剥掉「帮我做一个」这类祈使前缀，但不得把短输入整段吃掉
"""
from utils.requirement_title import TITLE_MAX_LEN, summarize_title


def test_short_input_kept_as_is():
    assert summarize_title("贪吃蛇") == "贪吃蛇"


def test_strips_imperative_prefix():
    assert summarize_title("帮我做一个待办清单应用，支持拖拽排序") == "待办清单应用"
    assert summarize_title("请帮我写一个天气查询 App") == "天气查询 App"
    assert summarize_title("我想做一个记账本") == "记账本"


def test_keeps_enough_text_when_remainder_is_tiny():
    """「做一个 App」这类：剥到只剩 2 个字就该停手，不能连对象一起剥掉。"""
    assert summarize_title("做一个 App") == "App"
    assert summarize_title("写首诗") == "写首诗"


def test_truncates_long_input_with_ellipsis():
    long = "做一个" + "很长的描述" * 20
    title = summarize_title(long)
    assert len(title) <= TITLE_MAX_LEN
    assert title.endswith("…")


def test_only_first_sentence():
    title = summarize_title("做一个贪吃蛇游戏。还要有计分板和音效")
    assert "计分板" not in title


def test_empty_input_never_yields_empty_title():
    assert summarize_title("")
    assert summarize_title("   ")
    assert summarize_title(None or "")


def test_no_prefix_to_strip_falls_back_to_first_sentence():
    assert summarize_title("番茄钟，25 分钟工作 + 5 分钟休息") == "番茄钟"
