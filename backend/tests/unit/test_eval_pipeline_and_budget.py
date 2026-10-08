# -*- coding: utf-8 -*-
"""评测链路 + 迭代预算 四处修复的守卫。

背景（2026-10-05 / 10-07 评测实测）：
    `eval/run_eval.py` 直连 `ToolCallLoop`、`state["plan"] = None`。
    三层防线随之静默失效：

      ① 编码提示词里的「## 推荐文件结构 / 实现计划」整段消失
         （21 个任务的编码提示词里「## 推荐文件结构」出现 0 次）；
      ② `_pending_plan_files()` 恒返回 []→ 每轮「进度检查：还差 N 个文件」
         一条都没发出过（提示词里出现 0 次）；
      ③ 迭代预算退化成 max(0,3)+3 = 6 轮，与真实工作量脱钩。

    代价：8/21 个任务连 index.html 都没创建就被预算耗尽，断言第一条就挂。

本文件钉住的五件事（都是「改回去就会静默退化」的那种）：
    1. 评测默认走真实规划 + coder_node，且规划失败会显式记录而不是静默退回
    2. 迭代预算：有计划按计划文件数，无计划有独立兜底（不再是 6）
    3. 无计划时 `_pending_plan_files` 仍能报出「入口文件缺失」
    4. 写过文件但入口还没建 → 每轮催「先落入口」
    5. 纯记账轮不占预算 / 语法硬伤当轮提醒
"""

import ast
import importlib.util
import json
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from harness.runtime import ToolCallLoop
from harness.state.workspace import WorkspaceFS
from harness.tools.registry import ToolResult

_REPO = Path(__file__).resolve().parents[3]
_GOOD_INDEX = "<!doctype html><html><body><h1>ok</h1></body></html>"


# ---------- 桩：可控的 loop / client ----------

def _make_loop(tmp: Path, fake_exec=None, write_index: bool = True):
    ws = WorkspaceFS(user_id=999, requirement_id=1, base_dir=tmp)
    ws.init([])
    if write_index:
        ws.write("index.html", _GOOD_INDEX)

    class _StubLoop(ToolCallLoop):
        def _execute_tool(self, state, tool_call):
            if fake_exec is None:
                return ToolResult(content="ok")
            return fake_exec(self.workspace, state, tool_call)

    loop = _StubLoop(workspace=ws, git=None, tools=Mock())
    # 测试目标是「预算 / 提醒是否投递」，不是熔断 → 关掉无进展与读写比两条熔断，
    # 让唯一的终止条件就是迭代预算（否则桩客户端全程只读会先被读写比熔断终止，
    # 断言轮数就变成在测熔断而不是测预算）。熔断本身另有 test_tool_loop_antiloop.py。
    loop._check_no_progress = lambda state: False
    loop.READ_HEAVY_ABORT = 10_000
    return loop, ws


def _tc(name, **args):
    t = Mock()
    t.name = name
    t.arguments = args
    return t


def _resp(tool_calls):
    r = Mock()
    r.tool_calls = tool_calls
    r.content = "继续"
    r.reasoning_content = ""
    r.usage = None
    r.error = None
    r.is_error = False
    r.finish_reason = "tool_calls"
    return r


def _client(fn):
    c = Mock()
    c.chat_with_tools.side_effect = fn
    return c


def _state(**extra):
    s = {
        "requirement_id": 1,
        "requirement_content": "做一个页面",
        "user_id": 1,
        "dialogue_history": [],
        "code_files": [],
        "tool_call_count": 0,
        "no_progress_count": 0,
        "last_file_list": [],
        "hook_failures": {},
        "metadata": {},
    }
    s.update(extra)
    return s


def _run(loop, state, client):
    with patch("harness.runtime.get_client", return_value=client):
        return loop.run(state)


# ---------- 1. 评测默认口径：真实规划 + coder_node ----------

def _run_eval_source() -> str:
    return (_REPO / "eval" / "run_eval.py").read_text(encoding="utf-8")


