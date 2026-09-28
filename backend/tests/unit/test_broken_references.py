# -*- coding: utf-8 -*-
"""交付前「引用闭合」硬门禁的单元测试。

背景（t16 实测）：模型写出 index.html 引用 js/sort.js，却只创建了 js/storage.js。
每个文件单独看语法都合法，语法门禁全过，但预览直接 ERR_FILE_NOT_FOUND 白屏。
这类缺陷是**机器可判定**的（解析引用 + 判断文件在不在），因此并入交付前硬门禁，
与语法检查共用同一套"阻断 / 3 轮后放行"逻辑。

本文件锁住三类行为：
1. 真断链必须被检出
2. 外部/内联/目录/工作区外引用不得误报（误报会打断正常交付）
3. 门禁自身异常不得抛出（绝不能因 lint 故障阻断交付）
"""

import sys
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.runtime import ToolCallLoop          # noqa: E402
from harness.state.workspace import WorkspaceFS   # noqa: E402


class _Gate:
    """只取门禁相关方法的最小宿主，避免构造完整 ToolCallLoop。"""

    _is_deliverable = staticmethod(ToolCallLoop._is_deliverable)
    _REF_SKIP_PREFIX = ToolCallLoop._REF_SKIP_PREFIX
    _check_broken_references = ToolCallLoop._check_broken_references
    _check_deliverable_syntax = ToolCallLoop._check_deliverable_syntax

    def __init__(self, workspace):
        self.workspace = workspace


@pytest.fixture()
def ws():
    tmp = Path(tempfile.mkdtemp(prefix="t2c_broken_ref_"))
    workspace = WorkspaceFS(user_id=9901, requirement_id=1, base_dir=tmp)
    workspace.init([])
    try:
        yield workspace
    finally:
        # 沙箱环境下 standard 的 rmtree 会被安全删除补丁接管；忽略失败即可
        shutil.rmtree(tmp, ignore_errors=True)


# ==================== 1. 真断链必须检出 ====================

def test_missing_js_reference_detected(ws):
    """t16 的真实形态：引用了未创建的 js/sort.js。"""
    ws.write("index.html", (
        '<!doctype html><html><head>'
        '<link rel="stylesheet" href="css/style.css">'
        '</head><body><button>开始</button>'
        '<script src="js/sort.js"></script>'
        '<script src="js/storage.js"></script>'
        '</body></html>'
    ))
    ws.write("css/style.css", "body { margin: 0; }\n")
    ws.write("js/storage.js", "const k = 1;\n")

    problems = _Gate(ws)._check_broken_references()
    assert len(problems) == 1
    assert "index.html" in problems[0]
    assert "js/sort.js" in problems[0]


def test_missing_css_link_detected(ws):
    ws.write("index.html", '<html><head><link href="css/missing.css" rel="stylesheet"></head></html>')
    problems = _Gate(ws)._check_broken_references()
    assert any("css/missing.css" in p for p in problems)


def test_all_references_present_passes(ws):
    ws.write("index.html", (
        '<html><head><link href="css/style.css" rel="stylesheet"></head>'
        '<body><script src="js/app.js"></script></body></html>'
    ))
    ws.write("css/style.css", "body{margin:0}\n")
    ws.write("js/app.js", "console.log(1);\n")
    assert _Gate(ws)._check_broken_references() == []


# ==================== 2. 不得误报 ====================

def test_external_and_inline_refs_ignored(ws):
    """CDN、协议相对、data URI、锚点、mailto 都不是工作区文件。"""
    ws.write("index.html", (
        '<html><head>'
        '<link href="https://cdn.example.com/a.css" rel="stylesheet">'
        '<link href="//cdn.example.com/b.css" rel="stylesheet">'
        '<link href="data:text/css,body%7B%7D" rel="stylesheet">'
        '<style>@import url("https://x.example.com/c.css");</style>'
        '</head><body>'
        '<a href="#top">top</a>'
        '<a href="mailto:a@b.com">mail</a>'
        '<a href="tel:10086">tel</a>'
        '<img src="data:image/png;base64,AAAA">'
        '</body></html>'
    ))
    assert _Gate(ws)._check_broken_references() == []


def test_directory_reference_ignored(ws):
    """`<a href="css/">` 指向目录，不是断链。"""
    ws.write("index.html", '<html><body><a href="css/">CSS 目录</a></body></html>')
    ws.write("css/style.css", "body{margin:0}\n")
    assert _Gate(ws)._check_broken_references() == []


def test_parent_dir_reference_ignored(ws):
    """指向工作区外的 `../x.html` 不属交付范围，不判定为断链。"""
    ws.write("index.html", '<html><body><a href="../outside.html">外</a></body></html>')
    assert _Gate(ws)._check_broken_references() == []


