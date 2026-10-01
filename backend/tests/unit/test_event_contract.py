# -*- coding: utf-8 -*-
"""事件契约守卫测试。

契约（harness/observability/event_contract.py）是事件类型的唯一定义处，
前端的中文名与配色都从它下发。这些用例保证「新增类型只改一处」这件事
不会悄悄退化 —— 比如某个 kind 挂了一个契约里没有的 stage，前端就会渲染成
灰点，而后端完全无感。
"""
import pytest

from harness.observability.event_contract import (
    EVENT_KINDS, EVENT_STAGES, STAGE_CODING, STAGE_VERIFYING,
    contract_payload, infer_stage_from_legacy, kind_spec, stage_spec,
    unknown_kinds_in,
)


class TestContractSelfConsistency:

    def test_every_kind_has_a_known_stage(self):
        """每个 kind 挂的 stage 必须是契约里登记过的阶段。"""
        bad = {k: v["stage"] for k, v in EVENT_KINDS.items()
               if v["stage"] not in EVENT_STAGES}
        assert not bad, f"这些 kind 挂了未登记的 stage：{bad}"

    def test_every_kind_has_label_and_color(self):
        for k, v in EVENT_KINDS.items():
            assert v.get("label"), f"kind={k} 缺中文名"
            assert v.get("color"), f"kind={k} 缺配色"

    def test_payload_shape_matches_frontend(self):
        """前端按这份结构渲染，字段不能悄悄改名。"""
        p = contract_payload()
        assert set(p) == {"kinds", "stages", "filter_kinds",
                          "fallback_kind", "fallback_stage"}
        assert all(k in p["kinds"] for k in p["filter_kinds"]), \
            "筛选 chip 引用的 kind 必须已在契约里登记"


class TestFallback:

    def test_unknown_kind_is_reported_not_swallowed(self):
        """未登记的类型必须能被识别出来，而不是默默当成正常数据。"""
        assert unknown_kinds_in(["llm_turn", "bogus"]) == ["bogus"]
        assert unknown_kinds_in([]) == []

    def test_unknown_kind_gets_neutral_color(self):
        """兜底是中性灰 + label 为 None（前端显示原名），不能编一个假名字。"""
        spec = kind_spec("totally_new_kind")
        assert spec["label"] is None
        assert spec["color"]

    def test_unknown_stage_gets_neutral_color(self):
        assert stage_spec("nope")["label"] is None


class TestStageInferenceForLegacyLogs:
    """旧 jsonl 没有 stage 字段，回填靠 iteration 推断 —— 这条判据必须锁死。

    ToolCallLoop 传的是 `iteration + 1`（从 1 起），verify / repair / AC 翻译
    走 _log_llm_turn_safe 固定传 0。一旦哪天 ToolCallLoop 改成从 0 起，
    这里的推断就全错了，存量数据会集体标错阶段。
    """

    def test_tool_loop_iteration_starts_at_one(self):
        """埋点必须保持 `iteration + 1`（从 1 起）。

        断言整个类的源码而不是某一个方法：ToolCallLoop 的迭代主体在重构中
        挪过位置（run 拆成 run + _run_impl），锁死在方法名上会让守卫变成
        「改个函数名就红」的噪声，而真正的判据是「这个类里仍以 iteration+1 埋点」。
        """
        import inspect
        from harness.runtime import ToolCallLoop
        src = inspect.getsource(ToolCallLoop)
        assert "iteration + 1" in src, \
            "ToolCallLoop 的埋点必须保持 iteration + 1（从 1 起）"

    @pytest.mark.parametrize("iteration,expected", [
        (0, STAGE_VERIFYING),    # 旧日志：辅助链路把 0 当哨兵传
        (None, STAGE_VERIFYING),  # 新日志：辅助链路传 None
        (1, STAGE_CODING),
        (7, STAGE_CODING),
    ])
    def test_legacy_inference(self, iteration, expected):
        assert infer_stage_from_legacy(iteration) == expected


class TestAuxInstrumentationDiscipline:
    """辅助链路的埋点不得再用 `0` 当迭代号。

    0 不是「第 0 轮编码」，而是一个哨兵值。传 0 的后果是：规划 / AC 翻译 /
    验收 / 修复这些不参与编码迭代的调用，会顶着「第 0 轮」混进编码子迭代
    分组，界面上分不出哪条属于哪轮 —— 与 `file_count` 记成 0 同一类问题，
    都是**日志在说不真的事**。正确写法是 `None`（无迭代归属）。

    用 AST 而不是字符串匹配：格式化、换行、改用关键字传参都不该让守卫失效，
    而「第二个实参缺失」这种情况必须能报出来。
    """

    def test_aux_calls_pass_none_not_zero(self):
        import ast
        import inspect
        from harness.instructions import nodes

        tree = ast.parse(inspect.getsource(nodes))
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "_log_llm_turn_safe"):
                continue
            kw = next((k.value for k in node.keywords if k.arg == "iteration"), None)
            val = kw if kw is not None else (node.args[1] if len(node.args) > 1 else None)
            if not (isinstance(val, ast.Constant) and val.value is None):
                bad.append((node.lineno, ast.unparse(val) if val is not None else "<缺省>"))

        assert not bad, f"辅助埋点的 iteration 必须显式传 None，实测：{bad}"