def test_run_eval_defaults_to_real_planning():
    """`--with-plan` 必须默认 True，且 run_one_task 真的调用 coder_node。

    这是一条防退化断言：把默认值翻回 False、或把 coder_node 换回裸 loop.run，
    评测数字立刻退回「无计划的裸 Coder」口径 —— 而那个口径既不代表产品链路，
    也验证不了任何防护（见文件顶部）。
    """
    tree = ast.parse(_run_eval_source())

    defaults = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        flag = node.args[0].value
        if flag in ("--with-plan", "--no-plan"):
            defaults[flag] = {
                kw.arg: kw.value.value
                for kw in node.keywords
                if isinstance(kw.value, ast.Constant)
            }

    assert defaults.get("--with-plan", {}).get("default") is True, (
        "--with-plan 的默认值必须为 True（评测默认走真实规划）"
    )
    assert defaults.get("--no-plan", {}).get("action") == "store_false", (
        "--no-plan 必须用 store_false 把同一个开关置 False"
    )
    assert defaults["--no-plan"].get("dest") == "with_plan", (
        "--no-plan 必须写进同一个 dest，否则两个开关互相覆盖"
    )

    src = _run_eval_source()
    assert "from harness.instructions.nodes import coder_node" in src, (
        "run_one_task 必须走真实 coder_node（否则 tool_thinking / 契约 / Phase2 补全全丢）"
    )


def test_main_snapshots_progress_after_every_task():
    """主循环里**每跑完一题**都要落一次进度快照（含 resume 跳过的分支）。

    这是「跑的时候能逐题看到成绩」的唯一来源：漏掉任何一条追加 `results` 的
    分支，那一类题在整轮里都不会出现在页面上，而且不会有任何报错 ——
    典型的静默退化。所以按 AST 数调用次数，而不是靠 grep 看个大概。
    """
    tree = ast.parse(_run_eval_source())
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    loop = next(n for n in ast.walk(main)
                if isinstance(n, ast.For)
                and isinstance(n.target, ast.Tuple)
                and any(isinstance(t, ast.Name) and t.id == "task"
                        for t in n.target.elts))
    snaps = [n for n in ast.walk(loop)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "snap"]
    appends = [n for n in ast.walk(loop)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "append"
               and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "results"]
    assert len(snaps) == len(appends) == 2, (
        f"每条追加 results 的分支（正常 + resume 跳过）后面都要跟一次快照，"
        f"当前 snap={len(snaps)} append={len(appends)}"
    )


