# -*- coding: utf-8 -*-
"""
Evaluator 代码上下文拼装测试（req 147 复盘回归）

原实现 `content[:6000]` 无条件切一刀且不带任何标记：game.js（10,939 字符）的
move() 整个方法体被切掉，评估 LLM 把「我没看到」当成「代码没写」，
幻觉出 `move 未定义` 的根因结论。本文件锁死三条不变量：

1. 主逻辑文件（index.html / main / game / app，体积 ≤ 20k）一律完整给出
2. 确实被截断的文件，截断说明必须出现在 ``` 代码块**外面**
3. 截断说明里必须明确「不得推断为缺失」，防止 LLM 再次把截断当报错
"""

from harness.instructions.nodes import (
    _EVALUATOR_FILE_CHAR_CAP,
    _build_evaluator_code_blocks,
)


class _FakeWorkspace:
    def __init__(self, files=None):
        self._files = dict(files or {})

    def list(self):
        return list(self._files.keys())

    def read(self, name):
        if name not in self._files:
            raise FileNotFoundError(name)
        return self._files[name]


def _main_file(size: int) -> str:
    body = "// " + "x" * (size - 20)
    # 把关键方法放在文件后半段：截断实现会把这一段切掉
    return body + "\nfunction move(dir) { /* 关键逻辑 */ }\n"


class TestEvaluatorCodeBlocks:
    def test_main_file_not_truncated(self):
        """主逻辑文件（js/game.js，10k）必须完整给出，含后半段的 move()"""
        src = _main_file(10_000)
        ws = _FakeWorkspace({"index.html": "<html></html>", "js/game.js": src})
        out = _build_evaluator_code_blocks(ws, ws.list())
        assert "function move(dir)" in out, "主文件后半段被切掉了——这正是 req 147 的事故模式"
        assert "⚠️" not in out, "未超限却出现截断标记"

    def test_oversized_file_is_truncated_with_visible_marker(self):
        """超预算文件被截断时，标记必须在代码块外面且提示不得判为缺失"""
        huge = _main_file(60_000)
        ws = _FakeWorkspace({"js/game.js": huge, "css/style.css": "body{}"})
        out = _build_evaluator_code_blocks(ws, ws.list())
        assert "⚠️ 上下文限制" in out
        assert "不得" in out and "未定义" in out
        # 截断说明必须紧跟在闭合 ``` 之后（在代码块外面）
        assert "```\n> ⚠️ 上下文限制" in out, "截断标记被写进了代码块内部，会被当成源码"
        assert len(out) < len(huge), "超预算文件确实应被截断"

    def test_html_and_css_are_complete_for_normal_project(self):
        ws = _FakeWorkspace({
            "index.html": "<html><body><div id='app'></div></body></html>",
            "js/game.js": _main_file(9_000),
            "css/style.css": "body { margin: 0 }\n" * 100,
        })
        out = _build_evaluator_code_blocks(ws, ws.list())
        for frag in ("<div id='app'>", "function move(dir)", "body { margin: 0 }"):
            assert frag in out

    def test_unreadable_file_does_not_break(self):
        ws = _FakeWorkspace({"index.html": "<html></html>"})
        out = _build_evaluator_code_blocks(ws, ["index.html", "js/missing.js"])
        assert "(无法读取)" in out
        assert "<html></html>" in out

    def test_cap_constant_is_sane(self):
        """单文件降级上限必须显著大于旧的 6000，否则修正没意义"""
        assert _EVALUATOR_FILE_CHAR_CAP >= 8_000
