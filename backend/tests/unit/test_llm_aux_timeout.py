# -*- coding: utf-8 -*-
"""辅助 LLM 调用超时必须可配置，不得再散落硬编码。

背景（req 186，agnes-3.0-flash）：该模型是 reasoning 模型（返回 reasoning_content），
而 research / 文件审查 / 记忆整合 / Playwright 分析这些辅助调用点各自硬编码
timeout=15/20/30 —— 186 里多次 `Read timed out (read timeout=30)`，且辅助调用
失败即静默降级（不重跑），等于悄悄丢功能。现统一到 config.LLM_AUX_TIMEOUT /
config.LLM_CLASSIFY_TIMEOUT。

这两条用例的作用是**防回归**：单看 helper 返回值没人会写错，真正的风险是
以后有人图省事又写回 `timeout=30`。
"""

import re
from pathlib import Path

import pytest

# 受约束的调用点：(文件, 该文件中不得出现的裸超时值)
_GUARDED_FILES = [
    "harness/instructions/nodes.py",
    "harness/instructions/intent_router.py",
    "harness/state/memory.py",
    "harness/state/memory_store.py",
]

# 裸 `timeout=NN` 传给 llm chat 的写法（排除注释行与 helper 定义本身）
_BARE_TIMEOUT_RE = re.compile(r"^\s*timeout\s*=\s*(15|20|30)\s*[,)]", re.MULTILINE)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_aux_timeout_comes_from_settings():
    from config import get_settings
    from harness.instructions.nodes import _aux_timeout, _classify_timeout

    s = get_settings()
    assert _aux_timeout() == s.LLM_AUX_TIMEOUT
    assert _classify_timeout() == s.LLM_CLASSIFY_TIMEOUT


def test_defaults_are_sane_for_reasoning_models():
    """agnes-3.0-flash / glm 全系会先烧 reasoning token，30s 已被实测撞穿。

    分类档同步阻塞用户回车，故可以比辅助档短，但同样不能回到 15s。
    """
    from config import Settings

    assert Settings().LLM_AUX_TIMEOUT >= 60
    assert Settings().LLM_CLASSIFY_TIMEOUT >= 30
    assert Settings().LLM_CLASSIFY_TIMEOUT <= Settings().LLM_AUX_TIMEOUT


@pytest.mark.parametrize("rel_path", _GUARDED_FILES)
def test_no_bare_timeout_in_guarded_files(rel_path):
    src = (_backend_root() / rel_path).read_text(encoding="utf-8")
    # 去掉注释行再扫，避免注释里引用旧数值造成误报
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    hits = [ln for ln in code_lines if _BARE_TIMEOUT_RE.search(ln)]
    assert not hits, (
        f"{rel_path} 仍有硬编码辅助超时，应改用 _aux_timeout() / _classify_timeout()："
        f"{hits[:3]}"
    )