def _load_run_eval():
    import sys

    spec = importlib.util.spec_from_file_location(
        "eval_run_eval_under_test", _REPO / "eval" / "run_eval.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # 必须先登记进 sys.modules 再 exec_module：dataclass 装饰器要反查
    # sys.modules[cls.__module__] 判断类型别名，否则报 'NoneType' has no attribute '__dict__'
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def test_plan_for_task_passes_clarify_marker(monkeypatch):
    """评测需求必须带 `[用户补充说明]` 放行标记，否则永远拿不到计划。

    题库只有一句话需求，TL 的需求确认门禁会拦下来问问题（needs_clarification），
    评测就永远走不到 plan。这里用生产真实存在的标记（用户填完澄清表单后需求里
    带的就是它）放行。
    """
    mod = _load_run_eval()
    captured = {}

    def _fake_tl(state):
        captured.update(state)
        return {
            "plan": {"file_structure": ["index.html"], "implementation_order": ["index.html"]},
            "implementation_order": ["index.html"],
            "metadata": {"complexity": "standard"},
            "current_step": "team_leader_done",
        }

    monkeypatch.setattr("harness.instructions.nodes.team_leader_node", _fake_tl)
    res, err = mod.plan_for_task({"id": "t01", "requirement": "做一个个人名片页"})

    assert err == ""
    assert res["plan"]["file_structure"] == ["index.html"]
    req = captured["requirement_content"]
    assert req.startswith("做一个个人名片页"), "原需求必须原样前置，不能被标记改写"
    assert "[用户补充说明]" in req, "缺少放行标记 → TL 会走澄清分支，评测拿不到计划"


def test_plan_for_task_reports_failure_explicitly(monkeypatch):
    """规划失败必须显式返回原因，而不是静默退回裸编码口径。"""
    mod = _load_run_eval()

    monkeypatch.setattr(
        "harness.instructions.nodes.team_leader_node",
        lambda state: {"plan": {}, "current_step": "needs_clarification"},
    )
    res, err = mod.plan_for_task({"id": "t02", "requirement": "做一个着陆页"})
    assert res == {}
    assert "澄清" in err


# ---------- 1b. 规划器容错：tasks 可为空 / 实现顺序可推导 ----------
#
# 2026-10-07 评测实测：agnes-3.0-flash 对「做一个个人名片页」返回
# `tasks: []`（features / file_structure / implementation_order 都齐全），
# 而 L3 完整性校验把 tasks 当硬必需字段 → 整份合法计划被丢弃 → TL 失败
# → 整条链路退回没有计划的裸编码。下游 plan_validator 早就写明
# 「simple 复杂度允许省略」，两处口径必须一致。

def test_derive_implementation_order_puts_entry_last():
    from harness.instructions.nodes import _derive_implementation_order

    plan = {"file_structure": ["index.html", "js/app.js", "css/style.css"]}
    assert _derive_implementation_order(plan) == ["js/app.js", "css/style.css", "index.html"], (
        "被依赖的 css/js 在前、入口 html 最后（与 tl_analysis.md 示例一致）"
    )
    assert _derive_implementation_order({"file_structure": []}) == []


def test_team_leader_accepts_plan_with_empty_tasks(monkeypatch):
    """空 tasks 不再让整份计划作废；缺失的 implementation_order 会被推导出来。"""
    import json as _json

    from harness.instructions import nodes

    plan_json = _json.dumps({
        "requirement_restated": "一张渐变背景的个人名片",
        "features": ["展示姓名与职位", "展示简介", "渐变背景"],
        "assumptions": ["内容硬编码在 HTML 中"],
        "acceptance_criteria": [
            {"id": "AC-1", "label": "能看到姓名", "feature": "展示姓名与职位",
             "anchor": "页面顶部的主标题区域",
             "how_to_verify": "打开页面，顶部能看到姓名与职位文字"},
        ],
        "file_structure": ["index.html", "css/style.css"],
        "tech_stack": {"css": "native", "framework": "vanilla"},
        "implementation_order": [],
        "tasks": [],
        "complexity": "simple",
    }, ensure_ascii=False)

    client = Mock()
    client.chat.side_effect = lambda *a, **kw: Mock(
        content=plan_json, is_error=False, error=None, finish_reason="stop"
    )
    monkeypatch.setattr(nodes, "get_client", lambda: client)

    res = nodes.team_leader_node({
        "requirement_id": 1,
        "requirement_content": "做一个个人名片页\n\n[用户补充说明]\n无需澄清",
        "dialogue_history": [],
        "metadata": {},
    })

    assert res.get("current_step") == "team_leader_done", (
        f"空 tasks 不应让规划失败：{res.get('error')}"
    )
    assert res["plan"]["file_structure"] == ["index.html", "css/style.css"]
    assert res["implementation_order"] == ["css/style.css", "index.html"], (
        "implementation_order 缺失时必须从 file_structure 推导，"
        "否则迭代预算 / 缺文件提醒 / 契约 / Phase2 补全四件事全部静默失效"
    )


def test_resolve_content_counts_inline_style():
    """查 .css 时须把 HTML 里的 inline `<style>` 也算进来（同扩展名回退的同类项）。

    2026-10-07 实测 t01：plan 选了单文件方案，页面用 inline `<style>` 里的
    `--grad-cool: linear-gradient(...)` 实现了渐变背景、预览零错误，
    却因为工作区里没有 .css 文件被判 `content_contains` 失败 —— 假阴性。
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        mod = _load_run_eval()
        ws = WorkspaceFS(user_id=0, requirement_id=1, base_dir=tmp)
        ws.init([])
        ws.write("index.html",
                 '<!doctype html><html><head><style>\n'
                 'body { background: var(--grad-cool); }\n'
                 '--grad-cool: linear-gradient(135deg, #0e7490, #164e63);\n'
                 '</style></head><body><h1>张明</h1></body></html>')

        checker = mod.AssertionChecker(ws, run_preview=False)
        res = checker.check({"type": "content_contains",
                             "filename": "style.css", "text": "linear-gradient"})
        assert res.passed, f"inline <style> 里的样式不应被判缺失：{res.detail}"

        # 反向断言同样要覆盖 inline 样式（更严而非更松）
        res2 = checker.check({"type": "content_not_contains",
                              "filename": "style.css", "text": "grad-cool"})
        assert not res2.passed, "content_not_contains 也应看到 inline 样式"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_selector_match_supports_union_and_class_substring():
    """选择器匹配要支持**逗号并集**与 `[class*=x]`。

    2026-10-07 实测 t02（着陆页）：CTA 写成 `<a class="btn btn--sm" href="#cta">`
    —— 着陆页里更常见的写法，语义上比 `<button>` 更对（它是跳转不是提交）。
    断言只写字面 `button` 就把它判成"未匹配"，是一条与产物质量无关的假阴性：
    整页 4 条断言里另外 3 条全过，唯独卡在这里。
    """
    mod = _load_run_eval()
    sel = "button, [class*=btn], [class*=button]"
    assert mod.AssertionChecker._selector_match(
        '<a class="btn btn--sm" href="#cta">立即开始</a>', sel)          # 类名 btn
    assert mod.AssertionChecker._selector_match(
        '<a class="cta-button" href="#">开始</a>', sel)                  # 类名含 button
    assert mod.AssertionChecker._selector_match(
        '<button type="submit">提交</button>', sel)                      # 真按钮
    # 不能因为放宽就漏拦：普通导航链接不是按钮
    assert not mod.AssertionChecker._selector_match(
        '<a class="nav__link" href="#cta">产品</a>', sel)
    # 旧写法（单标签 / 单类 / 单 id）行为不变
    assert mod.AssertionChecker._selector_match("<ul><li>a</li></ul>", "ul")
    assert not mod.AssertionChecker._selector_match("<ol></ol>", "ul")
    assert mod.AssertionChecker._selector_match('<div id="app"></div>', "#app")
    assert mod.AssertionChecker._selector_match('<i class="icon-x"></i>', ".icon-x")


# ---------- 2. 迭代预算 ----------

def test_budget_uses_plan_file_count():
    """有计划 → 预算 = 文件数 + ITERATION_SLACK（上限 PLANNED_ITERATION_CAP）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, _ = _make_loop(tmp)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("read_file", filename=f"filler/r{counter['n']}.txt")])

        plan_files = ["index.html", "css/style.css", "js/app.js", "js/storage.js"]
        _run(loop, _state(implementation_order=plan_files), _client(_respond))

        expected = min(len(plan_files) + ToolCallLoop.ITERATION_SLACK,
                       ToolCallLoop.PLANNED_ITERATION_CAP)
        assert counter["n"] == expected, (
            f"有计划时预算应为 {expected}，实际跑了 {counter['n']} 轮"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_budget_without_plan_is_not_six():
    """无计划 → 独立兜底轮数（此前的 max(0,3)+3=6 会让入口文件永远写不完）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, _ = _make_loop(tmp)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("read_file", filename=f"filler/r{counter['n']}.txt")])

        _run(loop, _state(), _client(_respond))

        assert ToolCallLoop.UNPLANNED_ITERATIONS > 6, "无计划兜底必须严格大于历史值 6"
        assert counter["n"] == ToolCallLoop.UNPLANNED_ITERATIONS, (
            f"无计划时预算应为 {ToolCallLoop.UNPLANNED_ITERATIONS}，实际 {counter['n']}"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 3. 无计划时仍能报出入口缺失 ----------

def test_pending_plan_files_falls_back_to_entry():
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, ws = _make_loop(tmp, write_index=False)
        st = _state()
        assert loop._pending_plan_files(st) == ["index.html"], (
            "无计划时也必须报出入口缺失，否则「进度检查」永久静音"
        )
        ws.write("index.html", _GOOD_INDEX)
        assert loop._pending_plan_files(st) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 4. 入口优先提醒 ----------

def test_entry_reminder_fires_when_no_html_yet():
    tmp = Path(tempfile.mkdtemp())
    try:
        def _fake_exec(workspace, state, tc):
            fname = tc.arguments.get("filename", "")
            if fname:
                workspace.write(fname, "body { color: red; }\n")
            return ToolResult(content="ok")

        loop, _ = _make_loop(tmp, fake_exec=_fake_exec, write_index=False)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("write_file", filename=f"css/c{counter['n']}.css",
                              content="body { color: red; }")])

        final = _run(loop, _state(), _client(_respond))

        hits = [
            m for m in final["dialogue_history"]
            if m.get("role") == "system" and "必须先创建 index.html" in str(m.get("content", ""))
        ]
        assert hits, "写过文件却没有入口时，必须每轮催「先落入口」"
        assert hits[0].get("preserve") is True, "preserve=False 会在上下文压缩时被丢掉"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_entry_reminder_silent_before_any_write():
    """一个文件都还没写时不催 —— 给模型读模板 / 定方案的空间。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, _ = _make_loop(tmp, write_index=False)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("read_file", filename=f"filler/r{counter['n']}.txt")])

        final = _run(loop, _state(), _client(_respond))
        hits = [
            m for m in final["dialogue_history"]
            if m.get("role") == "system" and "必须先创建 index.html" in str(m.get("content", ""))
        ]
        assert not hits, "零写入阶段不应催入口，否则会打断方案探索"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 5. 纯记账轮 / 语法硬伤 ----------

