# -*- coding: utf-8 -*-
"""
Plugin Loader —— .talk2code-plugin 插件扫描与加载

支持通过 plugin.json 声明式定义插件，扫描 T2C_PLUGINS_DIR 目录，
加载插件的 skills / hooks / tools。

约定：
- 每个插件是一个包含 plugin.json 的目录：.talk2code-plugin/
- 插件目录可放置在项目根目录或 T2C_PLUGINS_DIR 环境变量指定路径
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)

# 环境变量：指定插件目录
ENV_PLUGINS_DIR = "T2C_PLUGINS_DIR"

# 默认插件目录名称
PLUGIN_DIR_NAME = ".talk2code-plugin"

# plugin.json 必填字段
REQUIRED_FIELDS = {"name", "version"}

# 合法字段及其类型
PLUGIN_SCHEMA = {
    "name": str,
    "version": str,
    "description": str,
    "author": str,
    "skills": list,
    "hooks": list,
    "tools": list,
    "prompts": list,
    "requirements": list,
}


class PluginManifest:
    """插件 manifest（plugin.json 解析结果）"""

    __slots__ = (
        'name', 'version', 'description', 'author',
        'skills', 'hooks', 'tools', 'prompts', 'requirements',
        'dir_path', 'manifest_path',
    )

    def __init__(self, data: dict, dir_path: Path):
        self.name: str = data["name"]
        self.version: str = data["version"]
        self.description: str = data.get("description", "")
        self.author: str = data.get("author", "")
        self.skills: List[str] = data.get("skills", [])
        self.hooks: List[str] = data.get("hooks", [])
        self.tools: List[str] = data.get("tools", [])
        self.prompts: List[str] = data.get("prompts", [])
        self.requirements: List[str] = data.get("requirements", [])
        self.dir_path: Path = dir_path
        self.manifest_path: Path = dir_path / "plugin.json"

    def get_skill_dir(self) -> Optional[Path]:
        """获取 skills 子目录路径"""
        p = self.dir_path / "skills"
        return p if p.exists() and p.is_dir() else None

    def get_tools_dir(self) -> Optional[Path]:
        """获取 tools 子目录路径"""
        p = self.dir_path / "tools"
        return p if p.exists() and p.is_dir() else None

    def get_hooks_file(self) -> Optional[Path]:
        """获取 hooks.py 路径"""
        p = self.dir_path / "hooks.py"
        return p if p.exists() else None

    def get_prompts_dir(self) -> Optional[Path]:
        """获取 prompts 子目录路径"""
        p = self.dir_path / "prompts"
        return p if p.exists() and p.is_dir() else None

    def __repr__(self):
        return f"PluginManifest({self.name} v{self.version})"


class PluginLoader:
    """插件加载器

    扫描插件目录，加载符合 plugin.json 约定的插件。
    支持通过 T2C_PLUGINS_DIR 环境变量指定额外插件目录。
    """

    def __init__(self, extra_dirs: List[Path] = None):
        self._manifests: List[PluginManifest] = []
        self._loaded = False
        self._extra_dirs = extra_dirs or []

    def _get_search_dirs(self) -> List[Path]:
        """获取所有需要扫描的插件目录"""
        dirs = list(self._extra_dirs)

        # 从环境变量获取
        env_dir = os.environ.get(ENV_PLUGINS_DIR)
        if env_dir:
            p = Path(env_dir).expanduser().resolve()
            if p.exists() and p.is_dir():
                dirs.append(p)

        # 默认项目根目录
        cwd = Path.cwd()
        project_plugin = cwd / PLUGIN_DIR_NAME
        if project_plugin.exists() and project_plugin.is_dir():
            dirs.append(project_plugin)

        return dirs

    def discover(self) -> List[PluginManifest]:
        """扫描所有插件目录，返回发现的插件 manifest 列表"""
        if self._loaded:
            return list(self._manifests)

        self._manifests.clear()
        search_dirs = self._get_search_dirs()

        for search_dir in search_dirs:
            self._scan_directory(search_dir)

        self._loaded = True
        logger.info(
            f"[PluginLoader] 发现 {len(self._manifests)} 个插件"
        )
        return list(self._manifests)

    def _scan_directory(self, directory: Path):
        """递归扫描目录（支持嵌套子目录作为独立插件）"""
        if not directory.exists() or not directory.is_dir():
            return

        # 检查当前目录是否是一个插件（包含 plugin.json）
        manifest_path = directory / "plugin.json"
        if manifest_path.exists():
            manifest = self._load_manifest(manifest_path, directory)
            if manifest:
                self._manifests.append(manifest)
                return  # 不递归进入插件内部

        # 扫描子目录
        try:
            for subdir in sorted(directory.iterdir()):
                if subdir.is_dir() and not subdir.name.startswith('.'):
                    self._scan_directory(subdir)
        except PermissionError:
            pass

    def _load_manifest(self, manifest_path: Path, dir_path: Path) -> Optional[PluginManifest]:
        """加载并验证单个 plugin.json"""
        try:
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[PluginLoader] 无法解析 {manifest_path}: {e}")
            return None

        # 验证必填字段
        missing = REQUIRED_FIELDS - set(data.keys())
        if missing:
            logger.warning(
                f"[PluginLoader] {manifest_path} 缺少必填字段: {missing}"
            )
            return None

        # 类型校验
        for field, expected_type in PLUGIN_SCHEMA.items():
            if field in data and not isinstance(data[field], expected_type):
                logger.warning(
                    f"[PluginLoader] {manifest_path} 字段 {field} "
                    f"类型错误：期望 {expected_type.__name__}，"
                    f"实际 {type(data[field]).__name__}"
                )
                return None

        return PluginManifest(data, dir_path)

    def load_plugin_tools(self, manifest: PluginManifest, registry=None) -> int:
        """加载插件的工具模块

        如果提供了 ToolRegistry，将插件工具注册到注册表中。
        返回加载的工具数量。
        """
        tools_dir = manifest.get_tools_dir()
        if not tools_dir:
            return 0

        count = 0
        # 将插件目录加入 sys.path 以支持导入
        plugin_root = str(manifest.dir_path.parent)
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith('_'):
                continue
            try:
                module_name = f"_plugin_{manifest.name}_{py_file.stem}"
                # 动态导入
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    module_name, str(py_file)
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    # 如果模块有 register_tools 函数，调用它
                    if hasattr(module, 'register_tools') and registry:
                        module.register_tools(registry)
                    count += 1
                    logger.info(
                        f"[PluginLoader] 加载工具: {manifest.name}/{py_file.name}"
                    )
            except Exception as e:
                logger.warning(
                    f"[PluginLoader] 无法加载工具 {manifest.name}/{py_file.name}: {e}"
                )

        return count

    def load_plugin_skills(self, manifest: PluginManifest) -> List[str]:
        """加载插件的技能（SKILL.md 文件列表），返回技能名称列表"""
        skills_dir = manifest.get_skill_dir()
        if not skills_dir:
            return []

        skill_names = []
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                skill_names.append(skill_dir.name)
                logger.info(
                    f"[PluginLoader] 发现技能: {manifest.name}/{skill_dir.name}"
                )

        return skill_names

    def load_all(self, registry=None) -> Dict[str, Any]:
        """加载所有已发现插件的资源

        Returns:
            {
                "tools_loaded": int,
                "skills_loaded": List[str],
                "hooks_loaded": int,
            }
        """
        manifests = self.discover()
        total_tools = 0
        total_skills = []
        total_hooks = 0

        for manifest in manifests:
            # 加载工具
            total_tools += self.load_plugin_tools(manifest, registry)
            # 加载技能
            total_skills.extend(self.load_plugin_skills(manifest))

        return {
            "tools_loaded": total_tools,
            "skills_loaded": total_skills,
            "hooks_loaded": total_hooks,
        }

    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有已发现的插件（摘要信息）"""
        manifests = self.discover()
        return [
            {
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "author": m.author,
                "skills": len(m.skills),
                "tools": len(m.tools),
                "hooks": len(m.hooks),
            }
            for m in manifests
        ]

    def reload(self):
        """强制重新扫描插件目录"""
        self._loaded = False
        self._manifests.clear()
        return self.discover()


# ==================== 全局函数 ====================

def load_plugins(registry=None) -> Dict[str, Any]:
    """便捷函数：加载所有插件"""
    loader = PluginLoader()
    return loader.load_all(registry)


def discover_plugins() -> List[Dict[str, Any]]:
    """便捷函数：发现所有插件"""
    loader = PluginLoader()
    return loader.list_plugins()
