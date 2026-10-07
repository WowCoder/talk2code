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

import re as _re_mod
import string
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

# mtime -> content 缓存：key 为 rel_path。dict 读写受 GIL 保护，
# 单条目原子替换，读旧值最多一次 IO，无需加锁。
_cache: dict[str, tuple[float, str]] = {}

# 片段复用：`<!-- @include <rel_path> -->` 会被替换成该文件的正文。
#
# 为什么需要它：同一段规则（如「平台能力边界」）在多个提示词里重复维护，
# 改了一处另一处就漂移 —— 漂移后的两份提示词会给出互相矛盾的判断，
# 而这种矛盾只在真实需求上才会暴露，很难在单测里发现。
_INCLUDE_RE = _re_mod.compile(r"<!--\s*@include\s+([^\s>]+)\s*-->")
_INCLUDE_MAX_DEPTH = 4


def load_prompt(rel_path: str, _seen: frozenset = frozenset()) -> str:
    """加载 .md 文件中的 Prompt 文本（按 mtime 缓存，改动即失效）。

    Args:
        rel_path: 相对于 prompts/ 目录的路径，如 "verify/evaluator.md"
        _seen: **内部参数**，展开 @include 时已经加载过的路径集合（防环）。
               调用方不要传。

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
    text = _resolve_includes(text, _seen | {rel_path})
    # 被 include 的片段不写缓存：它这次的结果已经内联进调用方了，
    # 缓存下来会让后续「单独加载该片段」拿到一份没展开的版本。
    if not _seen:
        _cache[rel_path] = (mtime, text)
    return text


def _resolve_includes(text: str, _seen: frozenset = frozenset()) -> str:
    """展开 `<!-- @include rel_path -->` 标记（在 .format() 之前）。

    在 format 之前展开，是为了让被包含片段里的 `{{ }}` 转义 JSON 也能被正确
    还原 —— 否则片段会带着双花括号原样发给模型。

    `_seen` 是防环的关键：**片段里如果出现自己的 include 标记字面量**（写注释时
    顺手把标记抄进去就会这样），展开会无限递归直到撑爆栈 —— 实测踩过一次。
    按路径集合判重，重复引用直接跳过。
    解析不到的 include 保留原标记并记 warning —— 静默吞掉会让提示词缺一大段
    却看不出原因。
    """
    if not _INCLUDE_RE.search(text):
        return text
    missing = []

    def _sub(m):
        rel = m.group(1)
        if rel in _seen:
            missing.append(f"{rel}: 循环引用（已跳过）")
            return ""
        try:
            return load_prompt(rel)
        except Exception as e:  # noqa: BLE001 - 片段缺失不能让整个 prompt 加载失败
            missing.append(f"{rel}: {e}")
            return m.group(0)

    new_text = _INCLUDE_RE.sub(_sub, text)
    if missing:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"[prompts] include 片段加载失败，已保留原标记: {'; '.join(missing)}"
        )
    return new_text


# ==================== 模板注册表 ====================
# 所有通过 load_prompt_template 加载的模板必须在此注册：
# key 为相对路径，value 为必填 kwargs 列表。
# tests/unit/test_prompt_format_safety.py 会用 dummy kwargs 渲染全部模板，
# 防止「.format 与 load_prompt 双加载约定」再埋雷。

TEMPLATES: dict[str, list[str]] = {
    "coding/coder_base.md": [
        "requirement", "plan_section", "api_contracts", "file_hint",
        "existing_text", "task_state", "first_round_section",
        "craft_rules", "environment_contract",
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
    "verify/ac_translator.md": [
        "anchor_text", "visible_text_text", "selector_text", "ac_text",
        "render_info",
    ],
    # 恒定规则段（无占位符，但仍走 .format：模板里的 JSON 示例按约定写成 `{{ }}`，
    # 不 format 会把 `{{` 原样发给模型）。
    "verify/ac_translator_system.md": [],
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
