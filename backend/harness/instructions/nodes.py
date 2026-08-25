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
from typing import Dict, Any

from harness.state.agent_state import AgentState
from harness.agent_names import TL_NAME, DEV_NAME, QA_NAME
from llm.client import get_client
from llm.client import _try_fix_json as try_fix_json
from harness.instructions.prompts import load_prompt
from harness.observability.logger import get_logger
from harness.harness_context import get_tool_loop, get_workspace

logger = get_logger(__name__)


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
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

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
            inner = match.group(1).strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                pass

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
        json_str = raw[start_idx:end_idx + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Step 4: 最后尝试 regex 提取（向后兼容）
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

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


def _generate_clarify_questions(client, requirement: str) -> list:
    """生成澄清问题----LLM 根据已有信息自主判断还缺什么

    如果需求已经很详细，只问 1-2 个最关键的问题（如视觉风格偏好）；
    如果需求模糊，问 2-3 个问题帮助明确方向。
    """
    is_detailed = not _is_vague_requirement(requirement)
    detail_hint = (
        "用户的需求已经很详细了，只需要确认 1-2 个最关键的选择（如视觉风格偏好）。"
        if is_detailed else
        "用户的需求比较模糊，请分析缺少哪些关键信息，生成 2-3 个澄清问题帮助明确方向。"
    )

    prompt = f"""用户提出需求："{requirement}"

{detail_hint}

注意：
- 用户已经明确说过的信息不要再问（比如用户说了"做一个待办清单"，就不要再问"你想做什么类型的应用"）
- 如果需求涉及 UI/页面/界面，必须询问视觉风格偏好
- 只问真正能影响实现方案的关键问题

视觉风格问题的选项固定为（如果需要问的话）：
{{"id": "visual_style", "type": "radio",
  "label": "你偏好哪种视觉风格？",
  "options": [
    "极简白 -- 白色背景，灰黑文字，大量留白，功能优先",
    "暖柔风格 -- 暖色调、圆角卡片、柔和阴影 (默认)",
    "暗黑科技 -- 深色背景、霓虹强调色、终端风格",
    "活泼多彩 -- 明亮渐变、大色块、趣味性设计",
    "无偏好，自动选择"
  ]
}}

只返回 JSON 数组，不要其他文字。"""

    response = client.chat(
        prompt=prompt,
        system_prompt="你是一位产品经理，帮助澄清用户需求。用户已经说过的信息不要再问。",
        use_memory=False, max_tokens=500, timeout=20
    )
    if response.is_error or not response.content:
        return []
    try:
        return json.loads(response.content)
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', response.content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
    return []


def _format_plan_summary(plan: dict) -> str:
    """将 TL plan 格式化为用户可见的结构化 Markdown 摘要"""
    if not isinstance(plan, dict):
        return "已完成需求分析和架构设计"

    lines = ["## 📋 需求分析结果\n"]

    features = plan.get("features", [])
    if features:
        lines.append("### 核心功能")
        for f in features:
            lines.append(f"- ✅ {f}")
        lines.append("")

    acceptance = plan.get("acceptance_criteria", [])
    if acceptance:
        lines.append("### 验收条件")
        for ac in acceptance:
            ac_id = ac.get("id", "?") if isinstance(ac, dict) else "?"
            ac_label = ac.get("label", str(ac)) if isinstance(ac, dict) else str(ac)
            lines.append(f"- **{ac_id}**: {ac_label}")
        lines.append("")

    file_structure = plan.get("file_structure", [])
    if file_structure:
        lines.append("### 文件结构")
        for f in file_structure:
            lines.append(f"- `{f}`")
        lines.append("")

    tasks = plan.get("tasks", [])
    if tasks:
        lines.append("### 任务列表")
        for t in tasks:
            fpath = t.get("file", "?") if isinstance(t, dict) else str(t)
            desc = t.get("description", "") if isinstance(t, dict) else ""
            lines.append(f"- **{fpath}**: {desc[:80]}")
        lines.append("")

    complexity = plan.get("complexity", "S")
    tech = plan.get("tech_stack", {})
    lines.append(f"**复杂度**: {complexity}  |  **技术栈**: CSS={tech.get('css', '?')}, Storage={tech.get('storage', '?')}")

    return "\n".join(lines)


def _extract_plan_metadata(plan: dict) -> dict:
    """从 plan 中提取关键元数据（供程序使用，前端可选渲染）"""
    if not isinstance(plan, dict):
        return {}
    return {
        "features": plan.get("features", []),
        "acceptance_criteria": plan.get("acceptance_criteria", []),
        "file_structure": plan.get("file_structure", []),
        "tech_stack": plan.get("tech_stack", {}),
        "data_model": plan.get("data_model", ""),
        "implementation_notes": plan.get("implementation_notes", ""),
        "implementation_order": plan.get("implementation_order", []),
        "tasks": plan.get("tasks", []),
        "complexity": plan.get("complexity", "S"),
    }


def team_leader_node(state: AgentState) -> Dict[str, Any]:
    """TeamLeader 节点：需求分析 → 结构化 Plan

    澄清由上游 IntentRouter 统一处理（进入此节点前 intent 已固定为 'task'）。
    """
    requirement = state['requirement_content']

    # ---- 输入质量门禁：过短的需求不应直接编造 plan，转为追问澄清 ----
    # 阈值设为 8 字符：低于此值可能是无意义的短输入（如"帮我"、"一个"等），
    # 而"贪吃蛇游戏"、"Todo App"、"计算器"等经典明确需求可达 8+ 字符
    MIN_REQUIREMENT_CHARS = 8
    if len(requirement.strip()) < MIN_REQUIREMENT_CHARS:
        logger.info(
            f"[TeamLeader] 输入过短 ({len(requirement)} 字符)，"
            f"转发澄清流程"
        )
        try:
            client = get_client()
            questions = _generate_clarify_questions(client, requirement)
        except Exception as e:
            logger.warning(f"[TeamLeader] 生成澄清问题失败: {e}")
            questions = []
        if not questions:
            questions = FALLBACK_CLARIFY_QUESTIONS

        # question_form 同时写入对话消息（前端刷新恢复）和 metadata（SSE 即时推送）
        question_form = {'questions': questions}
        return {
            'plan': {},
            'current_step': 'needs_clarification',
            'dialogue_history': [{
                'role': 'agent', 'name': 'Leon（负责人）',
                'content': (
                    f"你的需求「{requirement}」比较简短。"
                    f"为了生成更准确的开发计划，请补充以下信息："
                ),
                'status': 'needs_clarification',
                'question_form': question_form,
            }],
            'metadata': {
                **state.get('metadata', {}),
                'team_leader_success': False,
                'needs_clarification_reason': 'input_too_short',
                'question_form': question_form,
            },
        }

    try:
        client = get_client()
        system_prompt = load_prompt("coding/tl_analysis.md")
        user_prompt = f"请分析以下需求并生成开发计划：\n\n{requirement}"

        # L0: 前置检测 + 分层容错
        # 策略：检测截断 → 重试(最多2次) → 降级修复 → 完整性校验

        def _fetch_and_extract(max_tokens: int) -> tuple[dict | None, bool, object]:
            """获取响应并提取 JSON，返回 (plan, is_truncated, resp)"""
            resp = client.chat(
                prompt=user_prompt, system_prompt=system_prompt,
                use_memory=False, max_tokens=max_tokens, timeout=60,
                thinking='enabled',  # 结构化 plan JSON 需要思考模式保证格式正确
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
            'features': plan.get('features', []),
            'acceptance_criteria': plan.get('acceptance_criteria', []),
            'file_structure': plan.get('file_structure', []),
            'tech_stack': plan.get('tech_stack', {}),
            'data_model': plan.get('data_model', ''),
            'implementation_notes': plan.get('implementation_notes', ''),
            'implementation_order': impl_order,
            'tasks': tasks,
            'complexity': complexity,
            'visual_direction': plan.get('visual_direction', ''),
            'layout_structure': plan.get('layout_structure', ''),
            'key_interactions': plan.get('key_interactions', []),
        } if isinstance(plan, dict) else {}

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
            }],
            'metadata': {
                **state.get('metadata', {}),
                'team_leader_success': True,
                'visual_style': visual_style,
                'complexity': complexity,
            },
            'tasks': tasks,
            'interfaces': interfaces,
            'implementation_order': impl_order,
        }

    except Exception as e:
        logger.error(f"[TeamLeader] 执行失败：{e}")
        return {
            'plan': {},
            'current_step': 'team_leader_failed',
            'error': f"TeamLeader 失败：{e}",
            'dialogue_history': [{
                'role': 'agent', 'name': TL_NAME,
                'content': f"分析失败: {requirement[:50]}...",
                'status': 'failed'
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
                'status': 'failed'
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
                timeout=30,
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
        resp = client.chat(prompt=prompt, max_tokens=1000, timeout=30)
        return resp.content if resp and resp.content else "审查未返回结果"
    except Exception as e:
        return f"审查异常: {e}"


def coder_node(state: AgentState) -> Dict[str, Any]:
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

    # CompletionContract：standard 复杂度使用，simple 跳过
    if complexity == "standard":
        from harness.constraints.completion_contract import CompletionContract
        impl_order = state.get("implementation_order") or []
        if impl_order:
            workspace = get_workspace(state)
            if not workspace:
                workspace = tool_loop.workspace
            contract = CompletionContract(workspace)
            if contract.exists():
                contract.initialize_incremental(impl_order)
            else:
                contract.initialize(impl_order)
            state["_completion_contract"] = contract
            state.setdefault("metadata", {})["_completion_contract"] = contract

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

    # Phase 2: 定向补全（仅当 standard 且有缺失文件时触发）
    if complexity == "standard" and next_step == "coding_done":
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


def _translate_acs_to_scripts(acceptance_criteria: list, code_text: str, requirement: str) -> list[dict]:
    """用 LLM 将验收条件翻译为 Playwright 操作序列

    每个 AC 的 how_to_verify 字段描述验证方法（如"输入文字点击添加按钮，列表中显示新项目"），
    LLM 需要翻译为具体的 DOM 操作步骤。
    """
    if not acceptance_criteria:
        return []

    ac_text = "\n".join(
        f"- {ac.get('id', '?')}: {ac.get('label', '')}\n  验证方式: {ac.get('how_to_verify', '')}"
        for ac in acceptance_criteria
    )

    # 从代码中提取可用的 CSS 选择器（供 LLM 参考，减少 selector 猜测错误）
    import re as _re
    selectors_hint = []
    id_pattern = _re.compile(r'id=["\']([^"\']+)["\']')
    class_pattern = _re.compile(r'class=["\']([^"\']+)["\']')
    for m in id_pattern.finditer(code_text):
        selectors_hint.append(f"#{m.group(1)}")
    for m in class_pattern.finditer(code_text):
        for cls in m.group(1).split():
            selectors_hint.append(f".{cls}")
    selector_text = ", ".join(list(set(selectors_hint))[:40]) if selectors_hint else "(从代码中提取)"

    prompt = f"""将以下验收条件翻译为 Playwright DOM 操作序列。

## 可用 CSS 选择器（从实际代码中提取）
{selector_text}

## 验收条件
{ac_text}

## 翻译规则
- 每个步骤的 action 必须是: type | click | select | press | wait | assert_exists | assert_visible | assert_text | assert_count | assert_value | assert_canvas_change
- selector 必须从"可用 CSS 选择器"中选择，或从 AC 描述中合理推断
- type 需要 value 字段；只用于 input/textarea，禁止对 canvas/普通元素使用
- press 需要 key 字段（如 ArrowUp/ArrowDown/Enter/Space）：键盘交互（游戏方向键、快捷键）必须用 press，禁止用 type 模拟
- wait 需要 ms 字段（默认 500）
- assert_text 需要 contains 字段
- assert_count 需要 min_count 字段
- assert_value 需要 value 字段
- assert_canvas_change 需要 wait_ms 字段（默认 2000）：验证 canvas 画面随操作变化（游戏移动/动画）
- 游戏类 AC 的标准模式：click 开始按钮 → wait 800ms → press 方向键 → wait 1500ms → assert_canvas_change(wait_ms=2000)
- 【重要】游戏类 AC 严禁断言精确分数文本（如 contains="10"），因为蛇吃到食物需要时间和多次操作，分数值不确定。
  验证"得分/吃食物"改为：assert_canvas_change 验证画面变化（蛇移动/变长），或 assert_exists 验证分数元素存在即可，不要断言具体数字。
- 【重要】分享类 AC：游戏可能设计为"有成绩才能分享"（score=0 时提示"先玩一局"）。这是合理设计，不要断言 toast 必须包含"复制"等特定文本。
  验证"分享"改为：assert_exists/assert_visible 验证分享按钮存在，点击后 assert_exists/assert_visible 验证有反馈提示（toast）出现即可，不断言具体文案。

## 输出格式
只返回 JSON 数组:
```json
[
  {{
    "ac_id": "AC-1",
    "label": "...",
    "steps": [
      {{"action": "type", "selector": "#input", "value": "测试文字"}},
      {{"action": "click", "selector": "#add-btn"}},
      {{"action": "wait", "ms": 500}},
      {{"action": "assert_exists", "selector": ".result-item", "label": "新项目出现在列表中"}}
    ]
  }}
]
```"""

    try:
        from llm.client import get_client
        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt="你是 Playwright 自动化测试专家。只返回 JSON，不要其他文字。",
            use_memory=False,
            max_tokens=2000,
            timeout=30,
            thinking='enabled',
        )
        if response.is_error or not response.content:
            return []

        import json as _json
        content = response.content.strip()
        # 提取 JSON 数组
        try:
            scripts = _json.loads(content)
        except _json.JSONDecodeError:
            match = _re.search(r'\[[\s\S]*\]', content)
            if match:
                try:
                    scripts = _json.loads(match.group())
                except _json.JSONDecodeError:
                    return []
            else:
                return []

        if isinstance(scripts, list):
            # 只保留结构合法的条目（LLM 偶尔返回字符串数组导致下游 .get 崩溃）
            valid = [
                s for s in scripts
                if isinstance(s, dict) and isinstance(s.get("steps"), list) and s["steps"]
            ]
            dropped = len(scripts) - len(valid)
            from harness.observability.logger import get_logger
            get_logger(__name__).info(
                f"[AC Translate] {len(valid)} 条 AC 翻译完成"
                + (f"（丢弃 {dropped} 条格式非法）" if dropped else "")
            )
            return valid
    except Exception as e:
        from harness.observability.logger import get_logger
        get_logger(__name__).warning(f"[AC Translate] 翻译失败: {e}")

    return []


# ==================== Verify 节点（Fresh-Context Evaluator） ====================


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
    code_blocks = []
    for fname in code_files:
        try:
            content = workspace.read(fname)
            line_count = content.count('\n') + 1
            # 根据文件类型确定语言标记
            if fname.endswith('.html'):
                lang = 'html'
            elif fname.endswith('.css'):
                lang = 'css'
            elif fname.endswith('.js'):
                lang = 'javascript'
            else:
                lang = ''
            code_blocks.append(
                f"### {fname} ({line_count} 行)\n```{lang}\n{content[:6000]}\n```"
            )
        except Exception:
            code_blocks.append(f"### {fname}\n(无法读取)")

    code_text = "\n\n---\n\n".join(code_blocks) if code_blocks else "(无代码文件)"

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
                "smoke_defects": [], "metadata": meta}  # 显式返回：不依赖浅拷贝副作用

    # 运行 run_preview 获取浏览器执行结果
    browser_result = {"available": False, "errors": [], "warnings": []}
    if any(f.endswith("index.html") for f in code_files):
        try:
            tl = get_tool_loop(state)
            if tl and tl._preview_handler:
                preview = tl._preview_handler.run_preview("index.html")
                if preview and preview.metadata:
                    browser_result = preview.metadata
        except Exception as e:
            logger.warning(f"[Verify] run_preview 失败: {e}")
            browser_result["errors"].append(f"run_preview 异常: {e}")

    # ========== Playwright AC 逐条验收 ==========
    ac_check_results = []
    plan = state.get("plan", {})
    acceptance_criteria = plan.get("acceptance_criteria", []) if isinstance(plan, dict) else []

    # 验证走与用户一致的预览链路（沙箱 iframe + 能力 URL），杜绝"验证过、用户废"
    preview_url = None
    try:
        from utils.preview_token import make_preview_url
        preview_url = make_preview_url(workspace.user_id, workspace.req_id, "index.html")
    except Exception as e:
        logger.warning(f"[Verify] 构造预览 URL 失败，回退直读文件: {e}")

    if acceptance_criteria and any(f.endswith("index.html") for f in code_files):
        logger.info(f"[Verify] 启动 AC 逐条验收: {len(acceptance_criteria)} 条 AC")
        try:
            # Step 1: LLM 将 AC 描述翻译为 Playwright 操作序列
            # 带缓存首轮锁定：每轮重译会导致同一份代码结果漂移（选择器/步骤不稳定）
            ac_cache_path = workspace.path / ".task" / "ac_scripts.json"
            import hashlib as _hashlib
            _ac_hash = _hashlib.md5(
                json.dumps(acceptance_criteria, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            ac_scripts = None
            if ac_cache_path.exists():
                try:
                    cached = json.loads(ac_cache_path.read_text())
                    if (
                        cached.get("ac_hash") == _ac_hash
                        and isinstance(cached.get("scripts"), list)
                        and cached["scripts"]
                    ):
                        ac_scripts = cached["scripts"]
                        logger.info(f"[Verify] AC 脚本命中缓存（{len(ac_scripts)} 条，首轮锁定不重译）")
                except Exception:
                    pass
            if ac_scripts is None:
                ac_scripts = _translate_acs_to_scripts(acceptance_criteria, code_text, requirement)
                if ac_scripts:
                    try:
                        ac_cache_path.parent.mkdir(parents=True, exist_ok=True)
                        ac_cache_path.write_text(
                            json.dumps({"ac_hash": _ac_hash, "scripts": ac_scripts}, ensure_ascii=False, indent=2)
                        )
                    except Exception:
                        pass
            # Step 2: Playwright 执行
            if ac_scripts:
                from harness.tools.preview_runner import run_ac_checks
                index_path = workspace.path / "index.html"
                if index_path.exists():
                    ac_check_results = run_ac_checks(index_path, ac_scripts, preview_url=preview_url)
                    _prod_fail = sum(1 for r in ac_check_results if r.get("failures"))
                    _harness_fail = sum(1 for r in ac_check_results if r.get("harness_errors"))
                    logger.info(
                        f"[Verify] AC 验收完成: {len(ac_check_results) - _prod_fail}/"
                        f"{len(ac_check_results)} 通过（产品断言失败 {_prod_fail} 条，"
                        f"脚本驱动失败 {_harness_fail} 条不计入缺陷）"
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
                smoke_result = run_universal_smoke(index_path, preview_url=preview_url)
                logger.info(
                    f"[Verify] 通用冒烟: available={smoke_result.get('available')}, "
                    f"checks={smoke_result.get('checks')}, defects={len(smoke_result.get('defects', []))}"
                )
        except Exception as e:
            logger.warning(f"[Verify] 通用冒烟异常（跳过）: {e}")
    smoke_defects = smoke_result.get("defects", [])

    # 判断是否可以走快速通道
    preview_clean = len(browser_result.get("errors", [])) == 0
    ac_all_passed = (
        len(ac_check_results) > 0 and
        all(r["passed"] and not r.get("harness_errors") for r in ac_check_results)
    )
    fast_pass = preview_clean and ac_all_passed and not smoke_defects

    if fast_pass:
        # 快速通道：preview 零错误 + 所有 AC 通过 → 跳过深度 LLM 评估
        logger.info(f"[Verify] 快速通道: preview 零错误 + {len(ac_check_results)} 条 AC 全部通过 → PASS")
        evaluator_result = {
            "verdict": "PASS",
            "summary": f"浏览器验证无错误，{len(ac_check_results)} 条验收条件全部通过",
            "score": {"functionality": 10, "runtime": 10, "ui_quality": 8, "acceptance": 10, "code_quality": 8},
            "overall_score": 9.2,
            "findings": [],
            "ac_results": ac_check_results,
            "browser_result": browser_result,
            "fast_pass": True,
            "timestamp": __import__('time').time(),
        }
        state["verify_passed"] = True
        state["current_step"] = "verify_done"
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
        state.setdefault("dialogue_history", []).append({
            "role": "agent", "name": QA_NAME,
            "content": (
                f"## 代码评估: ✅ PASS (快速通道)\n\n"
                f"**浏览器验证**: 无错误\n"
                f"**验收条件**: {len(ac_check_results)} 条全部通过\n"
            ),
            "status": "completed",
        })
        return {"verify_passed": True, "current_step": "verify_done"}

    # 构建评估 prompt（含 AC 验收结果供 LLM 参考）
    ac_results_text = ""
    if ac_check_results:
        prod_pass = sum(1 for r in ac_check_results if not r.get("failures"))
        harness_fail_n = sum(1 for r in ac_check_results if r.get("harness_errors"))
        ac_results_text = (
            f"\n\n## AC 逐条验收结果（浏览器实际执行）\n"
            f"产品断言 {prod_pass}/{len(ac_check_results)} 通过；"
            f"另有 {harness_fail_n} 条存在脚本驱动失败（假阴性嫌疑，不计入产品缺陷，供定性判断）:\n"
        )
        for r in ac_check_results:
            if r.get("failures"):
                status = "❌"
            elif r.get("harness_errors"):
                status = "⚠️"
            else:
                status = "✅"
            ac_results_text += f"- {status} {r['ac_id']}: {r.get('label', '')}"
            failures = "; ".join(r.get("failures", []))
            if failures:
                ac_results_text += f" — [产品缺陷] {failures}"
            herr = "; ".join(r.get("harness_errors", []))
            if herr:
                ac_results_text += f" — [脚本错误·可能假阴性] {herr}"
            ac_results_text += "\n"

    # 层1 冒烟结果注入评估 prompt（确定性证据，供 LLM 参考）
    smoke_text = ""
    if smoke_result.get("available"):
        smoke_text = "\n\n## 通用冒烟测试结果（浏览器确定性检查，非 LLM 判断）\n"
        for cname, cok in (smoke_result.get("checks") or {}).items():
            smoke_text += f"- {'✅ 通过' if cok else '❌ 未通过'} {cname}\n"
        for d in smoke_defects:
            smoke_text += f"- ❌ [{d['type']}] {d['message']}\n  证据: {d.get('evidence', '')}\n"

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
{ac_results_text}{smoke_text}

请基于以上信息，按照 Evaluator 的评估维度和输出格式，给出结构化评估结果。"""

    logger.info(f"[Verify] 启动评估: {len(code_files)} 个文件, prompt 长度={len(user_prompt)}")

    def _call_evaluator(focus_instruction: str, max_tokens: int = 3000):
        """调用 Evaluator LLM，支持 finish_reason=length 自动重试"""
        prompt = user_prompt + "\n\n" + focus_instruction
        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt=evaluator_prompt,
            use_memory=False,
            max_tokens=max_tokens,
            timeout=90,
            thinking='enabled',
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
                timeout=120,
                thinking='enabled',
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

        # 层1 冒烟缺陷是确定性结论：直接并入 findings 并强制 NEEDS_WORK（进入修复循环）
        if smoke_defects:
            smoke_findings = [
                {
                    "severity": d.get("severity", "major"),
                    "dimension": d.get("dimension", "runtime"),
                    "description": f"[{d['type']}] {d['message']}",
                    "evidence": d.get("evidence", ""),
                    "suggestion": d.get("suggestion", ""),
                }
                for d in smoke_defects
            ]
            findings = smoke_findings + findings
            verdict = "NEEDS_WORK"
            if not overall_score or overall_score >= 6:
                overall_score = 5.5
            logger.warning(
                f"[Verify] 通用冒烟发现 {len(smoke_defects)} 个确定性缺陷，"
                f"强制判定 NEEDS_WORK: {[d['type'] for d in smoke_defects]}"
            )

        # 将 verdict 转为 verify_passed
        state["verify_passed"] = (verdict == "PASS")
        state["current_step"] = "verify_done"

        # 构建评估结果对象（始终构建，供后续 QA 反馈使用）
        evaluator_result = {
            "verdict": verdict,
            "summary": combined.get("summary", ""),
            "score": score,
            "overall_score": overall_score,
            "findings": findings,
            "ac_results": ac_check_results,  # Playwright 实际执行的逐条 AC 结果
            "browser_result": browser_result,
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
            # 防御验证器矛盾输出：score < 6 但 findings 为空时，尝试重试一次
            # 这是一种 LLM 评估失败，不应让 coder 承担"无问题可修"的代价
            if overall_score < 6 and not findings:
                verifier_errors = state.get("metadata", {}).get("_verifier_error_count", 0)

                if verifier_errors == 0:
                    # 第一次矛盾：用更强的 prompt 重试，要求必须给出具体 findings
                    logger.warning(
                        f"[Verify] ⚠️ 验证器返回矛盾结果 (verdict=NEEDS_WORK, score={overall_score}, "
                        f"findings=[])，将以更强约束重试 evaluator"
                    )
                    retry_prompt = user_prompt + (
                        "\n\n⚠️ 重要：你上一次的评估返回了 NEEDS_WORK 但没有给出任何具体的 findings。"
                        "\n根据硬性规则，NEEDS_WORK 必须伴随至少一个具体的 finding（包含 severity/description/evidence/suggestion）。"
                        "\n请重新评估：如果代码确实有问题，必须逐条列出具体 findings；"
                        "\n如果找不到任何具体问题，verdict 必须改为 PASS。"
                        "\n特别注意检查：入口函数是否被调用、事件监听器是否绑定、run_preview 是否报告错误。"
                    )
                    try:
                        retry_response = client.chat(
                            prompt=retry_prompt,
                            system_prompt=evaluator_prompt,
                            use_memory=False,
                            max_tokens=4000,
                            timeout=90,
                            thinking='enabled',
                        )
                        if not retry_response.is_error and retry_response.content:
                            retry_content = retry_response.content.strip()
                            try:
                                retry_result = json.loads(retry_content)
                            except json.JSONDecodeError:
                                match = re.search(r'\{[\s\S]*\}', retry_content)
                                if match:
                                    try:
                                        retry_result = json.loads(match.group())
                                    except json.JSONDecodeError:
                                        retry_result = {}
                            verdict = retry_result.get("verdict", "NEEDS_WORK")
                            findings = retry_result.get("findings", [])
                            overall_score = retry_result.get("overall_score", overall_score)
                            score = retry_result.get("score", score)
                            # 重试翻转 verdict 后必须同步 verify_passed，
                            # 否则 QA 反馈显示 PASS 但图仍会拉回 coder 多跑一轮
                            state["verify_passed"] = (verdict == "PASS")
                            logger.info(
                                f"[Verify] 重试后: verdict={verdict}, score={overall_score}, "
                                f"findings={len(findings)}"
                            )
                    except Exception as retry_err:
                        logger.warning(f"[Verify] 重试 evaluator 失败: {retry_err}")

                # 重试后仍然矛盾
                if overall_score < 6 and not findings:
                    verifier_errors += 1
                    logger.warning(
                        f"[Verify] ⚠️ 验证器返回矛盾结果: verdict=NEEDS_WORK, "
                        f"score={overall_score}, 但 findings 为空。"
                        f"连续矛盾次数={verifier_errors}"
                    )
                    state.setdefault("metadata", {})["_verifier_error"] = True
                    state["metadata"]["_verifier_error_count"] = verifier_errors
                    # 连续 2 次矛盾 → 保守标记为 PASS（避免无限循环）
                    if verifier_errors >= 2:
                        logger.warning(
                            f"[Verify] 连续 {verifier_errors} 次矛盾结果，"
                            f"保守标记为 PASS 以终止修复循环"
                        )
                        state["verify_passed"] = True
                        state["metadata"].pop("_verifier_error", None)
                        state["metadata"]["_verifier_error_count"] = 0
                    # 更新 evaluator_result（重试后可能变化）
                    try:
                        evaluator_result.update({
                            "verdict": verdict,
                            "score": score,
                            "overall_score": overall_score,
                            "findings": findings,
                        })
                        workspace.write(
                            ".task/evaluator/result.json",
                            json.dumps(evaluator_result, ensure_ascii=False, indent=2)
                        )
                    except Exception:
                        pass
                else:
                    # 重试后找到了具体 findings，走正常 NEEDS_WORK 流程
                    state["metadata"].pop("_verifier_error", None)
                    state["metadata"]["_verifier_error_count"] = 0
                    meta = dict(state.get("metadata") or {})
                    meta["repair_count"] = meta.get("repair_count", 0) + 1
                    state["metadata"] = meta
            elif findings:
                # NEEDS_WORK 且有具体 findings：递增 repair_count
                meta = dict(state.get("metadata") or {})
                meta["repair_count"] = meta.get("repair_count", 0) + 1
                state["metadata"] = meta
                state["metadata"].pop("_verifier_error", None)
                state["metadata"]["_verifier_error_count"] = 0
            else:
                # 边缘情况：NEEDS_WORK 但 score >= 6 且 findings 为空
                # 可能是 evaluator 输出异常，保守处理为 PASS
                logger.warning(
                    f"[Verify] 边缘矛盾: verdict=NEEDS_WORK, score={overall_score}, "
                    f"findings 为空，保守标记为 PASS"
                )
                state["verify_passed"] = True
                state["metadata"].pop("_verifier_error", None)
                state["metadata"]["_verifier_error_count"] = 0

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
            if overall_score < 6:
                # 验证器矛盾：评分低但无具体问题 → 要求 coder 自行检查
                qa_feedback += (
                    "⚠️ **验证器未发现具体问题，但评分较低。请自行检查以下方面：**\n"
                    "- 所有函数是否被正确调用（特别是初始化/入口函数）\n"
                    "- 事件监听器是否已绑定\n"
                    "- 页面加载后功能是否正常启动\n"
                    "- 所有引用的函数/变量是否已定义\n"
                    "请用 run_preview 验证后报告结果。"
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

        logger.info(
            f"[Verify] 评估完成: verdict={verdict}, "
            f"score={overall_score}, findings={len(findings)}"
        )

    except Exception as e:
        logger.warning(f"[Verify] 评估异常: {e}，保守判定为 NEEDS_WORK")
        return {"verify_passed": False, "current_step": "verify_done",
                "smoke_defects": [],
                "error": f"评估异常: {e}",
                "metadata": state.get("metadata") or {}}

    # verify_node 原地修改了 state（dialogue_history, role_outputs 等），
    # 只返回变更字段，避免 add reducer 重复拼接 dialogue_history；
    # metadata 显式返回，避免依赖 langgraph 未文档化的浅拷贝副作用
    return {"verify_passed": state.get("verify_passed", False),
            "current_step": state.get("current_step", "verify_done"),
            "smoke_defects": smoke_defects,
            "metadata": state.get("metadata") or {}}


# ==================== Defect Repair 节点（小上下文定向修复） ====================


# 定向修复单轮上下文预算：最多携带 6 个文件、单文件 8000 字符
_DEFECT_REPAIR_MAX_FILES = 6
_DEFECT_REPAIR_FILE_CHAR_CAP = 8_000


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


def _collect_defect_repair_context(workspace) -> tuple[str, list[str]]:
    """收集定向修复的最小上下文：index.html 优先，其余 js/css 按相关性排序"""
    files = [f for f in workspace.list()
             if not f.startswith("docs/") and not f.startswith(".task/")]
    html = [f for f in files if f.endswith(".html")]
    js = [f for f in files if f.endswith(".js")]
    css = [f for f in files if f.endswith(".css")]

    def _relevance(name: str) -> int:
        # 入口/主逻辑文件优先（main/game/app/index 命名的权重更高）
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
            truncated = ""
            if len(content) > _DEFECT_REPAIR_FILE_CHAR_CAP:
                content = content[:_DEFECT_REPAIR_FILE_CHAR_CAP]
                truncated = "\n<!-- [上下文截断：仅展示前 8000 字符，修复时请基于可见内容] -->"
            lang = fname.rsplit(".", 1)[-1] if "." in fname else ""
            blocks.append(f"### {fname}\n```{lang}\n{content}{truncated}\n```")
        except Exception:
            blocks.append(f"### {fname}\n(无法读取)")
    return "\n\n".join(blocks) if blocks else "(无文件)", ordered


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

    repair_round = state.get("metadata", {}).get("defect_repair_count", 0) + 1
    # 无论本轮成败都必须递增计数，否则缺陷未修复时 verify↔defect_repair 会无限循环
    meta = dict(state.get("metadata") or {})
    meta["defect_repair_count"] = repair_round
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
    files_text, context_files = _collect_defect_repair_context(workspace)
    user_prompt = (
        "## 确定性缺陷清单（无头浏览器实测复现）\n\n"
        f"{defects_text}\n\n"
        f"## 当前文件（共 {len(context_files)} 个）\n\n{files_text}\n\n"
        "请按系统指令的修复原则和输出格式，返回修复后的 JSON。"
    )

    # ---- 单次 LLM 调用（截断检测 + 一次更大 max_tokens 重试） ----
    from llm.client import get_client
    client = get_client()
    system_prompt = load_prompt("tasks/defect_repair.md")

    def _call(max_tokens: int):
        return client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=max_tokens,
            timeout=150,
            thinking='enabled',  # 补丁 JSON 需要思考模式保证格式正确
        )

    response = None
    for max_tokens in (16_000, 32_000):
        try:
            resp = _call(max_tokens)
        except Exception as e:
            logger.warning(f"[DefectRepair] LLM 调用失败 (max_tokens={max_tokens}): {e}")
            break
        if not resp.is_error and resp.content:
            response = resp
            if getattr(resp, "finish_reason", None) == "length":
                logger.warning(
                    f"[DefectRepair] 响应截断 (max_tokens={max_tokens})，扩大重试"
                )
                continue
            break

    if not response or response.is_error or not response.content:
        logger.error("[DefectRepair] LLM 未返回有效内容，本轮修复失败")
        return {"current_step": "defect_repair_failed",
                "error": "LLM 调用失败",
                "metadata": state.get("metadata") or {}}

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
                single_resp = client.chat(
                    prompt=single_prompt,
                    system_prompt=system_prompt,
                    use_memory=False,
                    max_tokens=16_000,
                    timeout=150,
                    thinking='enabled',
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

    return {"current_step": "defect_repair_done",
                "metadata": state.get("metadata") or {}}
