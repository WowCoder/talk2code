# -*- coding: utf-8 -*-
"""
edit_file 增量编辑工具测试

覆盖 Aider 风格 search-replace 协议的核心行为：
- 精确匹配替换（单块/多块）
- 匹配失败时的上下文回流
- 多义匹配（同一片段出现多次）报错
- 空文件 / 文件不存在回退
- 空替换 = 删除
- 格式非法报错
"""

import pytest

from harness.tools.edit_tools import EditToolHandler, parse_edit_blocks


class _FakeWS:
    """最小化 workspace 替身，内存存储"""
    def __init__(self, files=None):
        self._files = files or {}

    def exists(self, name):
        return name in self._files

    def read(self, name):
        if name not in self._files:
            raise FileNotFoundError(name)
        return self._files[name]

    def write(self, name, content):
        self._files[name] = content


# ---------- parse_edit_blocks ----------

class TestParseEditBlocks:

    def test_single_block(self):
        edit = "<<<< SEARCH\nold\n====\nnew\n>>>>"
        assert parse_edit_blocks(edit) == [("old", "new")]

    def test_multiple_blocks(self):
        # 多块之间用换行分隔（与真实 LLM 输出一致）
        edit = "<<<< SEARCH\na\n====\nA\n>>>>\n<<<< SEARCH\nb\n====\nB\n>>>>"
        assert parse_edit_blocks(edit) == [("a", "A"), ("b", "B")]

    def test_multiline_search(self):
        edit = "<<<< SEARCH\ndef foo():\n    return 1\n====\ndef foo():\n    return 2\n>>>>"
        blocks = parse_edit_blocks(edit)
        assert len(blocks) == 1
        assert "return 1" in blocks[0][0]

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_edit_blocks("just some text without blocks")

    def test_empty_replace(self):
        """空替换 = 删除片段"""
        edit = "<<<< SEARCH\nremove me\n====\n>>>>"
        # 注意：空 replace 块
        edit_with_empty = "<<<< SEARCH\nremove me\n====\n\n>>>>"
        blocks = parse_edit_blocks(edit_with_empty)
        # replace 为空串（块内 ==== 之后为空）
        assert blocks[0][0] == "remove me"


# ---------- EditToolHandler.edit_file ----------

class TestEditFileHandler:

    def test_exact_single_replace(self):
        ws = _FakeWS({"app.js": "function greet() {\n  console.log('hi');\n}"})
        handler = EditToolHandler(ws)
        edit = "<<<< SEARCH\nconsole.log('hi');\n====\nconsole.log('hello');\n>>>>"
        result = handler.edit_file("app.js", edit)
        assert result.success
        assert "hello" in ws.read("app.js")
        assert "hi');" not in ws.read("app.js")

    def test_multiple_blocks_one_file(self):
        ws = _FakeWS({"app.js": "const a = 1;\nconst b = 2;\nconst c = 3;"})
        handler = EditToolHandler(ws)
        edit = (
            "<<<< SEARCH\nconst a = 1;\n====\nconst a = 10;\n>>>>"
            "<<<< SEARCH\nconst c = 3;\n====\nconst c = 30;\n>>>>"
        )
        result = handler.edit_file("app.js", edit)
        assert result.success
        content = ws.read("app.js")
        assert "const a = 10" in content
        assert "const c = 30" in content
        assert "const b = 2" in content  # 未动

    def test_no_match_returns_context(self):
        ws = _FakeWS({"app.js": "function greet() {\n  return 'hi';\n}"})
        handler = EditToolHandler(ws)
        edit = "<<<< SEARCH\nthis does not exist\n====\nreplacement\n>>>>"
        result = handler.edit_file("app.js", edit)
        assert not result.success
        assert "未找到" in result.error or "not found" in result.error.lower() or "精确匹配" in result.error
        # 文件未被修改
        assert ws.read("app.js") == "function greet() {\n  return 'hi';\n}"

    def test_ambiguous_match_returns_error(self):
        """SEARCH 片段在文件中出现多次 → 报错，要求增加上下文"""
        ws = _FakeWS({"app.js": "const x = 1;\nconst y = 1;\nconst z = 2;"})
        handler = EditToolHandler(ws)
        edit = "<<<< SEARCH\nconst x = 1;\n====\nconst x = 5;\n>>>>"
        # x = 1 出现 1 次... 但 "= 1;" 出现 2 次。构造真正歧义的片段：
        ws2 = _FakeWS({"app.js": "dup\ndup\nother"})
        handler2 = EditToolHandler(ws2)
        edit2 = "<<<< SEARCH\ndup\n====\nunique\n>>>>"
        result = handler2.edit_file("app.js", edit2)
        assert not result.success
        assert "匹配到 2 处" in result.error or "唯一" in result.error

    def test_file_not_exist(self):
        ws = _FakeWS({})
        handler = EditToolHandler(ws)
        edit = "<<<< SEARCH\nold\n====\nnew\n>>>>"
        result = handler.edit_file("missing.js", edit)
        assert not result.success
        assert "不存在" in result.error or "write_file" in result.error

    def test_empty_replace_deletes(self):
        """REPLACE 为空 → 删除该片段"""
        ws = _FakeWS({"app.js": "keep this\nremove this line\nkeep that"})
        handler = EditToolHandler(ws)
        # 空 replace
        edit = "<<<< SEARCH\nremove this line\n====\n\n>>>>"
        result = handler.edit_file("app.js", edit)
        assert result.success
        content = ws.read("app.js")
        assert "remove this line" not in content
        assert "keep this" in content

    def test_indented_block_matches(self):
        """缩进敏感：SEARCH 块必须逐字符含缩进"""
        ws = _FakeWS({"app.js": "function f() {\n    if (x) {\n        doStuff();\n    }\n}"})
        handler = EditToolHandler(ws)
        edit = "<<<< SEARCH\n    if (x) {\n        doStuff();\n    }\n====\n    if (x) {\n        doStuff();\n        doMore();\n    }\n>>>>"
        result = handler.edit_file("app.js", edit)
        assert result.success
        assert "doMore()" in ws.read("app.js")

    def test_all_or_nothing(self):
        """多块中任一失败 → 整个文件不修改"""
        ws = _FakeWS({"app.js": "exists\nother"})
        handler = EditToolHandler(ws)
        edit = (
            "<<<< SEARCH\nexists\n====\nchanged\n>>>>"
            "<<<< SEARCH\nnot_in_file\n====\nx\n>>>>"
        )
        original = ws.read("app.js")
        result = handler.edit_file("app.js", edit)
        assert not result.success
        # 第 2 块失败，第 1 块的修改不应生效
        assert ws.read("app.js") == original

    def test_invalid_format_returns_error(self):
        ws = _FakeWS({"app.js": "content"})
        handler = EditToolHandler(ws)
        result = handler.edit_file("app.js", "no blocks here")
        assert not result.success
        assert "SEARCH/REPLACE" in result.error or "格式" in result.error


# ---------- 注册 ----------

class TestEditFileRegistration:

    def test_edit_file_in_registry(self):
        from harness.tools.registry import create_tool_registry
        registry = create_tool_registry()
        names = [t["function"]["name"] for t in registry.get_schemas()]
        assert "edit_file" in names

    def test_edit_file_requires_filename_and_edit(self):
        from harness.tools.registry import create_tool_registry
        registry = create_tool_registry()
        schema = next(t["function"] for t in registry.get_schemas()
                      if t["function"]["name"] == "edit_file")
        assert set(schema["parameters"]["required"]) == {"filename", "edit"}
