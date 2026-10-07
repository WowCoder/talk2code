# -*- coding: utf-8 -*-
"""
文件操作工具：read_file / write_file / list_files / delete_file

每个工具对应一个 ToolHandler 子类，支持通过 @register_tool 装饰器或
register_file_tools() 函数注册。
"""

import json as _json
import subprocess

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)


def _syntax_problem(filename: str, content: str) -> str:
    """检查内容是否存在**确定性**残缺/语法错误，无问题返回空串。

    为什么需要它（需求 183 根因）：coder 一轮内连续写多个大文件时，LLM 输出额度
    会被 reasoning 吃掉，后写的文件在运行到一半时被截断（实测 js/game.js 停在
    `DragonGame.prototype._`、index.html 停在 47 行且缺 </html> 与全部 <script>）。
    残缺文件通过 write_file **覆盖**了此前已经写好的完整版本，QA 两轮评分因此
    只有 4.2 / 2.4。写入前看不见截断，只能在写后校验并回滚。

    判据与 lint_js / lint_css 对齐，只针对可机器判定的问题：
    - .js   → node --check
    - .css  → 花括号平衡
    - .html → 声明为完整文档（含 <html / <!doctype）时必须以 </html> 收尾
    - .json → 可解析
    """
    low = (filename or "").lower()
    if low.endswith(".js"):
        try:
            r = subprocess.run(["node", "--check", "-"], input=content,
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                # node --check 的 stderr 首行是源码位置（形如 "[stdin]:1"），
                # 真正的判据在含 "Error" 的那一行，取它才对模型有意义。
                lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip()]
                detail = next((l for l in lines if "Error" in l), "")
                if not detail and len(lines) > 1:
                    detail = lines[1]
                return f"JS 语法错误: {(detail or 'JS 语法错误')[:160]}"
        except FileNotFoundError:
            return ""
        except Exception:
            return ""
        # 末尾启发式：node 判不出「半句话」——`DragonGame.prototype._` 是合法
        # 表达式语句，语法检查照样通过，但文件明显被截断（req 183 就是这个形态）。
        tail = content.rstrip()
        if tail:
            last_line = tail.splitlines()[-1].rstrip()
            if last_line and not last_line.startswith("//") and not last_line.endswith("*/"):
                if last_line[-1] in "_=+-*/(&|<.":
                    return f"文件末尾不完整（最后一行以 '{last_line[-1]}' 结束，疑似输出被截断）"
    elif low.endswith(".css"):
        if content.count("{") != content.count("}"):
            return f"花括号不匹配 ({{ {content.count('{')}, }} {content.count('}')})"
    elif low.endswith(".html") or low.endswith(".htm"):
        # 只对「完整文档」要求闭合标签，避免误判片段模板
        head = content[:400].lower()
        if ("<html" in head or "<!doctype" in head) and not content.rstrip().endswith("</html>"):
            return "HTML 未以 </html> 结束（疑似内容被截断）"
    elif low.endswith(".json"):
        try:
            _json.loads(content)
        except Exception as e:
            return f"JSON 解析失败: {str(e)[:100]}"
    return ""


# ==================== ToolHandler 子类 ====================

# write_file 回显完整正文的字符上限。
#
# 需求 220 根因 B 的三方契约之一：write_file 的**返回内容**必须与 prompt 对它的
# 承诺一致。此前无论文件大小一律只回「前 80 行 + 后 10 行」再被 `preview[:3000]`
# 硬截，而 prompt 却写着「返回结果含完整正文，这就是权威副本，禁止回读」。
# 阈值内的文件直接回全文（承诺即事实）；超阈值的如实标注为「首尾回执」。
# 定 6000：实测交付文件多在此量级内，且相对旧行为（≤3000 预览）只多 ~3k 字符，
# 换来的是「模型不再回读」——一次回读往返的成本远高于这 3k。
WRITE_FULL_ECHO_CHAR_CAP = 6000