def test_root_absolute_reference_resolves_to_workspace_root(ws):
    """`/css/style.css` 应按工作区根解析。"""
    ws.write("index.html", '<html><head><link href="/css/style.css" rel="stylesheet"></head></html>')
    ws.write("css/style.css", "body{margin:0}\n")
    assert _Gate(ws)._check_broken_references() == []


def test_css_url_and_import_checked(ws):
    ws.write("css/theme.css", '@import url("base.css");\n.x{background:url("../img/logo.png?v=2");}\n')
    problems = _Gate(ws)._check_broken_references()
    joined = " ".join(problems)
    assert "base.css" in joined
    assert "img/logo.png" in joined
    # 查询串必须被剥离后再比较，否则永远匹配不上
    assert "?v=2" not in joined

    ws.write("css/base.css", "html{color:red}\n")
    ws.write("img/logo.png", "x")
    assert _Gate(ws)._check_broken_references() == []


# ==================== 3. 边界与鲁棒性 ====================

def test_metadata_and_template_dirs_excluded(ws):
    """模板与元数据目录不参与引用闭合检查。"""
    ws.write("index.html", '<html><body>ok</body></html>')
    ws.write(".design/preset-game.css", 'body{background:url("nope.png")}\n')
    ws.write(".task/TASK_STATE.md", "见 index.html")
    assert _Gate(ws)._check_broken_references() == []


def test_many_broken_refs_capped(ws):
    """一次最多报 5 条，避免把上下文灌满。"""
    refs = "".join(f'<script src="js/m{i}.js"></script>' for i in range(20))
    ws.write("index.html", f"<html><body>{refs}</body></html>")
    problems = _Gate(ws)._check_broken_references()
    assert len(problems) == 5


def test_duplicate_ref_reported_once(ws):
    ws.write("index.html", '<html><body>'
                           '<script src="js/a.js"></script>'
                           '<script src="js/a.js"></script>'
                           '</body></html>')
    assert len(_Gate(ws)._check_broken_references()) == 1


def test_empty_workspace_returns_no_problem(ws):
    assert _Gate(ws)._check_broken_references() == []


def test_broken_workspace_does_not_raise(ws):
    """门禁自身故障绝不能阻断交付。"""

    class Boom:
        def list(self):
            raise RuntimeError("磁盘炸了")

    gate = _Gate(ws)
    gate.workspace = Boom()
    assert gate._check_broken_references() == []


def test_unreadable_file_does_not_raise(ws):
    class HalfBoom:
        def list(self):
            return ["index.html"]

        def read(self, name):
            raise OSError("读不了")

    gate = _Gate(ws)
    gate.workspace = HalfBoom()
    assert gate._check_broken_references() == []


def test_non_string_content_does_not_raise(ws):
    """回归：workspace.read 返回 Mock（单测常见夹具）时曾抛
    TypeError: expected string or bytes-like object, got 'Mock'。

    注意不能写成 `self.workspace.read(name) or ""` —— Mock 是 truthy，
    会一路带进 re.findall。必须显式判类型。
    """

    class Mocky:
        def list(self):
            return ["index.html", "js/app.js"]

        def read(self, name):
            from unittest.mock import Mock
            return Mock(name=name)

    gate = _Gate(ws)
    gate.workspace = Mocky()
    assert gate._check_broken_references() == []


def test_none_content_does_not_raise(ws):
    class Noney:
        def list(self):
            return ["index.html"]

        def read(self, name):
            return None

    gate = _Gate(ws)
    gate.workspace = Noney()
    assert gate._check_broken_references() == []


def test_gate_never_breaks_syntax_result(ws):
    """引用检查崩了，也不能让语法检查的结果一起丢掉。"""

    class SyntaxOkButRefBoom:
        def list(self):
            return ["js/bad.js"]

        def read(self, name):
            # 抛非预期异常，模拟引用检查内部故障
            raise ValueError("boom")

    gate = _Gate(ws)
    gate.workspace = SyntaxOkButRefBoom()
    # 不抛异常即可（语法检查对同一次故障也会跳过）
    assert isinstance(gate._check_deliverable_syntax(), list)


# ==================== 4. 与语法门禁合并 ====================

def test_merged_gate_reports_both_kinds(ws):
    ws.write("index.html", '<html><body><script src="js/missing.js"></script></body></html>')
    ws.write("js/bad.js", "function a( {")          # 语法错误（未被引用）

    problems = _Gate(ws)._check_deliverable_syntax()
    assert any("引用了不存在" in p for p in problems), problems
    assert any("语法错误" in p for p in problems), problems


def test_merged_gate_clean_project_is_empty(ws):
    ws.write("index.html", '<html><body><script src="js/app.js"></script></body></html>')
    ws.write("js/app.js", "console.log('ok');\n")
    assert _Gate(ws)._check_deliverable_syntax() == []
