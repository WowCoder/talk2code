# -*- coding: utf-8 -*-
"""把项目自带的记忆系统验证脚本接进 pytest，提供回归保护。

背景：本次记忆重构新增的行为（注入 token 预算、access_count 落库、全局索引
+ 用户过滤、空内容过滤、相关性门禁、注入即记账、eval A/B 开关）此前只有
`tmp/` 下的独立脚本覆盖 —— 而 `tmp/` 被 gitignore，不进仓库、无回归保护
（`test_memory_store.py` 测的是已废弃的旧 `MemoryStore`）。

这里把脚本迁到 `tests/memory_verify/` 并以**子进程**方式执行：
- 脚本内会 monkeypatch `harness.state.memory` 的模块级单例（SessionLocal /
  AgentMemoryV2 / MemoryHit），子进程隔离可避免污染同进程内其它测试；
- 脚本自带 in-memory sqlite / stub，无外部依赖（不需要数据库或网络），CI 可跑；
- 脚本自身在断言失败时以非零退出码退出，这里断言 returncode == 0。
"""

import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent.parent   # backend/tests
_VERIFY_DIR = _TESTS_DIR / "memory_verify"
_BACKEND = _TESTS_DIR.parent                          # backend

_CASES = [
    "verify_memory_injection_budget.py",
    "verify_memory_hits_accounting.py",
    "verify_eval_memory_ab.py",
]


@pytest.mark.unit
@pytest.mark.parametrize("script", _CASES)
def test_memory_verification_script(script):
    path = _VERIFY_DIR / script
    assert path.exists(), f"缺少验证脚本：{path}"

    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(_BACKEND),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"{script} 断言未全部通过（rc={proc.returncode}）\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
