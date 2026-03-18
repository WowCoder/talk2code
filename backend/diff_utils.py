# -*- coding: utf-8 -*-
"""
Diff 工具函数
实现 unified diff 的解析和应用
"""

import re
from typing import List, Dict, Optional


class DiffHunk:
    """表示一个 diff hunk（代码块）"""
    def __init__(self, old_start: int, old_count: int, new_start: int, new_count: int):
        self.old_start = old_start      # 原文件起始行号
        self.old_count = old_count      # 原文件行数
        self.new_start = new_start      # 新文件起始行号
        self.new_count = new_count      # 新文件行数
        self.lines = []                 # hunk 内的所有行

    def __repr__(self):
        return f"DiffHunk(old={self.old_start}-{self.old_start+self.old_count}, new={self.new_start}-{self.new_start+self.new_count})"


class DiffFile:
    """表示一个文件的 diff"""
    def __init__(self, filename: str):
        self.filename = filename
        self.hunks: List[DiffHunk] = []

    def __repr__(self):
        return f"DiffFile({self.filename}, {len(self.hunks)} hunks)"


def parse_diff(diff_text: str) -> List[DiffFile]:
    """
    解析 unified diff 文本

    支持解析格式：
    ```diff
    --- a/index.html
    +++ b/index.html
    @@ -1,5 +1,6 @@
     line1
    -old line
    +new line
    +added line
     line4
     line5
    ```
    """
    diff_files = []
    lines = diff_text.strip().split('\n')

    i = 0
    while i < len(lines):
        line = lines[i]

        # 跳过 markdown 代码块标记
        if line.startswith('```diff') or line.startswith('```'):
            i += 1
            continue
        if line == '```':
            i += 1
            continue

        # 查找文件头 --- a/filename
        if line.startswith('--- '):
            # 提取文件名
            old_file = line[4:].strip()
            if old_file.startswith('a/'):
                old_file = old_file[2:]

            # 读取 +++ b/filename
            i += 1
            if i < len(lines) and lines[i].startswith('+++ '):
                new_file = lines[i][4:].strip()
                if new_file.startswith('b/'):
                    new_file = new_file[2:]

                filename = new_file or old_file
                diff_file = DiffFile(filename)

                # 读取 hunks
                i += 1
                while i < len(lines):
                    hunk_line = lines[i]

                    # 遇到新的文件头或结束
                    if hunk_line.startswith('--- ') or hunk_line.startswith('```'):
                        break

                    # 解析 hunk 头 @@ -old_start,old_count +new_start,new_count @@
                    if hunk_line.startswith('@@'):
                        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', hunk_line)
                        if match:
                            old_start = int(match.group(1))
                            old_count = int(match.group(2)) if match.group(2) else 1
                            new_start = int(match.group(3))
                            new_count = int(match.group(4)) if match.group(4) else 1

                            hunk = DiffHunk(old_start, old_count, new_start, new_count)

                            # 读取 hunk 内容
                            i += 1
                            while i < len(lines):
                                content_line = lines[i]
                                # 遇到新的 hunk 或文件头或结束
                                if (content_line.startswith('@@') or
                                    content_line.startswith('--- ') or
                                    content_line.startswith('```') or
                                    content_line.strip() == ''):
                                    break
                                hunk.lines.append(content_line)
                                i += 1

                            diff_file.hunks.append(hunk)
                        else:
                            i += 1
                    else:
                        i += 1

                diff_files.append(diff_file)
            else:
                i += 1
        else:
            i += 1

    return diff_files