class ReadFileHandler(ToolHandler):
    """读取工作区文件内容"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        try:
            content = ws.read(filename)
            lines = content.split('\n')
            total_lines = len(lines)

            if start_line is not None or end_line is not None:
                start = max(0, (start_line or 1) - 1)
                end = min(total_lines, end_line or total_lines)
                selected = lines[start:end]
                content = '\n'.join(selected)
                # 旧写法 "(行 200-373 / 共 435 行)" 让弱模型把 373 读成文件总行数，
                # 进而怀疑文件被截断并反复回读确认（需求 182）。改为总行数前置 +
                # 明确说明剩余部分如何读取。
                if end >= total_lines:
                    tail_note = "已到文件末尾"
                else:
                    # ⚠️ 分页邀请必须按文件来源区分（需求 220 根因 B 的第 ⑥ 环）：
                    # 对**本任务自己创建**的文件，这句「需要时用 start_line=N 继续读取」
                    # 是回读空转循环的直接推手 —— 模型刚写入、正文就被系统从上下文
                    # 卸掉，于是沿着提示一页页往下读，19 次后触发熔断。
                    # 对既有文件（模型从未见过其内容）分页提示仍然是必要信息。
                    if self._is_self_created(filename, state):
                        tail_note = (
                            f"第 {end + 1}-{total_lines} 行未包含在本段，"
                            f"但该文件是你本次任务写入的，未包含部分与你写入时逐字一致，"
                            f"**无需继续读取**"
                        )
                    else:
                        tail_note = (
                            f"第 {end + 1}-{total_lines} 行未包含在本段，"
                            f"需要时用 start_line={end + 1} 继续读取"
                        )
                range_info = f"共 {total_lines} 行；本次返回第 {start + 1}-{end} 行，{tail_note}"
            else:
                range_info = f"全文共 {total_lines} 行，未截断"

            header = f"[文件: {filename} — {range_info}]\n\n"
            return ToolResult(
                content=header + content,
                metadata={
                    "filename": filename,
                    "total_lines": total_lines,
                    "start_line": start_line or 1,
                    "end_line": end_line or total_lines,
                    "chars": len(content),
                }
            )
        except Exception as e:
            return ToolResult(error=str(e))

    @staticmethod
    def _is_self_created(filename: str, state) -> bool:
        """该文件是否由本次任务自己写入且此后未被改动。

        判据复用 ToolCallLoop 的「已知正文」登记表（`_known_content_files`）：
        它由 write_file 成功时登记、edit_file 成功时撤销（内容已被局部改动，
        模型手上的副本过期），语义正是「本次任务创建 + 内容未变」。
        取不到 state（兼容路径直接调 handler）时返回 False —— 保守回到旧行为，
        不会误伤既有文件的分页提示。
        """
        if not state or not filename:
            return False
        try:
            known = state.get("_known_content_files") or {}
        except Exception:
            return False
        return filename in known

    # 保留旧方法名以兼容现有调用
    def read_file(self, filename: str, start_line: int = None, end_line: int = None) -> ToolResult:
        return self.execute({
            "filename": filename,
            "start_line": start_line,
            "end_line": end_line,
        })


def _completeness_hints(filename: str, content: str) -> list[str]:
    """返回「不阻断但值得立刻看一眼」的完整性提示。

    与 _syntax_problem 的区别：这里的问题不一定要回滚（可能是有意为之），
    但放任不管几乎必然导致 QA 低分（实测 req 183 首轮 4.2 分即由此而来：
    index.html 只有 47 行，既没有 <script> 也没有 </html>，页面点了没反应）。
    """
    hints = []
    low = (filename or "").lower()
    if low.endswith(".html") or low.endswith(".htm"):
        head = content[:400].lower()
        if "<html" in head or "<!doctype" in head:
            if "<script" not in content.lower():
                hints.append("入口 HTML 未引入任何 <script>，页面不会有任何交互逻辑")
            if "<link" not in content.lower() and "css" not in content.lower():
                hints.append("入口 HTML 未引入任何样式表")
    return hints


class WriteFileHandler(ToolHandler):
    """创建或覆盖工作区文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        content = args.get("content", "")
        try:
            lines = content.count('\n') + 1
            char_count = len(content)

            # ---- 写入保护：先取上一版，写坏时能回滚（需求 183 根因防御） ----
            prev = None
            try:
                prev = ws.read(filename)
            except Exception:
                prev = None

            ws.write(filename, content)

            # ---- 写后完整性校验 ----
            problem = _syntax_problem(filename, content)
            prev_ok = bool(prev and prev.strip() and not _syntax_problem(filename, prev))
            if problem and prev_ok:
                # 上一版是好的、这一版写坏了 → 回滚。绝不允许把完整文件覆盖成残件。
                ws.write(filename, prev)
                prev_lines = prev.count('\n') + 1
                return ToolResult(
                    error=(
                        f"已拒绝本次写入并回滚 {filename}：新内容不完整（{problem}）。"
                        f"写入前的版本（{prev_lines} 行）已恢复，文件保持可用状态。\n"
                        f"这通常是一次性输出过长、额度不足导致内容被截断 —— 请改用：\n"
                        f"1) edit_file 做局部修改（SEARCH/REPLACE，只传改动片段）；\n"
                        f"2) 或把文件拆成两个更小的文件，分轮写入；\n"
                        f"3) 或先写一个骨架文件，再逐段 edit_file 补齐。"
                    ),
                    metadata={"filename": filename, "rolled_back": True,
                              "lines": prev_lines, "chars": len(prev)},
                )

            # 返回内容预览。⚠️ 这里的措辞必须与 prompt 里的承诺**逐字一致**
            # （需求 220 根因 B）：主 prompt 此前宣称「write_file 返回完整正文，
            # 这就是权威副本，禁止回读」，而实际只给 ≤3000 字符的头尾片段 ——
            # 模型按承诺省掉回读，随后发现中段缺失（14607 字符的 particles.js
            # 只见到 20%），于是**理性地**开始分页回读，19 次后被熔断。
            # 系统先撒谎、再惩罚说真话的模型，这个循环必须断在这里。
            if char_count <= WRITE_FULL_ECHO_CHAR_CAP:
                # 够小 → 直接回完整正文，承诺即为事实，模型再无任何回读理由。
                preview_note = (
                    f"已创建 {filename} ({lines} 行, {char_count} 字符)\n\n"
                    f"--- 以下是你写入的**完整正文**（与磁盘逐字一致，权威副本，无需回读）---\n"
                    f"{content}"
                )
            else:
                # 太大 → 如实说明「你拿到的是首尾回执，不是全文」，并明确中段
                # 与你写入的逐字相同。**绝不能**再写「中间省略 N 行」——那句话
                # 在模型看来等于「这里有内容缺失」，是触发分页回读的直接诱因。
                all_lines = content.split('\n')
                head_lines = all_lines[:80]
                tail_lines = all_lines[-10:] if len(all_lines) > 80 else []
                preview = '\n'.join(head_lines)
                if tail_lines:
                    preview += "\n\n... (首尾之间的内容与你写入的逐字相同，"
                    preview += "已确认写入成功，无需回读核对) ...\n\n" + '\n'.join(tail_lines)
                preview_note = (
                    f"已创建 {filename} ({lines} 行, {char_count} 字符)\n\n"
                    f"--- 首尾回执（文件共 {char_count} 字符，超过 "
                    f"{WRITE_FULL_ECHO_CHAR_CAP} 字符故不回显全文）---\n"
                    f"{preview[:3000]}\n\n"
                    f"文件已完整写入磁盘，未回显的中间部分与你提交的 content 逐字一致。"
                    f"**不要为确认内容而回读它。**"
                )

            if problem:
                # 不再教「用 start_line/end_line 分段重写」（需求 220 根因 B 第 ③ 环）：
                # 那条建议会把模型引导到分页读/分页写的循环里，而分页读又会绕过
                # 防回读的两道防线，最终撞上 read 次数上限被熔断。
                preview_note += (
                    f"\n\n⚠️ 完整性告警：{problem}。内容疑似被截断，"
                    f"请立即用 edit_file 补齐（SEARCH/REPLACE 只传改动片段），"
                    f"或把文件拆成两个更小的文件分轮写入，不要留给下一轮。"
                )
            if prev is not None and prev.strip():
                prev_lines = prev.count('\n') + 1
                if prev_lines >= 120 and lines < prev_lines * 0.5:
                    preview_note += (
                        f"\n\n⚠️ 本次写入 {lines} 行，远少于写入前的 {prev_lines} 行。"
                        f"若非有意精简，说明输出被截断 —— 请检查文件末尾是否完整。"
                    )
            for hint in _completeness_hints(filename, content):
                preview_note += f"\n\n⚠️ {hint} —— 如属遗漏请立即补上，不要留到验证阶段。"
            return ToolResult(
                content=preview_note,
                metadata={"filename": filename, "lines": lines, "chars": char_count,
                          "truncated": bool(problem)}
            )
        except Exception as e:
            return ToolResult(error=str(e))

    def write_file(self, filename: str, content: str) -> ToolResult:
        return self.execute({"filename": filename, "content": content})


