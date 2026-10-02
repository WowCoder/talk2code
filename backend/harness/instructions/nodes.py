# -*- coding: utf-8 -*-
"""
LangGraph 智能体节点函数
- team_leader_node: 需求分析 + 结构化 Plan
- coder_node: 统一编码节点（内部根据 complexity 选择策略）
- verify_node: Fresh-Context Evaluator 独立评估
- repair_node: 定向修复
"""

import json
import re
import time
from typing import Dict, Any

from harness.state.agent_state import AgentState
from harness.agent_names import TL_NAME, DEV_NAME, QA_NAME
from llm.client import get_client
from llm.client import _try_fix_json as try_fix_json
from utils.sse import get_current_timestamp as _ts
from harness.instructions.prompts import load_prompt, load_prompt_template
from harness.observability.logger import get_logger
from harness.harness_context import get_tool_loop, get_workspace
from harness.instructions.ac_verdict_policy import _ac_state, _should_invalidate_ac_cache
from harness.observability.event_contract import (
    STAGE_PLANNING, STAGE_CODING, STAGE_VERIFYING, STAGE_REPAIRING,
    KIND_CLARIFY, KIND_PLAN, KIND_CODING, KIND_VERIFY, KIND_REPAIR,
)

logger = get_logger(__name__)


def _aux_timeout() -> int:
    """辅助 LLM 调用的超时预算（research / 文件审查 / Playwright 分析 / 澄清问生成）。

    此前这些调用点硬编码 timeout=20~30：对 reasoning 模型严重偏紧，req 186 多次
    `Read timed out (read timeout=30)` 并一次耗尽重试额度。统一由配置控制。
    """
    from config import settings
    return settings.LLM_AUX_TIMEOUT


def _classify_timeout() -> int:
    """极轻量分类/筛选调用的超时预算（同步阻塞用户输入，故比辅助档更短）。"""
    from config import settings
    return settings.LLM_CLASSIFY_TIMEOUT


def _plan_timeout() -> int:
    """TeamLeader 规划调用的超时预算。

    与辅助档分开：辅助调用输出 <500 token，60s 够；规划调用是长结构化输出
    （max_tokens 6000~10000 且开 thinking），实测中位 40s 出头。固定 60s 时，
    需求多轮澄清后 prompt 变长就会顶到上限并**连续** Read timed out
    （req 205 实测连撞 3 次，白等 3 分钟后把 TL 判失败）。
    """
    from config import settings
    return settings.LLM_PLAN_TIMEOUT


def _log_llm_turn_safe(requirement_id, iteration, client, system_prompt, prompt,
                       response, thinking=None, latency_ms=None, stage=None,
                       turn_index=0, trace_id=None):
    """LLM 调用统一埋点：文件明细（开发排查）+ 运营后台索引。

    coder 阶段的埋点在 ToolCallLoop 内部；verify / defect_repair 走独立调用路径，
    必须显式带 stage —— 否则回填后所有调用一律被标成 coding，运营后台的时间线上
    看不到验收与修复这两段。

    turn_index / trace_id 同样必须透传（调用方从 state.metadata 取）：多轮对话里
    不带的话，验收与修复的事件会全部落到「初次生成」（turn 0），轮次切换器
    把第 N 轮的验收显示在第一轮下面。

    iteration 一律传 None：这些辅助链路（规划 / AC 翻译 / 验收 / 修复）不参与
    编码迭代，不应有迭代号。**不要用 0 当哨兵** —— 传 0 会让它们顶着「第 0 轮」
    混进编码子迭代分组，与真正的 1..N 轮混为一谈，界面上看不出哪条属于哪轮
    （这与 `file_count` 记成 0 是同一类问题：日志在说不真的事）。
    """
    if stage is None:
        from harness.observability.event_contract import STAGE_CODING
        stage = STAGE_CODING
    try:
        from harness.observability.trace_writer import record_llm_turn
        record_llm_turn(
            requirement_id, stage=stage, iteration=iteration,
            model=getattr(client, "model", None),
            system_prompt=system_prompt, prompt=prompt,
            tools=[], response=response, thinking=thinking, latency_ms=latency_ms,
            turn_index=turn_index, trace_id=trace_id,
        )
    except Exception:
        pass


def _md(state: AgentState) -> dict:
    """state.metadata 的安全取值。"""
    return state.get("metadata") or {}


def _record_milestone(state: AgentState, kind: str, label: str, *,
                      status: str = "ok", meta: dict = None,
                      duration_ms: int = None,
                      messages: list = None, response: dict = None):
    """里程碑事件埋点（意图/记忆/澄清/规划/确认/编码/验收结论/修复/门禁/回滚/交付）。

    时间线只记 llm_turn + tool_call 就是一本流水账，排查时得逐条点开猜
    「验收为什么没过」。里程碑事件把结论写进 label 与 meta，时间线本身就能回答问题。

    `messages / response` 可选：结论背后确实有 LLM 调用时把输入输出一并传入
    （如澄清问题生成），后台点开能看到依据。没有调用的事件不要传 ——
    不伪造空的输入输出。

    失败静默：可观测性不得阻断主流程（失败计数在 TraceWriter 侧统一记录）。
    """
    try:
        from harness.observability.trace_writer import record_event
        md = _md(state)
        record_event(
            state.get("requirement_id") or md.get("requirement_id"),
            kind, label, status=status, meta=meta, duration_ms=duration_ms,
            turn_index=int(md.get("turn_index") or 0),
            trace_id=md.get("trace_id") or None,
            messages=messages, response=response,
        )
    except Exception:
        pass


def _detect_truncation(content: str) -> bool:
    """
    前置检测：判断 LLM 响应是否被截断。

    检测逻辑：
    1. 括号是否匹配（{} 和 []）
    2. 字符串是否闭合
    3. 是否存在不完整的 key-value 对

    注意：只检测不修复，用于决定是否需要重试。

    Returns:
        True: 可能被截断，建议重试
        False: 结构完整，可以直接提取
    """
    if not content:
        return False

    raw = content.strip()
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

    start_idx = raw.find('{')
    if start_idx == -1:
        return False

    depth = 0
    in_str = False
    escaped = False
    for i in range(start_idx, len(raw)):
        ch = raw[i]
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    # 找到匹配的闭合括号，再检查后面是否还有内容
                    remaining = raw[i+1:].strip()
                    # 如果后面还有非空白内容，可能是截断或额外文本
                    if remaining:
                        # 检查是否只是 "```" 结束标记
                        if remaining.startswith('```'):
                            return False
                        # 否则可能是截断
                        return True
                    return False

    # 没有找到匹配的闭合括号 → 被截断
    return True


def _rescue_unescaped_quotes(raw: str) -> str:
    """把 JSON 字符串值内部的**裸双引号**换成中文引号，使 JSON 重新合法。

    需求 187 事故：agnes-3.0-flash 输出的 plan JSON 里写
    `"how_to_verify": "玩一局...分数栏"最高分"显示10..."`——内层双引号未转义，
    json.loads 全线失败，四层提取（纯 JSON / 代码围栏 / 括号计数 / 兜底正则）
    无一命中 → TeamLeader 抛「无法从 LLM 响应中提取 JSON」→ 整个需求判废。
    而此时 LLM 明明返回了 HTTP 200 与完整合法的 plan 内容。

    大模型在中文文案里夹带裸引号是普遍行为，提示词约束只能降低频率、无法根除，
    因此在提取侧做确定性兜底：区分「结束引号」与「内容引号」。

    判定规则（字符串内遇到 `"` 时）：跳过后续空白，若下一个字符是
    `,` `}` `]` `:` 或已到结尾 → 它是结束引号；否则是内容引号，成对替换为 “ ”。
    """
    out: list[str] = []
    in_str = False
    escaped = False
    parity = 0          # 内容引号配对：0 → 开引号 “，1 → 闭引号 ”
    n = len(raw)
    for i, ch in enumerate(raw):
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch != '"':
            out.append(ch)
            continue
        if not in_str:
            in_str = True
            parity = 0
            out.append(ch)
            continue
        j = i + 1
        while j < n and raw[j] in " \t\r\n":
            j += 1
        nxt = raw[j] if j < n else ""
        if nxt in ("", ",", "}", "]", ":"):
            in_str = False
            out.append(ch)
        else:
            out.append("\u201c" if parity == 0 else "\u201d")
            parity ^= 1
    return "".join(out)


def _try_json_loads(text: str) -> dict | None:
    """先按原样解析；失败则修复字符串内裸引号后重试（见 _rescue_unescaped_quotes）。"""
    text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_rescue_unescaped_quotes(text))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _extract_json_from_llm_response(content: str) -> dict | None:
    """
    从 LLM 原始响应中提取 JSON 对象（纯提取，不修复）。

    处理常见 LLM 输出格式：
    1. 纯 JSON
    2. ```json ... ``` 代码块包裹
    3. ``` ... ``` 无语言标记的代码块
    4. 开头有说明文字 + JSON
    5. JSON 内嵌在任意文本中

    使用括号计数匹配最外层 {}，避免贪婪匹配问题。
    注意：只做纯提取，不调用 try_fix_json。如果需要修复，由调用方决定。

    Returns:
        解析成功的 dict，或 None（表示提取失败）。
    """
    if not content:
        return None

    raw = content.strip()

    # Step 0: 移除非法控制字符
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

    # Step 1: 尝试直接解析
    parsed = _try_json_loads(raw)
    if parsed is not None:
        return parsed

    # Step 2: 去除 ```json ... ``` 或 ``` ... ``` 代码块
    code_block_patterns = [
        re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL),
        re.compile(r'```\s*\n(.*?)\n```', re.DOTALL),
        re.compile(r'```json\s*(.*?)```', re.DOTALL),
        re.compile(r'```\s*(.*?)```', re.DOTALL),
    ]
    for pattern in code_block_patterns:
        match = pattern.search(raw)
        if match:
            parsed = _try_json_loads(match.group(1))
            if parsed is not None:
                return parsed

    # Step 3: 括号计数法匹配最外层 {}
    # 找到第一个 {，然后计数匹配到对应的 }
    # ⚠️ 必须正确处理字符串内部的 { 和 }，避免被误计
    start_idx = raw.find('{')
    if start_idx == -1:
        return None

    depth = 0
    end_idx = -1
    in_str = False
    escaped = False
    for i in range(start_idx, len(raw)):
        ch = raw[i]
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

    if end_idx > start_idx:
        parsed = _try_json_loads(raw[start_idx:end_idx + 1])
        if parsed is not None:
            return parsed

    # Step 4: 最后尝试 regex 提取（向后兼容）
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        parsed = _try_json_loads(match.group())
        if parsed is not None:
            return parsed

    logger.warning(
        f"[JSON提取] 所有方法均失败，content 前200字符: {content[:200]!r}, "
        f"后200字符: {content[-200:]!r}"
    )
    return None


def _is_vague_requirement(text: str) -> bool:
    """检测需求是否过于模糊"""
    text = text.strip()
    if '[用户补充说明]' in text:
        return False
    if len(text) < 30:
        return True
    action_keywords = ['做', '开发', '实现', '创建', '设计', '添加', '支持', '显示', '生成']
    feature_keywords = ['功能', '页面', '按钮', '列表', '表单', '输入', '点击', '显示', '保存', '数据']
    has_action = any(k in text for k in action_keywords)
    has_feature = any(k in text for k in feature_keywords)
    return not (has_action and has_feature)


# 澄清问题兜底（LLM 生成失败时使用），结构与 QuestionForm.vue 对齐
FALLBACK_CLARIFY_QUESTIONS = [
    {"id": "q1", "type": "text", "label": "请更具体地描述你的需求"},
    {"id": "visual_style", "type": "radio",
     "label": "你偏好哪种视觉风格？",
     "options": ["极简白", "暖柔风格", "暗黑科技", "活泼多彩", "无偏好"]},
]

# 需求文本里出现这些词，就认为用户已经表达过风格偏好，不再反问。
# 判定必须确定性：把"要不要问风格"交给 LLM 判断是不可靠的——需求 204 实测，
# 「做一个个人记账本应用，能记录每天的花费和收入」这种完全没提风格的需求，
# LLM 也直接返回空数组放行，于是风格悄悄由 TL 替用户猜。猜错的代价是整份 UI
# 返工（十几分钟 + 一次人工重来），而问一句是零成本。
STYLE_HINTS = [
    "极简", "简约", "简洁", "暗黑", "暗色", "深色", "浅色", "明亮",
    "科技", "霓虹", "赛博", "活泼", "多彩", "渐变", "暖色", "冷色",
    "清新", "复古", "像素", "卡通", "商务", "拟物", "玻璃", "手绘",
    "无偏好", "随意", "都行", "你决定", "你看着办", "自动选择",
]

VISUAL_STYLE_QUESTION = {
    "id": "visual_style", "type": "radio",
    "label": "你偏好哪种视觉风格？",
    "options": ["极简白 -- 白底灰字，大量留白", "暖柔风格 -- 暖色调圆角卡片（默认）",
                "暗黑科技 -- 深色背景霓虹强调", "活泼多彩 -- 明亮渐变大色块",
                "无偏好，自动选择"],
}


def _requirement_mentions_style(requirement: str) -> bool:
    """需求文本里是否已经表达了视觉风格偏好。"""
    text = (requirement or "").lower()
    return any(hint in text for hint in STYLE_HINTS)


def _ensure_style_question(questions: list) -> list:
    """确保问题列表里有一条视觉风格问题；已有则不重复添加。"""
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        if q.get("id") == "visual_style" or "风格" in str(q.get("label", "")):
            return questions
    return [*(questions or []), VISUAL_STYLE_QUESTION]


CLARIFY_STATUS_ASKED = "asked"      # LLM 给出了问题 → 应当追问
CLARIFY_STATUS_NONE = "none"        # LLM 明确表示无需追问（返回空数组）
CLARIFY_STATUS_FAILED = "failed"    # 调用失败 / 返回不可解析 → **不等于**需求已明确

# 澄清问题生成的最大输出。原值 500 太小：该模型会把每个选项写成
# "基础账本 -- 仅记录金额、日期、收支类型、备注（默认）"这样的长句，
# 两个问题就能把 500 token 撑爆，响应被截断 → JSON 不闭合 → 整个问题列表被丢弃、
# 退化成罐头提问（req 206 实测：模型问出了"是否需要账本分类维度"这种高质量问题，
# 却因为截断被扔掉）。1200 约为其正常输出的 2 倍。
_CLARIFY_MAX_TOKENS = 1200


def _extract_clarify_payload(content: str):
    """从 LLM 响应里提取澄清问题（数组或单个对象）。

    模型在这一点上有三个稳定习惯，都要接住，否则好问题会被白白丢掉：
    1. 把 JSON 包在 ```json ``` 代码围栏里（实测最常见）
    2. 只问一个问题时返回**裸对象**而不是数组
    3. 选项写得长，响应可能被 max_tokens 截断 → JSON 不闭合

    Returns: list / dict / None（None = 确实提取不到）
    """
    if not content:
        return None

    # 控制字符会让 json.loads 直接失败
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content.strip())

    candidates = [raw]
    # 代码围栏内的内容
    for pat in (re.compile(r'```json\s*(.*?)```', re.DOTALL),
                re.compile(r'```\s*(.*?)```', re.DOTALL)):
        m = pat.search(raw)
        if m:
            candidates.append(m.group(1).strip())
    # 最外层数组 / 对象（用 rfind 取最后一个闭合符，避免截掉尾部条目）
    for opener, closer in (('[', ']'), ('{', '}')):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            candidates.append(raw[start:end + 1])

    for text in candidates:
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if data is None:
            try:
                data = try_fix_json(text)   # 截断的半截 JSON
            except Exception:
                data = None
        if isinstance(data, (list, dict)):
            return data
    return None


def _generate_clarify_questions_ex(client, requirement: str) -> tuple[list, str, dict]:
    """生成澄清问题，并区分「问了 / 明确不问 / 压根没问成」三种结果。

    返回 (questions, status, llm_trace)，status 取值见上面的常量。
    `llm_trace` 是产生这些问题的一次 LLM 调用的输入输出（可观测性）：
    此前只落 question_count，后台「这 2 个问题到底是什么、模型原话是什么」
    无从查起。调用异常时 trace 带 error；无调用场景不存在（本函数必有调用）。

    为什么必须区分：此前三种情况一律返回空列表，调用方只能 `if questions:` 判断，
    于是 LLM 一超时/返回散文，需求确认门禁就**静默全开**，需求和改造前一样直接
    进 TL 由它替用户拍板——这正是"用户事后才发现方向错了、整条链路白跑"的成因。
    "没问成"必须能和"不用问"区分开，否则门禁的失效是不可观测的。
    """
    from harness.instructions.prompts import load_prompt_template

    is_detailed = not _is_vague_requirement(requirement)
    detail_hint = (
        "用户的需求已经很详细了，只需要确认 1-2 个最关键的选择（如视觉风格偏好）。"
        if is_detailed else
        "用户的需求比较模糊，请分析缺少哪些关键信息，生成 2-3 个澄清问题帮助明确方向。"
    )

    prompt = load_prompt_template(
        "intent/clarify_generate.md",
        requirement=requirement,
        detail_hint=detail_hint,
    )

    _system = "你是一位产品经理，帮助澄清用户需求。用户已经说过的信息不要再问。"
    _trace_msgs = [
        {"role": "system", "content": _system},
        {"role": "user", "content": prompt},
    ]
    _trace = {"messages": _trace_msgs}

    response = client.chat(
        prompt=prompt,
        system_prompt=_system,
        use_memory=False, max_tokens=_CLARIFY_MAX_TOKENS, timeout=_aux_timeout()
    )
    if response.is_error or not response.content:
        logger.warning(
            f"[TeamLeader] 澄清问题调用失败 (is_error={response.is_error}, "
            f"content_len={len(response.content or '')})"
        )
        _trace["response"] = {"content": "",
                              "error": str(response.error or "empty")}
        return [], CLARIFY_STATUS_FAILED, _trace

    _trace["response"] = {"content": response.content}
    data = _extract_clarify_payload(response.content)

    if isinstance(data, dict):
        # 模型可能用容器包一层：{"questions": [...]}（键名不固定，故按值的形状识别）。
        # 只认"唯一一个值是对象数组"的包装，避免把真正的问题对象误当容器。
        list_vals = [v for v in data.values()
                     if isinstance(v, list) and any(isinstance(x, dict) for x in v)]
        if len(list_vals) == 1 and len(data) <= 3:
            data = list_vals[0]
        else:
            # 只问一个问题时**经常返回裸对象而不是数组**（req 206 实测）。
            # 包一层即可——为这点格式偏差丢掉一个好问题、退化成罐头提问，是净损失。
            data = [data]

    if not isinstance(data, list):
        # 保留原文片段：这类"模型答非所问/被截断"以前完全不可见，只能靠猜
        logger.warning(
            f"[TeamLeader] 澄清问题返回不可解析，按调用失败处理: {response.content[:200]!r}"
        )
        return [], CLARIFY_STATUS_FAILED, _trace

    # 只保留结构合法的条目：残缺条目会让前端渲染出空白选项（比不问更糟）
    questions = [q for q in data if isinstance(q, dict) and (q.get("label") or q.get("question"))]
    if len(questions) != len(data):
        logger.warning(
            f"[TeamLeader] 澄清问题里有 {len(data) - len(questions)} 条结构不合法，已丢弃"
        )
    return questions, (CLARIFY_STATUS_ASKED if questions else CLARIFY_STATUS_NONE), _trace


def _generate_clarify_questions(client, requirement: str) -> list:
    """兼容旧调用方：只要问题列表（失败与"无需追问"都返回空列表）。

    需要区分失败的新调用方请用 _generate_clarify_questions_ex。
    """
    questions, _, _ = _generate_clarify_questions_ex(client, requirement)
    return questions


def _format_plan_summary(plan: dict) -> str:
    """将 TL plan 格式化为用户可见的结构化 Markdown 摘要。

    对话流里这条消息是给人读的，所以按「需求确认」而不是「开发计划」的顺序组织：
    先复述要做什么，再列验收清单，最后才是工程细节。此前把文件结构放在最前面，
    用户第一眼看到的是一堆 .js 文件名，等于把技术细节顶到脸上。
    """
    if not isinstance(plan, dict):
        return "已完成需求分析"

    lines = []

    restated = (plan.get("requirement_restated") or "").strip()
    if restated:
        lines.append(f"**我理解你要做的是**：{restated}\n")

    features = plan.get("features", [])
    if features:
        lines.append("### 功能清单")
        for f in features:
            lines.append(f"- ✅ {f}")
        lines.append("")

    acceptance = plan.get("acceptance_criteria", [])
    if acceptance:
        lines.append("### 做完后我会逐条检查")
        for ac in acceptance:
            if not isinstance(ac, dict):
                continue
            ac_id = ac.get("id", "?")
            ac_label = ac.get("label", "")
            verify = ac.get("how_to_verify", "")
            lines.append(f"- **{ac_id}** {ac_label}：{verify}")
        lines.append("")

    assumptions = plan.get("assumptions", [])
    if assumptions:
        lines.append("### 我替你定的默认设置")
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines) if lines else "已完成需求分析"


def _extract_plan_metadata(plan: dict) -> dict:
    """从 plan 中提取关键元数据（供程序使用，前端可选渲染）。

    只保留下游真正会读的字段。visual_direction / layout_structure /
    key_interactions / implementation_notes / data_model 此前写满了、
    传了一路、然后被 `_compact_plan_text` 在第 2 轮压掉——纯损耗，已移除。
    """
    if not isinstance(plan, dict):
        return {}
    return {
        "requirement_restated": plan.get("requirement_restated", ""),
        "features": plan.get("features", []),
        "assumptions": plan.get("assumptions", []),
        "acceptance_criteria": plan.get("acceptance_criteria", []),
        "file_structure": plan.get("file_structure", []),
        "tech_stack": plan.get("tech_stack", {}),
        "implementation_order": plan.get("implementation_order", []),
        "tasks": plan.get("tasks", []),
        "complexity": plan.get("complexity", "S"),
    }


def _recover_previous_plan(dialogue_history: list) -> dict | None:
    """从对话历史里回捞上一版 plan（供「增量修订」使用）。

    用户在确认卡片上写了修改意见时，服务层会清掉 checkpoint 并重跑 TL
    ——plan 确实必须变，这一步省不掉。但对话历史是持久的：TL 那条消息的
    `plan` 字段里就存着上一版计划。回捞它，TL 才能做「增量修订」而不是
    「从零重新分析」；后者会把用户已经认可的部分（技术栈、文件结构）
    也一并改掉，用户看到的是「我只说加个按钮，怎么整个计划都变了」。
    """
    for msg in reversed(dialogue_history or []):
        if not isinstance(msg, dict):
            continue
        plan = msg.get("plan")
        if isinstance(plan, dict) and plan.get("features"):
            return plan
    return None


