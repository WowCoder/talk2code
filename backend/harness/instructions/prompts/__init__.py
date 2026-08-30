# -*- coding: utf-8 -*-
"""
统一 Prompt 加载器 —— 所有 LLM 提示词从 .md 文件加载，集中管理。

用法:
    from harness.instructions.prompts import load_prompt, load_prompt_template

    # 纯文本 Prompt（不做任何占位符替换）
    system = load_prompt("verify/evaluator.md")

    # 带占位符的模板（必须先在 TEMPLATES 注册，便于 format 安全测试覆盖）
    prompt = load_prompt_template("coding/coder_base.md",
        requirement="做一个待办清单",
        ...
    )

约定：
- 模板文件中的字面量 `{` `}` 必须转义为 `{{` `}}`（Python str.format 规则）
- 纯文本 Prompt 一律用 load_prompt 加载，禁止对它调用 .format()
  （evaluator/defect_repair 里大量未转义裸花括号，误用 .format 会当场 KeyError）
- 缓存按文件 mtime 失效：修改 .md 后下一次读取自动生效，无需重启进程
"""

import string
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

# mtime -> content 缓存：key 为 rel_path。dict 读写受 GIL 保护，
# 单条目原子替换，读旧值最多一次 IO，无需加锁。
_cache: dict[str, tuple[float, str]] = {}


def load_prompt(rel_path: str) -> str:
    """加载 .md 文件中的 Prompt 文本（按 mtime 缓存，改动即失效）。

    Args:
        rel_path: 相对于 prompts/ 目录的路径，如 "verify/evaluator.md"

    Returns:
        str: Prompt 文本内容（已去除首尾空白）
    """
    file_path = _PROMPTS_DIR / rel_path
    if not file_path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {file_path}")
    mtime = file_path.stat().st_mtime
    cached = _cache.get(rel_path)
    if cached and cached[0] == mtime:
        return cached[1]
    text = file_path.read_text(encoding="utf-8").strip()
    _cache[rel_path] = (mtime, text)
    return text


# ==================== 模板注册表 ====================
# 所有通过 load_prompt_template 加载的模板必须在此注册：
# key 为相对路径，value 为必填 kwargs 列表。
# tests/unit/test_prompt_format_safety.py 会用 dummy kwargs 渲染全部模板，
# 防止「.format 与 load_prompt 双加载约定」再埋雷。

TEMPLATES: dict[str, list[str]] = {
    "coding/coder_base.md": [
        "requirement", "plan_section", "api_contracts", "file_hint", "batch_hint",
        "existing_text", "craft_rules", "environment_contract",
        "mode_section", "max_repair_rounds",
    ],
    "coding/tl_analysis.md": ["environment_contract"],
    "coding/chat_modify.md": ["user_message", "file_list_text"],
    "coding/file_aware_coder.md": [
        "requirement", "plan_section", "file_path", "task_description",
        "exports_text", "imports_text", "interface_text",
        "completed_text", "error_text",
    ],
    "intent/clarify_generate.md": ["requirement", "detail_hint"],
    "memory/reflection_prompt.md": [
        "requirement", "code_summary", "rating", "failure_context",
    ],
    "memory/consolidate_prompt.md": ["memories"],
    "memory/verify_prompt.md": ["query", "candidates"],
    "verify/ac_translator.md": ["selector_text", "ac_text"],
}


def load_prompt_template(rel_path: str, **kwargs) -> str:
    """加载带 {placeholder} 占位符的 Prompt 模板，并用 kwargs 填充。

    模板必须在 TEMPLATES 注册；缺失 kwarg 时抛出带明确提示的 KeyError，
    避免「运行到一半才发现少传参」。

    Args:
        rel_path: 相对于 prompts/ 目录的路径
        **kwargs: 模板中 {key} 对应的值

    Returns:
        str: 填充后的 Prompt 文本
    """
    template = load_prompt(rel_path)
    return template.format(**kwargs)


def validate_template_placeholders(rel_path: str) -> list[str]:
    """返回模板中声明了但未在 TEMPLATES 注册的占位符名（治理用）。"""
    template = load_prompt(rel_path)
    declared = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }
    registered = set(TEMPLATES.get(rel_path, []))
    return sorted(declared - registered)
