# -*- coding: utf-8 -*-
"""
Craft 加载器 —— 从原 craft_loader.py 迁移
"""

import os
import re
from pathlib import Path


def get_craft_dir() -> Path:
    """获取 craft 规则目录"""
    return Path(__file__).parent.parent.parent / "craft"


def load_craft_rules(names: list) -> str:
    """
    加载指定名称的 Craft 规则

    Args:
        names: 规则名称列表，如 ["typography", "color"]

    Returns:
        拼接后的规则文本
    """
    craft_dir = get_craft_dir()
    rules = []
    for name in names:
        rule_file = craft_dir / f"{name}.md"
        if rule_file.exists():
            content = rule_file.read_text(encoding="utf-8")
            rules.append(content)
    return "\n\n".join(rules)


def is_craft_enabled() -> bool:
    """检查 Craft 是否启用"""
    try:
        from config import settings
        return settings.LLM_CRAFT_ENABLED
    except Exception:
        return True


def get_default_craft_names() -> list:
    """获取默认的 Craft 规则名称"""
    return ["typography", "color", "accessibility-baseline", "anti-ai-slop"]


# ==================== Skill 加载（从原 skill_loader.py 迁移） ====================

_skills_cache: dict = None


def _get_skills_dir() -> Path:
    return Path(__file__).parent.parent.parent / "skills"


def _parse_frontmatter(text: str) -> tuple:
    """解析 YAML 前导元数据（简易解析，避免引入 pyyaml）"""
    if not text.startswith('---'):
        return {}, text

    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', text, re.DOTALL)
    if not match:
        return {}, text

    frontmatter_str = match.group(1)
    body = match.group(2).strip()

    metadata = {}
    for line in frontmatter_str.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            if value.startswith('[') and value.endswith(']'):
                items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',') if v.strip()]
                metadata[key] = items
            else:
                metadata[key] = value.strip('"').strip("'")

    return metadata, body


def load_skill(name: str) -> dict:
    """
    加载指定名称的 Skill

    Args:
        name: Skill 名称（目录名）

    Returns:
        dict with frontmatter keys + 'body' key
    """
    skills_dir = _get_skills_dir()
    skill_dir = skills_dir / name

    skill_file = skill_dir / 'SKILL.md'
    if not skill_file.exists():
        return None

    text = skill_file.read_text(encoding='utf-8')
    metadata, body = _parse_frontmatter(text)

    metadata['body'] = body
    return metadata
