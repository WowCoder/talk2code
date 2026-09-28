# -*- coding: utf-8 -*-
"""
defect_repair 增量补丁（diff 模式）测试

背景（2026-09-28）：defect_repair 此前要求 LLM 返回整个文件的完整内容
（16K~32K tokens），按实测吞吐 60~90 tok/s 需要 180~360s，而超时被硬编码成
90s —— 每次必然超时（6 次调用 4 次失败，约占一次需求总耗时的 1/3）。

改为 SEARCH/REPLACE 增量补丁后输出降到 1~3K tokens（实测 10~14s）。
本文件守住这条路径的正确性，尤其是「同一文件多个补丁条目」的累积语义 ——
该场景曾导致后写覆盖前写、前面的补丁静默丢失但函数仍返回成功。
"""

import pytest

from harness.instructions.nodes import _apply_diff_edits, _defect_repair_timeout


class _FakeWS:
    """最小化 workspace 替身：内存存储，记录写盘调用"""

    def __init__(self, files=None):
        self._files = dict(files or {})
        self.written = []

    def exists(self, name):
        return name in self._files

    def read(self, name):
        if name not in self._files:
            raise FileNotFoundError(name)
        return self._files[name]

    def write(self, name, content):
        self._files[name] = content
        self.written.append(name)

    def list(self):
        return list(self._files)


def _block(search: str, replace: str) -> str:
    return f"<<<< SEARCH\n{search}\n====\n{replace}\n>>>>"


SRC = "function init(){\n  start();\n}\nfunction go(){\n  stop();\n}\n"


# ---------- 超时反推 ----------

class TestDefectRepairTimeout:
    """超时必须按实测吞吐反推，而不是沿用按标称值拍出来的 90s"""

    def test_scales_with_max_tokens(self):
        assert _defect_repair_timeout(8000) < _defect_repair_timeout(16000)
        assert _defect_repair_timeout(16000) < _defect_repair_timeout(32000)

    def test_16k_is_far_above_legacy_90s(self):
        # 16K tokens ÷ 60 tok/s × 1.3 + 20 ≈ 366s；旧的 90s 必然超时
        assert _defect_repair_timeout(16000) > 200

    def test_capped_by_absolute_ceiling(self):
        assert _defect_repair_timeout(10_000_000) <= 600

    def test_never_below_floor(self):
        assert _defect_repair_timeout(1) >= 30


# ---------- 增量补丁应用 ----------