def _render_previous_plan_section(prev_plan: dict) -> str:
    """把上一版 plan 渲染成「增量修订」指令段。"""
    lines = [
        "## 上一版计划（用户已看过，并提出修改意见）",
        "",
        "用户不是要你推倒重来，而是在这份计划上提了一条修改意见。",
        "**除反馈明确涉及的部分外，其余内容（技术栈、文件结构、已有功能、验收条件）",
        "必须与上一版保持一致**——擅自改掉用户没提的部分，用户会认为你没听懂他的话。",
        "",
    ]

    restated = (prev_plan.get("requirement_restated") or "").strip()
    if restated:
        lines.append(f"- 需求复述：{restated}")

    tech = prev_plan.get("tech_stack")
    if isinstance(tech, dict) and tech:
        lines.append(
            "- 技术栈：" + "、".join(f"{k}={v}" for k, v in tech.items() if v)
        )

    features = [f for f in (prev_plan.get("features") or []) if isinstance(f, str)]
    if features:
        lines.append("- 功能清单：")
        lines.extend(f"  - {f}" for f in features)

    files = [f for f in (prev_plan.get("file_structure") or []) if isinstance(f, str)]
    if files:
        lines.append("- 文件结构：" + "、".join(files))

    # AC 必须整条给全（anchor / feature 一并渲染）。
    # 少给这两项会同时踩两个坑：
    #   1) `feature` 要与 features 逐字一致才有意义（_validate_feature_coverage 硬校验），
    #      模型看不到原值只能重新措辞 → 校验失败 → 白跑一轮重试；
    #   2) `anchor` 是验收脚本唯一的外部参照，而「用户只提一条修改」正是最该稳住它的
    #      场景。上面刚写完「验收条件必须与上一版保持一致」却把 anchor 藏起来，
    #      等于让模型自己重新发明参照物 —— 指令与事实互相矛盾。
    acs = prev_plan.get("acceptance_criteria") or []
    ac_lines = []
    for ac in acs:
        if not isinstance(ac, dict):
            continue
        ac_lines.append(f"  - {ac.get('id', '?')} {ac.get('label', '')}")
        feature = str(ac.get("feature") or "").strip()
        if feature:
            ac_lines.append(f"    - 覆盖功能（须与上方功能清单逐字一致）：{feature}")
        anchor = str(ac.get("anchor") or "").strip()
        if anchor:
            ac_lines.append(f"    - 页面位置（anchor，未受影响的须原样保留）：{anchor}")
        how = str(ac.get("how_to_verify") or "").strip()
        if how:
            ac_lines.append(f"    - 验证步骤：{how}")
    if ac_lines:
        lines.append("- 验收条件（保留未受影响的，按反馈增删）：")
        lines.extend(ac_lines)

    lines.append("")
    lines.append("请输出修订后的**完整**计划 JSON（结构同上，不是补丁片段）。")
    return "\n".join(lines)


def team_leader_node(state: AgentState) -> Dict[str, Any]:
    """TeamLeader 节点：需求分析 → 结构化 Plan

    澄清由上游 IntentRouter 统一处理（进入此节点前 intent 已固定为 'task'）。
    """
    requirement = state['requirement_content']

    # ---- 需求确认门禁：未经用户事先确认的选择，不让 TL 替用户做决定 ----
    # 此前的触发条件是"输入过短（<8 字符）"，等于这道门几乎从不上锁：
    # "帮我做一个个人记账本应用"（12 字）会直接放行给 TL，视觉风格、数据落地
    # 方式全靠 TL 猜；猜错的结果不是重出一版计划，而是整条编码链路白跑
    # ——所以门禁改成"只要还没问过，就先问一次"。
    #
    # 门是全开还是拦一下由 LLM 判断：需求里已经写明风格/数据方案的，
    # 它会返回空数组直接放行，不给用户添没有意义的必答题。
    # `already_clarified` 是死循环的守门栓。两个标记都要认，缺一会出现
    # "用户明明只是提了条修改意见，却被同一个问题问第二遍"：
    # [用户补充说明] 来自澄清表单回填；[用户反馈] 来自 plan 确认时的反馈
    # （那条路径会清 checkpoint 重跑 TL，此时需求里没有任何补充说明标记）。
    already_clarified = (
        '[用户补充说明]' in requirement or '[用户反馈]' in requirement
    )
    if not already_clarified:
        # 低于此值连"要做什么"都读不出来（"帮我"、"一个"），必须拦住
        MIN_REQUIREMENT_CHARS = 8
        try:
            client = get_client()
            questions, clarify_status, clarify_trace = \
                _generate_clarify_questions_ex(client, requirement)
        except Exception as e:
            logger.warning(f"[TeamLeader] 生成澄清问题失败: {e}")
            questions, clarify_status, clarify_trace = [], CLARIFY_STATUS_FAILED, {}

        if clarify_status == CLARIFY_STATUS_FAILED:
            # 调用失败 ≠ 需求明确。失败时用兜底问题守住门禁——宁可多问一句，
            # 也不要让 TL 在没有任何用户输入的情况下替用户拍板视觉风格与数据方案。
            # 用户确实不想答可以直接跳过（question_form 支持 _skip）。
            questions = FALLBACK_CLARIFY_QUESTIONS
            clarify_reason = 'clarify_unavailable'
        elif not questions and len(requirement.strip()) < MIN_REQUIREMENT_CHARS:
            # LLM 没问出东西，但输入短到连"要做什么"都读不出来
            # （"帮我"、"一个"这类），此时必须拦住，不能交给 TL 去编。
            questions = FALLBACK_CLARIFY_QUESTIONS
            clarify_reason = 'input_too_short'
        else:
            clarify_reason = 'not_confirmed_by_user'

        # 视觉风格：确定性兜底。无论 LLM 是否问出东西，只要用户没表达过风格偏好，
        # 这条必须问到——它是"事后才发现方向错了"最常见也最便宜的一个来源。
        llm_asked = bool(questions)
        if not _requirement_mentions_style(requirement):
            merged = _ensure_style_question(questions)
            if len(merged) != len(questions or []):
                questions = merged
                # 只有"LLM 什么都没问、全靠这条兜底"时才归因给风格；
                # LLM 本来就问了别的问题时，归因保持 not_confirmed_by_user。
                if not llm_asked:
                    clarify_reason = 'style_not_confirmed'

        # LLM 判定"需求已经足够明确，无需追问"时放行，不再无谓卡一道
        if questions:
            logger.info(
                f"[TeamLeader] 需求未经确认，先澄清 "
                f"({len(questions)} 个问题) 再生成 plan"
            )
            question_form = {'questions': questions}
            # 里程碑：需求被拦下澄清。运营后台时间线上要能直接看出
            # 「需求走到哪一步停的、为什么停」，而不是只看到一串 LLM 调用。
            # questions 完整列表落 meta（此前只有 question_count，问题正文
            # 查不到）；llm_trace 是生成这些问题的一次 LLM 调用，输入输出
            # 一并落库 —— 是 LLM 问的还是兜底罐头问题，点开即见。
            _record_milestone(
                state, KIND_CLARIFY,
                f"需求待澄清 · {len(questions)} 个问题",
                meta={"reason": clarify_reason,
                      "question_count": len(questions),
                      "questions": questions[:6]},
                messages=clarify_trace.get("messages"),
                response=clarify_trace.get("response"),
            )
            return {
                'plan': {},
                'current_step': 'needs_clarification',
                'dialogue_history': [{
                    'role': 'agent', 'name': 'Leon（负责人）',
                    'content': (
                        f"收到，开工前有 {len(questions)} 件事想跟你确认一下，"
                        f"免得做完才发现方向不对："
                    ),
                    'status': 'needs_clarification',
                    'question_form': question_form,
                    'timestamp': _ts(),
                }],
                'metadata': {
                    **state.get('metadata', {}),
                    'team_leader_success': False,
                    'needs_clarification_reason': clarify_reason,
                    'question_form': question_form,
                },
            }
        logger.info("[TeamLeader] 澄清判定：需求已足够明确，直接进入需求分析")

    try:
        client = get_client()
        from harness.constraints.environment_contract import render_environment_contract
        from harness.constraints.plan_validator import validate_plan, build_plan_retry_feedback
        system_prompt = load_prompt_template(
            "coding/tl_analysis.md",
            environment_contract=render_environment_contract(),
        )
        user_prompt = f"请分析以下需求并生成开发计划：\n\n{requirement}"

        # 增量修订：需求里带 [用户反馈] 说明用户在上一版 plan 上提了修改意见。
        # 把上一版 plan 一并交给 TL，要求只改反馈涉及的部分——否则 TL 从零重分析，
        # 会把用户已经认可的决策也一并改掉（"我只说加个按钮，计划全变了"）。
        if '[用户反馈]' in requirement:
            prev_plan = _recover_previous_plan(state.get('dialogue_history') or [])
            if prev_plan:
                logger.info("[TeamLeader] 检测到用户反馈，按增量修订处理（附上一版 plan）")
                user_prompt += "\n\n---\n\n" + _render_previous_plan_section(prev_plan)
            else:
                logger.info("[TeamLeader] 检测到用户反馈，但未回捞到上一版 plan，按全新需求处理")

        # L0: 前置检测 + 分层容错
        # 策略：检测截断 → 重试(最多2次) → 降级修复 → 完整性校验 → DoD 程序化校验

        def _fetch_and_extract(max_tokens: int, prompt_override: str = None) -> tuple[dict | None, bool, object]:
            """获取响应并提取 JSON，返回 (plan, is_truncated, resp)"""
            _t0 = time.time()
            resp = client.chat(
                prompt=prompt_override or user_prompt,
                system_prompt=system_prompt,
                use_memory=False, max_tokens=max_tokens, timeout=_plan_timeout(),
                thinking='enabled',  # 结构化 plan JSON 需要思考模式保证格式正确
            )
            # 规划阶段的调用此前完全没被记录 —— 运营后台的时间线一开始就是编码
            # 阶段，看不出「需求是怎么被翻译成计划的」，规划耗时也无从统计。
            _log_llm_turn_safe(
                state.get("requirement_id"), None, client, system_prompt,
                prompt_override or user_prompt, resp, thinking='enabled',
                latency_ms=round((time.time() - _t0) * 1000, 1),
                stage=STAGE_PLANNING,
                turn_index=int(_md(state).get("turn_index") or 0),
                trace_id=_md(state).get("trace_id") or None,
            )
            if resp.is_error:
                return None, False, resp
            is_truncated = _detect_truncation(resp.content)
            plan = _extract_json_from_llm_response(resp.content)
            return plan, is_truncated, resp

        # 第1次请求
        plan, is_truncated, response = _fetch_and_extract(6000)

        # L1: 优先重试（最多2次）
        retry_count = 0
        max_retries = 2
        retry_tokens = [8000, 10000]
        while (response.finish_reason == "length" or is_truncated) and retry_count < max_retries:
            current_tokens = retry_tokens[retry_count]
            logger.warning(
                f"[TeamLeader] 检测到截断 (finish_reason={response.finish_reason}, is_truncated={is_truncated})，"
                f"第 {retry_count + 1}/{max_retries} 次重试，max_tokens={current_tokens}"
            )
            plan, is_truncated, response = _fetch_and_extract(current_tokens)
            if plan is not None and not is_truncated:
                logger.info(f"[TeamLeader] 重试成功，提取到完整 JSON")
                break
            retry_count += 1

        # L2: 降级修复（仅在重试失败时）
        if plan is None:
            logger.warning("[TeamLeader] 所有重试均失败，尝试降级修复")
            # 尝试用 try_fix_json 修复最后一次响应
            fixed = try_fix_json(response.content) if response else None
            if fixed:
                plan = fixed
                logger.warning("[TeamLeader] 降级修复成功，但数据可能不完整")

        # L3: 完整性校验
        if plan is not None:
            required_fields = ['features', 'file_structure', 'tasks']
            missing_fields = [f for f in required_fields if not plan.get(f)]
            if missing_fields:
                logger.error(f"[TeamLeader] 数据完整性校验失败，缺失字段: {missing_fields}")
                plan = None

        if plan is None:
            # 诊断信息
            truncation_hint = ""
            if response and response.finish_reason == "length":
                truncation_hint = (
                    "（提示：LLM 返回 finish_reason='length'，响应可能被 max_tokens 截断，"
                    f"已尝试 {max_retries} 次重试仍然失败）"
                )
            content_tail = response.content[-300:] if response and response.content else ""
            raise Exception(
                f"无法从 LLM 响应中提取 JSON{truncation_hint}\n"
                f"响应前200字符: {(response.content[:200] if response else '')}\n"
                f"响应后300字符: {content_tail}\n"
                f"响应总长度: {len(response.content) if response else 0} 字符"
            )

        # L4: DoD 程序化校验（机器可校验的完成定义）。
        # 不合格打回 TL 重出最多 1 次；仍不合格则带病放行但记录问题——
        # 让下游知道哪些 AC 是弱 AC，避免静默放水。
        plan_ok, plan_issues = validate_plan(plan)
        if not plan_ok:
            logger.warning(
                f"[TeamLeader] plan 未通过 DoD 校验 ({len(plan_issues)} 个问题)，打回重出 1 次: "
                f"{plan_issues[:3]}"
            )
            retry_prompt = (
                user_prompt
                + "\n\n---\n\n"
                + build_plan_retry_feedback(plan_issues)
            )
            retry_plan, _, retry_resp = _fetch_and_extract(8000, prompt_override=retry_prompt)
            if retry_plan is not None:
                ok2, issues2 = validate_plan(retry_plan)
                if ok2 or len(issues2) < len(plan_issues):
                    plan = retry_plan
                    plan_ok, plan_issues = ok2, issues2
        if not plan_ok:
            logger.warning(
                f"[TeamLeader] plan 带病放行（DoD 校验仍失败）: {plan_issues}"
            )

        visual_style = state.get('visual_style', '') or \
            state.get('metadata', {}).get('visual_style', '')

        # 提取复杂度评级（默认 standard）
        complexity = plan.get('complexity', 'standard') if isinstance(plan, dict) else 'standard'
        if complexity not in ('simple', 'standard'):
            complexity = 'standard'

        tasks = plan.get('tasks', []) if isinstance(plan, dict) else []
        interfaces = plan.get('interfaces', {}) if isinstance(plan, dict) else {}
        impl_order = plan.get('implementation_order', []) if isinstance(plan, dict) else []

        # 把 plan 数据编码到 dialogue 消息中（持久化到 DB，页面刷新后可用）
        tl_plan_data = {
            'requirement_restated': plan.get('requirement_restated', ''),
            'features': plan.get('features', []),
            'assumptions': plan.get('assumptions', []),
            'acceptance_criteria': plan.get('acceptance_criteria', []),
            'file_structure': plan.get('file_structure', []),
            'tech_stack': plan.get('tech_stack', {}),
            'implementation_order': impl_order,
            'tasks': tasks,
            'complexity': complexity,
        } if isinstance(plan, dict) else {}

        # DoD 校验结果挂在 plan 上而非只写 metadata：plan 是唯一确定会被持久、
        # 跨 checkpoint 传递并进到 verify 的载体。此前写进 metadata 后全库无人
        # 读取——等于"发现了问题，但没有任何人知道"，带病放行和没校验一样。
        if plan_issues:
            plan['_plan_dod_issues'] = plan_issues

        # 里程碑：规划产出。meta 带上 feature / AC 数与复杂度，
        # 时间线上直接能看出「这一版计划给的是什么量级的活、AC 是否带病放行」。
        _ac_list = plan.get('acceptance_criteria', []) if isinstance(plan, dict) else []
        _feat_list = plan.get('features', []) if isinstance(plan, dict) else []
        # 明细随结论落 meta：只有「5 个功能 / 5 条验收」两个计数，排查时
        # 「这一版计划到底规划了什么、AC 写了什么」还得去翻对话流。
        _ac_detail = [
            f"{a.get('id', '?')} {a.get('label', '')}"
            + (f"（{a.get('how_to_verify', '')}）" if a.get('how_to_verify') else "")
            for a in (_ac_list if isinstance(_ac_list, list) else [])
            if isinstance(a, dict)
        ]
        _record_milestone(
            state, KIND_PLAN,
            f"规划完成 · {len(_feat_list)} 个功能 / {len(_ac_list)} 条验收",
            status="ok" if plan_ok else "warning",
            meta={"features": len(_feat_list), "ac_count": len(_ac_list),
                  "complexity": complexity,
                  "dod_issues": len(plan_issues or []),
                  "feature_list": [str(f) for f in
                                   (_feat_list if isinstance(_feat_list, list) else [])][:10],
                  "ac_list": _ac_detail[:10],
                  "plan_issues": [str(x) for x in (plan_issues or [])][:10]},
        )

        return {
            'plan': plan,
            'current_step': 'team_leader_done',
            'dialogue_history': [{
                'role': 'agent', 'name': TL_NAME,
                'content': _format_plan_summary(plan),
                'status': 'completed',
                'plan': {
                    **tl_plan_data,
                    **_extract_plan_metadata(plan),
                },
                'preserve': True,
                'timestamp': _ts(),
            }],
            'metadata': {
                **state.get('metadata', {}),
                'team_leader_success': True,
                'visual_style': visual_style,
                'complexity': complexity,
                # DoD 校验结果：带病放行时记录弱 AC 清单，供 verify/交付门禁参考
                'plan_dod_issues': plan_issues if not plan_ok else [],
            },
            'tasks': tasks,
            'interfaces': interfaces,
            'implementation_order': impl_order,
        }

    except Exception as e:
        logger.error(f"[TeamLeader] 执行失败：{e}")
        _record_milestone(
            state, KIND_PLAN, f"规划失败 · {str(e)[:80]}", status="error",
            meta={"error": str(e)[:200]},
        )
        return {
            'plan': {},
            'current_step': 'team_leader_failed',
            'error': f"TeamLeader 失败：{e}",
            'dialogue_history': [{
                'role': 'agent', 'name': TL_NAME,
                'content': f"分析失败: {requirement[:50]}...",
                'status': 'failed',
                'timestamp': _ts(),
            }],
            'metadata': {**state.get('metadata', {}), 'team_leader_success': False}
        }


def tool_coder_node(state: AgentState) -> Dict[str, Any]:
    """
    FrontendEngineer 节点：内部执行完整的 ToolCallLoop

    不再依赖 LangGraph 的迭代机制，而是在本节点内一次性跑完所有工具调用。
    工作流简化为 team_leader → END，消除死循环。
    """
    tool_loop = state.get('metadata', {}).get('_tool_loop')
    if not tool_loop:
        return {
            'current_step': 'done',
            'error': 'ToolCallLoop 未注入到 state',
        }

    state['current_step'] = 'generating'

    try:
        final_state = tool_loop.run(state)
        final_state['current_step'] = 'done'
        return final_state
    except Exception as e:
        logger.error(f"[ToolCoder] 执行失败：{e}")
        return {
            'current_step': 'done',
            'error': f"代码生成失败：{e}",
            'dialogue_history': state.get('dialogue_history', []) + [{
                'role': 'agent', 'name': DEV_NAME,
                'content': f'生成过程出错: {e}',
                'status': 'failed',
                'timestamp': _ts(),
            }],
        }


def _execute_delegated_tasks(state: AgentState) -> Dict[str, Any]:
    """Agent 委派：按 TaskType 选择执行策略

    遍历 plan.tasks（按 implementation_order），根据 type 字段：
    - research: 单次 LLM 调用（client.chat()），结果注入 dialogue_history
    - code: ToolCallLoop 完整流程
    - review: 对已完成文件做审查

    未指定 type 时默认 code（向后兼容）。
    """
    from harness.state.agent_state import TaskType

    tasks = state.get("tasks") or []
    impl_order = state.get("implementation_order") or []
    all_code_files = list(state.get("code_files") or [])

    tool_loop = get_tool_loop(state)
    if not tool_loop:
        return {"current_step": "error", "error": "ToolCallLoop 未注入到 state"}

    workspace = get_workspace(state) or tool_loop.workspace
    requirement = state.get("requirement_content", "")

    for task in tasks:
        task_type = task.get("type", "code")
        task_file = task.get("file", "")
        task_desc = task.get("description", "")

        if task_type == TaskType.RESEARCH.value:
            # ---- Research: 轻量 LLM 调用，不分配工具权限 ----
            logger.info(f"[AgentDelegate] 执行 research 任务: {task_desc[:80]}")
            from llm.client import get_client
            client = get_client()
            resp = client.chat(
                prompt=f"请研究以下问题并给出简洁回答：\n\n{task_desc}\n\n"
                       f"上下文需求：{requirement[:500]}",
                max_tokens=2000,
                timeout=_aux_timeout(),
            )
            research_result = resp.content if resp and resp.content else ""
            # 将 research 结果注入 dialogue_history 供后续任务参考
            state.setdefault("dialogue_history", []).append({
                "role": "system",
                "name": "Research",
                "content": f"[Research 结果] {task_desc}:\n{research_result}",
                "preserve": True,
            })
            state.setdefault("role_outputs", {})[f"research_{task_desc[:30]}"] = research_result

        elif task_type == TaskType.REVIEW.value:
            # ---- Review: 对已完成文件做审查 ----
            logger.info(f"[AgentDelegate] 执行 review 任务: {task_file}")
            review_result = _review_single_file(workspace, task_file, state)
            # 注入审查结果
            state.setdefault("dialogue_history", []).append({
                "role": "system",
                "name": "Review",
                "content": f"[Review 结果] {task_file}:\n{review_result}",
                "preserve": True,
            })

        else:
            # ---- Code (默认): ToolCallLoop 完整流程 ----
            logger.info(f"[AgentDelegate] 执行 code 任务: {task_file}")
            state["current_step"] = "generating"
            result = tool_loop.run(state)
            if result.get("code_files"):
                all_code_files.extend(result["code_files"])
            # 更新 completed_files 摘要供后续任务参考
            existing = workspace.list()
            if existing:
                state.setdefault("dialogue_history", []).append({
                    "role": "system",
                    "name": "System",
                    "content": f"[已完成文件] {', '.join(existing)}",
                    "preserve": True,
                })

    # 编码结论的里程碑统一由 coder_node 的包装层落（见 _emit_coding_milestone）：
    # 委派与批量两条路径都从那里返回，集中一处才不会漏、也不会同一轮记两条。
    return {
        "current_step": "coding_done",
        "code_files": all_code_files,
    }


def _review_single_file(workspace, filename: str, state: AgentState) -> str:
    """审查单个文件，返回审查结果文本"""
    try:
        content = workspace.read(filename)
    except Exception as e:
        return f"无法读取文件 {filename}: {e}"

    # 使用 LLM 进行审查
    from llm.client import get_client
    client = get_client()
    prompt = (
        f"请审查以下文件代码，指出潜在问题（语法错误、逻辑缺陷、安全漏洞、"
        f"最佳实践违规）：\n\n文件: {filename}\n```\n{content[:8000]}\n```\n\n"
        f"请以简洁的方式列出发现的问题。如果没有问题，请回复\"LGTM\"。"
    )
    try:
        resp = client.chat(prompt=prompt, max_tokens=1000, timeout=_aux_timeout())
        return resp.content if resp and resp.content else "审查未返回结果"
    except Exception as e:
        return f"审查异常: {e}"


