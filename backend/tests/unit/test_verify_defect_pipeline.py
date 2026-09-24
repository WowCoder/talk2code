# -*- coding: utf-8 -*-
"""
verify → 修复 回传链路回归测试（req 148 复盘）

req 148 的因果链：AC 断言抓到了 4 条真实产品缺陷，但这些证据从未进入修复环节；
唯一回传的是 2 条静态分析误报；coder 第二轮 8 轮迭代全废；等第二轮跑出 AC 结果时
修复预算已经耗尽。这里锁死三件事：

1. AC 断言失败必须转成可执行缺陷，且带上复现步骤（能定位根因文件）
2. 路由不得因为「架构类预算耗尽」就丢掉手上的局部缺陷
3. 确定性证据必须独立于 evaluator 进入对话上下文（评估器无权替我们滤掉）
"""
import pytest

from harness.constraints.environment_contract import (
    _extract_js_apis, check_cross_file_contract, classify_defects,
)
from harness.graph import route_after_verify
from harness.instructions.nodes import _build_ac_failure_defects


AC_RESULTS = [
    {"ac_id": "AC-1", "label": "页面加载后显示初始方块", "passed": True, "failures": []},
    {
        "ac_id": "AC-2",
        "label": "方向键触发方块滑动合并",
        "passed": False,
        "failures": ["DOM 无变化: 棋盘发生变化（观察到变更 0 次，期望 ≥1）"],
    },
    {
        "ac_id": "AC-3",
        "label": "合并后分数立即增加",
        "passed": False,
        "failures": ["DOM 无变化: 棋盘发生变化（观察到变更 0 次，期望 ≥1）"],
    },
]

STEPS = {"AC-2": "click #start-btn → wait 800 → press ArrowLeft → wait 500"}


class TestAcFailureDefects:
    def test_failed_ac_becomes_defect(self):
        ds = _build_ac_failure_defects(AC_RESULTS, STEPS)
        assert len(ds) == 2, "2 条 AC 失败必须变成 2 条缺陷"
        assert {d["ac_id"] for d in ds} == {"AC-2", "AC-3"}
        assert all(d["type"] == "ac_failure" for d in ds)

    def test_defect_carries_repro_steps_for_root_cause_locate(self):
        """evidence 必须含 selector，否则 _extract_root_cause_files 定位不到文件"""
        ds = _build_ac_failure_defects(AC_RESULTS, STEPS)
        d2 = next(d for d in ds if d["ac_id"] == "AC-2")
        assert "#start-btn" in d2["evidence"]
        assert "ArrowLeft" in d2["evidence"]
        assert "复现步骤" in d2["suggestion"]

    def test_passed_ac_yields_no_defect(self):
        assert _build_ac_failure_defects([AC_RESULTS[0]], STEPS) == []
        assert _build_ac_failure_defects([], STEPS) == []
        assert _build_ac_failure_defects(None, None) == []

    def test_ac_defect_is_routed_to_local_repair(self):
        """AC 失败多为局部逻辑缺失，走小上下文定向修复（不消耗 coder 重构预算）"""
        ds = _build_ac_failure_defects(AC_RESULTS, STEPS)
        arch, local = classify_defects(ds)
        assert arch == []
        assert len(local) == 2


class TestRouteAfterVerifyDegrade:
    @staticmethod
    def _state(arch, local, repair_count, max_rounds=2, defect_count=0):
        return {
            "verify_passed": False,
            "architectural_defects": arch,
            "smoke_defects": local,
            "metadata": {
                "repair_count": repair_count,
                "defect_repair_count": defect_count,
                "defect_repair_llm_failures": 0,
                "complexity": "standard",
            },
        }

    def test_arch_budget_exhausted_still_tries_local_repair(self):
        """req 148：架构类预算耗尽时原先直接 done，手上 4 条 AC 缺陷没人处理"""
        st = self._state(
            arch=[{"type": "missing_api", "message": "x"}],
            local=[{"type": "ac_failure", "message": "方向键无响应"}],
            repair_count=2,
        )
        assert route_after_verify(st) == "defect_repair"

    def test_arch_within_budget_goes_to_coder(self):
        st = self._state(
            arch=[{"type": "missing_api", "message": "x"}],
            local=[],
            repair_count=0,
        )
        assert route_after_verify(st) == "coder"

    def test_pass_short_circuits(self):
        st = self._state(arch=[], local=[], repair_count=0)
        st["verify_passed"] = True
        assert route_after_verify(st) == "done"