class TestApplyDiffEdits:

    def test_happy_path_single_block(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(
            ws, [{"filename": "a.js", "edit": _block("  start();", "  start(true);")}]
        )
        assert err == ""
        assert len(applied) == 1
        assert "start(true)" in applied[0]["content"]

    def test_does_not_write_to_disk(self):
        """只产出内容，写盘交给统一的完整性/语法/长度比闸门"""
        ws = _FakeWS({"a.js": SRC})
        _apply_diff_edits(ws, [{"filename": "a.js", "edit": _block("  start();", "  x();")}])
        assert ws.written == []

    def test_multiple_blocks_in_one_edit(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(ws, [{
            "filename": "a.js",
            "edit": _block("  start();", "  start(1);") + "\n" + _block("  stop();", "  stop(2);"),
        }])
        assert err == ""
        content = applied[0]["content"]
        assert "start(1)" in content and "stop(2)" in content

    def test_same_file_multiple_entries_accumulate(self):
        """回归：同文件多个 edits 条目必须累积，而不是后写覆盖前写。

        修复前的实现对每个条目都从磁盘原始内容重新计算，写回时最后一条
        覆盖前面所有条目 —— 前面的补丁静默丢失，函数却返回成功。
        """
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "a.js", "edit": _block("  start();", "  start(true);")},
            {"filename": "a.js", "edit": _block("  stop();", "  stop(false);")},
        ])
        assert err == ""
        assert len(applied) == 1, "同文件应合并为一条，避免写回互相覆盖"
        content = applied[0]["content"]
        assert "start(true)" in content, "前一条补丁被丢弃了"
        assert "stop(false)" in content

    def test_same_file_three_entries_all_applied(self):
        ws = _FakeWS({"a.js": "A\nB\nC\n"})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "a.js", "edit": _block("A", "A1")},
            {"filename": "a.js", "edit": _block("B", "B1")},
            {"filename": "a.js", "edit": _block("C", "C1")},
        ])
        assert err == ""
        assert applied[0]["content"] == "A1\nB1\nC1\n"

    def test_multiple_files_each_one_entry(self):
        ws = _FakeWS({"a.js": SRC, "b.js": "let v = 1;\n"})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "a.js", "edit": _block("  start();", "  s();")},
            {"filename": "b.js", "edit": _block("let v = 1;", "let v = 2;")},
        ])
        assert err == ""
        assert [a["filename"] for a in applied] == ["a.js", "b.js"]

    def test_unmatched_search_rejects_whole_patch(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(
            ws, [{"filename": "a.js", "edit": _block("  no_such();", "  x();")}]
        )
        assert applied == []
        assert "未匹配" in err

    def test_later_failure_discards_earlier_success(self):
        """任一补丁失败即整体放弃，不留半改状态"""
        ws = _FakeWS({"a.js": "A\nB\n"})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "a.js", "edit": _block("A", "A1")},
            {"filename": "a.js", "edit": _block("ZZZ", "X")},
        ])
        assert applied == []
        assert "未匹配" in err
        assert ws.written == []

    def test_ambiguous_search_rejected(self):
        """SEARCH 命中多处时必须拒绝 —— 猜错一处就是静默改坏代码"""
        ws = _FakeWS({"a.js": "  x();\n  x();\n"})
        applied, err = _apply_diff_edits(
            ws, [{"filename": "a.js", "edit": _block("  x();", "  y();")}]
        )
        assert applied == []
        assert "唯一" in err or "匹配到" in err

    def test_missing_file_rejected(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(
            ws, [{"filename": "nope.js", "edit": _block("  start();", "  z();")}]
        )
        assert applied == []
        assert "不存在" in err

    def test_invalid_block_format_rejected(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(ws, [{"filename": "a.js", "edit": "没有块标记"}])
        assert applied == []
        assert err

    def test_empty_edits_rejected(self):
        applied, err = _apply_diff_edits(_FakeWS({"a.js": SRC}), [])
        assert applied == []
        assert err

    def test_items_missing_fields_skipped(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "", "edit": _block("a", "b")},
            {"edit": _block("a", "b")},
            {"filename": "a.js", "edit": ""},
        ])
        assert applied == []
        assert err

    def test_mixed_valid_and_invalid_items_apply_valid_only(self):
        ws = _FakeWS({"a.js": SRC})
        applied, err = _apply_diff_edits(ws, [
            {"filename": "a.js", "edit": _block("  start();", "  s();")},
            "not-a-dict",
            {"filename": "", "edit": "x"},
        ])
        assert err == ""
        assert len(applied) == 1


# ---------- 与真实模型返回形状的一致性 ----------

class TestRealModelOutputShape:
    """真实模型（agnes-3.0-flash）一次会返回同文件多条 edits，必须全部生效"""

    def test_real_two_edits_same_file(self):
        game = (
            "function placeStone(idx) {\n"
            "  if (current === 'black') {\n"
            "    cells[idx].classList.add('black');\n"
            "  } else {\n"
            "    cells[idx].classList.add('white');\n"
            "  }'\n"
            "  statusEl.textContent = 'x';\n"
            "}\n"
        )
        edits = [
            {"filename": "js/game.js", "edit": _block(
                "function placeStone(idx) {\n  if (current === 'black') {",
                "function placeStone(idx) {\n  if (cells[idx].classList.contains('black')) return;\n"
                "  if (current === 'black') {",
            )},
            {"filename": "js/game.js", "edit": _block(
                "  } else {\n    cells[idx].classList.add('white');\n  }'",
                "  } else {\n    cells[idx].classList.add('white');\n  }",
            )},
        ]
        applied, err = _apply_diff_edits(_FakeWS({"js/game.js": game}), edits)
        assert err == ""
        assert len(applied) == 1
        content = applied[0]["content"]
        assert "contains('black')" in content, "补丁 1 丢失"
        assert "'\n  statusEl" not in content, "补丁 2 丢失（多余单引号未删除）"