def _emit_coding_milestone(state: AgentState, result) -> None:
    """把这一次编码的结论落成一条里程碑事件。

    埋在包装层而不是各个 return 前面：coder 有多条返回路径（未注入 ToolCallLoop /
    委派失败 / 批量抛异常 / 正常收尾），逐条埋必漏一条 —— 而漏掉的恰好是
    「编码到底产出了几个文件」（实测：第一次端到端跑完，时间线上 13 类事件
    只缺 coding，就是被漏在这里）。

    文件数**以工作区实际产物为准**，不取 `result["code_files"]`：批量路径下该字段
    常是空的（实测需求 215：工作区里 4 个交付文件，事件却写 file_count=0）。
    日志里的数字必须是在磁盘上数出来的，否则排查时会被它带偏。
    """
    step = result.get("current_step") if isinstance(result, dict) else None
    err = result.get("error") if isinstance(result, dict) else None
    raw = (result.get("code_files") or []) if isinstance(result, dict) else []
    files = [str(f.get("filename") if isinstance(f, dict) else f) for f in raw]
    files = _delivered_files(state) or files

    if step == "coding_done" and not err:
        _record_milestone(state, KIND_CODING,
                          f"编码完成 · {len(files)} 个文件",
                          meta={"file_count": len(files), "files": files[:20]})
    elif err or step in ("coding_error", "llm_error", "error"):
        _record_milestone(state, KIND_CODING,
                          f"编码失败 · {str(err or step)[:80]}",
                          status="error",
                          meta={"error": str(err or step)[:200],
                                "file_count": len(files)})
    else:
        # 既没宣告完成也没报错（如 max_iterations / no_progress 提前收尾）：
        # 如实记下状态，不假装成功 —— 这条恰恰是最需要人来看的。
        # 带上已有文件数：能让「跑了 10 轮一个文件没写」和「写了 4 个但没写完」
        # 一眼分开，前者是空转事故，后者只是预算不够。
        _record_milestone(state, KIND_CODING,
                          f"编码收尾 · {step or '未知状态'} · {len(files)} 个文件",
                          meta={"file_count": len(files), "files": files[:20]})


def _delivered_files(state: AgentState) -> list:
    """工作区里的实际交付文件（排除 `.task/**`、`.design/**` 这些过程文件）。

    任何异常都返回空列表 —— 埋点辅助函数不得影响主流程。
    """
    try:
        ws = get_workspace(state)
        if ws is None:
            return []
        return [str(f) for f in ws.list()
                if not str(f).startswith((".task/", ".design/"))]
    except Exception:
        return []


def coder_node(state: AgentState) -> Dict[str, Any]:
    """编码节点入口：跑实现体，再把本次编码的结论落成里程碑（见上）。"""
    result = _coder_node_impl(state)
    try:
        _emit_coding_milestone(state, result)
    except Exception as e:
        logger.debug(f"[Coder] 编码里程碑写入失败（不阻断）：{e}")
    return result


def _coder_node_impl(state: AgentState) -> Dict[str, Any]:
    """
    统一编码节点：内部根据 complexity 选择策略

    - simple:  直接 ToolCallLoop（极简，5 轮上限）
    - standard: ToolCallLoop + CompletionContract（完整流程）
    """
    complexity = state.get("metadata", {}).get("complexity", "standard")
    has_tasks = bool(state.get("implementation_order"))

    # ---- Agent 委派：检查是否有混合类型的子任务 ----
    tasks = state.get("tasks") or []
    has_typed_tasks = any(
        isinstance(t, dict) and t.get("type") and t.get("type") != "code"
        for t in tasks
    )

    if has_typed_tasks and has_tasks:
        try:
            return _execute_delegated_tasks(state)
        except Exception as e:
            logger.error(f"[Coder] 委派任务执行失败：{e}")
            return {"current_step": "coding_error", "error": str(e)}

    # ---- Batch-First: 统一批量编码 + 定向补全 ----
    tool_loop = get_tool_loop(state)
    if not tool_loop:
        return {
            "current_step": "error",
            "error": "ToolCallLoop 未注入到 state",
        }
    state.setdefault("metadata", {})["coder_name"] = DEV_NAME
    state["metadata"]["thinking_name"] = DEV_NAME
    # 关闭 coder 思考模式（ToolCallLoop 读取 metadata["tool_thinking"]）：
    # 实测写码大轮 reasoning tokens 占 completion 60-75%（req 156：10610 token
    # 中 6877 为思考），是编码阶段耗时的最大单因素。Phase 2 定向补全复用同一
    # metadata，一并关闭。TeamLeader/verify 不设置此键，维持默认 enabled。
    # ⚠️ config.py 有 effort=low 的 A/B 负信号记录（贪吃蛇 1 个运行时错误），
    # 全关比 effort=low 更激进——上线后需跑 eval 集对照通过率；回退 = 删除本行。
    state["metadata"]["tool_thinking"] = "disabled"

    # CompletionContract：standard 复杂度使用，simple 跳过
    if complexity == "standard":
        from harness.constraints.completion_contract import CompletionContract
        impl_order = state.get("implementation_order") or []
        if impl_order:
            workspace = get_workspace(state)
            if not workspace:
                workspace = tool_loop.workspace
            plan = state.get("plan") or {}
            acs = plan.get("acceptance_criteria") or [] if isinstance(plan, dict) else []
            contract = CompletionContract(workspace)
            if contract.exists():
                contract.initialize_incremental(impl_order, acceptance_criteria=acs)
            else:
                contract.initialize(impl_order, acceptance_criteria=acs)
            state["_completion_contract"] = contract
            state.setdefault("metadata", {})["_completion_contract"] = contract

            # ---- SSE 编码进度（1/2）：推送目标文件清单给前端 TaskPanel ----
            # 前端已支持 task_list/task_update 事件（TaskPanel.vue），此前后端从未
            # 推送过。文件状态按 contract 现状初始化，修复循环重入时不回退已亮进度。
            try:
                sse = getattr(tool_loop, "sse", None)
                req_id = (state.get("metadata", {}).get("requirement_id")
                          or state.get("requirement_id"))
                if sse is not None and req_id and impl_order:
                    plan_tasks = (plan.get("tasks") if isinstance(plan, dict) else None) or []
                    desc_by_file = {
                        t.get("file"): str(t.get("description", ""))[:60]
                        for t in plan_tasks if isinstance(t, dict)
                    }
                    sse.task_list(req_id, [
                        {
                            "file": f,
                            "description": desc_by_file.get(f, ""),
                            "status": "completed" if contract.is_created(f) else "pending",
                        }
                        for f in impl_order
                    ])
            except Exception as e:
                logger.debug(f"[Coder] task_list SSE 推送失败（不阻断）: {e}")

    # 注入 Hook 失败历史（去重：同一摘要只注入一次，避免 verify→coder
    # 修复循环多轮重入时重复累积相同内容、无谓膨胀上下文）
    hook_failures = state.get("hook_failures", {})
    if hook_failures:
        failure_lines = []
        for hook_name, count in hook_failures.items():
            if count > 0:
                failure_lines.append(f"- {hook_name}: 失败 {count} 次")
        if failure_lines:
            failure_summary = "\n".join(failure_lines)
            dialogue = state.setdefault("dialogue_history", [])
            already_injected = any(
                isinstance(m, dict) and m.get("_hook_failure_summary") == failure_summary
                for m in dialogue
            )
            if not already_injected:
                dialogue.append({
                    "role": "user",
                    "name": "System",
                    "content": (
                        "## 历史验证失败记录（请注意避免）\n"
                        + failure_summary
                        + "\n\n请在编码时特别注意以上问题，避免重复出现。"
                    ),
                    "_hook_failure_summary": failure_summary,  # 注入去重标记（hidden 前哨）
                })

    logger.info(f"[Coder] Phase 1: 启动 {complexity} 批量编码")

    # Phase 1: 批量编码
    try:
        result = tool_loop.run(state)
        if result.get("current_step") == "task_complete":
            next_step = "coding_done"
        elif result.get("current_step") == "llm_error":
            next_step = "llm_error"
        elif result.get("error"):
            next_step = "coding_error"
        else:
            next_step = result.get("current_step", "done")
    except Exception as e:
        logger.error(f"[Coder] Phase 1 执行失败：{e}")
        return {"current_step": "coding_error", "error": str(e)}

    # Phase 2: 定向补全
    # 触发条件不再要求 next_step == "coding_done"。批量编码空转结束
    # （max_iterations / no_progress）恰恰是文件缺失最严重的时候，此前这条兜底
    # 被条件挡在外面：需求 182 跑满 9 轮只读不写、4 个文件缺失，补全一次都没执行。
    # llm_error 表示模型不可用，逐文件补全同样会失败，直接跳过避免空耗。
    if complexity == "standard" and next_step != "llm_error":
        impl_order = state.get("implementation_order") or []
        if impl_order:
            workspace = get_workspace(state)
            if not workspace:
                workspace = tool_loop.workspace
            existing = set(workspace.list())
            missing = [
                f for f in impl_order
                if f not in existing
                and not any(e.endswith(f.split("/")[-1]) for e in existing)
            ]
            if missing:
                logger.warning(
                    f"[Coder] Phase 2: 批量编码遗漏 {len(missing)} 个文件: {missing}"
                )
                try:
                    from harness.instructions.file_coder import targeted_recovery
                    recovery_result = targeted_recovery(state, tool_loop, missing)
                    # 合并 recovery 的错误
                    if recovery_result.get("code_errors"):
                        state.setdefault("code_errors", []).extend(
                            recovery_result["code_errors"]
                        )
                except Exception as e:
                    logger.error(f"[Coder] Phase 2 定向补全失败：{e}")

    return {
        "current_step": next_step,
        "code_files": result.get("code_files", []),
        "error": result.get("error", "") if not result.get("llm_error") else result.get("error", "LLM call failed"),
        "hook_failures": result.get("hook_failures", {}),
    }


def repair_node(state: AgentState) -> Dict[str, Any]:
    """[DEPRECATED v5] QA 反馈注入已移至 verify_node，graph 不再调用此节点。

    保留此函数仅为向后兼容。v5 中 verify_node 直接将 QA findings 写入
    dialogue_history，然后 graph 路由 verify → coder（不再是 verify → repair → coder）。

    这消除了独立的 repair 节点带来的上下文重置问题：
    - coder 保持连续上下文（不会丢失之前的编码记忆）
    - coder 拥有完整工具权限（不受 MAX_ITERATIONS=8 限制）
    - coder 可以自主决定修复策略（edit_file 或 write_file）
    """
    logger.warning("[Repair] v5 中此节点不再被 graph 调用，QA 反馈由 verify_node 直接注入 dialogue_history")
    return {"current_step": "repair_done"}


# ==================== Verify 辅助：AC → Playwright 脚本翻译 ====================


# AC 脚本生成器版本：当「翻译器产出脚本的语义」发生**不兼容变更**时递增，
# 旧缓存随之自动作废并重译。
#
# 为什么必须有：修好翻译器/动作语义后，老需求仍会命中旧缓存继续用旧脚本 ——
# 用户看到的现象是「你说修了，可我的验收结论一点没变」。req 199 的 AC-1/AC-2
# 假 critical 就是旧脚本留下的（那一版 click 还没有「落点」概念，只能点元素中心，
# 多点交互全部失效）；不递增版本号，修好的翻译器对新触发的验收依然不起作用。
#
# v1 → v2：click 支持 at 比例坐标 / offset 像素落点；选择器提示纳入
#          JS 动态生成与 CSS 中定义的类名（req 199）。
# v2 → v3：每条 AC 附 anchor（页面语义区域），脚本定位优先用可见文案而非
#          代码选择器。老缓存里的脚本是「照着代码选择器出的题」，留着就是
#          让自证循环继续生效 —— 必须整批作废。
# v3 → v4：定位材从「anchor 区域描述 + id/class 清单」扩到**确定性提取的可见文案**
#          （按钮名/标题/placeholder/JS 文本赋值），脚本开始出现 text= / :has-text()。
#          v3 老缓存里的脚本 100% 是 id/class 定位（实测 0/55）——不递增的话，
#          这项修复对已跑过的需求毫无作用，又变成"你说修了但结论没变"。
AC_SCRIPT_SCHEMA_VERSION = 4


def _render_signature(code_text: str) -> str:
    """从代码文本确定性检测渲染方式，返回 "canvas" / "dom"。

    这是 AC 断言选型的唯一依据（req 147 复盘）：翻译模板曾把「游戏」写死为 canvas 断言，
    导致 DOM 实现的游戏三条 AC 永久「不适用」。选型必须由 harness 做确定性判断，
    不能交给 LLM 猜。
    """
    import re as _re
    if not code_text:
        return "dom"
    canvas_hits = len(_re.findall(r'<canvas', code_text, _re.I)) + \
        len(_re.findall(r'createElement\(\s*[\'"]canvas', code_text, _re.I))
    return "canvas" if canvas_hits else "dom"


