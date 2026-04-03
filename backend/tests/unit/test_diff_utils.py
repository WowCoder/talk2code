# -*- coding: utf-8 -*-
"""
diff_utils 模块单元测试
"""

import pytest
from diff_utils import (
    parse_diff,
    apply_diff,
    validate_diff,
    DiffFile,
    DiffHunk,
    generate_diff
)


class TestValidateDiff:
    """Diff 格式验证测试"""

    def test_valid_diff_with_markdown(self, sample_diff_text):
        """测试带 markdown 包装的有效 diff"""
        is_valid, error = validate_diff(sample_diff_text)
        assert is_valid is True
        assert error == ""

    def test_valid_diff_without_markdown(self):
        """测试不带 markdown 的有效 diff"""
        diff_text = """--- a/app.js
+++ b/app.js
@@ -1,3 +1,4 @@
 function main() {
-    console.log("Hello");
+    console.log("Hello World");
+    return true;
 }"""
        is_valid, error = validate_diff(diff_text)
        assert is_valid is True
        assert error == ""

    def test_empty_diff(self):
        """测试空 diff"""
        is_valid, error = validate_diff("")
        assert is_valid is False
        assert "空" in error

    def test_missing_file_header(self):
        """测试缺少文件头"""
        diff_text = """@@ -1,3 +1,4 @@
 line1
-old
+new"""
        is_valid, error = validate_diff(diff_text)
        assert is_valid is False
        assert "文件头" in error

    def test_missing_hunk_header(self):
        """测试缺少 hunk 头"""
        diff_text = """--- a/file.txt
+++ b/file.txt
 line1
-old
+new"""
        is_valid, error = validate_diff(diff_text)
        assert is_valid is False
        assert "hunk" in error.lower()

    def test_invalid_hunk_format(self):
        """测试无效的 hunk 格式"""
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ invalid @@
 line1"""
        is_valid, error = validate_diff(diff_text)
        assert is_valid is False
        assert "Hunk" in error


class TestParseDiff:
    """Diff 解析测试"""

    def test_parse_single_file_diff(self, sample_diff_text):
        """测试解析单文件 diff"""
        diff_files = parse_diff(sample_diff_text)
        assert len(diff_files) == 1
        assert diff_files[0].filename == 'index.html'
        assert len(diff_files[0].hunks) == 1

    def test_parse_multi_file_diff(self):
        """测试解析多文件 diff"""
        diff_text = """--- a/file1.txt
+++ b/file1.txt
@@ -1,2 +1,2 @@
 line1
-old1
+new1
--- a/file2.txt
+++ b/file2.txt
@@ -1,2 +1,2 @@
 line2
-old2
+new2"""
        diff_files = parse_diff(diff_text)
        assert len(diff_files) == 2
        assert diff_files[0].filename == 'file1.txt'
        assert diff_files[1].filename == 'file2.txt'

    def test_parse_hunk_info(self, sample_diff_text):
        """测试解析 hunk 信息"""
        diff_files = parse_diff(sample_diff_text)
        hunk = diff_files[0].hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 5
        assert hunk.new_start == 1
        assert hunk.new_count == 6

    def test_parse_empty_diff(self):
        """测试解析空 diff"""
        diff_files = parse_diff("")
        assert len(diff_files) == 0

    def test_parse_no_changes_diff(self):
        """测试解析无变更的 diff"""
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
 line2
 line3"""
        diff_files = parse_diff(diff_text)
        assert len(diff_files) == 1
        # 只有上下文行，没有实际变更


class TestApplyDiff:
    """Diff 应用测试"""

    def test_apply_simple_diff(self):
        """测试应用简单 diff"""
        original = """<html>
<head>
    <title>Old Title</title>
</head>
<body>
    <h1>Hello</h1>
</body>
</html>"""

        diff_text = """--- a/index.html
+++ b/index.html
@@ -1,5 +1,6 @@
 <html>
 <head>
-    <title>Old Title</title>
+    <title>New Title</title>
+    <meta charset="utf-8">
 </head>
 <body>"""

        diff_files = parse_diff(diff_text)
        new_content, success, error = apply_diff(original, diff_files[0])

        assert success is True
        assert error == ""
        assert "New Title" in new_content
        assert "meta charset" in new_content
        assert "Old Title" not in new_content

    def test_apply_add_lines(self):
        """测试应用添加行 diff"""
        original = "line1\nline2\nline3"
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,4 @@
 line1
 line2
+new_line
 line3"""

        diff_files = parse_diff(diff_text)
        new_content, success, error = apply_diff(original, diff_files[0])

        assert success is True
        assert "new_line" in new_content
        lines = new_content.split('\n')
        assert len(lines) == 4

    def test_apply_delete_lines(self):
        """测试应用删除行 diff"""
        original = "line1\nline2\nline3\nline4"
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ -1,4 +1,3 @@
 line1
-line2
 line3
 line4"""

        diff_files = parse_diff(diff_text)
        new_content, success, error = apply_diff(original, diff_files[0])

        assert success is True
        assert "line2" not in new_content
        lines = new_content.split('\n')
        assert len(lines) == 3

    def test_apply_no_changes(self):
        """测试应用无变更 diff"""
        original = "line1\nline2\nline3"
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
 line2
 line3"""

        diff_files = parse_diff(diff_text)
        new_content, success, error = apply_diff(original, diff_files[0])

        assert success is False
        assert "没有实际的代码变更" in error
        assert new_content == original

    def test_apply_context_mismatch(self):
        """测试上下文不匹配"""
        original = "different1\ndifferent2\ndifferent3"
        diff_text = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 line1
-old
+new"""

        diff_files = parse_diff(diff_text)
        new_content, success, error = apply_diff(original, diff_files[0])

        assert success is False
        assert "无法定位" in error or "不匹配" in error
        assert new_content == original


class TestGenerateDiff:
    """Diff 生成测试"""

    def test_generate_simple_diff(self):
        """测试生成简单 diff"""
        old_content = "line1\nline2\nline3"
        new_content = "line1\nnew_line2\nline3"

        diff = generate_diff(old_content, new_content, "test.txt")
        assert "--- a/test.txt" in diff
        assert "+++ b/test.txt" in diff
        assert "-line2" in diff
        assert "+new_line2" in diff


class TestDiffHunk:
    """DiffHunk 类测试"""

    def test_hunk_repr(self):
        """测试 hunk repr"""
        hunk = DiffHunk(1, 5, 1, 6)
        assert "old=1-6" in repr(hunk)
        assert "new=1-7" in repr(hunk)


class TestDiffFile:
    """DiffFile 类测试"""

    def test_file_repr(self):
        """测试 file repr"""
        diff_file = DiffFile("test.txt")
        diff_file.hunks.append(DiffHunk(1, 3, 1, 4))
        assert "test.txt" in repr(diff_file)
        assert "1 hunks" in repr(diff_file)