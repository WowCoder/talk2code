# -*- coding: utf-8 -*-
"""
UI 视觉硬伤判定测试（把「页面难看」变成可判定、可修复的缺陷）

背景：预置成品模板（`.design/preset-*.css`）此前只是建议 —— 模型不用它也没人发现，
因为 evaluator 的 ui_quality 是盲评，页面难看照样走快速通道通过。这让模板收益归零。
`build_ui_lint_defects` 把浏览器实测的视觉硬伤转成确定性缺陷送入 defect_repair。

阈值刻意取「宽松下限」（对比度 3.0 / 点区 24px / 字号 10px），只拦明显硬伤，
避免把正常设计判成缺陷把修复预算耗在审美分歧上。
"""

from harness.tools.preview_runner import (
    build_ui_lint_defects,
    _UI_CONTRAST_HARD_MIN,
    _UI_FONT_HARD_MIN,
    _UI_LINT_MAX_DEFECTS,
    _UI_TARGET_HARD_MIN,
    _contrast_ratio,
)


def _raw(**kw):
    base = {
        "bg": "rgb(15, 23, 42)",
        "fg": "rgb(241, 245, 249)",
        "fontSizes": ["16px", "14px"],
        "textColors": ["rgb(241, 245, 249)"],
        "smallTargets": [],
        "hasHover": True,
        "hasFocus": True,
        "cssVarCount": 10,
        "inlineStyleCount": 0,
        "domOutline": ["div.page", "h1.title"],
        "textLength": 300,
    }
    base.update(kw)
    return base


def _types(defects):
    return sorted(d["type"] for d in defects)


class TestCleanPagePasses:
    """合格页面不得被误判 —— 误判会白白触发修复轮次"""

    def test_clean_page_has_no_defects(self):
        assert build_ui_lint_defects(_raw()) == []

    def test_acceptable_contrast_not_flagged(self):
        # 灰字深底，对比度约 4.4:1 —— 高于 3.0 硬下限，不该拦
        raw = _raw(fg="rgb(148, 163, 184)", bg="rgb(30, 41, 59)")
        assert _contrast_ratio(raw["fg"], raw["bg"]) >= _UI_CONTRAST_HARD_MIN
        assert build_ui_lint_defects(raw) == []

    def test_12px_is_acceptable(self):
        assert build_ui_lint_defects(_raw(fontSizes=["12px", "16px"])) == []

    def test_24px_target_is_acceptable(self):
        raw = _raw(smallTargets=[{"tag": "button", "w": 24, "h": 24, "text": "x"}])
        assert build_ui_lint_defects(raw) == []

    def test_non_dict_input_returns_empty(self):
        assert build_ui_lint_defects(None) == []
        assert build_ui_lint_defects("x") == []
        assert build_ui_lint_defects([]) == []


class TestContrastDefect:

    def test_low_contrast_flagged(self):
        raw = _raw(fg="rgb(110, 110, 110)", bg="rgb(100, 100, 100)")
        defects = build_ui_lint_defects(raw)
        assert "ui_contrast" in _types(defects)
        d = next(d for d in defects if d["type"] == "ui_contrast")
        assert d["dimension"] == "ui_quality"
        assert "对比度" in d["message"]
        assert d["evidence"] and d["suggestion"]

    def test_unparseable_colors_do_not_crash(self):
        # 透明色 / 渐变等取不到 rgb 时，对比度返回 None，不得判定也不得抛异常
        raw = _raw(fg="rgba(0, 0, 0, 0)", bg="none")
        assert build_ui_lint_defects(raw) == []


class TestTargetSizeDefect:

    def test_tiny_button_flagged(self):
        raw = _raw(smallTargets=[{"tag": "button", "w": 20, "h": 18, "text": "×"}])
        defects = build_ui_lint_defects(raw)
        assert "ui_target_size" in _types(defects)

    def test_one_dimension_small_is_enough(self):
        # 宽够但高不足 —— 仍然是点不中的目标
        raw = _raw(smallTargets=[{"tag": "a", "w": 120, "h": 12, "text": "链接"}])
        assert "ui_target_size" in _types(build_ui_lint_defects(raw))

    def test_44px_target_satisfies(self):
        raw = _raw(smallTargets=[{"tag": "button", "w": 96, "h": 44, "text": "ok"}])
        assert build_ui_lint_defects(raw) == []


