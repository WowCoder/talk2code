# -*- coding: utf-8 -*-
"""
统一 Prompt 加载器 —— 所有 LLM 提示词从 .md 文件加载，集中管理。

用法:
    from harness.instructions.prompts import load_prompt, load_prompt_template

    # 纯文本 Prompt
    system = load_prompt("coding/tl_analysis.md")

    # 带占位符的模板
    prompt = load_prompt_template("coding/chat_modify.md",
        user_message="添加删除按钮",
        file_list_text="- index.html\n- app.js",
        ...
    )
"""

from pathlib import Path
from functools import lru_cache

_PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=128)
def load_prompt(rel_path: str) -> str:
    """加载 .md 文件中的 Prompt 文本。结果会被 LRU 缓存。

    Args:
        rel_path: 相对于 prompts/ 目录的路径，如 "roles/team_leader.md"

    Returns:
        str: Prompt 文本内容（已去除首尾空白）
    """
    file_path = _PROMPTS_DIR / rel_path
    if not file_path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {file_path}")
    return file_path.read_text(encoding="utf-8").strip()


def load_prompt_template(rel_path: str, **kwargs) -> str:
    """加载带 {placeholder} 占位符的 Prompt 模板，并用 kwargs 填充。

    Args:
        rel_path: 相对于 prompts/ 目录的路径
        **kwargs: 模板中 {key} 对应的值

    Returns:
        str: 填充后的 Prompt 文本
    """
    template = load_prompt(rel_path)
    return template.format(**kwargs)
