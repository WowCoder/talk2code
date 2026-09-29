# -*- coding: utf-8 -*-
"""LLM 调用的超时必须可配置，不得再散落硬编码。

背景（req 186，agnes-3.0-flash）：该模型是 reasoning 模型（返回 reasoning_content），
而 research / 文件审查 / 记忆整合 / Playwright 分析这些辅助调用点各自硬编码
timeout=15/20/30 —— 186 里多次 `Read timed out (read timeout=30)`，且辅助调用
失败即静默降级（不重跑），等于悄悄丢功能。现统一到 config.LLM_AUX_TIMEOUT /
config.LLM_CLASSIFY_TIMEOUT。

后续又发现同类漏网：nodes.py 里 evaluator 的 `timeout=110` / `timeout=150`
是裸写的（守卫正则当时只匹配 15/20/30）。已提到 config.LLM_EVALUATOR_TIMEOUT /
config.LLM_EVALUATOR_RETRY_TIMEOUT，并把守卫放宽到「任何裸数字」。

这些用例的作用是**防回归**：单看 helper 返回值没人会写错，真正的风险是
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
    "harness/runtime.py",
]

# 裸 `timeout=NN` 传给 llm chat 的写法（排除注释行与 helper 定义本身）
#
# 原版只匹配 15|20|30（当时要清的债）。结果 nodes.py 里 evaluator 的
# `timeout=110` / `timeout=150` 从缝里漏了整整一个版本 —— 这类守卫一旦
# 写成"黑名单几个具体数字"，就只防得住已知的那几个，防不住新写的。
# 现在改为**匹配任意裸数字**：想给某个调用点单独定超时，就必须走 config
# 或 helper（如 `_evaluator_timeout()` / `_defect_repair_timeout()`），
# 让"这个数字为什么是这个值"有地方写注释、有地方改配置。
_BARE_TIMEOUT_RE = re.compile(r"^\s*timeout\s*=\s*\d+\s*[,)]", re.MULTILINE)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_aux_timeout_comes_from_settings():
    from config import get_settings
    from harness.instructions.nodes import (
        _aux_timeout, _classify_timeout, _evaluator_timeout,
    )

    s = get_settings()
    assert _aux_timeout() == s.LLM_AUX_TIMEOUT
    assert _classify_timeout() == s.LLM_CLASSIFY_TIMEOUT
    # 评估档：首试/截断重试两个档位必须都从配置来（此前是裸写的 110 / 150）
    assert _evaluator_timeout() == s.LLM_EVALUATOR_TIMEOUT
    assert _evaluator_timeout(retry=True) == s.LLM_EVALUATOR_RETRY_TIMEOUT


def test_evaluator_retry_budget_is_not_smaller_than_first_try():
    """截断重试的输出预算更大（3000→6000），超时不能反而更短。"""
    from config import Settings

    s = Settings()
    assert s.LLM_EVALUATOR_RETRY_TIMEOUT >= s.LLM_EVALUATOR_TIMEOUT
    # 评估要读完整产物 + thinking，实测 54~107s，不能退回到辅助档的 60s
    assert s.LLM_EVALUATOR_TIMEOUT >= 90


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
        f"{rel_path} 仍有裸写的超时数字，应改用 config 配置或对应 helper"
        f"（_aux_timeout / _classify_timeout / _evaluator_timeout / "
        f"_defect_repair_timeout）：{hits[:3]}"
    )