class TestFontSizeDefect:

    def test_tiny_font_flagged(self):
        raw = _raw(fontSizes=["16px", "9px"])
        defects = build_ui_lint_defects(raw)
        assert "ui_font_size" in _types(defects)
        d = next(d for d in defects if d["type"] == "ui_font_size")
        assert "9px" in d["message"]

    def test_threshold_boundary(self):
        assert build_ui_lint_defects(_raw(fontSizes=[f"{_UI_FONT_HARD_MIN}px"])) == []
        assert "ui_font_size" in _types(
            build_ui_lint_defects(_raw(fontSizes=[f"{_UI_FONT_HARD_MIN - 1}px"]))
        )

    def test_malformed_size_ignored(self):
        assert build_ui_lint_defects(_raw(fontSizes=["auto", "1.2rem", None])) == []


class TestDefectCap:
    """最多 2 条：不要把修复预算耗在审美分歧上"""

    def test_all_three_violations_capped(self):
        raw = _raw(
            fg="rgb(110, 110, 110)", bg="rgb(100, 100, 100)",
            fontSizes=["9px", "16px"],
            smallTargets=[{"tag": "button", "w": 10, "h": 10, "text": "x"}],
        )
        defects = build_ui_lint_defects(raw)
        assert len(defects) == _UI_LINT_MAX_DEFECTS

    def test_every_defect_is_actionable(self):
        raw = _raw(
            fg="rgb(110, 110, 110)", bg="rgb(100, 100, 100)",
            fontSizes=["9px"],
            smallTargets=[{"tag": "button", "w": 10, "h": 10, "text": "x"}],
        )
        for d in build_ui_lint_defects(raw):
            assert d.get("message") and d.get("evidence") and d.get("suggestion")
            assert d.get("severity") == "major"

    def test_missing_optional_keys_do_not_crash(self):
        assert build_ui_lint_defects({}) == []


class TestDefectRouting:
    """路由正确性：UI 硬伤必须走「局部可修」分支，不能被误判成架构缺陷。

    为什么这条测试重要：`classify_defects` 把缺陷分成两类，
    - 架构类 → 结构上禁止走 defect_repair，必须带根因卡片回 coder 重做规划；
    - 局部类 → 走小上下文的定向修复。
    如果 UI 类型被误判成架构类，每个 UI 小问题都会触发一次重新规划（大幅变慢），
    或者干脆被跳过（永远不修）。这两条都直接决定 UI 硬伤机制是否真的生效。
    """

    def test_ui_defects_are_local_not_architectural(self):
        from harness.constraints.environment_contract import classify_defects

        raw = _raw(
            fg="rgb(110, 110, 110)", bg="rgb(100, 100, 100)",
            fontSizes=["9px", "16px"],
            smallTargets=[{"tag": "button", "w": 10, "h": 10, "text": "x"}],
        )
        defects = build_ui_lint_defects(raw)
        assert defects, "前置条件：应至少产出一条硬伤"

        architectural, local = classify_defects(defects)
        assert architectural == [], (
            f"UI 硬伤被误判为架构缺陷，会触发重新规划: {[d['type'] for d in architectural]}"
        )
        assert len(local) == len(defects)

    def test_ui_defect_type_names_are_not_in_architectural_set(self):
        """防御性断言：类型名一旦被加进架构集合，上面那条会静默失效。"""
        from harness.constraints.environment_contract import ARCHITECTURAL_DEFECT_TYPES

        for t in ("ui_contrast", "ui_target_size", "ui_font_size"):
            assert t not in ARCHITECTURAL_DEFECT_TYPES
