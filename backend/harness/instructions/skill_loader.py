# -*- coding: utf-8 -*-
"""
SkillLoader —— 声明式 Skill 加载器

基于 manifest.json 的 Skill 发现、匹配、缓存和热加载。
替代旧的 skills/__init__.py 中的 LLM 选择 + 关键词回退机制。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)

# 默认 Skills 目录
_DEFAULT_SKILLS_DIR = Path(__file__).parent / "prompts" / "skills"


class SkillManifest:
    """Skill 的 manifest.json 解析结果"""

    __slots__ = (
        'name', 'trigger', 'type', 'priority', 'description',
        'dir_path', 'skill_md_path', 'manifest_path',
        '_body_cache', '_trigger_re',
    )

    def __init__(self, data: dict, dir_path: Path):
        self.name: str = data.get("name", dir_path.name)
        self.trigger: str = data.get("trigger", ".*")
        self.type: str = data.get("type", "knowledge")
        self.priority: int = data.get("priority", 0)
        self.description: str = data.get("description", "")
        self.dir_path: Path = dir_path
        self.manifest_path: Path = dir_path / "manifest.json"
        self.skill_md_path: Path = dir_path / "SKILL.md"
        self._body_cache: Optional[str] = None
        self._trigger_re: Optional[re.Pattern] = None

    def get_trigger_re(self) -> re.Pattern:
        """获取编译后的 trigger 正则（惰性缓存）"""
        if self._trigger_re is None:
            try:
                self._trigger_re = re.compile(self.trigger, re.IGNORECASE)
            except re.error:
                logger.warning(
                    f"[SkillLoader] Skill '{self.name}' 的 trigger 正则无效: "
                    f"{self.trigger}，回退到 '.*'"
                )
                self._trigger_re = re.compile(".*", re.IGNORECASE)
        return self._trigger_re

    def matches(self, requirement: str) -> bool:
        """检查需求文本是否匹配此 Skill 的 trigger 正则"""
        if not requirement:
            return False
        return bool(self.get_trigger_re().search(requirement))

    def load_body(self) -> str:
        """加载 SKILL.md 正文（惰性缓存）"""
        if self._body_cache is not None:
            return self._body_cache
        try:
            if self.skill_md_path.exists():
                text = self.skill_md_path.read_text(encoding='utf-8')
                # 去除 YAML frontmatter
                body = self._strip_frontmatter(text)
                self._body_cache = body
                return body
        except Exception as e:
            logger.warning(f"[SkillLoader] 无法加载 {self.skill_md_path}: {e}")
        self._body_cache = ""
        return ""

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        """去除 YAML frontmatter（--- ... ---）"""
        if not text.startswith('---'):
            return text.strip()
        match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    def get_mtime(self) -> float:
        """获取 manifest.json 的修改时间（用于热加载检测）"""
        try:
            return self.manifest_path.stat().st_mtime
        except OSError:
            return 0.0

    def __repr__(self):
        return f"SkillManifest({self.name}, type={self.type}, priority={self.priority})"


class SkillLoader:
    """声明式 Skill 加载器

    扫描 prompts/skills/ 下的 manifest.json，建立索引，支持：
    - 正则关键词匹配
    - 优先级排序
    - 文件修改时间热加载
    - 缓存管理
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._manifests: List[SkillManifest] = []
        self._mtime_snapshot: dict[str, float] = {}  # name -> mtime
        self._loaded = False
        self._last_scan_time: float = 0.0

    def _ensure_loaded(self, force: bool = False):
        """确保索引已加载。如果 manifest 有变更则自动重建。"""
        if self._loaded and not force:
            # 检查是否有文件变更（热加载）
            if self._check_stale():
                logger.info("[SkillLoader] 检测到 manifest 变更，重建索引")
                self._rebuild_index()
            return
        self._rebuild_index()

    def _rebuild_index(self):
        """扫描 skills 目录，重建 manifest 索引"""
        self._manifests.clear()
        self._mtime_snapshot.clear()

        if not self._skills_dir.exists():
            logger.warning(f"[SkillLoader] Skills 目录不存在: {self._skills_dir}")
            self._loaded = True
            return

        for d in sorted(self._skills_dir.iterdir()):
            if not d.is_dir() or d.name.startswith('_'):
                continue

            manifest_path = d / "manifest.json"
            if not manifest_path.exists():
                logger.warning(
                    f"[SkillLoader] 目录 {d.name} 缺少 manifest.json，跳过"
                )
                continue

            try:
                data = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[SkillLoader] 无法解析 {manifest_path}: {e}")
                continue

            manifest = SkillManifest(data, d)
            self._manifests.append(manifest)
            self._mtime_snapshot[manifest.name] = manifest.get_mtime()

        # 按 priority 降序排列（高分优先）
        self._manifests.sort(key=lambda m: m.priority, reverse=True)
        self._loaded = True
        self._last_scan_time = time.time()

        logger.info(
            f"[SkillLoader] 索引重建完成: {len(self._manifests)} 个 Skill"
        )

    def _check_stale(self) -> bool:
        """检查是否有 manifest 文件变更（热加载检测）"""
        for manifest in self._manifests:
            current_mtime = manifest.get_mtime()
            if current_mtime != self._mtime_snapshot.get(manifest.name, 0.0):
                return True
        # 也检查是否有新目录出现
        if self._skills_dir.exists():
            current_dirs = {
                d.name for d in self._skills_dir.iterdir()
                if d.is_dir() and not d.name.startswith('_')
                and (d / "manifest.json").exists()
            }
            indexed_names = {m.name for m in self._manifests}
            if current_dirs != indexed_names:
                return True
        return False

    def match_skills(self, requirement: str) -> List[SkillManifest]:
        """根据需求文本匹配适用的 Skill（按 priority 降序返回）"""
        self._ensure_loaded()

        matched = []
        for manifest in self._manifests:
            if manifest.matches(requirement):
                matched.append(manifest)

        return matched

    def load_for_task(self, requirement: str) -> str:
        """根据任务需求加载匹配的 Skill 正文

        Returns:
            拼接后的 Skill 正文（"---" 分隔），无匹配时返回空字符串
        """
        matched = self.match_skills(requirement)
        if not matched:
            return ""

        parts = []
        for manifest in matched:
            body = manifest.load_body()
            if body:
                parts.append(body)

        return "\n\n---\n\n".join(parts) if parts else ""

    def list_all(self) -> List[SkillManifest]:
        """列出所有已加载的 Skill"""
        self._ensure_loaded()
        return list(self._manifests)

    def get_skill(self, name: str) -> Optional[SkillManifest]:
        """按名称获取 Skill"""
        self._ensure_loaded()
        for m in self._manifests:
            if m.name == name:
                return m
        return None

    def get_selection_summary(self, requirement: str) -> str:
        """调试：返回选择摘要"""
        self._ensure_loaded()
        matched = self.match_skills(requirement)
        lines = [f"需求: {requirement[:80]}"]
        for m in self._manifests:
            marker = "✓" if m in matched else "✗"
            lines.append(f"  {marker} [{m.type}] {m.name} (priority={m.priority})")
        body = self.load_for_task(requirement)
        lines.append(f"  总注入: {len(body)} chars")
        return "\n".join(lines)

    def invalidate_cache(self):
        """强制重建索引（手动触发）"""
        self._loaded = False
        self._rebuild_index()


# ==================== 全局单例 ====================

_loader: Optional[SkillLoader] = None


def get_skill_loader(skills_dir: Optional[Path] = None) -> SkillLoader:
    """获取 SkillLoader 单例"""
    global _loader
    if _loader is None:
        _loader = SkillLoader(skills_dir)
    return _loader


def load_for_task(requirement: str) -> str:
    """便捷函数：加载匹配的 Skill 正文"""
    return get_skill_loader().load_for_task(requirement)


def match_skills(requirement: str) -> List[SkillManifest]:
    """便捷函数：匹配适用的 Skill"""
    return get_skill_loader().match_skills(requirement)