class ListFilesHandler(ToolHandler):
    """列出工作区所有文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        try:
            files = ws.list()
            return ToolResult(content="\n".join(files) if files else "(空目录)")
        except Exception as e:
            return ToolResult(error=str(e))

    def list_files(self) -> ToolResult:
        return self.execute({})


class DeleteFileHandler(ToolHandler):
    """删除工作区文件"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        ws = workspace or self.workspace
        if not ws:
            return ToolResult(error="workspace 未初始化")
        filename = args.get("filename", "")
        try:
            ws.delete(filename)
            return ToolResult(content=f"已删除 {filename}")
        except Exception as e:
            return ToolResult(error=str(e))

    def delete_file(self, filename: str) -> ToolResult:
        return self.execute({"filename": filename})


# ==================== 兼容旧 FileToolHandler 类 ====================

class FileToolHandler:
    """向后兼容：聚合所有文件工具处理器（委托给子类实例）"""

    def __init__(self, workspace):
        self.workspace = workspace
        self._read = ReadFileHandler(workspace)
        self._write = WriteFileHandler(workspace)
        self._list = ListFilesHandler(workspace)
        self._delete = DeleteFileHandler(workspace)

    def read_file(self, filename: str, start_line: int = None, end_line: int = None) -> ToolResult:
        return self._read.read_file(filename, start_line, end_line)

    def write_file(self, filename: str, content: str) -> ToolResult:
        return self._write.write_file(filename, content)

    def list_files(self) -> ToolResult:
        return self._list.list_files()

    def delete_file(self, filename: str) -> ToolResult:
        return self._delete.delete_file(filename)


