# -*- coding: utf-8 -*-
"""写入保护回归测试（需求 183 根因）

两轮 QA 低分（4.2 / 2.4）的共同根因是：LLM 输出额度被 reasoning 吃掉后，
后写的文件在半句话处被截断，并**覆盖**掉此前已经写好的完整版本
（js/game.js 555 行 → 90 行，末尾停在 `DragonGame.prototype._`）。
写入前看不见截断，只能在写后校验并回滚。
"""

import pytest

from harness.tools.file_tools import (
    WriteFileHandler, _syntax_problem, _completeness_hints,
)

GOOD_JS = (
    "(function (global) {\n"
    "  'use strict';\n"
    "  var DragonGame = function () { this.score = 0; };\n"
    "  DragonGame.prototype.start = function () { return true; };\n"
    "  global.DragonGame = DragonGame;\n"
    "})(window);\n"
)

TRUNCATED_JS = "(function (global) {\n  'use strict';\n  DragonGame.prototype._"


class _WS:
    """最小 workspace 替身"""

    def __init__(self):
        self.files = {}

    def read(self, name):
        if name not in self.files:
            raise FileNotFoundError(name)
        return self.files[name]

    def write(self, name, content):
        self.files[name] = content


@pytest.fixture
def handler():
    h = WriteFileHandler()
    h.workspace = _WS()
    return h


class TestSyntaxProblem:
    def test_truncated_js_detected(self):
        assert _syntax_problem("js/game.js", TRUNCATED_JS)

    def test_complete_js_clean(self):
        assert _syntax_problem("js/game.js", GOOD_JS) == ""

    def test_truncated_html_detected(self):
        assert _syntax_problem("index.html", "<!DOCTYPE html><html><body><div>hi")

    def test_complete_html_clean(self):
        assert _syntax_problem(
            "index.html",
            "<!DOCTYPE html><html><body><div>hi</div></body></html>",
        ) == ""

    def test_fragment_html_not_flagged(self):
        """片段模板不以 </html> 结尾，不应误判"""
        assert _syntax_problem("tpl.html", '<div class="x">hi</div>') == ""

    def test_unbalanced_css_detected(self):
        assert _syntax_problem("css/style.css", ".a{ color:red;")

    def test_plain_text_untouched(self):
        assert _syntax_problem("README.md", "# 标题") == ""


class TestRollbackGuard:
    def test_bad_overwrite_rolled_back(self, handler):
        handler.execute({"filename": "js/game.js", "content": GOOD_JS})
        r = handler.execute({"filename": "js/game.js", "content": TRUNCATED_JS})
        assert not r.success, "残缺内容必须被拒绝"
        assert "回滚" in r.error
        # 关键断言：磁盘上仍是写入前的完整版本
        assert handler.workspace.read("js/game.js") == GOOD_JS
        assert r.metadata.get("rolled_back") is True

    def test_good_overwrite_allowed(self, handler):
        handler.execute({"filename": "js/game.js", "content": GOOD_JS})
        better = GOOD_JS.replace("return true;", "return 1;")
        r = handler.execute({"filename": "js/game.js", "content": better})
        assert r.success
        assert handler.workspace.read("js/game.js") == better

    def test_new_file_truncated_kept_with_warning(self, handler):
        """首次创建就写残：没有可回滚的上一版，保留但必须告警"""
        r = handler.execute({"filename": "js/game.js", "content": TRUNCATED_JS})
        assert r.success
        assert "完整性告警" in r.content
        assert r.metadata.get("truncated") is True

    def test_shrink_warning(self, handler):
        big = "\n".join(f"var v{i} = {i};" for i in range(200))
        handler.execute({"filename": "js/game.js", "content": big})
        r = handler.execute({"filename": "js/game.js", "content": "var a = 1;"})
        assert r.success
        assert "远少于写入前" in r.content


class TestCompletenessHints:
    def test_entry_html_without_script_flagged(self):
        hints = _completeness_hints(
            "index.html", "<!DOCTYPE html><html><body><div>hi</div></body></html>"
        )
        assert any("<script>" in h for h in hints)

    def test_entry_html_with_script_clean(self):
        html = (
            "<!DOCTYPE html><html><head><link rel=stylesheet href=css/style.css>"
            "</head><body><script src=js/main.js></script></body></html>"
        )
        assert _completeness_hints("index.html", html) == []

    def test_non_html_untouched(self):
        assert _completeness_hints("js/main.js", "var a = 1;") == []