def apply_diff(original_content: str, diff_file: DiffFile) -> str:
    """
    将 diff 应用到原始内容，返回新内容

    算法：
    1. 将原始内容按行分割
    2. 按顺序应用每个 hunk（从后往前应用，避免行号偏移）
    3. 每个 hunk 通过上下文匹配位置
    """
    original_lines = original_content.split('\n')

    # 收集所有需要删除/添加的行
    all_changes = []

    for hunk in diff_file.hunks:
        # 解析 hunk 内容
        delete_lines = []  # (相对行号，内容)
        add_lines = []     # (相对行号，内容)
        context_lines = [] # (相对行号，内容)

        current_pos = 0  # 在 hunk 内的位置
        old_line_num = 0  # 在原文件中的行号（从 0 开始）
        new_line_num = 0  # 在新文件中的行号

        i = 0
        while i < len(hunk.lines):
            line = hunk.lines[i]
            if line.startswith('-'):
                delete_lines.append((hunk.old_start - 1 + old_line_num, line[1:]))
                old_line_num += 1
            elif line.startswith('+'):
                add_lines.append((hunk.new_start - 1 + new_line_num, line[1:]))
                new_line_num += 1
            elif line.startswith(' ') or line == '':
                # 上下文行或空行
                context_lines.append((hunk.old_start - 1 + old_line_num, line[1:] if line.startswith(' ') else line))
                old_line_num += 1
                new_line_num += 1
            elif line.startswith('\\'):
                # "\ No newline at end of file" - 忽略
                pass
            i += 1

        all_changes.append({
            'hunk': hunk,
            'delete': delete_lines,
            'add': add_lines,
            'context': context_lines
        })

    # 从后往前应用变更，避免行号偏移
    for change in reversed(all_changes):
        hunk = change['hunk']

        # 找到 hunk 在原始内容中的实际位置
        # 通过匹配上下文来确定精确位置
        actual_start = find_hunk_position(original_lines, change)

        if actual_start is None:
            # 无法匹配，尝试使用 hunk 头部的行号
            actual_start = hunk.old_start - 1

        # 应用删除（从后往前删除，避免索引偏移）
        delete_info = change['delete']
        for i in range(len(delete_info) - 1, -1, -1):
            rel_pos, content = delete_info[i]
            actual_pos = actual_start + (rel_pos - (hunk.old_start - 1))
            if 0 <= actual_pos < len(original_lines):
                # 验证这一行确实匹配
                if original_lines[actual_pos].strip() == content.strip():
                    original_lines.pop(actual_pos)

        # 应用添加（从前往后添加）
        add_info = change['add']
        for rel_pos, content in add_info:
            actual_pos = actual_start + (rel_pos - (hunk.new_start - 1))
            # 考虑已经删除的行数偏移
            actual_pos = min(actual_pos, len(original_lines))
            original_lines.insert(actual_pos, content)

    return '\n'.join(original_lines)


def find_hunk_position(original_lines: List[str], change: Dict) -> Optional[int]:
    """
    通过匹配上下文找到 hunk 在原始内容中的位置

    返回 hunk 第一行在原始内容中的索引（从 0 开始），如果找不到返回 None
    """
    context = change['context']
    delete_lines = change['delete']
    hunk = change['hunk']

    if not context and not delete_lines:
        # 没有上下文也没有删除，使用 hunk 头部的行号
        return hunk.old_start - 1

    # 使用所有非删除行作为匹配模式
    # 优先考虑删除行，因为它们更能精确定位
    match_lines = delete_lines if delete_lines else context

    if not match_lines:
        return hunk.old_start - 1

    # 在原始内容中滑动窗口匹配
    for start_pos in range(len(original_lines)):
        if matches_context(original_lines, start_pos, match_lines):
            # 返回第一个匹配行的位置
            return match_lines[0][0] - (hunk.old_start - 1) + start_pos

    return None


def matches_context(original_lines: List[str], start_pos: int, match_lines: List[tuple]) -> bool:
    """检查从 start_pos 开始是否匹配所有上下文/删除行"""
    if not match_lines:
        return True

    # 计算相对位置
    first_line_num = match_lines[0][0]

    for rel_num, content in match_lines:
        actual_pos = start_pos + (rel_num - first_line_num)
        if actual_pos < 0 or actual_pos >= len(original_lines):
            return False
        # 宽松匹配：忽略前后空白
        if original_lines[actual_pos].strip() != content.strip():
            return False

    return True


def generate_diff(old_content: str, new_content: str, filename: str) -> str:
    """
    生成 unified diff（用于调试和日志）
    """
    from difflib import unified_diff

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = unified_diff(old_lines, new_lines,
                        fromfile=f'a/{filename}',
                        tofile=f'b/{filename}')

    return ''.join(diff)
