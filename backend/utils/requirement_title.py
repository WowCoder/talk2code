# -*- coding: utf-8 -*-
"""需求名称：把用户整段输入压成一句能扫读的短名。

背景：此前 title 直接存 content[:100]，于是列表里每一行都是「帮我做一个可以……
的网页，要求……」的前 100 字 —— 标题区放不下就靠 CSS 省略号截断，用户既看不全
也分不清哪条是哪条。名称的作用是「一眼认出这条需求」，不是「复述整段输入」。

这里只做确定性规则（零 LLM）：需求刚建、TL 还没产出计划时靠它占位；TL 出计划后
由 plan.requirement_restated（一句人话复述）覆盖为正式名称。
"""
import re

__all__ = ["summarize_title", "TITLE_MAX_LEN"]

TITLE_MAX_LEN = 30

# 祈使前缀：用户开口几乎都是「帮我做一个……」，真正描述对象的是后半段。
# 必须长优先排序，否则「帮我」会先命中，把「做一个」留在标题里。
_PREFIXES = (
    "请帮我做一个", "请帮我写一个", "请帮我", "请做一个", "请写一个", "请做", "请写", "请",
    "能不能帮我做", "能不能帮我", "能不能做一个", "能不能做", "能不能",
    "可以帮我做一个", "可以帮我写一个", "可以帮我", "可以做一个", "可以做",
    "麻烦帮我做一个", "麻烦帮我", "麻烦做一个", "麻烦",
    "帮我做一个", "帮我写一个", "帮我实现", "帮我开发", "帮我生成", "帮我做", "帮我写", "帮我",
    "我想要一个", "我想要", "我想做一个", "我想写一个", "我想做", "我想",
    "我要一个", "我要做", "我要",
    "给我做一个", "给我写", "给我",
    "做一个", "做个", "写一个", "写个", "实现", "开发", "生成", "来一个",
)

# 第一句之后的都是补充说明，不进名称
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n；;，,]")


def _strip_prefix(text: str) -> str:
    """反复剥掉祈使前缀（「请帮我做一个 X」→「X」）。"""
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            # 至少留 2 个字：否则「帮我做一个 App」会被剥到只剩一个字，
            # 「写首诗」这类短输入也会被整段吃掉。
            if text.startswith(prefix) and len(text) - len(prefix) >= 2:
                text = text[len(prefix):].lstrip(" \t：:、")
                changed = True
                break
    return text


def summarize_title(content: str, max_len: int = TITLE_MAX_LEN) -> str:
    """把需求原文压成不超过 max_len 个字的短名。

    规则：取第一句 → 剥祈使前缀 → 超长截断加省略号。任何一步失败都退回原文截断，
    保证返回值永远非空（title 列 NOT NULL）。
    """
    text = (content or "").strip()
    if not text:
        return "未命名需求"

    first = _SENTENCE_SPLIT.split(text)[0].strip() or text
    short = _strip_prefix(first).strip() or first

    if len(short) > max_len:
        return short[: max_len - 1].rstrip() + "…"
    return short or "未命名需求"
