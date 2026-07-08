# -*- coding: utf-8 -*-
"""
统一 Skills 加载器 —— LLM 自主选择 + 渐进式披露

所有可注入知识放在 skills/<name>/SKILL.md，YAML frontmatter 声明元数据。

选择策略：
  1. LLM 根据 name + when_to_use 自主选择（可靠、灵活）
  2. LLM 调用失败时降级到关键词匹配（triggers 字段）
  3. L0 / always=true 始终注入，不占用 LLM 选择

用法:
    from skills import load_for_task
    text = load_for_task("做一个待办清单")
"""

import json
import re
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent


# ==================== Frontmatter 解析 ====================

def _parse_frontmatter(text: str) -> tuple:
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
            if value.lower() == 'true':
                metadata[key] = True
            elif value.lower() == 'false':
                metadata[key] = False
            elif value.startswith('[') and value.endswith(']'):
                items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',') if v.strip()]
                metadata[key] = items
            else:
                metadata[key] = value.strip('"').strip("'")
    return metadata, body


# ==================== Skill ====================

class Skill:
    __slots__ = ('name', 'level', 'description', 'when_to_use', 'triggers', 'always', 'body')

    def __init__(self, metadata: dict, body: str):
        self.name = metadata.get('name', '')
        self.level = metadata.get('level', '')
        self.description = metadata.get('description', '')
        self.when_to_use = metadata.get('when_to_use', '')
        self.triggers = metadata.get('triggers', [])
        self.always = metadata.get('always', False)
        self.body = body

    def matches_keywords(self, requirement: str) -> bool:
        """关键词匹配（LLM 失败时的降级方案）"""
        if self.always or self.level == 'L0':
            return True
        if self.triggers:
            req_lower = requirement.lower()
            return any(t.lower() in req_lower for t in self.triggers)
        return True  # 无 triggers = 通用，始终匹配

    def __repr__(self):
        return f"Skill({self.name}, level={self.level or '-'})"


# ==================== SkillLoader ====================

class SkillLoader:

    def __init__(self):
        self._items: list[Skill] = []
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        if not _SKILLS_DIR.exists():
            self._loaded = True
            return
        for d in sorted(_SKILLS_DIR.iterdir()):
            if not d.is_dir():
                continue
            f = d / 'SKILL.md'
            if not f.exists():
                continue
            text = f.read_text(encoding='utf-8')
            metadata, body = _parse_frontmatter(text)
            if body:
                self._items.append(Skill(metadata=metadata, body=body))
        self._loaded = True

    # ---- 核心：LLM 自主选择 ----

    def _build_menu(self) -> str:
        """构建 Skill 菜单（只发 name + when_to_use，不发 body）"""
        lines = []
        for i, s in enumerate(self._items):
            if s.always or s.level == 'L0':
                continue  # L0 始终注入，不需要 LLM 判断
            lines.append(f"{i}. **{s.name}**: {s.when_to_use}")
        return "\n".join(lines)

    def _select_by_llm(self, requirement: str) -> list[int]:
        """LLM 根据菜单自主选择需要注入的 Skill"""
        menu = self._build_menu()
        if not menu:
            return list(range(len(self._items)))  # 只有 L0 时全部注入

        from harness.instructions.prompts import load_prompt, load_prompt_template
        system_prompt = load_prompt("skills/select_system.md")
        prompt = load_prompt_template("skills/select_prompt.md",
            requirement=requirement,
            menu=menu,
        )

        try:
            from llm.client import get_client
            client = get_client()
            resp = client.chat(
                prompt=prompt,
                system_prompt=system_prompt,
                use_memory=False,
                max_tokens=50,
                timeout=10,
            )
            if resp.is_error or not resp.content:
                raise ValueError("LLM 调用失败")

            # 解析 JSON 数组
            content = resp.content.strip()
            match = re.search(r'\[[\d,\s]*\]', content)
            if match:
                return json.loads(match.group())
            return []
        except Exception:
            return None  # None = 降级到关键词

    def select_skills(self, requirement: str) -> list[Skill]:
        """选择适用的 Skill：LLM 优先，失败降级到关键词"""
        self._ensure_loaded()

        # L0: always=true 始终注入
        selected = [s for s in self._items if s.always or s.level == 'L0']

        # 可选的 Skill（非 L0）
        selectable = [s for s in self._items if not (s.always or s.level == 'L0')]

        if not selectable:
            return selected

        # 尝试 LLM 选择
        indices = self._select_by_llm(requirement)

        if indices is not None:
            # LLM 成功
            for idx in indices:
                if 0 <= idx < len(self._items):
                    s = self._items[idx]
                    if s not in selected:
                        selected.append(s)
        else:
            # 降级：关键词匹配
            for s in selectable:
                if s.matches_keywords(requirement):
                    selected.append(s)

        return selected

    def load_for_task(self, requirement: str) -> str:
        """根据任务需求加载 Skill 正文"""
        skills = self.select_skills(requirement)
        parts = [s.body for s in skills]
        return "\n\n---\n\n".join(parts) if parts else ""

    def list_all(self) -> list[Skill]:
        self._ensure_loaded()
        return list(self._items)

    def get_selection_summary(self, requirement: str) -> str:
        """调试：返回选择摘要"""
        skills = self.select_skills(requirement)
        lines = [f"需求: {requirement[:80]}"]
        for s in self._items:
            marker = "✓" if s in skills else "✗"
            tag = f"[{s.level}]" if s.level else "[Skill]"
            lines.append(f"  {marker} {tag} {s.name}")
        lines.append(f"  总注入: {len(self.load_for_task(requirement))} chars")
        return "\n".join(lines)


# ==================== 全局单例 ====================

_loader: SkillLoader = None

def get_loader() -> SkillLoader:
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader

def load_for_task(requirement: str) -> str:
    return get_loader().load_for_task(requirement)

def select_skills(requirement: str) -> list[Skill]:
    return get_loader().select_skills(requirement)