# ==================== 注册函数 ====================

def register_file_tools(registry):
    """注册文件操作工具到 ToolRegistry"""
    # 创建 handler 实例（workspace 稍后注入）
    read_handler = ReadFileHandler()
    write_handler = WriteFileHandler()
    list_handler = ListFilesHandler()
    delete_handler = DeleteFileHandler()

    registry.register(ToolDefinition(
        name="read_file",
        description=(
            "读取工作区中的文件内容。对于大文件（>300行），请使用 start_line/end_line 分页读取，"
            "避免一次性读取整个文件。返回头部会给出文件总行数与本次返回的行范围。\n"
            "重要：你自己刚写入的文件，内容已由 write_file 返回（≤6000 字符返回完整正文，"
            "更大的文件返回首尾回执并说明中间部分与你写入的逐字一致），"
            "不要为了确认 class/id 反复读取同一个文件；同一文件在一次任务中最多读 1-2 次，"
            "把时间用在创建尚未生成的文件上。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要读取的文件名（相对于工作区根目录）"},
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（1-based，可选）。用于分页读取大文件。不指定则从第1行开始。"
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（1-based，可选）。不指定则读到文件末尾。"
                },
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: read_handler.execute(kwargs),
        permission="read",
        tool_handler=read_handler,
    ))

    registry.register(ToolDefinition(
        name="write_file",
        description="创建或覆盖工作区中的文件",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "文件名（相对于工作区根目录，支持子目录如 css/style.css）"},
                "content": {"type": "string", "description": "文件内容"}
            },
            "required": ["filename", "content"]
        },
        handler=lambda **kwargs: write_handler.execute(kwargs),
        permission="write",
        tool_handler=write_handler,
    ))

    registry.register(ToolDefinition(
        name="list_files",
        description="列出工作区中的所有文件",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        handler=lambda **kwargs: list_handler.execute(kwargs),
        permission="read",
        tool_handler=list_handler,
    ))

    registry.register(ToolDefinition(
        name="delete_file",
        description="删除工作区中的文件",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "要删除的文件名"}
            },
            "required": ["filename"]
        },
        handler=lambda **kwargs: delete_handler.execute(kwargs),
        permission="write",
        tool_handler=delete_handler,
    ))
