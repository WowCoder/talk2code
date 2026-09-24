# -*- coding: utf-8 -*-
"""ac_verdict_policy 的契约测试（纯函数，不碰 app / DB / 浏览器，沙箱秒级跑完）。

守卫两条逻辑：
1. _should_invalidate_ac_cache（P2.5）：多数 AC 验不了 ⇒ 作废缓存重译。
2. _ac_state（P4 数据来源）：单条结果归一到四态信号。
"""
import pytest

from harness.instructions.ac_verdict_policy import _ac_state, _should_invalidate_ac_cache


# ============ P2.5：缓存作废判据 ============

def _r(**kw):
    """构造一条 run_ac_checks 结果（只填本测试关心的字段，其余默认）。"""
    return {
        "passed": kw.get("passed", False),
        "failures": kw.get("failures", []),
        "harness_errors": kw.get("harness_errors", []),
        "unverified": kw.get("unverified", False),
        "not_applicable": kw.get("not_applicable", []),
        "compromised": kw.get("compromised", False),
    }


def test_empty_results_no_invalidate():
    assert _should_invalidate_ac_cache([]) is False
    assert _should_invalidate_ac_cache(None) is False


def test_single_unverified_does_not_invalidate():
    # 地板为 2：单条验不了不足以触发重译（避免偶发抖动就重译）
    assert _should_invalidate_ac_cache([_r(unverified=True)]) is False


def test_two_of_two_unverified_invalidates():
    # 2 条全验不了 → >= 2 触发
    assert _should_invalidate_ac_cache([_r(unverified=True), _r(unverified=True)]) is True


def test_three_with_two_bad_invalidates():
    # 3 条里 1 harness + 1 unverified = 2 条不可信；max(2, 3//2+1=2)=2 → 2>=2 触发
    bad = [_r(harness_errors=["x"]), _r(unverified=True), _r(passed=True)]
    assert _should_invalidate_ac_cache(bad) is True


def test_three_with_one_bad_keeps_cache():
    # 3 条里仅 1 条 harness 失败 → 1 < 2 不触发
    bad = [_r(harness_errors=["x"]), _r(passed=True), _r(passed=True)]
    assert _should_invalidate_ac_cache(bad) is False


def test_four_with_two_bad_keeps_cache():
    # 4 条里 2 条不可信；max(2, 4//2+1=3)=3 → 2<3 不触发（未过半）
    bad = [_r(unverified=True), _r(unverified=True), _r(passed=True), _r(passed=True)]
    assert _should_invalidate_ac_cache(bad) is False


def test_four_with_three_bad_invalidates():
    # 4 条里 3 条不可信 → 3>=3 触发
    bad = [_r(unverified=True), _r(unverified=True), _r(harness_errors=["x"]), _r(passed=True)]
    assert _should_invalidate_ac_cache(bad) is True


def test_five_with_three_bad_invalidates():
    # 5 条里 2 unverified + 1 harness = 3 不可信；max(2, 5//2+1=3)=3 → 3>=3 触发
    bad = [
        _r(unverified=True),
        _r(unverified=True),
        _r(harness_errors=["x"]),
        _r(passed=True),
        _r(passed=True),
    ]
    assert _should_invalidate_ac_cache(bad) is True


def test_all_passed_keeps_cache():
    good = [_r(passed=True), _r(passed=True), _r(passed=True)]
    assert _should_invalidate_ac_cache(good) is False


# ============ P4：四态信号归一 ============

def test_state_passed():
    assert _ac_state(_r(passed=True)) == "passed"


def test_state_compromised_when_fail_plus_harness():
    # preview_runner 已把 compromised 预计算为 bool(failures) and bool(harness_errors)；
    # _ac_state 信任该字段，优先级高于 fail（失败不可信，勿直接照此修）。
    assert _ac_state(_r(failures=["f"], harness_errors=["h"], compromised=True)) == "compromised"


def test_state_fail_when_only_failures():
    assert _ac_state(_r(failures=["f"])) == "fail"


def test_state_unverified_when_not_applicable_clean():
    # 断言前提不成立且无失败/无驱动失败 ⇒ unverified
    assert _ac_state(_r(not_applicable=["n/a"], unverified=True)) == "unverified"


def test_state_not_applicable_when_only_flag():
    assert _ac_state(_r(not_applicable=["n/a"])) == "not_applicable"


def test_state_pending_default():
    assert _ac_state(_r()) == "pending"
