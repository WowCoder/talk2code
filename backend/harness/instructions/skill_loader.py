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
        'input_schema', 'output_schema', 'tools', 'composes', 'entry',
        'dir_path', 'skill_md_path', 'manifest_path',
        '_body_cache', '_trigger_re',
    )

    def __init__(self, data: dict, dir_path: Path):
        self.name: str = data.get("name", dir_path.name)
        self.trigger: str = data.get("trigger", ".*")
        self.type: str = data.get("type", "knowledge")
        self.priority: int = data.get("priority", 0)
        self.description: str = data.get("description", "")
        # 扩展字段（向后兼容：旧 knowledge skill 不填则为空）
        self.input_schema: Optional[dict] = data.get("input_schema") or None
        self.output_schema: Optional[dict] = data.get("output_schema") or None
        self.tools: List[str] = data.get("tools", []) or []
        self.composes: List[str] = data.get("composes", []) or []
        self.entry: str = data.get("entry", "") or ""
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

    def is_workflow(self) -> bool:
        """是否为可被 Agent 规划/调用/组合的工作流技能"""
        return self.type == "workflow"

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

    def match_workflow_skills(self, requirement: str) -> List[SkillManifest]:
        """仅匹配可被 Agent 调用的工作流技能（按 priority 降序）"""
        return [m for m in self.match_skills(requirement) if m.is_workflow()]

    def get_workflow_skills(self) -> List[SkillManifest]:
        """列出所有工作流技能"""
        self._ensure_loaded()
        return [m for m in self._manifests if m.is_workflow()]

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


def build_skill_dispatch(skill_name: str, args: Optional[dict] = None) -> str:
    """Skill Dispatcher：把一个工作流技能编排为 Coder 可直接执行的上下文。

    返回结构化文本（执行步骤 + 输入/输出契约 + 工具白名单 + 组合子技能），
    供 `run_skill` 工具喂给 Coder，使其能规划、调用并按 `composes` 组合子技能。

    Args:
        skill_name: 技能名（对应 manifest 的 name）
        args: 调用方已收集的参数（可选，仅做回显提示）

    Returns:
        可注入 Coder 上下文的技能执行说明；技能不存在/非工作流时返回提示信息。
    """
    loader = get_skill_loader()
    m = loader.get_skill(skill_name)
    if not m:
        return f"[run_skill] 未找到技能: {skill_name}"
    if not m.is_workflow():
        return (
            f"[run_skill] 技能 {skill_name} 不是可调用的工作流技能"
            f"（type={m.type}），它仅作为知识注入到编码 Prompt。"
        )

    parts: List[str] = []
    parts.append(f"# 技能执行：{m.name}")
    if m.description:
        parts.append(m.description)

    if args:
        parts.append("## 已提供的参数")
        parts.append(json.dumps(args, ensure_ascii=False, indent=2))

    if m.input_schema:
        parts.append("## 输入契约 (input_schema)")
        parts.append(json.dumps(m.input_schema, ensure_ascii=False, indent=2))

    body = m.load_body()
    if body:
        parts.append("## 执行步骤 (SKILL.md)")
        parts.append(body)

    if m.tools:
        parts.append("## 允许使用的工具 (tools 白名单)")
        parts.append("、".join(m.tools))

    # 组合子技能：递归展开 composes 中声明的子技能说明
    for child in (m.composes or []):
        child_m = loader.get_skill(child)
        if not child_m:
            continue
        parts.append(f"\n---\n## 组合子技能：{child}")
        cbody = child_m.load_body()
        if cbody:
            parts.append(cbody)
        if child_m.input_schema:
            parts.append(
                "输入契约: " + json.dumps(child_m.input_schema, ensure_ascii=False)
            )

    if m.output_schema:
        parts.append("## 输出契约 (output_schema)")
        parts.append(json.dumps(m.output_schema, ensure_ascii=False, indent=2))

    parts.append(
        "\n> 请严格按上述步骤执行，仅使用白名单内的工具，"
        "并最终产出符合 output_schema 的结果。"
    )
    return "\n\n".join(parts)
