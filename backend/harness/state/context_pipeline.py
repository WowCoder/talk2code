# -*- coding: utf-8 -*-
"""
ContextPipeline —— 短期记忆 v2 的上下文管线（L0–L5）

取代旧的分层压缩器（已移除）。设计原则见
docs/design/context-pipeline-v2.md：

- 按可重建性分类：文件内容（read_file 结果）可随时重读 → 允许遮蔽/卸载；
  决策、失败、对话不可重建 → 永不丢弃。
- 前缀稳定：head（system prompt）由调用方传入，本模块不改动它。
- Compaction 是兜底不是日常：仅 L3/L4 装不下时才触发 L5（实测 2%）。

本模块只依赖标准库，不反向依赖 runtime，便于单测。
"""

import json
import re
import uuid

from harness.observability.logger import get_logger
from harness.agent_names import TL_NAME

logger = get_logger(__name__)

# 单条工具结果进入历史前的截断上限（L3a 入口闸门）
SINGLE_RESULT_LIMIT = 2000
# 预算驱动遮蔽时，最近 N 条 read_file 永不遮蔽（对应 Anthropic keep 默认值 3）
KEEP_RECENT = 3
# L5 兜底时，保留最近 N 条对话不摘要
KEEP_TAIL = 6
# 永不遮蔽的工具（write/edit 变更摘要 + 验证结果），对应 Anthropic exclude_tools 白名单
EXCLUDE_TOOLS = {
    "write_file", "edit_file", "update_task_notes",
}
_EXCLUDE_PREFIXES = (
    "validate", "lint", "verify", "check", "test", "preview", "run_preview",
    "screenshot", "smoke",
)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中英文字符分段加权，避免中文低估约 50%）。

    与预算判断口径一致（中英文字符分段加权），避免两处估算漂移。
    """
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = max(len(text) - chinese, 0)
    return int(chinese / 1.5 + other / 4)


def _truncate_to_tokens(text: str, limit: int) -> str:
    """按 token 估算截断到 limit 以内，优先保留开头。"""
    if estimate_tokens(text) <= limit:
        return text
    # 逐字符累加，遇到接近上限即停
    out = []
    acc = 0
    for ch in text:
        out.append(ch)
        acc += 1.5 if "\u4e00" <= ch <= "\u9fff" else 0.25
        if acc >= limit:
            break
    return "".join(out)


_READ_HEADER_RE = re.compile(
    r"^\[文件:\s*(.+?)\s*\("
    r"(行\s*(\d+)-(\d+)\s*/\s*共\s*(\d+)\s*行|共\s*(\d+)\s*行)"
    r"\)\]"
)


def _parse_read_header(content: str):
    """从 read_file 结果里解析 (filename, range_info)。"""
    m = _READ_HEADER_RE.match(content.lstrip())
    if not m:
        return None, ""
    filename = m.group(1).strip()
    if m.group(3):  # 带行范围
        range_info = f"行 {m.group(3)}-{m.group(4)} / 共 {m.group(5)} 行"
    else:
        range_info = f"共 {m.group(6)} 行"
    return filename, range_info


def _extract_symbol_summary(body: str) -> str:
    """规则提取一句话摘要（首行 / 导出符号），不调 LLM。"""
    symbols = []
    first_line = ""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if not first_line:
            first_line = s[:80]
        mm = re.match(r"(export\s+(default\s+)?)?(function|const|let|var|class|def)\s+([A-Za-z_]\w*)", s)
        if mm:
            symbols.append(mm.group(4))
        if len(symbols) >= 3:
            break
    if symbols:
        return "导出 " + "/".join(symbols)
    if first_line:
        return "首行: " + first_line
    return "（空文件）"


def _local_summary(text: str) -> str:
    """L5 本地抽取式摘要（不调 LLM，零成本）。

    提取文件操作 / 错误 / 修复线索。
    """
    key = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(k in s for k in ("创建", "修改", "文件", "错误", "失败", "修复",
                                 "def ", "class ", "export ", "read_file", "write_file")):
            key.append(s[:120])
        if len(key) >= 10:
            break
    if not key:
        key = [l.strip()[:120] for l in text.splitlines() if l.strip()][:5]
    return "\n".join(f"- {k}" for k in key[:10])


# =====================================================================
# L2 TASK_STATE.md 相关：staleness 检测 + 交付边界折叠（§3.H / §4）
# 这些helper为纯函数/轻量IO，便于单测，不反向依赖 runtime。
# =====================================================================

STALENESS_THRESHOLD = 2
STALENESS_REMINDER = (
    "提醒：你已多次修改文件（write_file / edit_file）但未用 update_task_notes "
    "更新 .task/TASK_STATE.md。请在本轮用 update_task_notes 记录目标 / 决策 / "
    "文件状态 / 未决问题 / 下一步——否则上下文滚动后将丢失进度与决策理由。"
)


def note_task_activity(state: dict, tool_name: str, success: bool):
    """记录本轮的文件变更 / notes 更新事件（harness 唯一介入点，不解析语义）。

    在 _execute_tool 成功后调用：write_file/edit_file 成功累计一次文件变更；
    update_task_notes 成功记 notes 已更新。
    """
    if tool_name in ("write_file", "edit_file") and success:
        state["_round_file_change"] = True
        # 累计计数（req 147 修正）：真实轮次是「写/读交替」的，用「连续轮」判据
        # 会被中间的只读轮重置为 0，7 轮下来一次都没触发。
        state["_pending_file_changes"] = state.get("_pending_file_changes", 0) + 1
    if tool_name == "update_task_notes" and success:
        state["_round_notes_updated"] = True


def evaluate_staleness(state: dict) -> str:
    """在每轮 _build_messages 开头评估 staleness（基于上一轮事件）。

    返回提醒文案（达到阈值时）或空串；同时维护并重置轮内计数。

    阈值 STALENESS_THRESHOLD=2，判据是「**累计**未记录的文件变更次数」而非
    「连续轮数」——写/读交替时连续轮判据会被重置，req 147 因此 7 轮 0 触发。
    触发后计数清零，避免每轮重复刷屏。
    """
    had_notes = state.get("_round_notes_updated", False)
    pending = state.get("_pending_file_changes", 0)
    if had_notes:
        pending = 0
    state["_pending_file_changes"] = pending
    # 重置本轮标记，供下一轮重新累积
    state["_round_file_change"] = False
    state["_round_notes_updated"] = False
    if pending >= STALENESS_THRESHOLD:
        # 提醒过一次就清零，给 Agent 一轮执行机会，不反复刷
        state["_pending_file_changes"] = 0
        return STALENESS_REMINDER
    return ""


def _build_handoff(state: dict, workspace) -> str:
    """构造 DELIVERY.md（handoff）文本：需求快照 + 计划 + TASK_STATE.md + 怎么跑。"""
    try:
        req = state.get("requirement_content", "") or ""
        plan = state.get("plan")
        task_state = ""
        try:
            if workspace and hasattr(workspace, "list") and ".task/TASK_STATE.md" in workspace.list():
                task_state = workspace.read(".task/TASK_STATE.md")
        except Exception:
            task_state = ""
        lines = ["# 交付 Handoff（.task/DELIVERY.md）", ""]
        lines.append("## 原始需求")
        lines.append((req[:2000] if req else "(无)"))
        if isinstance(plan, dict):
            fs = plan.get("file_structure", [])
            if isinstance(fs, list) and fs:
                lines.append("\n## 目标文件")
                lines.append(", ".join(str(f) for f in fs))
        if task_state:
            lines.append("\n## 任务状态（TASK_STATE.md 快照）")
            lines.append(task_state)
        lines.append("\n## 怎么跑 / 怎么验")
        lines.append("在 workspace 根目录用静态服务器打开 index.html；run_preview 无 console 错误即通过。")
        return "\n".join(lines)
    except Exception:
        return ""


def _archive_tool_trail(state: dict, workspace) -> str:
    """把工具轨迹（thinking / tool_call / system_hidden）归档到 .task/EXECUTION.jsonl。

    为什么是「归档」而不是「删除」：交付折叠的**目标**是让跨轮 LLM 上下文轻量，
    但早期实现直接从 dialogue_history 里丢弃这些消息 → Agent 的思考 / 工具调用与结果
    当场永久消失，事后无法观测（实测 req 144 交付后轨迹全丢，详情页无日志可看）。
    这里把轨迹原样落盘成 dev artifact，dialogue_history 仍只保留人类对话。
    """
    try:
        hist = state.get("dialogue_history", []) or []
        trail = [m for m in hist if m.get("role") in ("thinking", "tool_call", "system")]
        if not trail:
            return ""
        path = ".task/EXECUTION.jsonl"
        lines = [json.dumps(m, ensure_ascii=False) for m in trail]
        workspace.write(path, "\n".join(lines) + "\n")
        return path
    except Exception as e:
        logger.warning(f"[Delivery] 工具轨迹归档失败（不阻断）: {e}")
        return ""


def _slim_iteration_batch(msg: dict) -> dict:
    """给 iteration_batch 瘦身，使其可以安全落库 / 跨轮恢复。

    迭代卡片的 arguments 里带着 write_file 的**全文**（实测单个 js/game.js 就有
    15,605 字符），原样落进 DB 会让 dialogue_history 膨胀到数 MB。前端展示只需要
    文件名与简短参数，这里把长值替换为长度标记。
    """
    tools = msg.get("tools") or []
    slim_tools = []
    for t in tools:
        if not isinstance(t, dict):
            slim_tools.append(t)
            continue
        args = t.get("arguments") or {}
        slim_args = {}
        if isinstance(args, dict):
            for k, v in args.items():
                if k == "content":
                    # 正文不落库，只留体量信息（前端展示用 display_label 已足够）
                    slim_args["content_chars"] = len(v) if isinstance(v, str) else 0
                    continue
                if isinstance(v, str) and len(v) > 200:
                    slim_args[k] = f"<{len(v)} 字符，已省略>"
                else:
                    slim_args[k] = v
        slim_tools.append({**t, "arguments": slim_args})
    return {**msg, "tools": slim_tools}


def finalize_delivery(state: dict, workspace) -> str:
    """交付边界折叠（§3.H）：写 .task/DELIVERY.md（handoff）+ **归档**工具轨迹。

    - 非重建项（需求/计划/决策/反馈）已落地到 requirement 状态 + TASK_STATE.md + DELIVERY.md。
    - dialogue_history 保留人类可读的对话（user/agent/assistant）；thinking/tool_call/system_hidden
      属工具轨迹，**归档到 .task/EXECUTION.jsonl** 后再从对话历史移除（保持交付后上下文轻量）。
    - handoff 起点消息以 **agent（TL）** 身份追加，不用 user：它是系统注入的
      交接说明而非用户发言，落成 user 会在对话流里伪装成用户消息。
    - 返回 handoff 路径；写入失败返回 ""（不阻断交付）。
    """
    handoff = _build_handoff(state, workspace)
    path = ""
    if handoff:
        try:
            workspace.write(".task/DELIVERY.md", handoff)
            path = ".task/DELIVERY.md"
        except Exception:
            path = ""
    # 先归档工具轨迹（供开发排查），再收敛对话历史为「人类对话」
    _archive_tool_trail(state, workspace)
    hist = state.get("dialogue_history", []) or []
    # ⚠️ iteration_batch 必须保留：coder 的 assistant 自述消息带 hidden=True
    # （runtime 约定由迭代卡片承载展示），一旦把 iteration_batch 也丢掉，
    # 详情页「开发工程师」这一整段编码过程就彻底为空（实测 req 183）。
    kept = []
    for m in hist:
        role = m.get("role")
        if role in ("user", "agent", "assistant"):
            kept.append(m)
        elif role == "iteration_batch":
            kept.append(_slim_iteration_batch(m))
    if path:
        # ⚠️ 角色必须是 agent（TL），不能是 user。
        # 这条是系统在交付边界注入的交接起点，**不是用户说的话**；早先写成
        # role=user，于是详情页对话流里凭空多出一条「用户消息：上一轮已交付…」
        # （req 144/191/202 实测），用户看到自己的消息框里出现自己没发过的内容。
        # 归属规则：用户消息只能来自用户本人；Agent 侧消息只归属三个角色，
        # 无法判断归属时归 TL。这条是 TL 在交付边界留下的状态交接说明。
        kept.append({
            "role": "agent",
            "name": TL_NAME,
            "content": f"上一轮已交付。任务状态 handoff 见 {path}，请据此继续。",
        })
    state["dialogue_history"] = kept
    return path


class ContextPipeline:
    """上下文管线：把原始 dialogue_history 转成发给 LLM 的 messages。"""

    def __init__(self, budget: int = 24000, single_result_limit: int = SINGLE_RESULT_LIMIT,
                 keep_recent: int = KEEP_RECENT, keep_tail: int = KEEP_TAIL,
                 llm_summary=None, ref_store=None):
        """
        Args:
            budget: head + history 的总 token 预算（默认 24000，与现状一致）
            single_result_limit: L3a 单条工具结果截断上限
            keep_recent: 最近 N 条 read_file 永不遮蔽
            keep_tail: L5 兜底时保留最近 N 条对话不摘要
            llm_summary: L5 兜底用的摘要函数 text->str（测试注入 stub）
            ref_store: 非文件大结果落盘回调 (filename, content) -> path（测试注入 fake）
        """
        self.budget = budget
        self.single_result_limit = single_result_limit
        self.keep_recent = keep_recent
        self.keep_tail = keep_tail
        self.llm_summary = llm_summary
        self.ref_store = ref_store

    # ---------- 对外入口 ----------

    def build(self, head_content: str, history: list, hook_failures=None,
              requirement_content: str = "") -> tuple:
        """组装完整 messages。

        Returns:
            (messages, stats)
            messages: [{"role":..., "content":...}, ...]
            stats: 可观测性字典
        """
        head_tokens = estimate_tokens(head_content)
        staged = self._stage_history(history)
        # L3a 入口闸门：截断单条过大的工具结果
        for s in staged:
            if s["kind"] == "tool":
                s["content"] = self._entry_gate(s["name"], s["content"])
        messages = [self._to_message(s) for s in staged]
        hist_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        stats = {
            "head_tokens": head_tokens,
            "history_tokens": hist_tokens,
            "masked_read": 0,
            "masked_nonfile": 0,
            "offloaded": 0,
            "dropped": 0,
            "compacted": 0,
            "cache_hit": True,
        }

        # L3b + L4：预算内零遮蔽；超预算按时间序从最旧遮蔽
        if head_tokens + hist_tokens > self.budget:
            messages, sub = self._budget_mask(staged, head_tokens, messages)
            for k in sub:
                stats[k] = sub[k]
            stats["history_tokens"] = sum(estimate_tokens(m["content"]) for m in messages)

        # 注入最近 hook 失败（保留既有行为）
        if hook_failures:
            failure_text = (
                "## 最近验证失败（请立即修复这些问题）\n"
                + "\n".join(f"- {f}" for f in hook_failures[-5:])
            )
            messages.append({"role": "user", "content": failure_text})

        # 冷启动兜底：保证至少一个 user 角色
        if not any(m.get("role") == "user" for m in messages):
            req = requirement_content or "请根据以上系统提示开始任务。"
            messages.append({"role": "user", "content": req})

        return messages, stats

    # ---------- L0 角色通道 ----------

    def _stage_history(self, history: list) -> list:
        """把原始 dialogue_history 转成内部 staging 结构（L0 角色通道）。"""
        staged = []
        for m in history:
            role = m.get("role")
            if role == "thinking":
                continue  # 丢弃
            if role == "iteration_batch":
                continue  # UI 元数据，不发送
            preserve = bool(m.get("preserve", False))
            if role == "tool_call":
                staged.append({
                    "kind": "tool", "name": m.get("name", ""),
                    "content": str(m.get("content", "")), "preserve": preserve,
                    # arguments 只作**遮蔽决策的元数据**（取 filename 判断 read 是否
                    # 已被后续写入覆盖），绝不渲染进消息体 —— 否则 write 正文会撑爆上下文。
                    "arguments": m.get("arguments") or {},
                })
            elif role == "system":
                # 隐藏的系统提示：进消息列表带前缀（修 D11 静默丢弃）
                staged.append({
                    "kind": "system_hidden", "content": str(m.get("content", "")),
                    "preserve": preserve,
                })
            else:  # user / agent / assistant
                staged.append({
                    "kind": "chat",
                    "role": "user" if role == "user" else "assistant",
                    "content": str(m.get("content", "")), "preserve": preserve,
                })
        return staged

    @staticmethod
    def _to_message(s: dict) -> dict:
        if s["kind"] == "tool":
            return {"role": "user", "content": f"[工具 {s['name']} 返回结果]\n{s['content']}"}
        if s["kind"] == "system_hidden":
            return {"role": "user", "content": f"[系统提示]\n{s['content']}"}
        return {"role": s["role"], "content": s["content"]}

    # ---------- L3a 入口闸门 ----------

    def _entry_gate(self, name: str, content: str) -> str:
        if estimate_tokens(content) <= self.single_result_limit:
            return content
        # read_file：保留文件头（含行号），截断正文并提示重读
        filename, range_info = _parse_read_header(content)
        if filename:
            header = f"[文件: {filename} ({range_info})]\n\n"
            body = content[len(header):] if content.startswith(header) else content
            kept = _truncate_to_tokens(body, self.single_result_limit)
            note = "\n…（已截断，如需后续内容请用 start_line/end_line 重新读取）"
            return header + kept + note
        # 非文件：直接截断
        kept = _truncate_to_tokens(content, self.single_result_limit)
        return kept + "\n…（已截断，超出单条上限）"

    # ---------- L3b 预算遮蔽 + L4 装载 ----------

    @staticmethod
    def _tool_target_filename(s: dict) -> str:
        """取工具消息作用的文件名（write/edit 取 arguments.filename，read 取结果头）。"""
        args = s.get("arguments") or {}
        if isinstance(args, dict):
            fname = args.get("filename")
            if fname:
                return str(fname)
        if s.get("name") == "read_file":
            fname, _ = _parse_read_header(s.get("content", ""))
            return fname or ""
        return ""

    def _superseded_reads(self, staged: list) -> dict:
        """找出「读完之后又被 write/edit 改过」的 read —— 它们的内容已经过期。

        这类 read 是最该被遮蔽的：留着不但占预算，还会让 LLM 拿旧版本去做
        edit_file 的 SEARCH 匹配（实证：style.css 361→366→245 行反复变动时，
        SEARCH 连续失配，因为模型手里是过期内容）。

        Returns:
            {staged 索引: 覆盖它的工具名}
        """
        superseded = {}
        tools = [(i, s) for i, s in enumerate(staged) if s["kind"] == "tool"]
        for idx, s in tools:
            if s["name"] != "read_file":
                continue
            fname = self._tool_target_filename(s)
            if not fname:
                continue
            for j, later in tools:
                if j <= idx:
                    continue
                if later["name"] in ("write_file", "edit_file") \
                        and self._tool_target_filename(later) == fname:
                    superseded[idx] = later["name"]
                    break
        return superseded

    def _budget_mask(self, staged: list, head_tokens: int, messages: list) -> tuple:
        sub = {"masked_read": 0, "masked_nonfile": 0, "offloaded": 0, "dropped": 0, "compacted": 0}
        cur_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        # 标记可遮蔽的工具消息
        tool_idx = [i for i, s in enumerate(staged) if s["kind"] == "tool"]
        # 受保护集合（永不遮蔽）
        protected = set()
        # EXCLUDE_TOOLS + 前缀命中：write/edit/验证 等
        for i in tool_idx:
            if staged[i]["name"] in EXCLUDE_TOOLS or staged[i]["name"].startswith(_EXCLUDE_PREFIXES):
                protected.add(i)
            if staged[i]["preserve"]:
                protected.add(i)
        # 最近 KEEP_RECENT 条 read_file 永不遮蔽（对应 Anthropic keep 默认值 3）。
        # 注意：keep_recent 只保护 read_file；非文件结果可重建性差但体积通常小，
        # 且已在 L3a 入口闸门截断过，故不参与 keep_recent，保持可遮蔽以便预算达标。
        read_recent = [i for i in tool_idx if staged[i]["name"] == "read_file"][-self.keep_recent:]
        protected.update(read_recent)

        # 可遮蔽候选排序：**先遮蔽已失效的 read** —— 它们的内容已被后续 write/edit
        # 覆盖，遮蔽是零信息损失；留着反而诱导模型拿旧内容去做 edit 的 SEARCH 匹配
        #（实证 style.css 361→366→245 行变动期间，SEARCH 连续失配）。
        # 其次才按时间序从最旧遮蔽（传统 LRU 语义）。
        superseded = self._superseded_reads(staged)
        maskable = [i for i in tool_idx if i not in protected]
        maskable.sort(key=lambda i: (0 if i in superseded else 1, i))

        for i in maskable:
            if head_tokens + cur_tokens <= self.budget:
                break
            s = staged[i]
            if s["name"] == "read_file":
                filename, range_info = _parse_read_header(s["content"])
                if not filename:
                    filename = s["name"]
                if i in superseded:
                    placeholder = (
                        f"[工具 read_file 返回结果]（已省略；文件：{filename}；"
                        f"该内容已被后续 {superseded[i]} 修改而过期；"
                        f"如需最新内容请重新 read_file）"
                    )
                else:
                    summary = _extract_symbol_summary(
                        s["content"][s["content"].find("\n\n") + 2:] if "\n\n" in s["content"] else s["content"]
                    )
                    placeholder = (
                        f"[工具 read_file 返回结果]（已省略；文件：{filename}；"
                        f"{range_info}；摘要：{summary}；"
                        f"如需请用 start_line/end_line 重新读取）"
                    )
                sub["masked_read"] += 1
            else:
                # 非文件大结果：先落盘再占位符
                ref_path = None
                if self.ref_store is not None:
                    ref_name = f"r{uuid.uuid4().hex[:12]}.md"
                    try:
                        ref_path = self.ref_store(ref_name, s["content"])
                        sub["offloaded"] += 1
                    except Exception as e:  # 落盘失败不阻断，退化为纯占位符
                        logger.warning(f"[ContextPipeline] 非文件结果落盘失败: {e}")
                        ref_path = None
                summary = _extract_symbol_summary(s["content"]) if s["content"] else ""
                if ref_path:
                    placeholder = (
                        f"[工具 {s['name']} 返回结果]（已省略；原文见 {ref_path}；"
                        f"摘要：{summary}）"
                    )
                else:
                    placeholder = (
                        f"[工具 {s['name']} 返回结果]（已省略；摘要：{summary}）"
                    )
                sub["masked_nonfile"] += 1
            s["content"] = placeholder
            s["masked"] = True
            messages[i] = self._to_message(s)
            cur_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        # L5 兜底：仍超预算 → 摘要最旧对话段
        if head_tokens + cur_tokens > self.budget and self.llm_summary is not None:
            messages, l5 = self._l5_fallback(staged, messages, head_tokens)
            sub["compacted"] = l5["compacted"]
            sub["dropped"] = l5["dropped"]
            cur_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        return messages, sub

    # ---------- L5 Compaction 兜底 ----------

    def _l5_fallback(self, staged: list, messages: list, head_tokens: int) -> tuple:
        sub = {"compacted": 0, "dropped": 0}
        # 可压缩的对话/system_hidden（不含受保护、不含工具占位符）
        compressible = [
            i for i, s in enumerate(staged)
            if s["kind"] in ("chat", "system_hidden") and not s.get("preserve")
        ]
        if len(compressible) <= self.keep_tail:
            return messages, sub
        droppable = compressible[:-self.keep_tail]
        text = "\n".join(self._to_message(staged[i])["content"] for i in droppable)
        summary = None
        try:
            summary = self.llm_summary(text)
        except Exception as e:
            logger.warning(f"[ContextPipeline] L5 摘要失败，硬截断: {e}")
            summary = None
        if summary:
            # 移除 droppable，在开头插入一条摘要消息
            keep = [messages[i] for i in range(len(messages)) if i not in set(droppable)]
            keep.insert(0, {"role": "user", "content": f"[历史摘要]\n{summary}"})
            sub["compacted"] = len(droppable)
            return keep, sub
        # 失败兜底：硬截断（信息损失仅剩对话尾部）
        keep = [messages[i] for i in range(len(messages)) if i not in set(droppable)]
        sub["dropped"] = len(droppable)
        return keep, sub
