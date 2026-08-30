# -*- coding: utf-8 -*-
"""
Prompt 资产 format 安全冒烟测试

背景教训：evaluator.md / defect_repair.md 里存在大量未转义裸花括号（JSON 示例），
一旦被误改为 .format 加载即当场 KeyError。本测试锁死两条约定：

1. prompts/ 下所有 .md 都能被 load_prompt 加载（文件完整、编码正确）
2. TEMPLATES 注册的每个模板都能用注册的 kwargs 完整渲染：
   - 注册表遗漏 kwarg → 渲染时 KeyError，在此暴露
   - 模板里出现未转义的裸 {xxx} → 同样暴露
3. 模板中声明的占位符与 TEMPLATES 注册一致（治理不漂移）
"""

import string
from pathlib import Path

import pytest

from harness.instructions.prompts import (
    _PROMPTS_DIR,
    TEMPLATES,
    load_prompt,
    load_prompt_template,
    validate_template_placeholders,
)

# 各占位符的 dummy 取值：任何缺失的 key 自动回退为类型默认值
DUMMY_KWARG_VALUES = {
    "max_repair_rounds": 2,
}


def _dummy_value(name: str):
    if name in DUMMY_KWARG_VALUES:
        return DUMMY_KWARG_VALUES[name]
    return "测试占位"


def _iter_prompt_files() -> list[Path]:
    return sorted(p for p in _PROMPTS_DIR.rglob("*.md"))


class TestPromptAssetSafety:
    """全量 prompt 资产加载与渲染安全"""

    def test_all_prompts_loadable(self):
        """prompts/ 下所有 .md 均可加载且非空"""
        files = _iter_prompt_files()
        assert len(files) >= 10, f"prompt 资产异常减少: {len(files)}"
        for f in files:
            rel = f.relative_to(_PROMPTS_DIR).as_posix()
            text = load_prompt(rel)
            assert isinstance(text, str) and text.strip(), f"{rel} 内容为空"

    def test_every_registered_template_renders(self):
        """TEMPLATES 注册的每个模板用 dummy kwargs 渲染不抛异常"""
        for rel_path, required in TEMPLATES.items():
            kwargs = {k: _dummy_value(k) for k in required}
            rendered = load_prompt_template(rel_path, **kwargs)
            assert rendered, f"{rel_path} 渲染结果为空"

    @pytest.mark.parametrize("rel_path", sorted(TEMPLATES.keys()))
    def test_template_placeholders_fully_registered(self, rel_path):
        """模板中的每个占位符都在 TEMPLATES 里注册过（防运行时缺参）"""
        unregistered = validate_template_placeholders(rel_path)
        assert not unregistered, (
            f"{rel_path} 存在未注册的占位符 {unregistered}，"
            f"请在 prompts/__init__.py 的 TEMPLATES 中补齐"
        )

    def test_plain_prompts_are_not_templates(self):
        """纯文本 Prompt（含 JSON 示例的 evaluator/defect_repair）禁止被注册为模板

        这些文件包含大量未转义裸花括号，一旦走 .format 会当场 KeyError——
        这正是审查报告要求防御的事故模式。
        """
        for plain in ("verify/evaluator.md", "tasks/defect_repair.md"):
            assert plain not in TEMPLATES, (
                f"{plain} 是纯文本 Prompt，只能经 load_prompt 加载，"
                f"注册为模板会诱发误用 .format"
            )
        # tasks/defect_repair.md 含未转义裸花括号（JSON 示例），
        # 无参 .format 必须当场失败——锁死「不可作为模板」这一事实
        raw = load_prompt("tasks/defect_repair.md")
        with pytest.raises((KeyError, IndexError, ValueError)):
            raw.format()
