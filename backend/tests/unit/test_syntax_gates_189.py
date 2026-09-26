# -*- coding: utf-8 -*-
"""req 189 根因回归：语法坏补丁必须在三道写入路径上被拦住。

189 事故链：
1. defect_repair 节点绕过 ToolCallLoop 直接 workspace.write —— 补丁把
   `UtilsExt.$$` 写成 `$$/`，node --check 判死，全站 JS 瘫痪；
2. 后续 4 轮修复全部 LLM 超时白烧（repair prompt 里只有裸 message
   "Unexpected token ')'"，没有文件/行号）；
3. edit_file 路径同样没有语法守卫。
"""
import json
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def _mkws_root() -> pathlib.Path:
    """pytest 的 tmp_path 撞沙箱权限（EEXIST broker），改用仓库内 tmp/"""
    base = pathlib.Path(__file__).resolve().parents[2] / "tmp"
    if not base.exists():  # 沙箱 shim 对已存在目录的 mkdir(exist_ok=True) 也抛 EEXIST
        base.mkdir()
    return pathlib.Path(tempfile.mkdtemp(prefix="t189_", dir=str(base)))

from harness.tools.file_tools import _syntax_problem
from harness.tools.edit_tools import EditFileHandler
from harness.tools.preview_runner import _parse_error_location


GOOD_APP = (
    "(function (global) {\n"
    "  'use strict';\n"
    "  var Utils = global.Utils;\n"
    "  var UtilsExt = Utils;\n"
    "  if (!UtilsExt.$$) {\n"
    "    UtilsExt.$$ = function (selector) {\n"
    "      return Array.prototype.slice.call(document.querySelectorAll(selector));\n"
    "    };\n"
    "  }\n"
    "  global.App = {};\n"
    "})(window);\n"
)

# 189 真实事故形态：$$ → $$/（括号配平、长度正常，_content_looks_complete 拦不住）
BAD_APP = GOOD_APP.replace("UtilsExt.$$) {", "UtilsExt.$$/) {")


class _TmpWS:
    """WorkspaceFS 的最小替身（tmp 目录）"""

    def __init__(self, root: pathlib.Path):
        self.root = root

    def exists(self, name):
        return (self.root / name).exists()

    def list(self):
        out = []
        for p in self.root.rglob("*"):
            if p.is_file():
                out.append(str(p.relative_to(self.root)))
        return out

    def read(self, name):
        return (self.root / name).read_text()

    def write(self, name, content):
        f = self.root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


# ---------- _syntax_problem：189 真实数据形态 ----------

def test_syntax_problem_catches_189_typo():
    assert _syntax_problem("js/app.js", GOOD_APP) == ""
    assert "Unexpected token" in _syntax_problem("js/app.js", BAD_APP)


# ---------- edit_file 语法守卫 ----------

def test_edit_file_rejects_syntax_breaking_edit():
    ws = _TmpWS(_mkws_root())
    ws.write("js/app.js", GOOD_APP)
    h = EditFileHandler(ws)
    r = h.edit_file("js/app.js", "<<<< SEARCH\n  if (!UtilsExt.$$) {\n====\n  if (!UtilsExt.$$/) {\n>>>>")
    assert r.error, "语法变坏的编辑必须被拒绝"
    assert ws.read("js/app.js") == GOOD_APP, "拒绝后原文件必须原样保留"
    assert "语法错误" in r.error


def test_edit_file_allows_syntax_fixing_edit():
    """旧文件本身就语法坏时不得拦截（模型可能正在修它）"""
    ws = _TmpWS(_mkws_root())
    ws.write("js/app.js", BAD_APP)
    h = EditFileHandler(ws)
    r = h.edit_file("js/app.js", "<<<< SEARCH\n  if (!UtilsExt.$$/) {\n====\n  if (!UtilsExt.$$) {\n>>>>")
    assert not r.error
    assert ws.read("js/app.js") == GOOD_APP


def test_edit_file_allows_normal_edit():
    ws = _TmpWS(_mkws_root())
    ws.write("js/app.js", GOOD_APP)
    h = EditFileHandler(ws)
    r = h.edit_file("js/app.js", "<<<< SEARCH\n  global.App = {};\n====\n  global.App = { v: 1 };\n>>>>")
    assert not r.error
    assert "global.App = { v: 1 };" in ws.read("js/app.js")


# ---------- pageerror 位置解析 ----------

def test_parse_error_location_http_stack():
    stack = "SyntaxError: Unexpected token ')'\n    at http://127.0.0.1:8765/js/app.js:216:20"
    assert _parse_error_location(stack) == "app.js:216"


def test_parse_error_location_file_stack():
    stack = "SyntaxError: Unexpected token ')'\n    at file:///ws/1/189/js/game.js:33:7"
    assert _parse_error_location(stack) == "game.js:33"


def test_parse_error_location_no_stack():
    assert _parse_error_location("") == ""
    assert _parse_error_location("SyntaxError: boom") == ""


# ---------- defect_repair 写回语法闸门（节点级行为复现） ----------

def test_defect_repair_rejects_syntax_broken_patch():
    """用 189 真实补丁数据复现：语法坏补丁被拒、好版本保留。

    直接调用 defect_repair_node，mock 掉 LLM（返回坏补丁 JSON），
    断言磁盘文件未被污染。手动 patch（monkeypatch fixture 会连带初始化
    pytest basetemp，在沙箱里抛 EEXIST）。
    """
    from harness.instructions import nodes as nodes_mod

    ws = _TmpWS(_mkws_root())
    ws.write("js/app.js", GOOD_APP)

    class _FakeClient:
        def chat(self, **kwargs):
            r = type("R", (), {})()
            r.is_error = False
            r.content = json.dumps({
                "files": [{"filename": "js/app.js", "content": BAD_APP}],
                "summary": "修复",
            })
            r.finish_reason = "stop"
            return r

    saved = (nodes_mod.get_client, nodes_mod.get_workspace, nodes_mod.get_tool_loop)
    nodes_mod.get_client = lambda: _FakeClient()
    nodes_mod.get_workspace = lambda state: ws
    nodes_mod.get_tool_loop = lambda state: None
    try:
        state = {
            "requirement_id": 0,
            "smoke_defects": [{"type": "runtime_error", "message": "boom",
                               "evidence": "", "suggestion": ""}],
            "metadata": {},
        }
        result = nodes_mod.defect_repair_node(state)
    finally:
        (nodes_mod.get_client, nodes_mod.get_workspace, nodes_mod.get_tool_loop) = saved
    # 节点可能返回失败/跳过等多种结局，唯一不可协商的是：坏补丁不得落盘
    assert ws.read("js/app.js") == GOOD_APP, (
        "语法坏补丁写回了磁盘 —— defect_repair 语法闸门未生效"
    )
    skipped = result.get("skipped") or []
    assert any("语法错误" in s for s in skipped) or result.get("error"), skipped
