# -*- coding: utf-8 -*-
"""slug 生成：Crockford Base32，20 字符，不可枚举。

设计文档 §3.2：随机字节 → Crockford Base32 → 20 字符。
Crockford Base32 每字符 5 bit，20 字符 = 100 bit；取 13 字节(104 bit) 的高 100 bit。
字符集排除 I/L/O/U（易混淆），解码时也不区分大小写。
"""
import secrets

# Crockford Base32 字母表（无 I L O U）
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_slug() -> str:
    """生成 20 字符 Crockford Base32 slug。"""
    raw = secrets.token_bytes(13)  # 104 bit
    num = int.from_bytes(raw, "big") >> 4  # 取高 100 bit
    chars = []
    for _ in range(20):
        chars.append(_ALPHABET[num & 31])
        num >>= 5
    return "".join(reversed(chars))


def is_valid_slug(slug: str) -> bool:
    """slug 是否合法（长度 20 且全在 Crockford 字母表内）。"""
    return len(slug) == 20 and all(c in _ALPHABET for c in slug)