def _extract_selector_hints(code_text: str):
    """从产物代码提取可用 CSS 选择器提示，返回 (static_hints, dynamic_hints)。

    - static：静态 HTML 里 `id=""` / `class=""` 直接写出的选择器。
    - dynamic：JS 运行时 createElement 出来、或 CSS 里定义过的类名/id —— 静态
      HTML 里看不到，但页面加载后**真实存在**。

    req 199 事故：棋盘交叉点 `.cell` 由 `document.createElement` 生成，只扫静态
    属性的提取器完全漏掉它 → 翻译器手里只有容器 `.board`，只能写 `click .board`；
    而 Playwright 的 click 默认点元素中心，多条 click 全部压在同一个格子上
    （只有第一条生效）→「同一元素上多点交互」的 AC（下棋/画板/地图）必然假失败。
    """
    import re as _re

    static: list = []
    for _m in _re.finditer(r'id=["\']([^"\']+)["\']', code_text):
        static.append(f"#{_m.group(1)}")
    for _m in _re.finditer(r'class=["\']([^"\']+)["\']', code_text):
        for _cls in _m.group(1).split():
            static.append(f".{_cls}")

    dynamic: list = []
    for _pat in (
        r'className\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'classList\.(?:add|remove|toggle)\(\s*[\'"]([^\'"]+)[\'"]',
    ):
        for _m in _re.finditer(_pat, code_text):
            for _cls in _m.group(1).split():
                if _re.fullmatch(r'[A-Za-z_][\w-]*', _cls):
                    dynamic.append(f".{_cls}")
    # CSS 里定义的选择器（静态 HTML 可能只写容器，格子/子元素靠 JS 生成）
    for _m in _re.finditer(r'^\s*\.([A-Za-z_][\w-]*)', code_text, _re.M):
        dynamic.append(f".{_m.group(1)}")
    for _m in _re.finditer(r'^\s*#([A-Za-z_][\w-]*)', code_text, _re.M):
        dynamic.append(f"#{_m.group(1)}")

    static = list(dict.fromkeys(static))
    dynamic = [s for s in dict.fromkeys(dynamic) if s not in static]
    return static, dynamic


# 用户可见文案的提取来源（每组正则的组 1 即候选文本）。
# 全部是**确定性**规则 —— 不调用 LLM，因此同一份代码每次给出同一份清单。
#
# 为什么必须先把文案捞出来：req 206 实测，翻译器 11/11 次调用都把 anchor
# 读进了 prompt，却仍然写出 55/55 个 id/class 选择器。原因不是模型不听话，
# 而是 anchor 描述的是**区域**（"页面中部的添加表单区域：类型选择、金额输入框…"），
# 里面根本没有能直接当文案用的字 —— 模型想遵守"优先用可见文案定位"也无从下手。
# 约束不可执行时，加多少遍"必须"都不起作用，所以这里先给它可执行的材料。
_VISIBLE_TEXT_PATTERNS = (
    # HTML 标签之间的文本：<button>添加</button> / <h1>记账本</h1>
    r"<(?:button|a|label|option|legend|summary|th|td|li|h[1-6]|strong|em|title)\b[^>]*>"
    r"\s*([^<>{}%\n]{1,24}?)\s*</",
    # 用户可见属性。刻意不含 value=：它多数字符串是 "expense" 这类数据值而非文案
    r"""\b(?:placeholder|aria-label|title|alt)\s*=\s*["']([^"'<>{}%\n]{1,24})["']""",
    # JS 文本赋值
    r"""\.(?:textContent|innerText|innerHTML)\s*=\s*['"]([^'"<>{}%\n]{1,24})['"]""",
    # JS 建文本节点 / 建 option
    r"""createTextNode\(\s*['"]([^'"<>{}%\n]{1,24})['"]""",
    r"""new\s+Option\(\s*['"]([^'"<>{}%\n]{1,24})['"]""",
    # CSS 伪元素文案：content: "✓"
    r"""content\s*:\s*['"]([^'"<>{}%\n]{1,24})['"]""",
)

# 形如标识符而非文案的：全小写 ASCII、无空格、长度 ≤ 12（如 "expense"、"add-btn"）。
# 它们几乎必然是 id/class/type 的值，混进候选清单只会稀释信号；
# 中文文案与含空格的英文（"Add item"）不受影响。
_VISIBLE_TEXT_IDENTIFIER_RE = r"^[a-z][a-z0-9_-]{0,11}$"


def _extract_visible_texts(code_text: str, limit: int = 40) -> list[str]:
    """从产物代码里确定性提取「页面上真正看得见的文字」候选。

    返回去重、保序的短文本列表，供 AC 翻译器用 `text=` / `:has-text()` 定位。
    Playwright 按可见文案定位不受 id/class 改名影响，这类选择器不会因为实现
    换个命名就集体落到 compromised。

    与 `_extract_selector_hints` 的分工：那个给的是**兜底**手段（id/class），
    这个给的是**首选**手段。两者都从代码里提取，但只有文案对应得上
    "用户看到了什么"——也才对应得上 anchor 想表达的那一层。
    """
    import re as _re

    out: list[str] = []
    for pattern in _VISIBLE_TEXT_PATTERNS:
        for m in _re.finditer(pattern, code_text, _re.M):
            text = (m.group(1) or "").strip()
            if not text or len(text) > 24:
                continue
            if _re.fullmatch(_VISIBLE_TEXT_IDENTIFIER_RE, text):
                continue
            if not _re.search(r"\w", text):
                continue
            if text not in out:
                out.append(text)
                if len(out) >= limit:
                    return out
    return out


def _script_locating_stats(scripts: list) -> tuple[int, int]:
    """统计脚本里的 `(按可见文案定位的选择器数, 选择器总数)`。

    存在的意义：提示词里"定位优先级：可见文案 > 结构语义 > 兜底选择器"这条约束
    此前**没有任何验证方式**，模型有没有遵守无从得知。req 206 实测 11 次翻译、
    55 个选择器全是 id/class —— 直到有人手工去数才发现。约束缺验证方式 = 约束不存在，
    所以把这件事变成每轮都自动统计、为 0 就报警的量。
    """
    located = total = 0
    for script in scripts or []:
        if not isinstance(script, dict):
            continue
        for step in (script.get("steps") or []):
            if not isinstance(step, dict):
                continue
            selector = step.get("selector")
            if not selector:
                continue
            total += 1
            if "text=" in selector or ":has-text(" in selector:
                located += 1
    return located, total


def _resolve_ac_scripts(cached_scripts, cached_hash, ac_hash, translate_fn):
    """决定本轮用哪份 AC 脚本，返回 `(scripts, source)`。

    `source` 的取值让调用方能把「为什么没脚本」如实说出来：

    - `hit`            缓存命中（hash 一致），直接用，不重译
    - `fresh`          重新翻译成功
    - `stale_fallback` 需要重译但翻译失败 → 退回上一次成功翻译的脚本
    - `none`           需要重译、翻译失败，且没有可回退的脚本

    为什么要单独返回 source：此前"无需翻译"和"翻译失败"都用空列表表示，
    调用方写的是 `if ac_scripts:`，于是**翻译失败会静默跳过整段 AC 逐条验收**——
    日志里连 warning 都没有，用户看到的是"验收通过"，实际一条 AC 都没跑
    （需求 206 实测：翻译器 11 次调用全部被 reasoning 吃光预算，content 为空，
     5 条 AC 一条也没验）。空列表表示多种含义，是门禁失效的经典成因。
    """
    if cached_scripts and cached_hash == ac_hash:
        return list(cached_scripts), "hit"
    fresh = translate_fn() or []
    if fresh:
        return fresh, "fresh"
    if cached_scripts:
        return list(cached_scripts), "stale_fallback"
    return [], "none"


def _ac_translate_timeout() -> int:
    """AC 脚本翻译的超时预算。

    不能用 `_aux_timeout()`（60s）——那一档的注释写明是给"小输出（500 token 以内）"
    的调用用的，而这里要求的是多步 JSON 脚本。实测单批（2 条 AC）约 35s、
    单条最慢 78s，60s 会周期性撞穿并触发无谓重试。
    """
    from config import settings
    return settings.LLM_AC_TRANSLATE_TIMEOUT


def _evaluator_timeout(retry: bool = False) -> int:
    """Evaluator（验收评估）调用的超时预算。

    不能用 `_aux_timeout()`（60s）：评估要读完整产物（含浏览器结果）并开 thinking，
    实测耗时 54~107s，60s 会误杀。

    retry=True 对应 finish_reason=length 后以 max_tokens 3000→6000 重试的那一档，
    输出预算翻倍、耗时近似线性，故给更高上限。
    """
    from config import settings
    return (
        settings.LLM_EVALUATOR_RETRY_TIMEOUT if retry
        else settings.LLM_EVALUATOR_TIMEOUT
    )


# 单批最多翻几条 AC。
#
# 实测（agnes-3.0-flash，需求 206 的 5 条 AC + 26k 字符代码）：
#   5 条 / max_tokens=2000 → content 空（reasoning 6.1k chars 吃光预算）→ 0 条脚本
#   5 条 / max_tokens=6000 → 仍然 content 空 → 0 条脚本
#   2 条 / max_tokens=4000 → 2/2 成功，35s
#   1 条 / max_tokens=2000 → 1/1 成功，78s
#
# 结论：AC 一多，模型就陷进长篇推理直到预算耗尽，"加大预算"救不回来。
# 分批的第二个好处是**失败被隔离**——某一批挂了，其余批次照常产出脚本，
# 而不是像原来那样整批作废（空列表还会让调用方静默跳过整段 AC 验收）。
# 历史注记：8/16~9/28 的日志里 `5 条 AC 翻译完成` 很常见，说明是当前模型/
# 端点的行为变化让"一次翻 5 条"变得不可行，不是这段逻辑一直都坏。
_AC_TRANSLATE_BATCH = 2

# 单批最多尝试几次。
# 实测：同一批用同样的参数重发一次就成功了（端点瞬时拥塞 / 空响应），
# 而那一次失败会让两整条 AC 无人验收。代价只有"已经失败之后再发一次"，
# 收益是 AC 覆盖率——按 5 条 AC / 3 批算，一次失败就是 40% 的 AC 没跑。
_AC_TRANSLATE_ATTEMPTS = 2

# AC 翻译的系统提示词 —— 唯一定义处：真实调用与可观测性埋点共用同一份。
# 分别在两处写同义字面量，改了一处另一处就开始记录假信息。
_AC_TRANSLATE_SYSTEM_PROMPT = "你是 Playwright 自动化测试专家。只返回 JSON，不要其他文字。"


def _translate_one_ac_batch(
    batch: list, selector_text: str, render_info: str, visible_text_text: str,
    requirement_id: int = None, turn_index: int = 0, trace_id: str = None,
) -> list[dict]:
    """翻译一小批 AC。返回结构合法的脚本条目；失败返回空列表（由调用方计数）。

    requirement_id / turn_index / trace_id 只用于可观测性埋点，不参与翻译逻辑 ——
    默认值下静默跳过，单测直接调这个函数不受影响。
    """
    # 兜底提取 JSON 数组那一步要用 `_re`。本模块只在若干函数里**局部**
    # `import re as _re`，不做模块级别名，所以这里必须自己导入。
    # 少了它就会抛 `NameError: name '_re' is not defined`，被上层打成
    # "[AC Translate] 翻译失败: name '_re' is not defined"，
    # 把真正的失败原因（超时/截断）盖掉（req 203 实测踩过）。
    import re as _re

    ac_text = "\n".join(
        f"- {ac.get('id', '?')}: {ac.get('label', '')}\n  验证方式: {ac.get('how_to_verify', '')}"
        for ac in batch
    )

    # anchor 清单：需求阶段写下的「这条验收发生在页面哪一块」，用自然语言描述。
    # 它是独立于实现的定位参照。只有缺 anchor 的老 plan 才会走进兜底说明，
    # 那种情况下脚本更容易落到 compromised。
    anchor_lines = []
    for ac in batch:
        if not isinstance(ac, dict):
            continue
        anchor = (ac.get("anchor") or "").strip()
        if anchor:
            anchor_lines.append(f"- {ac.get('id', '?')}: {anchor}")
    anchor_text = (
        "\n".join(anchor_lines)
        if anchor_lines
        else "（本批 AC 未提供 anchor，请依据下方的选择器清单与 AC 描述谨慎定位）"
    )

    from harness.instructions.prompts import load_prompt_template
    prompt = load_prompt_template(
        "verify/ac_translator.md",
        anchor_text=anchor_text,
        visible_text_text=visible_text_text,
        selector_text=selector_text,
        ac_text=ac_text,
        render_info=render_info,
    )

    from llm.client import get_client
    response = get_client().chat(
        prompt=prompt,
        system_prompt=_AC_TRANSLATE_SYSTEM_PROMPT,
        use_memory=False,
        # 预算与批大小成比例（实测 2 条 AC 在 4000 下稳定成功）。
        # 若仍被 reasoning 吃光，llm.client 里有"以更大额度重试一次"的救援。
        max_tokens=max(2000, 2000 * len(batch)),
        timeout=_ac_translate_timeout(),
        thinking='enabled',
    )
    # AC 翻译属于验收阶段：它决定「验收标准怎么变成可执行的检查」，
    # 之前完全没被记录，验收在后台上只有 Evaluator 那一次调用。
    # 记录的 system prompt 复用真实发送的那份常量，不另拼字面量 ——
    # 两处漂移后日志就开始说谎。
    if requirement_id:
        _log_llm_turn_safe(
            requirement_id, None, get_client(),
            _AC_TRANSLATE_SYSTEM_PROMPT,
            prompt, response, thinking='enabled', stage=STAGE_VERIFYING,
            turn_index=turn_index, trace_id=trace_id,
        )
    if response.is_error or not response.content:
        return []

    import json as _json
    content = response.content.strip()
    try:
        scripts = _json.loads(content)
    except _json.JSONDecodeError:
        match = _re.search(r'\[[\s\S]*\]', content)
        if not match:
            return []
        try:
            scripts = _json.loads(match.group())
        except _json.JSONDecodeError:
            return []

    if not isinstance(scripts, list):
        return []
    # 只保留结构合法的条目（LLM 偶尔返回字符串数组导致下游 .get 崩溃）
    valid = [
        s for s in scripts
        if isinstance(s, dict) and isinstance(s.get("steps"), list) and s["steps"]
    ]
    if len(valid) < len(scripts):
        from harness.observability.logger import get_logger
        get_logger(__name__).info(
            f"[AC Translate] 本批丢弃 {len(scripts) - len(valid)} 条格式非法条目"
        )
    return valid


def _translate_acs_to_scripts(acceptance_criteria: list, code_text: str,
                              requirement: str, requirement_id: int = None,
                              turn_index: int = 0, trace_id: str = None) -> list[dict]:
    """用 LLM 将验收条件翻译为 Playwright 操作序列

    每个 AC 的 how_to_verify 字段描述验证方法（如"输入文字点击添加按钮，列表中显示新项目"），
    LLM 需要翻译为具体的 DOM 操作步骤。

    按 `_AC_TRANSLATE_BATCH` 分批调用（原因见该常量的注释）。

    requirement_id / turn_index / trace_id 只用于可观测性埋点（AC 翻译属于验收阶段），
    不参与翻译逻辑。
    """
    if not acceptance_criteria:
        return []

    # 从代码中提取可用的 CSS 选择器（供 LLM 参考，减少 selector 猜测错误）
    # 拆成「静态 + 动态」两段；动态段是 req 199 的修复点：`.cell` 这类由
    # JS createElement 生成、只在 CSS 里出现的元素，静态属性扫描看不到，
    # 不补进候选就会让翻译器只能退而写容器选择器（→ 只能点元素中心）。
    selectors_hint, dynamic_hint = _extract_selector_hints(code_text)

    # 用户可见文案候选：这是**首选**定位材，与上面的 id/class 清单（兜底）分节给。
    # req 206 的根因是"约束不可执行"——anchor 只描述区域、不含可用文案，
    # 模型想优先用文案定位也无从下手。这里把代码里真实可见的字捞出来喂过去。
    visible_texts = _extract_visible_texts(code_text)
    visible_text_text = (
        "、".join(visible_texts)
        if visible_texts
        else "（未能从代码中提取到可见文案；请依据 AC 描述推断页面上的按钮/标签文字）"
    )

    # 渲染方式：由 harness 确定性检测，作为"事实"喂给 LLM。
    # req 147 复盘根因：模板原先写死「游戏类 AC → assert_canvas_change」，
    # 而该游戏是 DOM 实现的，三条 AC 因此永久「不适用」。
    if _render_signature(code_text) == "canvas":
        render_info = (
            "检测到 canvas 用法 → 本实现**基于 canvas 渲染**。\n"
            "画面类断言用 assert_canvas_change。"
        )
    else:
        render_info = (
            "代码中未检测到任何 canvas 用法 → 本实现**基于 DOM 渲染**（div/table 等元素）。\n"
            "画面类断言必须用 assert_dom_change，禁止使用 assert_canvas_change。"
        )

    # 保持出现顺序（原实现用 set → 顺序随机，同一输入每轮提示词都可能不同，
    # 不利于"第一版就翻对"）
    _static_sel = ", ".join(list(dict.fromkeys(selectors_hint))[:40])
    selector_text = _static_sel if _static_sel else "(从代码中提取)"
    if dynamic_hint:
        selector_text += (
            "\n- 动态生成（JS createElement / CSS 中定义，静态 HTML 里看不到；"
            "同类元素有多个时须配合 [data-*] 属性选择器或 click 的 at 比例坐标定位）: "
            + ", ".join(dynamic_hint[:40])
        )

    from harness.observability.logger import get_logger
    logger = get_logger(__name__)

    batches = [
        acceptance_criteria[i:i + _AC_TRANSLATE_BATCH]
        for i in range(0, len(acceptance_criteria), _AC_TRANSLATE_BATCH)
    ]
    collected: list[dict] = []
    failed_batches = 0
    for idx, batch in enumerate(batches, 1):
        scripts: list[dict] = []
        for attempt in range(1, _AC_TRANSLATE_ATTEMPTS + 1):
            try:
                # 按需传：没有 requirement_id 就完全不带这些关键字，保持与既有
                # 单测替身（四参数 fake_batch）的签名兼容。
                _extra = ({"requirement_id": requirement_id,
                           "turn_index": turn_index, "trace_id": trace_id}
                          if requirement_id else {})
                scripts = _translate_one_ac_batch(
                    batch, selector_text, render_info, visible_text_text, **_extra,
                )
            except Exception as e:
                scripts = []
                logger.warning(
                    f"[AC Translate] 第 {idx}/{len(batches)} 批第 {attempt} 次异常: {e}"
                )
            if scripts:
                break
            if attempt < _AC_TRANSLATE_ATTEMPTS:
                logger.warning(
                    f"[AC Translate] 第 {idx}/{len(batches)} 批首次未产出脚本，重试一次"
                )
        if scripts:
            collected.extend(scripts)
        else:
            failed_batches += 1
            logger.warning(
                f"[AC Translate] 第 {idx}/{len(batches)} 批未产出脚本"
                f"（本批 {len(batch)} 条 AC，已尝试 {_AC_TRANSLATE_ATTEMPTS} 次，"
                f"共 {len(batches)} 批）"
            )

    logger.info(
        f"[AC Translate] {len(collected)}/{len(acceptance_criteria)} 条 AC 翻译完成"
        f"（{len(batches)} 批，失败 {failed_batches} 批）"
    )
    # 「定位优先级」这条约束此前没有任何验证方式：模型有没有用可见文案定位，
    # 只能靠人事后手工去数（req 206：55/55 全是 id/class，翻了才知道）。
    # 现在每轮自动统计——为 0 就说明提示词里的优先级完全没被遵守，
    # 脚本绑死 id/class 时，实现改个命名就会让这一整批 AC 集体落到 compromised。
    if collected:
        located, total = _script_locating_stats(collected)
        logger.info(
            f"[AC Translate] 定位方式：{located}/{total} 个选择器按可见文案定位"
            f"（候选文案 {len(visible_texts)} 条）"
        )
        if total and located == 0:
            logger.warning(
                f"[AC Translate] ⚠️ {total} 个选择器无一按可见文案定位——"
                f"提示词里的定位优先级未被遵守，脚本已绑死 id/class。"
            )
    return collected


# ==================== Verify 节点（Fresh-Context Evaluator） ====================


def _mark_acs_checked(state: AgentState, ac_ids: list) -> None:
    """把 Playwright 实测通过的 AC 回写进两级契约（evidence_found=true）"""
    if not ac_ids:
        return
    try:
        contract = (state.get("metadata") or {}).get("_completion_contract") \
            or state.get("_completion_contract")
        if contract is not None and hasattr(contract, "mark_ac_checked"):
            for ac_id in ac_ids:
                contract.mark_ac_checked(ac_id)
    except Exception as e:
        logger.debug(f"[Verify] 回写 AC 证据失败（不阻断）: {e}")


# ==================== 节点级 trace span（审查报告 Phase 4.3） ====================
# 此前 trace 只覆盖编码阶段的迭代轮次，verify 评估 / defect_repair 修复
# 完全没有 span——线上质量问题无法归因。用装饰器统一补齐，
# 不侵入节点函数体。

def _emit_node_milestone(name: str, state: AgentState, result) -> None:
    """把节点结论落成一条里程碑事件。

    verify 与 defect_repair 各有多个 return 路径（快速通道 / 评估异常 /
    无缺陷跳过 / 失败），在切面上收口能保证「节点执行一次 = 时间线一条结论」，
    不必在每个 return 前各写一遍（漏一处就是时间线缺一段）。
    节点内的富信息（verdict / AC 明细 / 修复轮次）通过 state 上的
    `verify_verdict` / `repair_verdict` 传出来。

    只处理认识的节点名，且要求 state 是 dict：`@_traced_node("X")` 一旦挂错
    函数（HEAD 里就发生过一次 —— 它被挂在了 `_build_vision_images` 上，
    该函数第一个参数是文件名而不是 state），下面这行 `state.pop(...)` 会在
    每次调用时抛 AttributeError，把被装饰的函数整个打挂。
    可观测性出问题不该让业务停摆，所以这里先自证输入合法。
    """
    if not isinstance(state, dict) or name not in ("verify", "defect_repair"):
        return

    step = result.get("current_step") if isinstance(result, dict) else None

    if name == "verify":
        # pop：这两个键只是节点→切面的信息通道，不该被 checkpoint / 状态快照带走
        v = state.pop("verify_verdict", None) or {}
        passed = bool(result.get("verify_passed")) if isinstance(result, dict) else False
        failed = v.get("failed_ac_ids") or []
        meta = {
            "verdict": v.get("verdict") or ("PASS" if passed else "NEEDS_WORK"),
            "score": v.get("score"),
            "findings": v.get("findings", 0),
            "critical_count": v.get("critical_count", 0),
            "failed_ac_ids": failed,
            "defect_count": v.get("defect_count", 0),
            "ac_total": v.get("ac_total", 0),
            "fast_pass": bool(v.get("fast_pass")),
        }
        if passed:
            label = f"验收通过 · {meta['score'] if meta['score'] is not None else '-'} 分"
        else:
            label = (f"验收未通过 · 未达成 AC {len(failed)} 条"
                     if failed else "验收未通过")
        error = result.get("error") if isinstance(result, dict) else None
        _record_milestone(state, KIND_VERIFY, label,
                          status="error" if error else "ok", meta=meta)
        return

    if name == "defect_repair":
        v = state.pop("repair_verdict", None) or {}
        if step == "defect_repair_done":
            label = (f"第 {v.get('round', '?')} 轮修复完成 · "
                     f"{len(v.get('written_files') or [])} 个文件")
            status = "ok"
        elif step == "defect_repair_skipped":
            label, status = "无确定性缺陷，跳过修复", "ok"
        else:
            label = f"修复失败 · {step or '未知'}"
            status = "error"
        _record_milestone(
            state, KIND_REPAIR, label, status=status,
            meta={"round": v.get("round"),
                  "target_defects": v.get("target_defects") or [],
                  "written_files": v.get("written_files") or []},
        )


def _traced_node(name: str):
    """给 LangGraph 节点函数包一层 trace span + 里程碑事件

    成功静默（status=success），失败喧哗（status=error + error 信息）。
    tracer/trace_id 缺失时静默降级为直通。
    """
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state: AgentState, *args, **kwargs):
            span = None
            tracer = None
            try:
                tl = get_tool_loop(state)
                tracer = getattr(tl, "tracer", None) if tl else None
                trace_id = (state.get("metadata") or {}).get("trace_id", "")
                if tracer is not None and trace_id:
                    span = tracer.start_span(
                        trace_id, name,
                        metadata={"complexity": (state.get("metadata") or {}).get("complexity", "")},
                    )
            except Exception as e:
                logger.debug(f"[Trace] {name} span 创建失败（跳过）: {e}")

            try:
                result = fn(state, *args, **kwargs)
                if span is not None and tracer is not None:
                    error = result.get("error") if isinstance(result, dict) else None
                    tracer.end_span(span, status="error" if error else "success",
                                    error=str(error) if error else None)
                _emit_node_milestone(name, state, result)
                return result
            except Exception as e:
                if span is not None and tracer is not None:
                    try:
                        tracer.end_span(span, status="error", error=str(e))
                    except Exception:
                        pass
                _record_milestone(state, KIND_VERIFY if name == "verify" else KIND_REPAIR,
                                  f"{name} 异常 · {str(e)[:80]}", status="error",
                                  meta={"error": str(e)[:200]})
                raise
        return wrapper
    return decorator


@_traced_node("verify")
def verify_node(state: AgentState) -> Dict[str, Any]:
    """
    Fresh-Context Evaluator: 独立上下文评估代码质量

    与编码阶段隔离，使用全新 LLM 上下文（不含编码历史），
    通过真实浏览器执行 (run_preview) 提供 ground truth 验证。

    工作流程:
    1. 读取 SPEC 和原始需求
    2. 读取所有代码文件
    3. 运行 run_preview 获取浏览器输出
    4. 独立 LLM 评估输出结构化结果
    5. 持久化到 .task/evaluator/result.json
    """
    from harness.instructions.prompts import load_prompt
    from llm.client import get_client

    workspace = get_workspace(state)
    if not workspace:
        tl = get_tool_loop(state)
        workspace = tl.workspace if tl else None

    if not workspace:
        logger.error("[Verify] 无法获取 workspace")
        return {"verify_passed": False, "current_step": "verify_done",
                "smoke_defects": [],
                "architectural_defects": [],
                "error": "无法获取 workspace，评估流程异常"}

    requirement = state.get("requirement_content", "")
    spec_content = ""

    # 尝试读取 SPEC（来自 Architect 或 TL plan）
    try:
        spec_content = workspace.read("docs/SPEC.md")
    except Exception:
        plan = state.get("plan", {})
        if plan:
            spec_content = json.dumps(plan, ensure_ascii=False, indent=2)

    # 收集所有代码文件
    files = workspace.list()
    code_files = [f for f in files if not f.startswith("docs/") and not f.startswith(".task/")]
    # req 147 修正：不再无条件 content[:6000]，改为预算驱动 + 截断可见
    code_text = _build_evaluator_code_blocks(workspace, code_files)

    # ========== 硬性文件完整性校验 ==========
    # 从 SPEC/Plan 中提取预期文件列表，与实际生成的文件做对比
    missing_files = []
    plan = state.get("plan", {})
    file_structure = plan.get("file_structure", {}) if isinstance(plan, dict) else {}

    def _collect_expected_files(structure, prefix=""):
        """递归收集 file_structure 中定义的所有文件路径

        支持三种格式：
        1. 扁平列表: ["index.html", "css/style.css", ...]（TeamLeader 最常见输出）
        2. 嵌套字典: {"index.html": {"type": "file"}, "css/": {...}}
        3. 混合: {"src/": ["index.html", "app.js"]}
        """
        files = []
        if isinstance(structure, list):
            # 扁平列表：直接收集所有字符串元素
            for item in structure:
                if isinstance(item, str):
                    files.append(item)
                elif isinstance(item, dict):
                    files.extend(_collect_expected_files(item, prefix))
        elif isinstance(structure, dict):
            for name, info in structure.items():
                path = f"{prefix}/{name}" if prefix else name
                if isinstance(info, dict):
                    if info.get("type") == "file" or "." in name:
                        files.append(path)
                    elif "type" not in info:
                        # 可能是嵌套目录
                        files.extend(_collect_expected_files(info, path))
                elif isinstance(info, list):
                    # 文件列表
                    for item in info:
                        if isinstance(item, str):
                            files.append(f"{path}/{item}" if path else item)
                        elif isinstance(item, dict):
                            files.extend(_collect_expected_files(item, path))
                elif isinstance(info, str):
                    files.append(path)
        return files

    expected_files = _collect_expected_files(file_structure)

    # 如果 SPEC 中定义了文件结构，检查缺失文件
    if expected_files:
        # 标准化路径比较
        normalized_code = set(f.lstrip("/") for f in code_files)
        for expected in expected_files:
            normalized_expected = expected.lstrip("/")
            # 检查文件是否存在（允许文件在不同目录层级）
            found = any(
                cf.endswith(normalized_expected.split("/")[-1])
                for cf in normalized_code
            )
            if not found:
                missing_files.append(expected)

    # 硬性检查：index.html 是 Web 项目的入口文件，必须存在
    has_index_html = any(f.endswith("index.html") for f in code_files)
    if not has_index_html and code_files:
        # 检查 SPEC 是否期望一个 Web 项目（有 HTML 文件）
        all_expected = " ".join(expected_files + code_files)
        is_web_project = any(ext in all_expected for ext in [".html", ".css", ".js"])
        if is_web_project:
            if "index.html" not in missing_files:
                missing_files.append("index.html")

    if missing_files:
        logger.warning(
            f"[Verify] 文件完整性校验失败 - 缺失 {len(missing_files)} 个文件: {missing_files}"
        )
        missing_desc = "\n".join(f"- `{f}`" for f in missing_files)
        evaluator_result = {
            "verdict": "NEEDS_WORK",
            "summary": f"缺失 {len(missing_files)} 个关键文件，无法完成验证",
            "score": {"functionality": 0, "runtime": 0, "ui_quality": 0, "acceptance": 0, "code_quality": 0},
            "overall_score": 0.0,
            "findings": [
                {
                    "severity": "critical",
                    "dimension": "functionality",
                    "description": f"SPEC 定义但未生成的文件: {', '.join(missing_files)}",
                    "evidence": f"以下文件缺失:\n{missing_desc}",
                    "suggestion": "使用 write_file 工具创建缺失的文件，确保所有 SPEC 定义的文件都被生成",
                }
            ],
            "browser_result": {"available": False, "errors": ["缺失 index.html 无法运行浏览器验证"], "warnings": []},
            "timestamp": __import__('time').time(),
        }
        state["verify_passed"] = False
        state["current_step"] = "verify_done"
        state.setdefault("role_outputs", {})["Evaluator"] = json.dumps(evaluator_result, ensure_ascii=False)
        state.setdefault("dialogue_history", []).append({
            "role": "agent",
            "name": QA_NAME,
            "content": (
                f"## 代码评估: ❌ NEEDS_WORK\n\n"
                f"**评分**: 0/10 (文件完整性校验失败)\n\n"
                f"**缺失文件**:\n{missing_desc}\n\n"
                f"**摘要**: 关键文件缺失，无法进行浏览器验证"
            ),
            "status": "completed",
        })
        # 持久化
        try:
            workspace.write(
                ".task/evaluator/result.json",
                json.dumps(evaluator_result, ensure_ascii=False, indent=2)
            )
        except Exception:
            pass
        # 缺文件早退也必须递增 repair_count，否则 coder↔verify 会无限循环
        # （route_after_verify 只靠 repair_count >= max_rounds 终止）
        meta = dict(state.get("metadata") or {})
        meta["repair_count"] = meta.get("repair_count", 0) + 1
        state["metadata"] = meta  # 原地同步（兼容当前 langgraph 浅拷贝语义）
        return {"verify_passed": False, "current_step": "verify_done",
                "smoke_defects": [], "architectural_defects": [],
                "metadata": meta}  # 显式返回：不依赖浅拷贝副作用

    # ---- 验证阶段动作级进度 + 「质量工程师」账本 ----
    # 百分比与文案统一由 progress_plan 分配（此处不再出现裸数字）；
    # VerifyTrace 同时喂两条通道：实时卡片 verify_start/verify_step，
    # 以及验证结束时落库的一条 qa_summary（刷新后仍能看到"它做过什么"）。
    from harness.observability import progress_plan as _progress_plan
    from harness.observability.verify_trace import (
        VerifyTrace, build_summary_message, publish_summary,
    )

    _verify_sse = None
    try:
        _tl_probe = get_tool_loop(state)
        _verify_sse = _tl_probe.sse if _tl_probe else None
    except Exception:
        _verify_sse = None
    _verify_trace = VerifyTrace(state, sse=_verify_sse)

    def _verify_progress(step: str) -> None:
        """推送验证子步骤进度（step 取值见 progress_plan.VERIFY_STEPS）。"""
        try:
            _tl = get_tool_loop(state)
            _sse = _tl.sse if _tl else None
            if _sse is not None and state.get("requirement_id"):
                _sse.progress(
                    state["requirement_id"],
                    _progress_plan.verify_percent(step, _progress_plan.round_index(state)),
                    _progress_plan.STEP_LABEL[step],
                    stage="verifying",
                )
        except Exception as _e:
            logger.debug(f"[Verify] 进度推送失败（不阻断）: {_e}")

    # 运行 run_preview 获取浏览器执行结果
    browser_result = {"available": False, "errors": [], "warnings": []}
    if any(f.endswith("index.html") for f in code_files):
        try:
            _verify_progress("preview")
            tl = get_tool_loop(state)
            if tl and tl._preview_handler:
                preview = tl._preview_handler.run_preview("index.html")
                if preview and preview.metadata:
                    browser_result = preview.metadata
        except Exception as e:
            logger.warning(f"[Verify] run_preview 失败: {e}")
            browser_result["errors"].append(f"run_preview 异常: {e}")
        _verify_trace.done(
            "preview",
            "页面打不开（跳过）" if not browser_result.get("available")
            else f"{len([x for x in browser_result.get('errors') or [] if x.get('message')])} 个运行时错误",
        )
    else:
        _verify_trace.mark("preview", "skipped", "工作区没有 index.html")

    # ========== Playwright AC 逐条验收 ==========
    # ⚠️ 这两个变量都必须在**函数作用域**初始化，不能只在下面的
    # `if acceptance_criteria and 有 index.html` 分支里初始化：
    # 该分支被跳过时（plan 没有 acceptance_criteria，或工作区还没有 index.html），
    # 末尾 _build_ac_failure_defects(ac_check_results, _ac_steps_text) 仍会执行，
    # 于是抛 UnboundLocalError 把整个 verify 节点打挂（req 177 实测：
    # plan 无 AC → 分支跳过 → "cannot access local variable '_ac_steps_text'" → 需求直接 failed）。
    ac_check_results = []
    _ac_steps_text = {}  # ac_id -> 复现步骤文本（供缺陷回传定位根因）
    plan = state.get("plan", {})
    acceptance_criteria = plan.get("acceptance_criteria", []) if isinstance(plan, dict) else []
    # req 148 事故：state["plan"] 里取不到 AC 时，整轮 AC 逐条验收被**静默跳过**
    # （无任何日志），evaluator 于是在没有断言证据的情况下判 PASS 8.6。
    # SPEC.md 里明确写了 acceptance_criteria，这里做确定性兜底，宁可多跑不可漏跑。
    if not acceptance_criteria:
        try:
            _spec = json.loads(spec_content) if isinstance(spec_content, str) else None
            if isinstance(_spec, dict):
                acceptance_criteria = _spec.get("acceptance_criteria") or []
                if acceptance_criteria:
                    logger.info(
                        f"[Verify] state.plan 无 acceptance_criteria，"
                        f"已从 SPEC.md 兜底解析 {len(acceptance_criteria)} 条"
                    )
        except Exception:
            acceptance_criteria = []

    # 验证走与用户一致的预览链路（沙箱 iframe + 能力 URL），杜绝"验证过、用户废"
    preview_url = None
    try:
        from utils.preview_token import make_preview_url
        # absolute=True：验证宿主页是 file:// 临时文件，相对路径无法解析
        preview_url = make_preview_url(
            workspace.user_id, workspace.req_id, "index.html", absolute=True
        )
    except Exception as e:
        logger.warning(f"[Verify] 构造预览 URL 失败，回退直读文件: {e}")

    if not acceptance_criteria:
        logger.warning(
            "[Verify] ⚠️ 无验收条件（plan 与 SPEC.md 都没有 acceptance_criteria）"
            "→ 本轮跳过 AC 逐条验收，仅靠冒烟 + LLM 评估，判定可信度显著下降"
        )
    elif not any(f.endswith("index.html") for f in code_files):
        logger.warning("[Verify] ⚠️ 工作区无 index.html → 跳过 AC 逐条验收")

    if acceptance_criteria and any(f.endswith("index.html") for f in code_files):
        logger.info(f"[Verify] 启动 AC 逐条验收: {len(acceptance_criteria)} 条 AC")
        try:
            # Step 1: LLM 将 AC 描述翻译为 Playwright 操作序列
            # 带缓存首轮锁定：每轮重译会导致同一份代码结果漂移（选择器/步骤不稳定）。
            # 但 hash 必须带上"渲染方式"这一实现特征——req 147 复盘：DOM 实现的 1024
            # 被翻译成 canvas 断言后，脚本被永久锁死，三轮 repair 期间三条 AC 稳定 False。
            # 只加渲染方式（几乎不变），不加全代码 hash，以免退回"每轮重译"的漂移问题。
            ac_cache_path = workspace.path / ".task" / "ac_scripts.json"
            import hashlib as _hashlib
            _render_sig = _render_signature(code_text)
            _ac_hash = _hashlib.md5(
                (
                    json.dumps(acceptance_criteria, ensure_ascii=False, sort_keys=True)
                    + f"||render={_render_sig}"
                    + f"||schema={AC_SCRIPT_SCHEMA_VERSION}"
                ).encode()
            ).hexdigest()
            ac_scripts = None
            # 缓存里那份脚本（可能因为 schema / 渲染方式变化而"过期"）。
            # 重译失败时它是唯一的降级材料——见 _resolve_ac_scripts。
            stale_scripts = None
            cached_hash = None
            # _ac_steps_text 已在函数入口初始化（见上方注释），此处不再重复赋值
            if ac_cache_path.exists():
                try:
                    cached = json.loads(ac_cache_path.read_text())
                    if isinstance(cached.get("scripts"), list) and cached["scripts"]:
                        stale_scripts = cached["scripts"]
                        cached_hash = cached.get("ac_hash")
                except Exception:
                    pass
            ac_scripts, _ac_source = _resolve_ac_scripts(
                stale_scripts, cached_hash, _ac_hash,
                lambda: _translate_acs_to_scripts(
                    acceptance_criteria, code_text, requirement,
                    requirement_id=state.get("requirement_id"),
                    turn_index=int(_md(state).get("turn_index") or 0),
                    trace_id=_md(state).get("trace_id") or None),
            )
            if _ac_source == "hit":
                logger.info(f"[Verify] AC 脚本命中缓存（{len(ac_scripts)} 条，首轮锁定不重译）")
            elif _ac_source == "fresh":
                try:
                    ac_cache_path.parent.mkdir(parents=True, exist_ok=True)
                    ac_cache_path.write_text(
                        json.dumps({"ac_hash": _ac_hash, "scripts": ac_scripts}, ensure_ascii=False, indent=2)
                    )
                except Exception:
                    pass
            elif _ac_source == "stale_fallback":
                logger.info(
                    f"[Verify] AC 脚本缓存失效（渲染方式 {_render_sig} / "
                    f"脚本 schema v{AC_SCRIPT_SCHEMA_VERSION} 与缓存不符），重新翻译失败"
                )
                logger.warning(
                    f"[Verify] ⚠️ AC 脚本重译失败，回退到上一次成功翻译的 "
                    f"{len(ac_scripts)} 条脚本（schema/渲染方式已变，判定可能偏松或偏紧，"
                    f"但强于完全不验收）"
                )
            else:
                logger.info(
                    f"[Verify] AC 脚本缓存失效（渲染方式 {_render_sig} / "
                    f"脚本 schema v{AC_SCRIPT_SCHEMA_VERSION} 与缓存不符），重新翻译失败"
                )
                logger.warning(
                    "[Verify] ⚠️ AC 脚本翻译失败且无历史脚本可回退"
                    "→ 本轮跳过 AC 逐条验收，仅靠冒烟 + LLM 评估，判定可信度显著下降"
                )
            # Step 2: Playwright 执行
            if ac_scripts:
                from harness.tools.preview_runner import run_ac_checks
                for _s in ac_scripts:
                    _sid = _s.get("ac_id")
                    _frags = []
                    for st in (_s.get("steps") or []):
                        _frag = str(st.get("action", ""))
                        if st.get("selector"):
                            _frag += " " + str(st.get("selector"))
                        if st.get("value"):
                            _frag += " value=" + str(st.get("value"))
                        _frags.append(_frag)
                    if _sid:
                        _ac_steps_text[_sid] = " → ".join(_frags)[:400]
                index_path = workspace.path / "index.html"
                if index_path.exists():
                    _verify_progress("ac")
                    ac_check_results = run_ac_checks(
                        index_path, ac_scripts, preview_url=preview_url,
                        sse=tl.sse if tl else None,
                        requirement_id=state.get("requirement_id"),
                        dialogue_history=state.get("dialogue_history"),
                    )
                    _prod_fail = sum(1 for r in ac_check_results if r.get("failures"))
                    _harness_fail = sum(1 for r in ac_check_results if r.get("harness_errors"))
                    _unverified = sum(1 for r in ac_check_results if r.get("unverified"))
                    # 脚本锁死治理（P2.5）：多数 AC「验不了/驱动不动」说明翻译出来的脚本
                    # 对当前实现不适用（选择器猜错、断言类型选错）。首轮锁定本来是为了
                    # 防漂移，但锁死一个错脚本等于永久假绿/假红——这里作废缓存，
                    # 下一轮按当前代码重新翻译（req 147 的 canvas 断言锁死就是这么来的）。
                    # 判据抽到 ac_verdict_policy._should_invalidate_ac_cache 单测守卫。
                    if _should_invalidate_ac_cache(ac_check_results):
                        try:
                            ac_cache_path.unlink()
                            logger.warning(
                                f"[Verify] AC 脚本多数验不了（不适用 {_unverified} 条 / "
                                f"驱动失败 {_harness_fail} 条），已作废缓存，下轮重新翻译"
                            )
                        except Exception:
                            pass
                    logger.info(
                        f"[Verify] AC 验收完成: {len(ac_check_results) - _prod_fail}/"
                        f"{len(ac_check_results)} 通过（产品断言失败 {_prod_fail} 条，"
                        f"脚本驱动失败 {_harness_fail} 条不计入缺陷）"
                    )
                    _verify_trace.done(
                        "ac",
                        f"{len(ac_check_results) - _prod_fail}/"
                        f"{len(ac_check_results)} 通过",
                    )
                    # Step 3: 推送逐条 AC 结果到前端
                    tl = get_tool_loop(state)
                    sse = tl.sse if tl else None
                    for result in ac_check_results:
                        if sse:
                            hints = result.get("failures", []) + [
                                f"[脚本错误] {e}" for e in result.get("harness_errors", [])
                            ]
                            sse.checklist_update(
                                state.get("requirement_id", 0),
                                result["ac_id"],
                                result["passed"],
                                "; ".join(hints) if not result["passed"] else "",
                                state=_ac_state(result),
                            )
        except Exception as e:
            logger.warning(f"[Verify] AC 逐条验收异常（降级为 LLM 评估）: {e}")

    # ========== 层1 通用冒烟测试（品类无关的确定性不变量） ==========
    smoke_result = {"available": False, "defects": [], "checks": {}, "logs": []}
    if any(f.endswith("index.html") for f in code_files):
        try:
            from harness.tools.preview_runner import run_universal_smoke
            index_path = workspace.path / "index.html"
            if index_path.exists():
                _verify_progress("smoke")
                smoke_result = run_universal_smoke(index_path, preview_url=preview_url)
                logger.info(
                    f"[Verify] 通用冒烟: available={smoke_result.get('available')}, "
                    f"checks={smoke_result.get('checks')}, defects={len(smoke_result.get('defects', []))}"
                )
        except Exception as e:
            logger.warning(f"[Verify] 通用冒烟异常（跳过）: {e}")
    smoke_defects = list(smoke_result.get("defects", []))

    # ---- 到这里「打开页面 / AC 验收 / 冒烟」三步都已发生，建卡 ----
    # 建卡放在 AC 与冒烟**之后**是刻意的：卡片在对话流里的位置必须与落库的
    # qa_summary 一致（逐条 AC 卡在前、这张总结卡在后），刷新前后顺序才不会变。
    _smoke_checks = smoke_result.get("checks") or {}
    if not ac_check_results:
        _verify_trace.mark("ac", "skipped", "本轮没有可执行的验收标准")
    if smoke_result.get("available"):
        _verify_trace.done(
            "smoke",
            f"{sum(1 for v in _smoke_checks.values() if v)}/{len(_smoke_checks)} 项通过",
        )
    else:
        _verify_trace.mark("smoke", "skipped", "浏览器会话不可用")
    _verify_trace.begin()

    # 浏览器运行时错误（pageerror）必须进入定向修复清单 —— req 147 复盘的致命缺陷：
    # 当时 repair prompt 里 pageerror 出现 0 次，只有「点击按钮无反应」这一条症状，
    # 而该症状的真正根因就是 init() 里 this._updateScoreDisplay is not a function。
    # 修复环节被误导去改事件绑定，三轮全部打偏，真正的 6 行修复一次都没碰。
    # 惯例约定：确定性根因证据必须排在症状之前（defect_repair 按列表顺序呈现）。
    _browser_err_defects = [
        {
            "type": "runtime_error",
            "severity": "critical",
            "dimension": "runtime",
            # req 189：裸 message（"Unexpected token ')'”）不带位置，coder 在多个
            # JS 文件间盲猜 5 轮未中。pageerror 的 stack 已解析出 文件:行号，前置展示。
            "message": (
                f"浏览器运行时错误: {e.get('message', '')[:200]}"
                + (f"（位置: {e['location']}）" if e.get("location") else "")
            ),
            "evidence": (
                f"[{e.get('type', 'error')}] {e.get('message', '')}"
                + (f" at {e['location']}" if e.get("location") else "")
                + (f"\nstack 首帧: {e['stack'].splitlines()[-1].strip()[:200]}"
                   if e.get("stack") and "\n" in e["stack"] else "")
            ),
            "suggestion": (
                (f"先打开 {e['location']} 检查该行附近的语法/引用。"
                 if e.get("location") else
                 "这是**根因级证据**，优先修它：按报错定位到具体文件与方法，")
                +
                "确认该方法已正确定义并可被该调用点访问（例如原型方法是否真的挂载到了类上），"
                "修完再重新验证。「页面无反应/无变化」往往是本错误导致初始化中断的结果，"
                "不要只改事件绑定。"
            ),
        }
        for e in (browser_result.get("errors") or [])
        if e.get("message")
    ]
    if _browser_err_defects:
        logger.info(
            f"[Verify] 将 {len(_browser_err_defects)} 条浏览器运行时错误并入定向修复清单（根因前置）"
        )
        smoke_defects = _browser_err_defects + smoke_defects
    # 症状类缺陷的建议措辞纠偏：原先写死「检查事件绑定是否生效」，在有人跑 errors 时是错的方向
    for _d in smoke_defects:
        if _d.get("type") == "no_interaction":
            _d["suggestion"] = (
                "先确认页面是否存在运行时错误（若上面已列出，优先修那些）；"
                "确认初始化流程未在中途抛异常中断（异常之后的事件绑定不会执行），"
                "再检查元素选择器与脚本加载顺序，最后确认初始化函数确实被调用。"
            )

    # 跨文件 API 契约检查（确定性，零 LLM）：引用了未导出的方法/未定义的全局
    # 属于架构类缺陷，经 classify_defects 路由回 coder 携带根因卡片重构
    # （需求 124 事故：app.js 调用 utils.js 未实现的 toast/copyText）
    contract_warnings = []
    contract_defects = []
    _verify_progress("contract")
    try:
        js_css_files = {}
        for f in code_files:
            if f.endswith((".js", ".css", ".html")) and not f.startswith(".task"):
                try:
                    js_css_files[f] = workspace.read(f)
                except Exception:
                    pass
        if js_css_files:
            from harness.constraints.environment_contract import check_cross_file_contract
            contract_defects, contract_warnings = check_cross_file_contract(js_css_files)
            if contract_defects:
                logger.warning(
                    f"[Verify] 跨文件契约检查发现 {len(contract_defects)} 处断裂: "
                    + "; ".join(f"{d['type']}:{d.get('evidence', '')}" for d in contract_defects[:6])
                )
            if contract_warnings:
                logger.info(f"[Verify] 类名契约警告 {len(contract_warnings)} 条")
            smoke_defects = smoke_defects + contract_defects
    except Exception as e:
        logger.debug(f"[Verify] 契约检查异常（跳过）: {e}")
        _verify_trace.mark("contract", "skipped", "契约检查异常")
    else:
        _verify_trace.done(
            "contract",
            f"{len(contract_defects)} 处断裂 · {len(contract_warnings)} 条警告",
        )

    # ========== AC 断言失败 → 可执行的确定性缺陷（req 148 修复） ==========
    # 事故：AC 逐条验收抓到了 4 条真实产品缺陷（方向键无响应、棋盘无变化），
    # 但 ac_check_results 此前**只用于给 evaluator 打分**，从不转成 defect，
    # 于是这些硬证据永远到不了修复环节——coder 拿到手的只有 2 条静态分析误报，
    # 整个第二轮 8 轮迭代全花在查一个不存在的问题上。
    # 修法：把带 failures 的 AC 转成 defect，附上复现步骤，进入回传链。
    _ac_defects = _build_ac_failure_defects(ac_check_results, _ac_steps_text)
    if _ac_defects:
        logger.info(
            f"[Verify] 将 {len(_ac_defects)} 条 AC 断言失败并入修复清单: "
            + ", ".join(d["ac_id"] for d in _ac_defects)
        )
        # 确定性根因前置：AC 证据比静态分析更可信，排在最前
        smoke_defects = _ac_defects + smoke_defects

    # ========== 视觉硬伤 → 确定性缺陷（UI 闭环） ==========
    # 背景：预置成品模板（.design/preset-*.css）此前只是「建议」—— 模型完全不用它，
    # 也没有任何环节会发现：evaluator 的 ui_quality 是盲评，页面难看照样 fast_pass。
    # 这里把浏览器实测的视觉硬伤（对比度 <3:1 / 点区 <24px / 字号 <10px）转成缺陷，
    # 让「不好看」第一次可判定、可修复。阈值取宽松下限，只拦明显硬伤，最多 2 条。
    # 副作用是会让这类页面走深度评估 + 一轮定向修复（约 +2 分钟），这是为质量付的价。
    try:
        from config import settings as _ui_settings
        _ui_lint_on = bool(getattr(_ui_settings, "UI_LINT_AS_DEFECT", True))
    except Exception:
        _ui_lint_on = True
    if _ui_lint_on:
        try:
            from harness.tools.preview_runner import collect_ui_lint_defects
            _ui_defects = collect_ui_lint_defects(
                workspace.path / "index.html", preview_url=preview_url
            )
            if _ui_defects:
                logger.info(
                    f"[Verify] 视觉硬伤并入修复清单: {[d['type'] for d in _ui_defects]}"
                )
                smoke_defects = smoke_defects + _ui_defects
        except Exception as e:
            logger.warning(f"[Verify] 视觉硬伤采集异常（跳过）: {e}")

    # 判断是否可以走快速通道。
    # 带病放行的 plan（需求阶段没过 DoD 校验）不允许进 fast_pass：那条通道完全
    # 跳过 LLM 评估，若让不可操作的 AC 以 passed 收口，等于是给伪结论盖章放行。
    plan_dod_issues = (plan.get("_plan_dod_issues") or []) if isinstance(plan, dict) else []
    preview_clean = len(browser_result.get("errors", [])) == 0
    ac_all_passed = (
        len(ac_check_results) > 0 and
        all(r["passed"] and not r.get("harness_errors") for r in ac_check_results)
    )
    fast_pass = preview_clean and ac_all_passed and not smoke_defects and not plan_dod_issues
    _verify_progress("dod")
    if plan_dod_issues:
        logger.warning(
            f"[Verify] plan 带 DoD 弱项 {len(plan_dod_issues)} 条，"
            f"禁用快速通道，转深度评估: {plan_dod_issues[:3]}"
        )
    _verify_trace.done(
        "dod",
        f"{len(plan_dod_issues)} 条弱项（禁快速通道）" if plan_dod_issues
        else "需求契约完整",
    )

    if fast_pass:
        # 快速通道：preview 零错误 + 所有 AC 通过 → 跳过深度 LLM 评估。
        # 评分诚实化（审查报告根因 5）：只给确定性证据覆盖到的维度打分，
        # ui_quality / code_quality 置 null（此通道未评估），并截图留档供查看，
        # 不再用硬编码 8/9.2 伪装"视觉质量已评"。
        screenshot_path = None
        try:
            from harness.tools.preview_runner import capture_screenshot
            screenshot_path = capture_screenshot(
                workspace.path / "index.html",
                workspace.path / ".task" / "evaluator" / "screenshot.png",
                preview_url=preview_url,
            )
        except Exception as e:
            logger.debug(f"[Verify] fast_pass 截图失败（跳过）: {e}")

        measured = {"functionality": 10, "runtime": 10, "acceptance": 10}
        logger.info(f"[Verify] 快速通道: preview 零错误 + {len(ac_check_results)} 条 AC 全部通过 → PASS")
        evaluator_result = {
            "verdict": "PASS",
            "summary": (
                f"浏览器验证无错误，{len(ac_check_results)} 条验收条件全部通过"
                f"（确定性证据判定；UI/代码质量未做深度评估）"
            ),
            "score": {"functionality": 10, "runtime": 10, "ui_quality": None, "acceptance": 10, "code_quality": None},
            "overall_score": round(sum(measured.values()) / len(measured), 1),
            "findings": [],
            "ac_results": ac_check_results,
            "browser_result": browser_result,
            "screenshot": screenshot_path,
            "fast_pass": True,
            "timestamp": __import__('time').time(),
        }
        state["verify_passed"] = True
        state["current_step"] = "verify_done"
        # AC 全过 → 契约中标记对应 AC 已有满足证据（两级契约第二级）
        _mark_acs_checked(state, [r["ac_id"] for r in ac_check_results])
        # 持久化
        try:
            workspace.write(".task/evaluator/result.json", json.dumps(evaluator_result, ensure_ascii=False, indent=2))
        except Exception:
            pass
        # SSE 推送
        try:
            tl = get_tool_loop(state)
            if tl and tl.sse:
                tl.sse.evaluator_result(state.get("requirement_id", 0), evaluator_result)
        except Exception:
            pass
        # 快速通道不跑 LLM 评估，但**仍然要留下一张可见的验证卡**：
        # 否则同一份产物走快速通道时用户什么都看不到，走深度评估时又能看到，
        # 反而更难解释（此前这里落一条 agent 文本，现改为结构化卡片）。
        _verify_trace.mark("evaluate", "skipped", "快速通道：确定性证据已足够")
        _qa_card = _verify_trace.to_card(
            verdict="PASS",
            score=evaluator_result.get("overall_score"),
            findings=[],
            ac_results=ac_check_results,
            smoke_result=smoke_result,
            browser_result=browser_result,
        )
        _qa_card["fast_pass"] = True
        _qa_msg = build_summary_message(_qa_card, QA_NAME)
        state.setdefault("dialogue_history", []).append(_qa_msg)
        publish_summary(_verify_sse, state.get("requirement_id"), _qa_msg)
        # 验收结论留给 _traced_node 切面落里程碑事件（覆盖所有 return 路径，
        # 一处埋点比在 5 个 return 前各写一遍不容易漏）。
        state["verify_verdict"] = {
            "verdict": "PASS",
            "score": evaluator_result.get("overall_score"),
            "findings": 0,
            "critical_count": 0,
            "failed_ac_ids": [r.get("ac_id") for r in (ac_check_results or [])
                              if r.get("failures")][:20],
            "fast_pass": True,
        }
        return {"verify_passed": True, "current_step": "verify_done",
                "smoke_defects": [], "architectural_defects": []}

    # 构建评估 prompt（含 AC 验收结果供 LLM 参考）
    ac_results_text = ""
    if ac_check_results:
        prod_pass = sum(1 for r in ac_check_results if not r.get("failures"))
        harness_fail_n = sum(1 for r in ac_check_results if r.get("harness_errors"))
        unverified_n = sum(1 for r in ac_check_results if r.get("unverified"))
        ac_results_text = (
            f"\n\n## AC 逐条验收结果（浏览器实际执行）\n"
            f"产品断言 {prod_pass}/{len(ac_check_results)} 通过；"
            f"另有 {harness_fail_n} 条存在脚本驱动失败（假阴性嫌疑，不计入产品缺陷，供定性判断）"
        )
        if unverified_n:
            ac_results_text += (
                f"；{unverified_n} 条因断言前提不成立而**未被验证**（如页面无 canvas 却断言 canvas 变化）。\n"
                f"  ⚠️ 未被验证 ≠ 产品失败：请勿据此判定实现有缺陷，应结合代码与截图自行判断该 AC 是否成立。"
                f"同时也不要把未验证当作通过。"
            )
        ac_results_text += ":\n"
        for r in ac_check_results:
            if r.get("failures"):
                status = "❌"
            elif r.get("harness_errors"):
                status = "⚠️"
            elif r.get("unverified"):
                status = "❔"
            else:
                status = "✅"
            ac_results_text += f"- {status} {r['ac_id']}: {r.get('label', '')}"
            failures = "; ".join(r.get("failures", []))
            if failures:
                ac_results_text += f" — [产品缺陷] {failures}"
                # P0-4：文本断言失败常常是「断言写错」而不是「代码写错」。
                # 实证（req 199 AC-1）：断言期望页面显示"黑方回合"，但点击落子后
                # 回合已经切换成"白方回合"——脚本跑成功了，是期望值写死了旧状态。
                # 这类失败被当成真缺陷，驱动了 5 轮无效修复。这里显式标注，
                # 让评估方先判断"是期望值错还是代码错"，别照着错的断言改代码。
                if ("期望包含" in failures or "文本不匹配" in failures) and "实际" in failures:
                    ac_results_text += (
                        "  ⚠️【可能是断言写错，不是产品缺陷】该断言期望一个固定文本，"
                        "但页面在交互后状态会变化（例如点击落子后回合由黑方切到白方，"
                        "而期望值仍写死为交互前的黑方回合，就必然失败）。"
                        "请先判断是这个期望值本身写错了，还是实现真的有问题；"
                        "若属前者，不要据此判定产品有缺陷，也不要照它去改代码。"
                    )
            herr = "; ".join(r.get("harness_errors", []))
            if herr:
                ac_results_text += f" — [脚本错误·可能假阴性] {herr}"
            na = "; ".join(r.get("not_applicable", []))
            if na:
                ac_results_text += f" — [未验证·断言前提不成立] {na}"
            if r.get("compromised"):
                # P2.5：脚本没跑成 + 断言失败 ⇒ 这条失败可能是幽灵。
                # 实测 139 条 AC 中 45.3% 属于此类；不标注的话 repair 轮会去修不存在的问题。
                ac_results_text += (
                    " — ⚠️【失败不可信】脚本未完整驱动页面（如点击步骤超时），"
                    "后续断言可能是在未操作的状态下得出的。"
                    "请先核实该缺陷是否真实存在，不要直接照此修改。"
                )
            ac_results_text += "\n"

    # 层1 冒烟结果注入评估 prompt（确定性证据，供 LLM 参考）
    smoke_text = ""
    if smoke_result.get("available"):
        smoke_text = "\n\n## 通用冒烟测试结果（浏览器确定性检查，非 LLM 判断）\n"
        for cname, cok in (smoke_result.get("checks") or {}).items():
            smoke_text += f"- {'✅ 通过' if cok else '❌ 未通过'} {cname}\n"
        for d in smoke_defects:
            smoke_text += f"- ❌ [{d['type']}] {d['message']}\n  证据: {d.get('evidence', '')}\n"

    # ---- 视觉证据：让 ui_quality 不再是盲评 ----
    # 截图每轮都已生成（.task/evaluator/screenshot.png），但从未传给评估 LLM，
    # 于是 ui_quality 是"读 CSS 代码猜的"—— req 199 的评分 2→5→4→5 无规律波动
    # 就是这么来的，也导致"页面丑"永远进不了修复循环。
    # 按 EVALUATOR_VISION_MODE 决定传什么；默认 dom_css（确定性取值，零外部依赖）。
    _verify_progress("vision")
    vision_text = ""
    try:
        from config import settings as _vs
        _v_mode = str(
            getattr(_vs, "EVALUATOR_VISION_MODE", "dom_css") or "dom_css"
        ).strip().lower()
    except Exception:
        _v_mode = "dom_css"

    if _v_mode in ("dom_css", "auto"):
        try:
            from harness.tools.preview_runner import extract_dom_css_summary
            vision_text = extract_dom_css_summary(
                workspace.path / "index.html", preview_url=preview_url
            ) or ""
        except Exception as e:
            logger.warning(f"[Verify] 视觉证据提取失败（降级为无）: {e}")

    # 图片模式：先把截图落地，再按厂商能力转成可传的内容块
    vision_images: list = []
    if _v_mode in ("image_url", "base64", "auto"):
        try:
            from config import settings as _vs
            _vendor = str(getattr(_vs, "LLM_VENDOR", "auto") or "auto").strip().lower()
            if _vendor == "auto":
                _vendor = ("deepseek" if "deepseek" in str(
                    getattr(_vs, "LLM_BASE_URL", "")).lower() else "agnes")
        except Exception:
            _vendor = "agnes"
        _shot_path = workspace.path / ".task" / "evaluator" / "screenshot.png"
        if not _shot_path.exists():
            try:
                from harness.tools.preview_runner import capture_screenshot
                capture_screenshot(
                    workspace.path / "index.html", _shot_path, preview_url=preview_url
                )
            except Exception as e:
                logger.debug(f"[Verify] 截图失败（不传图）: {e}")
        vision_images = _build_vision_images(_shot_path, _v_mode, vendor=_vendor)

    if vision_text:
        logger.info(f"[Verify] 注入视觉证据（mode={_v_mode}）: {len(vision_text)} chars")
        _verify_trace.done("vision", f"{_v_mode} · {len(vision_text)} 字符")
    else:
        _verify_trace.mark("vision", "skipped", "无可采集的视觉证据")
    if vision_images:
        logger.info(f"[Verify] 附带 {len(vision_images)} 张截图参与评估（mode={_v_mode}）")
    elif _v_mode in ("image_url", "base64"):
        logger.warning(
            f"[Verify] 视觉模式={_v_mode} 当前不可用（无公网 URL / 端点不支持 base64），"
            f"本次评估不含视觉证据"
        )

    # 需求阶段带病放行时，把弱项清单交给评估器。
    # 没有这段，evaluator 会把一条本来就不可操作的 AC 当成有效证据，
    # 对着空气挑产品缺陷，驱动 repair 去改并不存在的问题。
    _dod_section = ""
    if plan_dod_issues:
        _dod_section = (
            "\n\n## 需求阶段已标记的验收项缺陷（程序化校验得出，不是本次执行结果）\n"
            "下面这些问题在**生成验收条件时**就存在，说明对应的断言天生不可靠：\n"
            "它通过了不代表功能可用，它失败了也不代表产品有缺陷。\n"
            "对这些项请先怀疑断言本身，不要据此判定产品缺陷。\n"
            + "\n".join(f"- {i}" for i in plan_dod_issues)
        )

    evaluator_prompt = load_prompt("verify/evaluator.md")
    user_prompt = f"""## 原始需求
{requirement}

## SPEC / 验收条件
{spec_content or "(无 SPEC)"}

## 代码文件
{code_text}

## 浏览器执行结果
```json
{json.dumps(browser_result, ensure_ascii=False, indent=2)}
```
{ac_results_text}{smoke_text}{_dod_section}
{(chr(10) + chr(10) + vision_text) if vision_text else ""}

请基于以上信息，按照 Evaluator 的评估维度和输出格式，给出结构化评估结果。"""

    # 深度评估是验证阶段最慢的一步（实测单次 45~107s，失败还会重试），
    # 此前它连同前面的契约/DoD/视觉三步全程零推送 → req 207 实测界面
    # 停在「正在做通用交互冒烟测试」约 2 分钟不动。
    _verify_progress("evaluate")
    logger.info(f"[Verify] 启动评估: {len(code_files)} 个文件, prompt 长度={len(user_prompt)}")

    def _call_evaluator(focus_instruction: str, max_tokens: int = 3000):
        """调用 Evaluator LLM，支持 finish_reason=length 自动重试"""
        prompt = user_prompt + "\n\n" + focus_instruction
        client = get_client()
        _t0 = time.time()
        response = client.chat(
            prompt=prompt,
            system_prompt=evaluator_prompt,
            use_memory=False,
            max_tokens=max_tokens,
            # 实测 thinking 评估耗时 54~107s，90s 会误杀；按实测吞吐留足余量
            timeout=_evaluator_timeout(),
            thinking='enabled',
            images=vision_images or None,
        )
        _log_llm_turn_safe(
            state.get("requirement_id"), None, client, evaluator_prompt, prompt,
            response, thinking='enabled',
            latency_ms=round((time.time() - _t0) * 1000, 1),
            stage=STAGE_VERIFYING,
            turn_index=int(_md(state).get("turn_index") or 0),
            trace_id=_md(state).get("trace_id") or None,
        )
        # finish_reason=length → 截断，用更大 max_tokens 重试
        if response.finish_reason == "length" and max_tokens < 6000:
            logger.warning(
                f"[Verify] 检测到截断 (finish_reason=length, max_tokens={max_tokens})，"
                f"以 max_tokens=6000 重试"
            )
            retry_response = client.chat(
                prompt=prompt,
                system_prompt=evaluator_prompt,
                use_memory=False,
                max_tokens=6000,
                # 6000 tokens 按实测吞吐约需 150s（此前 120s 会撞超时）
                timeout=_evaluator_timeout(retry=True),
                thinking='enabled',
                images=vision_images or None,
            )
            if not retry_response.is_error and retry_response.content:
                response = retry_response
        return response

    def _parse_evaluator_response(response) -> dict:
        """解析 Evaluator 响应，提取 JSON 结果"""
        if response.is_error or not response.content:
            return {}
        content = response.content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return {}

    # ---- 单次全维度评估（原先 Correctness+Quality 双视角 = 2 次 LLM 调用，合并为 1 次减半耗时）----
    try:
        from llm.client import CircuitBreakerOpenError
        client = get_client()
        _circuit_breaker_hit = False

        eval_focus = (
            "请基于以上信息，按照 Evaluator 的评估维度和输出格式，一次性给出结构化评估结果。\n"
            "必须覆盖 functionality / runtime / acceptance / ui_quality / code_quality 五个维度，"
            "并给出 verdict、overall_score、findings（只返回 JSON）。"
        )
        try:
            eval_response = _call_evaluator(eval_focus, max_tokens=4000)
            combined = _parse_evaluator_response(eval_response)
        except CircuitBreakerOpenError as e:
            logger.warning(f"[Verify] 熔断器打开，跳过评估: {e}")
            combined = {}
            _circuit_breaker_hit = True

        # ---- 解析失败（非熔断）：用更强约束重试一次 ----
        if not combined and not _circuit_breaker_hit:
            logger.warning("[Verify] 单次评估解析失败，以更强约束重试")
            retry_focus = (
                eval_focus + "\n\n⚠️ 必须只返回一个合法 JSON 对象，"
                "包含 verdict/summary/score/overall_score/findings 字段。"
            )
            try:
                retry_response = _call_evaluator(retry_focus, max_tokens=4000)
                combined = _parse_evaluator_response(retry_response)
            except CircuitBreakerOpenError as e:
                logger.warning(f"[Verify] 重试评估熔断: {e}")
                combined = {}
                _circuit_breaker_hit = True

        # ---- 熔断/解析失败降级：仅基于 preview + AC 结果判定 ----
        if not combined:
            logger.warning(
                "[Verify] LLM 不可用或评估失败，降级为仅基于 preview + AC 结果判定"
            )
            ac_all_passed = (
                len(ac_check_results) > 0 and
                all(r["passed"] for r in ac_check_results)
            )
            browser_errors = browser_result.get("errors", [])
            has_browser_errors = len(browser_errors) > 0

            if preview_clean and ac_all_passed and not smoke_defects:
                verdict = "PASS"
                overall_score = 8.0
                summary = "LLM 不可用，基于浏览器验证和 AC 验收结果判定通过"
            elif has_browser_errors or smoke_defects:
                verdict = "NEEDS_WORK"
                overall_score = 3.0
                summary = (
                    f"LLM 不可用，浏览器报 {len(browser_errors)} 个错误，"
                    f"通用冒烟发现 {len(smoke_defects)} 个确定性缺陷"
                )
            else:
                verdict = "NEEDS_WORK"
                overall_score = 5.0
                summary = "LLM 不可用，无法深度评估，保守判定为 NEEDS_WORK"

            combined = {
                "verdict": verdict,
                "summary": summary,
                "score": {"functionality": 5, "runtime": 5, "ui_quality": 5, "acceptance": 5, "code_quality": 5},
                "overall_score": overall_score,
                "findings": [
                    {
                        "severity": "major",
                        "dimension": "runtime",
                        "description": "LLM 评估服务不可用，无法进行深度代码审查",
                        "evidence": "熔断器已打开，LLM API 连续失败",
                        "suggestion": "请自行检查代码功能是否满足需求，确认无误后重新提交评估",
                    }
                ] if verdict == "NEEDS_WORK" else [],
                "browser_result": browser_result,
                "ac_results": ac_check_results,
                "degraded": True,
            }

        # 从合并结果中提取字段
        verdict = combined.get("verdict", "NEEDS_WORK")
        findings = combined.get("findings", [])
        overall_score = combined.get("overall_score", 0.0)
        score = combined.get("score", {})
        logger.info(
            f"[Verify] 评估完成: verdict={verdict}, overall_score={overall_score}, findings={len(findings)}"
        )

        # ---- 确定性证据下限（证据等级模型核心，审查报告 Phase 3.1） ----
        # 冒烟缺陷 / 浏览器错误 / AC 实测失败是机器实测事实，LLM verdict 无权推翻：
        # 任一存在而 LLM 判 PASS → 强制翻转为 NEEDS_WORK 并把证据并入 findings。
        # AC 有 harness_errors（如沙箱 iframe 空白致全 selector timeout）时，其 failures
        # 多为驱动失败的级联（连 createBtn 都点不到 → 后续 assert 必然"元素不存在"），
        # 属"无法可靠执行"而非产品缺陷——不计入确定性 critical，交由 LLM 评估裁量。
        ac_failed_results = [
            r for r in ac_check_results
            if r.get("failures") and not r.get("harness_errors")
        ]
        browser_errors = [
            f"[{e.get('type', 'error')}] {e.get('message', '')}"
            for e in browser_result.get("errors", [])
            if e.get("message")
        ]
        deterministic_findings: list[dict] = []
        deterministic_findings += [
            {
                "severity": d.get("severity", "major"),
                "dimension": d.get("dimension", "runtime"),
                "description": f"[{d['type']}] {d['message']}",
                "evidence": d.get("evidence", ""),
                "suggestion": d.get("suggestion", ""),
                "_source": "smoke",
            }
            for d in smoke_defects
        ]
        deterministic_findings += [
            {
                "severity": "critical",
                "dimension": "runtime",
                "description": f"浏览器运行时错误: {msg[:160]}",
                "evidence": msg,
                "suggestion": "定位报错源文件并修复后重新 run_preview 验证",
                "_source": "browser",
            }
            for msg in browser_errors
        ]
        deterministic_findings += [
            {
                "severity": "critical",
                "dimension": "acceptance",
                "description": f"验收条件未达成 {r['ac_id']}: {r.get('label', '')} — {'; '.join(r.get('failures', []))}",
                "evidence": "; ".join(r.get("failures", [])),
                "suggestion": "按 how_to_verify 描述补齐该场景的界面元素与交互逻辑",
                "_source": "ac",
            }
            for r in ac_failed_results
        ]

        if deterministic_findings:
            seen_desc = {f.get("description") for f in findings}
            findings = deterministic_findings + [
                f for f in findings if f.get("description") not in seen_desc
            ]
            # 同一条 AC 的验收失败只保留一条 critical。
            # 确定性通道记「验收条件未达成 AC-1: …」，LLM 又记「[ac_failure] 验收条件 AC-1 …」，
            # 描述不同、事实同一 —— 两条都进 critical 会把缺陷数虚高一倍
            # （req 198 实测：2 条未达成 AC 记出 4 条 critical，直接触发交付拦截）。
            import re as _re_ac
            _seen_ac: set[str] = set()
            _deduped: list[dict] = []
            for _f in findings:
                _desc = _f.get("description") or ""
                _prefix_dup = _desc.startswith("[ac_failure]") or _desc.startswith("验收条件未达成")
                if _prefix_dup:
                    _m = _re_ac.search(r"AC-\d+", _desc)
                    if _m:
                        if _m.group(0) in _seen_ac:
                            continue
                        _seen_ac.add(_m.group(0))
                _deduped.append(_f)
            if len(_deduped) != len(findings):
                logger.info(
                    f"[Verify] 验收缺陷去重: {len(findings)} → {len(_deduped)} 条"
                )
            findings = _deduped
            if verdict == "PASS":
                logger.warning(
                    f"[Verify] LLM 判定 PASS 被确定性证据推翻"
                    f"(冒烟={len(smoke_defects)}, 浏览器错误={len(browser_errors)}, "
                    f"AC失败={len(ac_failed_results)}) → 强制 NEEDS_WORK"
                )
            verdict = "NEEDS_WORK"
            if not overall_score or overall_score >= 6:
                overall_score = 5.5

        # 将 verdict 转为 verify_passed
        state["verify_passed"] = (verdict == "PASS")
        state["current_step"] = "verify_done"
        # 评估步在这里才算完成：确定性证据推翻 LLM 判定（上面那段）会改写
        # verdict 与分数，提前记录会把用户看到的结论与卡片对不上。
        _verify_trace.done(
            "evaluate", f"{overall_score}/10 · {len(findings)} 条待修"
        )

        # 截图留档：确定性证据 + 截图一起构成评估证据链（多模态评估的前置资产）
        screenshot_path = None
        try:
            from harness.tools.preview_runner import capture_screenshot
            screenshot_path = capture_screenshot(
                workspace.path / "index.html",
                workspace.path / ".task" / "evaluator" / "screenshot.png",
                preview_url=preview_url,
            )
        except Exception as e:
            logger.debug(f"[Verify] 截图失败（跳过）: {e}")

        # 构建评估结果对象（始终构建，供后续 QA 反馈使用）
        evaluator_result = {
            "verdict": verdict,
            "summary": combined.get("summary", ""),
            "score": score,
            "overall_score": overall_score,
            "findings": findings,
            "ac_results": ac_check_results,  # Playwright 实际执行的逐条 AC 结果
            "browser_result": browser_result,
            "screenshot": screenshot_path,
            "smoke_result": {
                "available": smoke_result.get("available", False),
                "checks": smoke_result.get("checks", {}),
                "defects": smoke_defects,
            },
            "timestamp": __import__('time').time(),
        }
        # 持久化到 .task/evaluator/result.json（全复杂度通用，支持 API 读取）
        try:
            workspace.write(
                ".task/evaluator/result.json",
                json.dumps(evaluator_result, ensure_ascii=False, indent=2)
            )
            logger.info(f"[Verify] 评估结果已写入 .task/evaluator/result.json")
        except Exception as e:
            logger.warning(f"[Verify] 写入结果文件失败: {e}")

        # 推送 evaluator_result SSE 事件到前端（实时展示评分面板）
        try:
            tl = get_tool_loop(state)
            if tl and tl.sse:
                tl.sse.evaluator_result(state.get("requirement_id", 0), evaluator_result)
                logger.info(f"[Verify] 已推送 evaluator_result SSE 事件")
        except Exception as e:
            logger.warning(f"[Verify] 推送 evaluator_result SSE 失败: {e}")

        # 添加评估对话（作为 QA 反馈注入，coder 再次进入时能看到）
        dimension_scores = ", ".join(
            f"{k}: {v}/10" for k, v in score.items()
        ) if score else f"overall: {overall_score}/10"

        if verdict != "PASS":
            # NEEDS_WORK 必须伴随具体 findings——不再重试 LLM、更不放水
            # （原「矛盾→保守 PASS」放水阀已按证据等级模型删除）。
            # findings 为空时从确定性证据合成；连确定性证据都没有，
            # 则合成一条"评估不可判定"，交由交付门禁按 critical 清零规则处理。
            if not findings:
                findings = [
                    f for f in deterministic_findings if f.get("_source")
                ] or [{
                    "severity": "major",
                    "dimension": "runtime",
                    "description": (
                        f"评估器给出 NEEDS_WORK（{overall_score}/10）但未列出具体问题，"
                        f"按评估异常处理"
                    ),
                    "evidence": "LLM 评估输出自相矛盾（低分无 findings）",
                    "suggestion": "对照验收条件自查：入口调用、事件绑定、浏览器 console 错误、引用完整性",
                }]
                logger.info(
                    f"[Verify] NEEDS_WORK 缺少 findings，已合成 {len(findings)} 条"
                )
            # NEEDS_WORK：递增 repair_count（graph 路由的修复预算依据）
            meta = dict(state.get("metadata") or {})
            meta["repair_count"] = meta.get("repair_count", 0) + 1
            state["metadata"] = meta

        # AC 实测通过的条目回写两级契约（evidence_found=true）
        _mark_acs_checked(
            state,
            [r["ac_id"] for r in ac_check_results
             if r.get("passed") and not r.get("harness_errors")],
        )

        # 构建 QA 反馈消息（对话式注入，coder 在下一轮 ToolCallLoop 中自然看到）
        # 增强修复指令：对每种 severity 级别给出精确的修复提示
        critical_findings = [f for f in findings if f.get("severity") == "critical"]
        major_findings = [f for f in findings if f.get("severity") == "major"]
        minor_findings = [f for f in findings if f.get("severity") == "minor"]

        qa_feedback = (
            f"## 代码评估: {'✅ PASS' if verdict == 'PASS' else '❌ NEEDS_WORK'}\n\n"
            f"**评分**: {overall_score}/10 ({dimension_scores})\n\n"
            f"**摘要**: {combined.get('summary', '')}\n\n"
        )
        # 确定性缺陷（浏览器实测）单列置顶：这不是 LLM 观点，是真实复现的缺陷
        if smoke_defects:
            qa_feedback += (
                "## 🔴 确定性缺陷（浏览器实测复现，最高优先级，必须先修复）\n\n"
                + "\n\n".join(
                    f"**{d['type']}** — {d['message']}\n"
                    f"📍 复现证据: {d.get('evidence', '')}\n"
                    f"🔧 修复方案: {d.get('suggestion', '')}"
                    for d in smoke_defects
                )
                + "\n\n以上缺陷由无头浏览器真实操作复现（非 LLM 判断），修复它们之前不要处理其他问题。\n\n"
            )
        if findings:
            qa_feedback += "**发现的问题**:\n" + "\n".join(
                f"- [{f.get('severity', '?')}] {f.get('description', '')}"
                + (f"\n  📍 证据: {f.get('evidence', '')}" if f.get('evidence') else "")
                + (f"\n  💡 修复建议: {f.get('suggestion', '')}" if f.get('suggestion') else "")
                + (f"\n  📂 维度: {f.get('dimension', '?')}")
                for f in findings
            )
            qa_feedback += "\n\n**修复指南**:\n"

            if critical_findings:
                qa_feedback += (
                    f"- 🔴 **{len(critical_findings)} 个严重问题**须优先修复："
                    f"{', '.join(f.get('description', '')[:80] for f in critical_findings)}\n"
                )
            if major_findings:
                qa_feedback += (
                    f"- 🟠 **{len(major_findings)} 个重要问题**："
                    f"{', '.join(f.get('description', '')[:80] for f in major_findings)}\n"
                )
            if minor_findings:
                qa_feedback += (
                    f"- 🟡 **{len(minor_findings)} 个建议优化**可最后处理\n"
                )

            qa_feedback += (
                "- 逐条修复以上问题，每修完一个问题用 run_preview 验证\n"
                "- 优先使用 edit_file 做局部修改；如果 edit_file 连续失败 2 次，改用 write_file 重写\n"
                "- 如果 finding 包含 📍 证据，先用 read_file 读取对应文件的问题区域再修改\n"
                "- 修复完成后调用 run_preview 确认所有问题已解决\n"
            )
        else:
            qa_feedback += "无问题发现"

        state.setdefault("dialogue_history", []).append({
            "role": "agent",
            "name": QA_NAME,
            "content": qa_feedback,
            "status": "completed",
        })

        # 如果有 findings，保存到 state 供 repair 使用
        if findings:
            state.setdefault("role_outputs", {})["Evaluator"] = json.dumps(
                evaluator_result, ensure_ascii=False
            )

        # ---- 缺陷类别路由准备（审查报告 Phase 3.2） ----
        # 架构类缺陷（模块加载/CDN/文件缺失/入口断裂）结构上超出
        # defect_repair 的能力（禁止新建/重构文件），必须携带根因卡片
        # 回 coder 做跨文件重构；局部类才走小上下文定向修复。
        architectural_defects: list = []
        local_defects: list = list(smoke_defects)
        try:
            from harness.constraints.environment_contract import (
                classify_defects, build_root_cause_card,
            )
            architectural_defects, local_defects = classify_defects(smoke_defects)
            if architectural_defects:
                card = build_root_cause_card(architectural_defects)
                state.setdefault("dialogue_history", []).append({
                    "role": "system", "name": QA_NAME,
                    "content": card,
                    "hidden": True,
                    "preserve": True,
                })
                logger.info(
                    f"[Verify] {len(architectural_defects)} 个架构类缺陷将回 coder 重构"
                    f"（根因卡片已注入）: {[d.get('type') for d in architectural_defects]}"
                )
        except Exception as e:
            logger.warning(f"[Verify] 缺陷分类失败（按全部局部处理）: {e}")
            architectural_defects, local_defects = [], list(smoke_defects)

        # ---- 确定性实测证据直接注入对话上下文（不依赖 evaluator 转述） ----
        # req 148 事故：AC 抓到 4 条真实缺陷，evaluator 却判 PASS 8.6，
        # findings 里只有 2 条无关痛痒的代码风格建议，coder 拿到的反馈里
        # 从头到尾没有出现过「方向键无响应」——8 轮迭代于是全部落空。
        # 机器实测结果必须**独立于 LLM 评估**进入上下文，评估器无权替我们滤掉。
        _all_defects = list(architectural_defects) + list(local_defects)
        if _all_defects:
            _lines = [
                "## 🔬 确定性实测证据（浏览器实跑结果，非 LLM 判断）",
                "以下每一条都必须逐条修复；若你认为某条是误报，先给出复现证据再跳过。",
            ]
            for _d in _all_defects[:8]:
                _lines.append(f"- **{_d.get('type')}**: {str(_d.get('message', ''))[:220]}")
                if _d.get("evidence"):
                    _lines.append(f"  证据: {str(_d.get('evidence'))[:200]}")
            state.setdefault("dialogue_history", []).append({
                "role": "system", "name": QA_NAME,
                "content": "\n".join(_lines),
                "hidden": True,
                "preserve": True,
            })
            logger.info(
                f"[Verify] 已注入 {len(_all_defects)} 条确定性实测证据到对话上下文"
            )

        logger.info(
            f"[Verify] 评估完成: verdict={verdict}, "
            f"score={overall_score}, findings={len(findings)}"
        )

        # 验收结论留给 _traced_node 切面落里程碑事件。meta 要能直接回答
        # 「为什么没过」：哪几条 AC 未达成、几条 critical，不必点开 LLM 原文猜。
        state["verify_verdict"] = {
            "verdict": verdict,
            "score": overall_score,
            "findings": len(findings or []),
            "critical_count": sum(
                1 for f in (findings or []) if f.get("severity") == "critical"),
            "failed_ac_ids": [r.get("ac_id") for r in (ac_check_results or [])
                              if r.get("failures")][:20],
            "defect_count": len(list(architectural_defects) + list(local_defects)),
            "ac_total": len(ac_check_results or []),
        }

        # ---- 落一条**可见**的验证摘要（「质量工程师做了什么」的唯一持久记录）----
        # 实时通道（progress / verify_step）都是瞬时的：刷新页面就没了，
        # evaluator_result 事件也不落库。此前用户跑完一轮刷新后完全查不到
        # 验证结论，只能靠猜。这条消息刻意**不带 hidden**。
        try:
            _qa_msg = build_summary_message(
                _verify_trace.to_card(
                    verdict=verdict,
                    score=overall_score,
                    findings=findings,
                    ac_results=ac_check_results,
                    smoke_result=smoke_result,
                    browser_result=browser_result,
                ),
                QA_NAME,
            )
            state.setdefault("dialogue_history", []).append(_qa_msg)
            # 同一条消息推一份实时（专用事件，dialogue 通道会丢结构化字段）
            publish_summary(_verify_sse, state.get("requirement_id"), _qa_msg)
            logger.info("[Verify] 已落库验证摘要卡（qa_summary）")
        except Exception as e:
            # 摘要卡失败不能影响验证结论与修复流程
            logger.warning(f"[Verify] 落库验证摘要失败（忽略）: {e}")

    except Exception as e:
        logger.warning(f"[Verify] 评估异常: {e}，保守判定为 NEEDS_WORK")
        return {"verify_passed": False, "current_step": "verify_done",
                "smoke_defects": [],
                "architectural_defects": [],
                "error": f"评估异常: {e}",
                "metadata": state.get("metadata") or {}}

    # verify_node 原地修改了 state（dialogue_history, role_outputs 等），
    # 只返回变更字段，避免 add reducer 重复拼接 dialogue_history；
    # metadata 显式返回，避免依赖 langgraph 未文档化的浅拷贝副作用。
    # smoke_defects 只保留局部类（defect_repair 的输入）；
    # 架构类经 architectural_defects 由 graph 路由回 coder。
    return {"verify_passed": state.get("verify_passed", False),
            "current_step": state.get("current_step", "verify_done"),
            "smoke_defects": local_defects,
            "architectural_defects": architectural_defects,
            "metadata": state.get("metadata") or {}}


# ==================== Defect Repair 节点（小上下文定向修复） ====================


# 定向修复单轮上下文预算：最多携带 6 个文件、单文件 8000 字符
_DEFECT_REPAIR_MAX_FILES = 6
_DEFECT_REPAIR_FILE_CHAR_CAP = 8_000

# Evaluator 上下文预算（req 147 修正：原先无条件 content[:6000] 且无截断标记，
# 导致 game.js 的 move() 整个方法体被切掉，评估 LLM 直接幻觉出「move 未定义」）
_EVALUATOR_TOTAL_CHAR_BUDGET = 40_000   # 全额完整装载的总预算
_EVALUATOR_FILE_CHAR_CAP = 8_000        # 超出总预算后，单文件的降级上限
_EVALUATOR_MAIN_FILE_FULL_CAP = 20_000  # 主逻辑文件只要不超过这个体积就一律完整给出


def _build_evaluator_code_blocks(workspace, code_files: list) -> str:
    """拼装 evaluator 的代码上下文（req 147 修正：禁止静默截断）

    原实现 `content[:6000]` 无条件切一刀且不带任何标记，game.js 的 move()
    整个方法体被切掉，评估 LLM 于是把「我没看到」当成「代码没写」，
    幻觉出 `move 未定义` 的根因结论。

    新策略：
    1. 主逻辑文件（index.html / main / game / app，且体积 ≤ 20k）一律完整给出
    2. 其余文件在 40k 总预算内完整；超预算才降级到 8k
    3. 降级时的截断说明必须写在 ``` 代码块**外面**（写在里面会被当成源码）
       ，并明确告诉 LLM「被截断的部分真实存在，不得判为缺失」
    """
    def _lang_of(fname: str) -> str:
        if fname.endswith('.html'):
            return 'html'
        if fname.endswith('.css'):
            return 'css'
        if fname.endswith('.js'):
            return 'javascript'
        return ''

    def _priority(fname: str) -> int:
        low = fname.lower()
        for i, kw in enumerate(["index.html", "main.", "game.", "app.", "index."]):
            if kw in low:
                return i
        return 99

    loaded = []
    for fname in code_files:
        try:
            loaded.append((fname, workspace.read(fname)))
        except Exception:
            loaded.append((fname, None))

    loaded.sort(key=lambda x: (_priority(x[0]), -(len(x[1]) if x[1] else 0)))

    blocks = []
    used = 0
    truncated_any = False
    for fname, content in loaded:
        if content is None:
            blocks.append(f"### {fname}\n(无法读取)")
            continue
        line_count = content.count('\n') + 1
        lang = _lang_of(fname)
        is_main = _priority(fname) < 99
        if len(content) <= _EVALUATOR_MAIN_FILE_FULL_CAP and (
            is_main or used + len(content) <= _EVALUATOR_TOTAL_CHAR_BUDGET
        ):
            used += len(content)
            blocks.append(
                f"### {fname} ({line_count} 行，完整)\n```{lang}\n{content}\n```"
            )
        else:
            shown = content[:_EVALUATOR_FILE_CHAR_CAP]
            truncated_any = True
            note = (
                f"\n> ⚠️ 上下文限制：以上为 `{fname}` 的前 "
                f"{_EVALUATOR_FILE_CHAR_CAP} 字符（全文 {len(content)} 字符 / "
                f"{line_count} 行），**不是完整文件**。被截断的后半部分是真实存在的代码，"
                f"**不得**将其推断为「未定义 / 缺失 / 语法错误」；"
                f"若你的结论依赖该文件不可见部分，请标注 ❔ 未验证，而不是判失败。\n"
            )
            blocks.append(f"### {fname}\n```{lang}\n{shown}\n```{note}")

    header = ""
    if truncated_any:
        header = (
            "> ⚠️ 部分文件因上下文预算被截断，截断点已在对应文件下方标出。"
            "被截断的内容**依然存在于磁盘**，不要推断为缺失或报错。\n\n"
        )
    return header + ("\n\n---\n\n".join(blocks) if blocks else "(无代码文件)")


def _content_looks_complete(fname: str, content: str) -> tuple[bool, str]:
    """检测 LLM 返回的文件内容是否被截断（写回前的完整性闸门）

    实测教训（需求106）：LLM 可能在 JSON 字符串值内部截断文件内容，
    JSON 本身合法但文件止于半条语句，写回后直接毁掉整个应用。
    """
    stripped = content.rstrip()
    if fname.endswith(".html"):
        if not stripped.lower().endswith("</html>"):
            return False, "HTML 未以 </html> 结尾"
    elif fname.endswith(".js") or fname.endswith(".css"):
        # 先剥离字符串字面量与注释再配平：字符串/注释内的括号（如
        # console.log("}")、正则、模板字面量）不应计入，否则会误拒合法补丁
        code = _strip_strings_and_comments(content)
        if code.count("{") != code.count("}"):
            return False, f"花括号不配平 {{={code.count('{')} }}={code.count('}')}"
        if fname.endswith(".js") and code.count("(") != code.count(")"):
            return False, "JS 圆括号不配平"
    return True, ""


def _strip_strings_and_comments(code: str) -> str:
    """把 JS/CSS 中的字符串字面量与注释替换为空，用于括号配平统计。

    覆盖：'...' / "..." / `...`（模板字面量，含 ${}）、// 行注释、/* */ 块注释。
    不做完整 JS 语法解析，仅尽力避免最常见误判；配平检查本就是启发式。
    """
    import re
    # 字符串与注释统一替换为占位（不删除行结构，保持 \n 数量不变）
    out = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch == "'" or ch == '"' or ch == "`":
            quote = ch
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == quote:
                    break
                j += 1
            out.append(" " * (j - i + 1))
            i = j + 1
        elif ch == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
        elif ch == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            if j == -1:
                j = n
            out.append(" " * (j + 2 - i))
            i = j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _build_ac_failure_defects(ac_check_results: list, ac_steps_text: dict) -> list:
    """把 AC 断言失败转成可执行的确定性缺陷（req 148 修复）。

    事故：AC 逐条验收抓到了 4 条真实产品缺陷（方向键无响应、棋盘无变化），
    但 ac_check_results 此前**只用于给 evaluator 打分**，从不转成 defect，
    于是这些硬证据永远到不了修复环节——coder 拿到手的只有静态分析误报，
    整个第二轮迭代全花在查一个不存在的问题上。
    """
    defects = []
    for r in ac_check_results or []:
        fails = [f for f in (r.get("failures") or []) if f]
        if not fails:
            continue
        steps = (ac_steps_text or {}).get(r.get("ac_id"), "")
        ev = "; ".join(fails)[:300]
        defects.append({
            "type": "ac_failure",
            "severity": "critical",
            "dimension": "acceptance",
            "message": (
                f"验收条件 {r.get('ac_id')} 「{r.get('label', '')}」未通过：{ev}"
            ),
            # evidence 带上 selector / 操作序列，供 _extract_root_cause_files 定位根因文件
            "evidence": f"AC {r.get('ac_id')} {r.get('label', '')} {ev} {steps}",
            "suggestion": (
                "这是浏览器实测的**确定性失败**（不是 LLM 猜测）。按复现步骤定位到"
                "对应的初始化与事件处理代码：确认初始化流程真的执行到了「产生可见结果」"
                "那一步（例如棋盘初始化时是否真的生成了初始方块），确认状态机的取值分支"
                "能被当前状态命中（死分支会导致按键完全无响应），再检查选择器与绑定时机。"
                f"复现步骤：{steps}"
            ),
            "ac_id": r.get("ac_id"),
            "_source": "ac_check",
        })
    return defects


def _extract_root_cause_files(workspace, files: list, defects: list) -> set:
    """从缺陷证据里确定性定位「根因文件」，这些文件定向修复时必须完整给出。

    req 147 复盘：game.js 有 10,939 字符，修复 prompt 里只给了前 8,042，
    断点正好落在 move() 中间。修复 LLM 于是「认为文件被截断」，自作主张补全了
    后半段——改动落在完全不需要动的地方，真正的 prototype 挂载一行没碰。
    结论：**被指向为根因的文件一律不截断**，宁可少带几个别的文件的全文。
    """
    import re as _re

    stopwords = {
        "the", "is", "not", "function", "undefined", "error", "typeerror", "uncaught",
        "cannot", "read", "properties", "null", "true", "false", "at", "of", "in",
        "uncaught", "pageerror", "browser", "runtime",
    }
    tokens = set()
    for d in defects or []:
        text = " ".join(
            str(d.get(k, "")) for k in ("message", "evidence", "suggestion")
        )
        for tok in _re.findall(r"[A-Za-z_$][A-Za-z0-9_$]{2,}", text):
            low = tok.lower()
            if low not in stopwords and not low.startswith("http"):
                tokens.add(tok)
    if not tokens:
        return set()

    root_files = set()
    for fname in files:
        try:
            content = workspace.read(fname)
        except Exception:
            continue
        hits = sum(content.count(t) for t in tokens)
        if hits:
            root_files.add(fname)
        elif len(tokens) <= 3:
            # 证据很少时退化为「报错里出现的标识符」强匹配
            if any(t in content for t in tokens):
                root_files.add(fname)
    return root_files


def _collect_defect_repair_context(workspace, defects: list = None) -> tuple[str, list[str]]:
    """收集定向修复的最小上下文：index.html 优先，其余 js/css 按相关性排序

    截断策略（req 147 修正）：
    - 根因文件（证据里提到的标识符所在文件）**完整给出**，绝不截断
    - 其余文件仍受字符上限保护，且截断标记放在代码块**外面**并明确说明后果
    """
    files = [f for f in workspace.list()
             if not f.startswith("docs/") and not f.startswith(".task/")]
    html = [f for f in files if f.endswith(".html")]
    js = [f for f in files if f.endswith(".js")]
    css = [f for f in files if f.endswith(".css")]

    root_files = _extract_root_cause_files(workspace, files, defects or [])

    def _relevance(name: str) -> int:
        # 入口/主逻辑文件优先（main/game/app/index 命名的权重更高）
        if name in root_files:
            return -1  # 根因文件排最前
        low = name.lower()
        for i, kw in enumerate(["main", "game", "app", "index", "storage", "util"]):
            if kw in low:
                return i
        return 99

    ordered = (
        sorted(html, key=_relevance)
        + sorted(js, key=_relevance)
        + sorted(css, key=_relevance)
    )[:_DEFECT_REPAIR_MAX_FILES]

    blocks = []
    for fname in ordered:
        try:
            content = workspace.read(fname)
            lang = fname.rsplit(".", 1)[-1] if "." in fname else ""
            if fname in root_files:
                blocks.append(
                    f"### {fname}（根因文件，已完整给出，请勿自行“补全”）\n"
                    f"```{lang}\n{content}\n```"
                )
            else:
                note = ""
                if len(content) > _DEFECT_REPAIR_FILE_CHAR_CAP:
                    content = content[:_DEFECT_REPAIR_FILE_CHAR_CAP]
                    note = (
                        f"\n> ⚠️ 上下文限制：以上为该文件前 {_DEFECT_REPAIR_FILE_CHAR_CAP} 字符，"
                        f"**不是完整文件**。本文件不是本次缺陷的根因文件，"
                        f"若你要修改它，请只修改可见部分，不要凭推测重写未展示的其余内容。\n"
                    )
                blocks.append(f"### {fname}\n```{lang}\n{content}\n```{note}")
        except Exception:
            blocks.append(f"### {fname}\n(无法读取)")
    return "\n\n".join(blocks) if blocks else "(无文件)", ordered


def _build_vision_images(screenshot_path, mode: str, vendor: str = "agnes") -> list:
    """按 EVALUATOR_VISION_MODE 把截图转成可传给 LLM 的图片内容块。

    返回空列表 = 本次不传图，调用方降级为纯文本评估（不静默失败，会打日志）。

    厂商能力差异（已核实官方文档）：
      - agnes-3.0-flash：仅支持**公网可访问 URL**，不支持 base64
      - deepseek-flash ：支持 base64 data URI / 公网 URL / Files API，
                         图片自动缩放，**每张最多 1024 token**
      - 共同约束：图片只能出现在 user 消息，放 system/assistant 会返 400
    """
    if mode == "dom_css" or not screenshot_path:
        return []
    try:
        from pathlib import Path
        p = Path(screenshot_path)
        if not p.exists():
            logger.warning(f"[Vision] 截图不存在，不传图: {screenshot_path}")
            return []
        from config import settings as _s
        max_bytes = int(getattr(_s, "EVALUATOR_SCREENSHOT_MAX_BYTES", 5 * 1024 * 1024) or 0)
        detail = str(getattr(_s, "EVALUATOR_VISION_DETAIL", "low") or "low")
        max_images = int(getattr(_s, "EVALUATOR_VISION_MAX_IMAGES", 2) or 1)
        public_base = (getattr(_s, "PREVIEW_PUBLIC_BASE_URL", "") or "").strip()
    except Exception:
        return []

    if max_bytes and p.stat().st_size > max_bytes:
        logger.warning(
            f"[Vision] 截图 {p.stat().st_size}B 超过上限 {max_bytes}B，不传图"
        )
        return []

    def _block(url: str):
        return {"type": "image_url", "image_url": {"url": url, "detail": detail}}

    # base64：本地文件直传，无公网依赖（DeepSeek 主路径）
    if mode == "base64":
        if vendor == "agnes":
            logger.warning(
                "[Vision] Agnes 不支持 base64 图片（仅公网 URL），本次降级为不传图"
            )
            return []
        import base64
        try:
            b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            return [_block(f"data:{mime};base64,{b64}")][:max_images]
        except Exception as e:
            logger.warning(f"[Vision] base64 编码失败: {e}")
            return []

    # image_url：需要公网可访问地址
    if mode == "image_url":
        if not public_base:
            logger.warning(
                "[Vision] image_url 模式需配置 PREVIEW_PUBLIC_BASE_URL（公网域名），"
                "当前未配置，降级为不传图"
            )
            return []
        return [_block(f"{public_base.rstrip('/')}/{p.name}")][:max_images]

    # auto：有公网用 URL，否则 base64（Agnes 下 base64 不可用则放弃）
    if mode == "auto":
        if public_base:
            return [_block(f"{public_base.rstrip('/')}/{p.name}")][:max_images]
        if vendor != "agnes":
            import base64
            try:
                b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
                mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                return [_block(f"data:{mime};base64,{b64}")][:max_images]
            except Exception as e:
                logger.warning(f"[Vision] auto 模式 base64 编码失败: {e}")
        logger.warning("[Vision] auto 模式：无公网 URL 且当前厂商不支持 base64，不传图")
        return []

    return []


def _defect_repair_timeout(max_tokens: int) -> int:
    """按实测吞吐反推缺陷修复的 LLM 超时（秒）。

    背景：官方标称输出 252.7 tok/s，实测根本达不到 ——
      官方端点长输出实测：68.6 / 88.7 / 91.8 tok/s（中位 88.7）
      中转网关生产日志：req199 中位 64.3、req200 中位 78.5
    只有标称值的约 1/3。此前 DEFECT_REPAIR_TIMEOUT=90 是按标称值拍的，
    输出 16K tokens 实际需要 180~360s，必然超时（日志实锤每次都是
    `Read timed out (read timeout=90)`）。

    公式：max_tokens ÷ LLM_MEASURED_TPS × 安全系数 + 固定开销，并受绝对上限约束。
    """
    try:
        from config import settings as _s
        if not getattr(_s, "DEFECT_REPAIR_TIMEOUT_AUTO", True):
            return int(getattr(_s, "DEFECT_REPAIR_TIMEOUT", 90))
        tps = max(1, int(getattr(_s, "LLM_MEASURED_TPS", 60) or 60))
        factor = float(getattr(_s, "DEFECT_REPAIR_TIMEOUT_FACTOR", 1.3) or 1.3)
        overhead = int(getattr(_s, "DEFECT_REPAIR_TIMEOUT_OVERHEAD", 20) or 0)
        ceiling = int(getattr(_s, "DEFECT_REPAIR_TIMEOUT_MAX", 600) or 600)
    except Exception:
        tps, factor, overhead, ceiling = 60, 1.3, 20, 600
    computed = int(max_tokens / tps * factor + overhead)
    return max(30, min(computed, ceiling))


def _apply_diff_edits(workspace, edits: list) -> tuple[list, str]:
    """应用 SEARCH/REPLACE 增量补丁，返回 (已应用的文件列表, 失败原因)。

    复用 harness.tools.edit_tools 里经过实证的匹配逻辑（exact → 行尾空白归一），
    而不是另写一套 —— 那套逻辑已经在 coder 的 edit_file 工具上跑了很久。

    任一文件的任一块匹配失败即整体放弃（返回空列表 + 原因），由调用方降级到
    整文件修复。这样"补丁应用失败"永远不会留下半改状态的文件。
    """
    try:
        from harness.tools.edit_tools import (
            parse_edit_blocks, _match, _replace_normalized,
        )
    except ImportError as e:
        return [], f"无法导入 edit_tools: {e}"

    # 按文件聚合累积应用（2026-09-28 实测修复）：
    # 模型对同一文件返回多个 edits 条目时，若每条都基于「原始文件内容」计算，
    # 写回阶段会变成后写覆盖前写 —— 只有最后一个补丁生效，前面的静默丢失，
    # 而函数仍返回"成功"。真实模型一次返回 2 条同文件补丁即触发该问题。
    # 故这里按 filename 累积：后续条目基于前一条的结果继续应用，最终每文件只产出 1 条。
    applied_map: dict = {}
    order: list = []
    for item in edits:
        if not isinstance(item, dict):
            continue
        filename = (item.get("filename") or "").strip()
        edit_text = item.get("edit") or ""
        if not filename or not edit_text:
            continue

        # 已有累积内容则继续在其上叠加，否则从磁盘读取一次
        new_content = applied_map.get(filename)
        if new_content is None:
            try:
                exists = workspace.exists(filename)
            except Exception:
                exists = False
            if not exists:
                return [], f"文件不存在: {filename}"
            try:
                new_content = workspace.read(filename)
            except Exception as e:
                return [], f"读取 {filename} 失败: {e}"

        try:
            blocks = parse_edit_blocks(edit_text)
        except ValueError as e:
            return [], f"{filename} 补丁格式非法: {e}"

        for i, (search, replace) in enumerate(blocks, 1):
            occ, mode = _match(search, new_content)
            if occ == 0:
                return [], f"{filename} 第 {i} 块在文件中未匹配到（SEARCH 需逐字符一致）"
            if occ > 1:
                return [], f"{filename} 第 {i} 块匹配到 {occ} 处，SEARCH 片段需唯一"
            if mode == "normalized":
                new_content = _replace_normalized(new_content, search, replace)
            else:
                new_content = new_content.replace(search, replace, 1)

        if filename not in applied_map:
            order.append(filename)
        applied_map[filename] = new_content

    if not order:
        return [], "edits 为空或全部缺少 filename/edit 字段"

    # 只产出「修改后的完整内容」，不直接写盘 —— 交给下方统一的写回流程
    # （完整性闸门 / 语法闸门 / 长度比闸门）复用，安全边界一致。
    # 副作用是"任一块匹配失败即整体放弃"天然成立：磁盘此时还没被改动。
    return [{"filename": fn, "content": applied_map[fn]} for fn in order], ""


@_traced_node("defect_repair")
def defect_repair_node(state: AgentState) -> Dict[str, Any]:
    """小上下文定向修复：针对通用冒烟测试发现的确定性缺陷

    与 coder_node 修复路径的区别：
    - 不进入 ToolCallLoop，不携带 plan/dialogue_history/CompletionContract
    - 单次 LLM 调用，上下文仅包含：缺陷清单（含复现证据与修复方案）+ 相关文件
    - LLM 返回补丁后的完整文件，直接写回 workspace
    - 修复后由 graph 路由回 verify 重新冒烟验证
    """
    workspace = get_workspace(state)
    if not workspace:
        tl = get_tool_loop(state)
        workspace = tl.workspace if tl else None
    if not workspace:
        logger.error("[DefectRepair] 无法获取 workspace")
        return {"current_step": "defect_repair_failed", "error": "无法获取 workspace"}

    smoke_defects = state.get("smoke_defects") or []
    if not smoke_defects:
        logger.warning("[DefectRepair] 无确定性缺陷，跳过")
        return {"current_step": "defect_repair_skipped"}

    prev_count = int(state.get("metadata", {}).get("defect_repair_count", 0) or 0)
    prev_llm_fail = int(state.get("metadata", {}).get("defect_repair_llm_failures", 0) or 0)
    repair_round = prev_count + 1
    # 计数语义修正（req 147）：
    #   - **补丁真正写回** → 计一轮（防 verify↔defect_repair 无限循环）
    #   - **LLM 调用本身失败**（超时/连接错误，本轮根本没产出补丁）→ 不计轮，另记 llm_failures
    #     req 147 实测第 2 轮就是一次 90s 读超时，白吃掉一轮预算，3 轮实际只跑了 2 次。
    #   - llm_failures 累计 ≥2 次才放弃，避免端点持续故障时无限重试。
    meta = dict(state.get("metadata") or {})
    meta["defect_repair_count"] = repair_round  # 乐观递增；LLM 故障路径回滚
    state["metadata"] = meta  # 原地同步；各 return 显式携带 metadata，不依赖浅拷贝副作用
    logger.info(
        f"[DefectRepair] 第 {repair_round} 轮定向修复: "
        f"{[d.get('type') for d in smoke_defects]}"
    )

    # SSE 通知前端进入定向修复阶段
    req_id = state.get("requirement_id", 0)
    try:
        tl = get_tool_loop(state)
        if tl and tl.sse:
            tl.sse.dialogue(
                req_id, "agent", DEV_NAME,
                f"## 🔧 小上下文定向修复（第 {repair_round} 轮）\n\n"
                + "\n".join(f"- **{d.get('type')}**: {d.get('message', '')}" for d in smoke_defects),
                status="in_progress",
            )
    except Exception:
        pass

    # ---- 构建最小上下文 ----
    defects_text = "\n\n".join(
        f"### 缺陷 {i + 1}: **{d.get('type', '?')}** — {d.get('message', '')}\n"
        f"- 复现证据: {d.get('evidence', '(无)')}\n"
        f"- 修复方案: {d.get('suggestion', '(无，需自行判断)')}"
        for i, d in enumerate(smoke_defects)
    )
    files_text, context_files = _collect_defect_repair_context(workspace, smoke_defects)
    user_prompt = (
        "## 确定性缺陷清单（无头浏览器实测复现）\n\n"
        f"{defects_text}\n\n"
        f"## 当前文件（共 {len(context_files)} 个）\n\n{files_text}\n\n"
        "请按系统指令的修复原则和输出格式，返回修复后的 JSON。"
    )

    # ---- 单次 LLM 调用（截断检测 + 一次更大 max_tokens 重试） ----
    from llm.client import get_client
    client = get_client()

    # 输出形态：diff = SEARCH/REPLACE 增量块（1~3K tokens，默认）；
    #           whole_file = 整个文件的完整 JSON（16K~32K tokens，回退用）。
    # 提超时只是止血，降输出量才是根治 —— 按实测吞吐（约 60~90 tok/s），
    # 整文件 16K 要 180~360s，增量补丁只要 20~50s。
    _dr_mode = "diff"
    try:
        from config import settings as _settings
        _dr_mode = str(
            getattr(_settings, "DEFECT_REPAIR_OUTPUT_MODE", "diff") or "diff"
        ).strip().lower()
    except Exception:
        pass
    if _dr_mode not in ("diff", "whole_file"):
        _dr_mode = "diff"

    if _dr_mode == "diff":
        system_prompt = load_prompt("tasks/defect_repair_edit.md")
        _budgets = (8_000, 16_000)
    else:
        system_prompt = load_prompt("tasks/defect_repair.md")
        _budgets = (16_000, 32_000)
    logger.info(f"[DefectRepair] 输出形态={_dr_mode} 额度序列={_budgets}")

    def _call(max_tokens: int):
        _t0 = time.time()
        _old_retries = client.max_retries
        # 修复路径不做「相同 prompt 重试」：超时/端点故障换多少次结果都一样，
        # 实测这正是 550s 的来源（90s × 3 次内部重试 × 2 轮额度 = 540s）。
        client.max_retries = 0
        try:
            resp = client.chat(
                prompt=user_prompt,
                system_prompt=system_prompt,
                use_memory=False,
                max_tokens=max_tokens,
                timeout=_defect_repair_timeout(max_tokens),
                thinking='enabled',  # 补丁 JSON 需要思考模式保证格式正确
            )
        finally:
            client.max_retries = _old_retries
        _log_llm_turn_safe(
            state.get("requirement_id"), None, client, system_prompt, user_prompt,
            resp, thinking='enabled',
            latency_ms=round((time.time() - _t0) * 1000, 1),
            stage=STAGE_REPAIRING,
            turn_index=int(_md(state).get("turn_index") or 0),
            trace_id=_md(state).get("trace_id") or None,
        )
        return resp

    response = None
    llm_down = False
    for max_tokens in _budgets:
        try:
            resp = _call(max_tokens)
        except Exception as e:
            # 端点故障（超时/连接错误）不是"修了一轮没修好"，不能消耗修复预算
            llm_down = True
            logger.warning(f"[DefectRepair] LLM 调用失败 (max_tokens={max_tokens}): {e}")
            break
        if resp.is_error or not resp.content:
            # 端点故障/超时：再换更大的 max_tokens 重试毫无意义 —— 同样的 prompt
            # 必然同样失败。此前这里仅在「异常」时 break，而 resp.is_error 会
            # 静默 continue 到下一个额度，于是每次烧满 2 轮 × 3 次内部重试 ≈ 550s
            # 才放弃（实测 6 次调用中 4 次如此）。
            llm_down = True
            logger.warning(
                f"[DefectRepair] LLM 返回错误 (max_tokens={max_tokens}): "
                f"{getattr(resp, 'error', None)}"
            )
            break
        response = resp
        if getattr(resp, "finish_reason", None) == "length":
            # 只有"被截断"才值得扩大额度重试
            logger.warning(
                f"[DefectRepair] 响应截断 (max_tokens={max_tokens})，扩大重试"
            )
            continue
        break

    if llm_down or (not response) or response.is_error or not response.content:
        logger.error("[DefectRepair] LLM 未返回有效内容，本轮不计入修复轮数")
        meta = dict(state.get("metadata") or {})
        meta["defect_repair_count"] = prev_count  # 回滚：预算留给下一次真正的修复
        meta["defect_repair_llm_failures"] = prev_llm_fail + 1
        state["metadata"] = meta
        if meta["defect_repair_llm_failures"] >= 2:
            logger.error(
                f"[DefectRepair] LLM 端点连续 {meta['defect_repair_llm_failures']} 次不可用，放弃定向修复"
            )
            return {"current_step": "defect_repair_failed",
                    "error": "LLM 端点持续不可用",
                    "metadata": meta}
        return {"current_step": "defect_repair_failed",
                "error": "LLM 调用失败（不计轮数）",
                "metadata": meta}

    # ---- 分层 JSON 解析：loads → 正则提取 → try_fix_json 兜底 ----
    content = response.content.strip()
    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass
        if parsed is None:
            try:
                parsed = try_fix_json(content)
            except Exception:
                parsed = None

    # ---- 增量补丁（diff 模式）优先：输出小、应用快，失败回退整文件 ----
    patched = None
    if _dr_mode == "diff" and isinstance(parsed, dict):
        _edits = parsed.get("edits")
        if isinstance(_edits, list) and _edits:
            _applied, _err = _apply_diff_edits(workspace, _edits)
            if _applied:
                logger.info(
                    f"[DefectRepair] 增量补丁解析成功（{len(_applied)} 个文件），"
                    f"交由统一写回闸门处理: {[a['filename'] for a in _applied]}"
                )
                patched = _applied
            else:
                logger.warning(f"[DefectRepair] 增量补丁应用失败，回退整文件修复: {_err}")
        else:
            logger.warning("[DefectRepair] diff 模式未解析到 edits 字段，回退整文件修复")

        if patched is None:
            # 回退：换整文件 prompt 重新调用一次（额度按整文件给）
            try:
                system_prompt = load_prompt("tasks/defect_repair.md")
                _old_retries = client.max_retries
                client.max_retries = 0
                try:
                    _wf_resp = client.chat(
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        use_memory=False,
                        max_tokens=16_000,
                        timeout=_defect_repair_timeout(16_000),
                        thinking='enabled',
                    )
                finally:
                    client.max_retries = _old_retries
                if not _wf_resp.is_error and _wf_resp.content:
                    try:
                        parsed = json.loads(_wf_resp.content.strip())
                    except json.JSONDecodeError:
                        _m = re.search(r'\{[\s\S]*\}', _wf_resp.content)
                        parsed = json.loads(_m.group()) if _m else None
            except Exception as e:
                logger.warning(f"[DefectRepair] 整文件回退调用失败: {e}")

    if patched is None:
        patched = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(patched, list) or not patched:
        # 整包 JSON 解析失败（通常是多文件输出超预算被截断）→ 降级为单文件修复
        logger.warning(
            "[DefectRepair] 整包补丁解析失败，降级为单文件修复（压缩输出预算）"
        )
        all_files = [f for f in workspace.list()
                     if not f.startswith("docs/") and not f.startswith(".task/")]
        js_files = sorted(
            [f for f in all_files if f.endswith(".js")],
            key=lambda n: -len(workspace.read(n)),
        )
        target = next((f for f in js_files if "main" in f.lower()), js_files[0] if js_files else "")
        if target:
            target_content = workspace.read(target)
            single_prompt = (
                f"## 确定性缺陷清单（无头浏览器实测复现）\n\n{defects_text}\n\n"
                f"## 待修复文件: {target}\n```javascript\n{target_content}\n```\n\n"
                "只修复这一个文件。返回 JSON: "
                '{"files": [{"filename": "' + target + '", "content": "修复后的完整文件"}], '
                '"summary": "..."}\n'
                "content 必须是完整文件（从第一行到最后一行，不得截断），最小改动。"
            )
            try:
                _t0 = time.time()
                single_resp = client.chat(
                    prompt=single_prompt,
                    system_prompt=system_prompt,
                    use_memory=False,
                    max_tokens=16_000,
                    timeout=_defect_repair_timeout(16_000),
                    thinking='enabled',
                )
                _log_llm_turn_safe(
                    state.get("requirement_id"), None, client, system_prompt, single_prompt,
                    single_resp, thinking='enabled',
                    latency_ms=round((time.time() - _t0) * 1000, 1),
                    stage=STAGE_REPAIRING,
                    turn_index=int(_md(state).get("turn_index") or 0),
                    trace_id=_md(state).get("trace_id") or None,
                )
                if single_resp.content and not single_resp.is_error:
                    s_content = single_resp.content.strip()
                    s_parsed = None
                    try:
                        s_parsed = json.loads(s_content)
                    except json.JSONDecodeError:
                        s_match = re.search(r'\{[\s\S]*\}', s_content)
                        if s_match:
                            try:
                                s_parsed = json.loads(s_match.group())
                            except json.JSONDecodeError:
                                pass
                    if isinstance(s_parsed, dict) and isinstance(s_parsed.get("files"), list):
                        patched = s_parsed["files"]
                        logger.info(f"[DefectRepair] 单文件降级修复成功解析: {target}")
            except Exception as e:
                logger.warning(f"[DefectRepair] 单文件降级修复调用失败: {e}")

    if not isinstance(patched, list) or not patched:
        logger.error("[DefectRepair] 整包与单文件降级均失败，本轮修复失败")
        return {"current_step": "defect_repair_failed",
                "error": "补丁 JSON 解析失败",
                "metadata": state.get("metadata") or {}}

    # ---- 校验并写回（最多 4 个文件，路径必须在 workspace 内） ----
    existing = set(workspace.list())
    written, skipped = [], []
    for f in patched[:4]:
        if not isinstance(f, dict):
            continue
        fname, fcontent = f.get("filename", ""), f.get("content", "")
        if not fname or not isinstance(fcontent, str) or not fcontent.strip():
            skipped.append(f"{fname or '(空文件名)'}: 内容为空")
            continue
        if fname not in existing:
            skipped.append(f"{fname}: 非现有文件（定向修复禁止新建文件）")
            continue
        if len(fcontent) > 200_000:
            skipped.append(f"{fname}: 内容异常超长")
            continue
        # 截断闸门 1: 完整性（花括号配平 / 结尾标记）
        ok, reason = _content_looks_complete(fname, fcontent)
        if not ok:
            skipped.append(f"{fname}: 疑似截断（{reason}），拒绝写回")
            logger.warning(f"[DefectRepair] {fname} 内容不完整: {reason}，拒绝写回")
            continue
        # 截断闸门 2: 长度比（定向修复是最小改动，新内容不应骤缩到原文件的 40% 以下）
        try:
            orig_len = len(workspace.read(fname))
            if orig_len > 1_000 and len(fcontent) < orig_len * 0.4:
                skipped.append(
                    f"{fname}: 新内容仅为原文 {len(fcontent)}/{orig_len} 字符，疑似截断，拒绝写回"
                )
                logger.warning(f"[DefectRepair] {fname} 长度比异常: {len(fcontent)}/{orig_len}")
                continue
        except Exception:
            pass
        # 语法闸门（req 189）：本节点绕过 ToolCallLoop 直接 workspace.write，
        # 不经过 write_file/edit_file 的任何守卫。189 实测补丁把 `UtilsExt.$$`
        # 写成 `$$/` —— 括号配平、长度正常，_content_looks_complete 拦不住，
        # 写入即全站 JS 瘫痪，且后续 4 轮修复全部超时白烧。此处守住最后一道：
        # 新内容语法坏而磁盘版本好 → 拒绝写回，保留好版本等下一轮。
        try:
            from harness.tools.file_tools import _syntax_problem
            _new_problem = _syntax_problem(fname, fcontent)
            if _new_problem:
                try:
                    _old_problem = _syntax_problem(fname, workspace.read(fname))
                except Exception:
                    _old_problem = ""
                if not _old_problem:
                    skipped.append(
                        f"{fname}: 补丁引入语法错误（{_new_problem}），"
                        "已拒绝写回并保留原版本"
                    )
                    logger.warning(
                        f"[DefectRepair] {fname} 补丁语法错误，拒绝写回: {_new_problem}"
                    )
                    continue
        except ImportError:
            pass
        try:
            workspace.write(fname, fcontent)
            written.append(fname)
        except Exception as e:
            skipped.append(f"{fname}: 写入失败 {e}")

    summary = parsed.get("summary", "") if isinstance(parsed, dict) else ""
    logger.info(
        f"[DefectRepair] 第 {repair_round} 轮完成: 写回 {written}"
        + (f"，跳过 {skipped}" if skipped else "")
    )

    # SSE 推送修复结果与更新后的文件
    try:
        tl = get_tool_loop(state)
        if tl and tl.sse:
            tl.sse.dialogue(
                req_id, "agent", DEV_NAME,
                f"## 🔧 定向修复完成（第 {repair_round} 轮）\n\n"
                f"**修改文件**: {', '.join(written) or '(无)'}\n\n"
                f"**修复说明**: {summary or '(未提供)'}"
                + (f"\n\n**跳过**: {'; '.join(skipped)}" if skipped else ""),
                status="completed",
            )
            if written:
                tl.sse.code(req_id, [
                    {"filename": fname, "content": workspace.read(fname)}
                    for fname in written
                ])
    except Exception:
        pass

    # 留痕到对话历史（供最终报告与人工排查）
    state.setdefault("dialogue_history", []).append({
        "role": "agent",
        "name": DEV_NAME,
        "content": (
            f"## 🔧 小上下文定向修复（第 {repair_round} 轮）\n"
            f"目标缺陷: {', '.join(d.get('type', '?') for d in smoke_defects)}\n"
            f"修改文件: {', '.join(written) or '(无)'}\n"
            f"修复说明: {summary or '(未提供)'}"
        ),
        "status": "completed",
    })

    # 修复结论留给 _traced_node 切面落里程碑事件（覆盖本节点的全部 return 路径）。
    # meta 要能回答「第几轮修的、修了什么、改了哪些文件」。
    state["repair_verdict"] = {
        "round": repair_round,
        "target_defects": [d.get("type") for d in smoke_defects][:10],
        "written_files": list(written)[:20],
    }

    return {"current_step": "defect_repair_done",
                "metadata": state.get("metadata") or {}}