class TestContractAliasExport:
    """req 148 的 missing_api 误报：`global.Storage = Store` 未被识别"""

    STORAGE = """(function (global) {
  var Store = {};
  Store.getHighScore = function () { return 0; };
  Store.setHighScore = function (s) {};
  global.Storage = Store;
})(window);"""

    GAME = """Storage.getHighScore();
Storage.setHighScore(10);"""

    def test_alias_of_attr_assigned_object_is_resolved(self):
        apis, _opaque, _declared = _extract_js_apis(self.STORAGE)
        assert "Storage" in apis
        assert apis["Storage"] >= {"getHighScore", "setHighScore"}

    def test_no_false_missing_api(self):
        defects, _w = check_cross_file_contract(
            {"js/storage.js": self.STORAGE, "js/game.js": self.GAME}
        )
        assert not [d for d in defects if d["type"] == "missing_api"]

    @pytest.mark.parametrize("src", [
        "var U = {}; U.a = function(){}; global.U = U;",
        "var U = {}; U.a = function(){}; window.U = U;",
        "var U = {}; U.a = function(){}; self.U = U;",
    ])
    def test_all_global_prefixes(self, src):
        apis, _, _ = _extract_js_apis(src)
        assert "U" in apis and "a" in apis["U"]

    def test_real_break_still_detected(self):
        """不能为了消误报把真实断裂也放过"""
        defects, _ = check_cross_file_contract({
            "js/utils.js": "var U = {}; U.a = function(){}; global.U = U;",
            "js/app.js": "U.b();",
        })
        assert any(d["type"] == "missing_api" for d in defects)

    def test_nested_namespace_not_flagged(self):
        """req 151：Utils.LocalStore.getItem 的 LocalStore 是 Utils 的属性对象，
        不是顶层全局——不得报 missing_global（否则占着架构类缺陷耗尽 coder 预算）"""
        defects, _ = check_cross_file_contract({
            "js/utils.js": (
                "var Utils = { LocalStore: { getItem: function(){return 0;}, "
                "setItem: function(){} } }; global.Utils = Utils;"
            ),
            "js/game.js": "Utils.LocalStore.getItem('x'); Utils.LocalStore.setItem('y',1);",
        })
        assert not any(d["type"] == "missing_global" and "LocalStore" in d["message"] for d in defects)
        assert not any(d["type"] == "missing_api" for d in defects)

    def test_window_prefixed_global_not_swallowed(self):
        """`window.X` 的 X 是顶层全局，不能被子命名空间逻辑吞掉"""
        defects, _ = check_cross_file_contract({
            "js/utils.js": "var Utils = { a: function(){} }; global.Utils = Utils;",
            "js/app.js": "window.Utils.toast();",
        })
        assert any(d["type"] == "missing_api" and "Utils.toast" in d.get("evidence", "") for d in defects)

    def test_iife_return_global_not_flagged(self):
        """req 152：var Store = (function(){...return{getItem,...};})();
        global.Store = Store; → Store 进 declared 但不在 apis（非对象字面量）。
        契约检查器此前忽略 declared，把 Store.getItem 误报 missing_global。
        修复：check_cross_file_contract 收集 declared 到 known_globals，
        在报 missing_global 前先查 known_globals。"""
        store_js = """(function (global) {
  var Store = (function () {
    return {
      getItem: function (key) { return null; },
      setItem: function (key, value) {}
    };
  })();
  global.Store = Store;
  var Game = {
    init: function () { Store.getItem('best'); },
    save: function () { Store.setItem('best', 1); }
  };
  global.Game = Game;
})(window);"""
        game_js = "Game.init(); Game.save(); Store.getItem('x');"
        defects, _ = check_cross_file_contract({
            "js/store.js": store_js,
            "js/main.js": game_js,
        })
        assert not any(d["type"] == "missing_global" and "Store" in d.get("message", "") for d in defects), \
            "IIFE 返回值导出的全局不应被误报 missing_global"
        assert not any(d["type"] == "missing_api" and "Store" in d.get("message", "") for d in defects), \
            "Store 方法不可静态枚举，不应报 missing_api"
        # Game 是对象字面量，方法可枚举 → 不应有 missing_api
        assert not any(d["type"] == "missing_api" and "Game" in d.get("message", "") for d in defects)

    def test_genuinely_missing_global_still_detected(self):
        """known_globals 修复不能放过真正未定义的全局"""
        defects, _ = check_cross_file_contract({
            "js/app.js": "UndefinedThing.doStuff();",
        })
        assert any(d["type"] == "missing_global" and "UndefinedThing" in d.get("message", "") for d in defects)
