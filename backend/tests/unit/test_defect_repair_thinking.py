# -*- coding: utf-8 -*-
"""守卫：定向修复路径必须**关闭思考**，且修复上下文只装交付文件。

背景（需求 223 实测，deepseek-v4-flash，多轮对照）：

  thinking=on/省略字段 → 修复调用 6/6 全部 finish_reason=length，
    reasoning 吃满 12000~20000 额度、content 恒为空。而且**三个变量都改不动**：
      ① 输入砍 57%（6 文件 → 2 文件）→ reasoning 仍吃满；
      ② 输出格式换锚点（去唯一性）→ 仍吃满；
      ③ 放松「SEARCH 必须唯一」约束 → 仍吃满。
    每次 reasoning 都停在 47~50k 字符处被额度截断 —— 额度给多少吃多少。

  thinking=off → 3/3 全部 finish_reason=stop、reasoning=0、产出有效补丁 JSON
    且逐条真实命中目标文件，单轮耗时 57s → 2.4s。

  另：DeepSeek 不支持 thinking.budget_tokens（实测传 2000 仍吃满 12000）。

故本文件把结论锁死，防止后人「顺手」改回 enabled；同时守住修复上下文的口径
（`.design/**`、`.task/**` 是过程目录，不是交付文件）。
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
NODES_PY = BACKEND / "harness" / "instructions" / "nodes.py"


def _fn_node(name: str):
    tree = ast.parse(NODES_PY.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"未找到函数 {name}")


class TestRepairThinkingIsDisabled:
    def test_constant_value(self):
        from harness.instructions.nodes import _DEFECT_REPAIR_THINKING

        assert _DEFECT_REPAIR_THINKING == "disabled", (
            "修复路径的思考模式被改动了 —— 需求 223 实测：开启后 6/6 次调用"
            "把额度全烧在 reasoning 上、content 恒为空"
        )

    def test_chat_call_passes_thinking_explicitly(self):
        """AST 级：defect_repair_node 内的 client.chat 必须显式传 thinking。

        不能依赖实例配置的缺省值 —— 缺省在 DeepSeek 上等于**开启**思考
        （省略字段 ≠ 关闭，实测仍有 reasoning token）。
        """
        fn = _fn_node("defect_repair_node")
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "chat"
        ]
        assert calls, "defect_repair_node 里没找到 client.chat 调用"
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords if k.arg}
            assert "thinking" in kw, (
                "client.chat 未显式传 thinking —— 缺省等于沿用实例配置，"
                "在 DeepSeek 上会开启思考并吃光额度"
            )
            v = kw["thinking"]
            assert isinstance(v, ast.Name) and v.id == "_DEFECT_REPAIR_THINKING", (
                f"client.chat 的 thinking 应传 _DEFECT_REPAIR_THINKING，实际是 {ast.dump(v)[:60]}"
            )

    def test_trace_logging_agrees_with_call(self):
        """轨迹落库的 thinking 必须与真实调用一致，否则轨迹会撒谎。"""
        fn = _fn_node("defect_repair_node")
        logged = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id == "_log_llm_turn_safe":
                kw = {k.arg: k.value for k in n.keywords if k.arg}
                if "thinking" in kw:
                    logged.append(kw["thinking"])
        assert logged, "_log_llm_turn_safe 未记录 thinking"
        for v in logged:
            assert isinstance(v, ast.Name) and v.id == "_DEFECT_REPAIR_THINKING", (
                "轨迹记录的 thinking 与实际调用不一致"
            )

    def test_no_hardcoded_enabled_left(self):
        """修复节点里不得残留 thinking='enabled' 字面量。"""
        fn = _fn_node("defect_repair_node")
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                kw = {k.arg: k.value for k in n.keywords if k.arg}
                v = kw.get("thinking")
                if isinstance(v, ast.Constant) and v.value == "enabled":
                    raise AssertionError(
                        "修复节点里残留 thinking='enabled'")


class TestRepairContextScope:
    class _WS:
        def __init__(self, files):
            self._f = files

        def list(self):
            return list(self._f)

        def read(self, name):
            return self._f[name]

    def _ws(self):
        return self._WS({
            "index.html": '<div class="app"></div>',
            "js/app.js": "document.getElementById('todoCount');",
            "js/utils.js": "window.Utils = {};",
            "css/style.css": ".app{color:#333}",
            ".design/preset-crud.css": "body{color:red}" * 200,
            ".design/preset-game.css": "body{color:blue}" * 200,
            ".task/TASK_STATE.md": "# 状态",
            "docs/readme.md": "# 文档",
        })

    def test_excludes_process_dirs(self):
        """`.design/**`、`.task/**`、`docs/**` 都不是交付文件，不得进修复上下文。"""
        from harness.instructions import nodes as N

        defects = [{
            "message": "引用了 #todoCount 但 HTML 中不存在",
            "evidence": "document.getElementById('todoCount')",
            "suggestion": "改成真实存在的 id",
        }]
        text, ctx = N._collect_defect_repair_context(self._ws(), defects)
        assert not any(f.startswith((".design/", ".task/", "docs/")) for f in ctx), ctx
        assert "preset-crud.css" not in text, (
            "成品模板被当成交付文件塞进修复上下文（需求 223 实测占 68% 上下文）"
        )

    def test_keeps_deliverable_files(self):
        from harness.instructions import nodes as N

        defects = [{
            "message": "引用了 #todoCount 但 HTML 中不存在",
            "evidence": "document.getElementById('todoCount')",
            "suggestion": "改成真实存在的 id",
        }]
        _, ctx = N._collect_defect_repair_context(self._ws(), defects)
        assert "index.html" in ctx
        assert "js/app.js" in ctx
