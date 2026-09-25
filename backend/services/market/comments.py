# -*- coding: utf-8 -*-
"""留言校验（Ship 2，设计文档 §8）。

三道约束，按成本从低到高：
1. 长度与非空 —— 纯字符串判断，零成本；
2. 频率 —— 同一用户 60 秒内的自有留言条数（比 IP 限流准，且不用额外存储）；
3. 关键词 —— 词表由部署方通过 MARKET_COMMENT_BLOCKWORDS 提供。

为什么不内置词库：一个硬编码的几十词"敏感词表"是**假装能用**——它拦不住真正的
垃圾内容，却会让代码看起来像做了审核。词表是可配置项，没配就是没启用，这点必须
诚实，不能靠内置几个词假装解决了。
"""
from typing import Optional

from config import settings

MAX_BODY_LEN = 200


def blocked_words() -> list:
    raw = (settings.MARKET_COMMENT_BLOCKWORDS or "").strip()
    if not raw:
        return []
    return [w.strip() for w in raw.split(",") if w.strip()]


def validate_body(body: str) -> Optional[str]:
    """返回错误文案；通过则返回 None。"""
    text = (body or "").strip()
    if not text:
        return "留言内容不能为空"
    if len(text) > MAX_BODY_LEN:
        return f"留言最多 {MAX_BODY_LEN} 个字"
    for w in blocked_words():
        if w in text:
            return "留言含有不允许的内容"
    return None


def too_frequent(recent_count: int) -> bool:
    """recent_count = 该用户最近 60 秒内已发的留言条数。"""
    return recent_count >= int(settings.MARKET_COMMENT_MAX_PER_MINUTE or 10)