def test_notes_only_round_does_not_consume_budget():
    """整轮只发 update_task_notes → 退还预算（有上限，不会变成无限轮）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, _ = _make_loop(tmp)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("update_task_notes", notes=f"第 {counter['n']} 轮")])

        final = _run(loop, _state(), _client(_respond))

        assert final.get("_notes_only_rounds") == ToolCallLoop.NOTES_ONLY_REFUND_MAX, (
            "纯记账轮的退款次数应达到上限后停止"
        )
        assert counter["n"] == (ToolCallLoop.UNPLANNED_ITERATIONS
                               + ToolCallLoop.NOTES_ONLY_REFUND_MAX), (
            f"记账轮应退还 {ToolCallLoop.NOTES_ONLY_REFUND_MAX} 轮，实际总轮数 {counter['n']}"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_notes_only_round_not_refunded_when_it_writes():
    """同一轮里既有记账又有写入 → 不退款（那是正常的推进轮）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        def _fake_exec(workspace, state, tc):
            fname = tc.arguments.get("filename", "")
            if fname:
                workspace.write(fname, "body { color: red; }\n")
            return ToolResult(content="ok")

        loop, _ = _make_loop(tmp, fake_exec=_fake_exec)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([
                _tc("update_task_notes", notes="x"),
                _tc("write_file", filename=f"css/c{counter['n']}.css", content="body{}"),
            ])

        final = _run(loop, _state(), _client(_respond))
        assert final.get("_notes_only_rounds", 0) == 0
        assert counter["n"] == ToolCallLoop.UNPLANNED_ITERATIONS
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_syntax_reminder_fires_same_round():
    """首写就被截断的文件（没有上一版可回滚）必须当轮说，不能等交付门禁。

    用 .css 花括号失衡作为判据：纯 Python 判定，不依赖 node 是否在 PATH 上。
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        def _fake_exec(workspace, state, tc):
            fname = tc.arguments.get("filename", "")
            if fname:
                # 半截 CSS：故意不闭合，模拟输出被截断
                workspace.write(fname, "body {\n  color: red;\n")
            return ToolResult(content="ok")

        loop, _ = _make_loop(tmp, fake_exec=_fake_exec)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("write_file", filename="css/style.css", content="body {")])

        final = _run(loop, _state(), _client(_respond))

        hits = [
            m for m in final["dialogue_history"]
            if m.get("role") == "system" and "语法检查不通过" in str(m.get("content", ""))
        ]
        assert hits, "语法硬伤必须在写入的当轮提醒，否则后面每轮都建在坏文件上"
        assert "css/style.css" in hits[0]["content"]
        assert hits[0].get("preserve") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_syntax_reminder_silent_when_clean():
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, _ = _make_loop(tmp)
        counter = {"n": 0}

        def _respond(*a, **kw):
            counter["n"] += 1
            return _resp([_tc("read_file", filename=f"filler/r{counter['n']}.txt")])

        final = _run(loop, _state(), _client(_respond))
        hits = [
            m for m in final["dialogue_history"]
            if m.get("role") == "system" and "语法检查不通过" in str(m.get("content", ""))
        ]
        assert not hits
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 6. 拆分后的语法检查口径 ----------

def test_syntax_only_excludes_broken_references():
    """`_check_deliverable_syntax_only` 不能把「引用缺失」也算成语法问题。

    两条提醒各管一件事：合成一条会让模型以为同一个缺失文件有两处问题。
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        loop, ws = _make_loop(tmp, write_index=False)
        ws.write("index.html",
                 '<!doctype html><html><body><script src="js/app.js"></script></body></html>')
        assert loop._check_deliverable_syntax_only() == []
        assert any("js/app.js" in p for p in loop._check_deliverable_syntax()), (
            "合并口径仍须包含引用缺失（交付门禁依赖它）"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 7. 运行中的进度快照 ----------

def test_write_progress_is_atomic_and_handed_over_to_run_json(monkeypatch):
    """进度快照：每跑完一题写一次，最终由 run.json 接管。

    两个容易做错的地方：
      · **写要原子**（先写 .tmp 再 os.replace）。后台随时可能在读它，
        读到写了一半的 JSON 会解析失败 —— 页面会在一次运行中途闪成「不存在」。
      · **交接要干净**。run.json 落盘后快照必须消失，否则「结果侧」有两个
        来源，而其中一个还写着「进行中」。
    """
    mod = _load_run_eval()
    monkeypatch.setattr(mod, "_resolve_model_name", lambda: "agnes-3.0-flash")
    run_dir = Path(tempfile.mkdtemp())
    try:
        args = SimpleNamespace(with_plan=True, with_memory=False,
                               no_preview=False, tasks=None)
        r = mod.TaskResult(id="t01", name="个人名片页", level=1, passed=True,
                           duration_s=12.3)

        mod.write_progress(run_dir, args=args, started_at=time.time(),
                           results=[r], tasks_total=21)

        snap = run_dir / "progress.json"
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert payload["tasks_total"] == 21
        assert payload["model"] == "agnes-3.0-flash"
        assert [t["id"] for t in payload["tasks"]] == ["t01"]
        assert payload["tasks"][0]["passed"] is True
        assert not (run_dir / "progress.json.tmp").exists(), (
            "临时文件必须被 os.replace 换掉，不能留在目录里被当成数据库文件"
        )

        mod.write_run_manifest(run_dir, args=args, started_at=time.time(),
                               results=[r])
        assert (run_dir / "run.json").exists()
        assert not snap.exists(), "run.json 落盘后不能留着写着「进行中」的快照"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
