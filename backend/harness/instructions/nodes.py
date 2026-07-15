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


def _extract_json_from_llm_response(content: str) -> dict | None:
    """
    从 LLM 原始响应中健壮提取 JSON 对象。

    处理常见 LLM 输出格式：
    1. 纯 JSON
    2. ```json ... ``` 代码块包裹
    3. ``` ... ``` 无语言标记的代码块
    4. 开头有说明文字 + JSON
    5. JSON 内嵌在任意文本中
    6. LLM 输出被 max_tokens 截断（通过 _try_fix_json 修复）

    使用括号计数匹配最外层 {}，避免贪婪匹配问题。

    Returns:
        解析成功的 dict，或 None（表示提取失败）。
        注意：None 明确表示"无法提取"，区别于空 JSON {}。
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
    start_idx = raw.find('{')
    if start_idx == -1:
        return None

    depth = 0
    end_idx = -1
    for i in range(start_idx, len(raw)):
        ch = raw[i]
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
            # 尝试修复常见 JSON 问题：尾部逗号、单引号
            pass
        try:
            # 移除尾部逗号
            fixed = re.sub(r',\s*}', '}', json_str)
            fixed = re.sub(r',\s*]', ']', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Step 3.5: 尝试修复截断 JSON（LLM 输出被 max_tokens 截断）
    json_str = raw[start_idx:]
    fixed = try_fix_json(json_str)
    if fixed:
        return fixed

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


def team_leader_node(state: AgentState) -> Dict[str, Any]:
    """TeamLeader 节点：需求分析 → 结构化 Plan

    澄清由上游 IntentRouter 统一处理（进入此节点前 intent 已固定为 'task'）。
    """
    requirement = state['requirement_content']

    try:
        client = get_client()
        system_prompt = load_prompt("coding/tl_analysis.md")
        user_prompt = f"请分析以下需求并生成开发计划：\n\n{requirement}"

        response = client.chat(
            prompt=user_prompt, system_prompt=system_prompt,
            use_memory=False, max_tokens=4000, timeout=45
        )

        if response.is_error:
            raise Exception(response.error)

        # 清理 LLM 响应并提取 JSON
        plan = _extract_json_from_llm_response(response.content)
        if plan is None and response.finish_reason == "length":
            # 截断响应：用更大 max_tokens 重试一次
            logger.warning(
                f"[TeamLeader] 检测到截断 (finish_reason=length)，"
                f"当前 content 长度={len(response.content)}，"
                f"准备以 max_tokens=8000 重试"
            )
            retry_response = client.chat(
                prompt=user_prompt, system_prompt=system_prompt,
                use_memory=False, max_tokens=8000, timeout=60
            )
            if not retry_response.is_error:
                plan = _extract_json_from_llm_response(retry_response.content)
                if plan is not None:
                    logger.info("[TeamLeader] 重试成功，提取到完整 JSON")

        if plan is None:
            # 诊断信息
            truncation_hint = ""
            if response.finish_reason == "length":
                truncation_hint = (
                    "（提示：LLM 返回 finish_reason='length'，响应可能被 max_tokens 截断，"
                    "已尝试以 max_tokens=8000 重试仍然失败）"
                )
            content_tail = response.content[-300:] if response.content else ""
            raise Exception(
                f"无法从 LLM 响应中提取 JSON{truncation_hint}\n"
                f"响应前200字符: {response.content[:200]}\n"
                f"响应后300字符: {content_tail}\n"
                f"响应总长度: {len(response.content)} 字符"
            )

        visual_style = state.get('visual_style', '') or \
            state.get('metadata', {}).get('visual_style', '')

        # 提取复杂度评级（默认 S）
        complexity = plan.get('complexity', 'S') if isinstance(plan, dict) else 'S'
        if complexity not in ('XS', 'S', 'M', 'L'):
            complexity = 'S'

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
                'content': '已完成需求分析和架构设计',
                'status': 'completed',
                'plan': tl_plan_data,
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

    - XS/S: 直接 ToolCallLoop（简单编码）
    - M/L: 逐文件编码 + CodeReview（使用 file_coder 的逻辑）

    支持 Agent 委派模式：当 plan.tasks 包含 type 字段时，
    按 research/code/review 类型选择不同的执行策略。
    """
    complexity = state.get("metadata", {}).get("complexity", "S")
    has_tasks = bool(state.get("implementation_order"))

    # ---- Agent 委派：检查是否有混合类型的子任务 ----
    tasks = state.get("tasks") or []
    has_typed_tasks = any(
        isinstance(t, dict) and t.get("type") and t.get("type") != "code"
        for t in tasks
    )

    if has_typed_tasks and has_tasks:
        # 混合类型任务 → 按 TaskType 选择执行策略
        return _execute_delegated_tasks(state)

    if complexity in ("M", "L") and has_tasks:
        # M/L 复杂度 + 有任务分解 → 逐文件编码
        from harness.instructions.file_coder import file_by_file_coder_node
        return file_by_file_coder_node(state)
    else:
        # XS/S 或缺少任务分解 → 简单编码
        tool_loop = get_tool_loop(state)
        if not tool_loop:
            return {
                "current_step": "error",
                "error": "ToolCallLoop 未注入到 state",
            }

        # 设置角色名称
        state.setdefault("metadata", {})["coder_name"] = DEV_NAME
        state["metadata"]["thinking_name"] = DEV_NAME

        # 初始化 CompletionContract（仅 M/L 复杂度使用，XS/S 跳过以减少开销）
        if complexity in ("M", "L"):
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

        # 注入 Hook 失败历史
        hook_failures = state.get("hook_failures", {})
        if hook_failures:
            failure_lines = []
            for hook_name, count in hook_failures.items():
                if count > 0:
                    failure_lines.append(f"- {hook_name}: 失败 {count} 次")
            if failure_lines:
                state.setdefault("dialogue_history", []).append({
                    "role": "user",
                    "name": "System",
                    "content": (
                        "## 历史验证失败记录（请注意避免）\n"
                        + "\n".join(failure_lines)
                        + "\n\n请在编码时特别注意以上问题，避免重复出现。"
                    ),
                })

        logger.info(f"[Coder] 启动 {complexity} 简单编码")

        try:
            result = tool_loop.run(state)
            # tool_loop.run() 已原地修改 state（含 dialogue_history），
            # node 只返回变更字段，避免 add reducer 重复拼接 dialogue_history
            if result.get("current_step") == "task_complete":
                next_step = "coding_done"
            elif result.get("current_step") == "llm_error":
                next_step = "llm_error"
            elif result.get("error"):
                next_step = "coding_error"
            else:
                next_step = result.get("current_step", "done")

            return {
                "current_step": next_step,
                "code_files": result.get("code_files", []),
                "error": result.get("error", "") if not result.get("llm_error") else result.get("error", "LLM call failed"),
                "hook_failures": result.get("hook_failures", {}),
            }

        except Exception as e:
            logger.error(f"[Coder] 执行失败：{e}")
            return {"current_step": "coding_error", "error": str(e)}


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
        return {"verify_passed": False, "current_step": "verify_done"}

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

    # 构建评估 prompt
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

请基于以上信息，按照 Evaluator 的评估维度和输出格式，给出结构化评估结果。"""

    logger.info(f"[Verify] 启动评估: {len(code_files)} 个文件, prompt 长度={len(user_prompt)}")

    try:
        client = get_client()
        response = client.chat(
            prompt=user_prompt,
            system_prompt=evaluator_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=90,
        )

        if response.is_error or not response.content:
            logger.warning(f"[Verify] LLM 调用失败: {response.error}，保守判定为 NEEDS_WORK")
            return {"verify_passed": False, "current_step": "verify_done",
                    "error": f"LLM 调用失败: {response.error}"}

        # 解析评估结果
        content = response.content.strip()
        result = {}
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    result = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        verdict = result.get("verdict", "NEEDS_WORK")
        findings = result.get("findings", [])
        overall_score = result.get("overall_score", 0.0)
        score = result.get("score", {})

        # 将 verdict 转为 verify_passed
        state["verify_passed"] = (verdict == "PASS")
        state["current_step"] = "verify_done"

        # 构建评估结果对象（始终构建，供后续 QA 反馈使用）
        complexity = state.get("metadata", {}).get("complexity", "S")
        evaluator_result = {
            "verdict": verdict,
            "summary": result.get("summary", ""),
            "score": score,
            "overall_score": overall_score,
            "findings": findings,
            "browser_result": browser_result,
            "timestamp": __import__('time').time(),
        }
        # 持久化到 .task/evaluator/result.json（XS 复杂度跳过）
        if complexity != "XS":
            try:
                workspace.write(
                    ".task/evaluator/result.json",
                    json.dumps(evaluator_result, ensure_ascii=False, indent=2)
                )
                logger.info(f"[Verify] 评估结果已写入 .task/evaluator/result.json")
            except Exception as e:
                logger.warning(f"[Verify] 写入结果文件失败: {e}")

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
                            max_tokens=2000,
                            timeout=90,
                        )
                        if not retry_response.is_error and retry_response.content:
                            retry_content = retry_response.content.strip()
                            try:
                                result = json.loads(retry_content)
                            except json.JSONDecodeError:
                                match = re.search(r'\{[\s\S]*\}', retry_content)
                                if match:
                                    try:
                                        result = json.loads(match.group())
                                    except json.JSONDecodeError:
                                        pass
                            verdict = result.get("verdict", "NEEDS_WORK")
                            findings = result.get("findings", [])
                            overall_score = result.get("overall_score", overall_score)
                            score = result.get("score", score)
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
                    repair_count = state.get("metadata", {}).get("repair_count", 0)
                    state.setdefault("metadata", {})["repair_count"] = repair_count + 1
            elif findings:
                # NEEDS_WORK 且有具体 findings：递增 repair_count
                repair_count = state.get("metadata", {}).get("repair_count", 0)
                state.setdefault("metadata", {})["repair_count"] = repair_count + 1
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
        qa_feedback = (
            f"## 代码评估: {'✅ PASS' if verdict == 'PASS' else '❌ NEEDS_WORK'}\n\n"
            f"**评分**: {overall_score}/10 ({dimension_scores})\n\n"
            f"**摘要**: {result.get('summary', '')}\n\n"
        )
        if findings:
            qa_feedback += "**发现的问题**:\n" + "\n".join(
                f"- [{f.get('severity', '?')}] {f.get('description', '')}"
                + (f"\n  → 修复建议: {f.get('suggestion', '')}" if f.get('suggestion') else "")
                for f in findings
            )
            qa_feedback += (
                "\n\n**修复指南**:\n"
                "- 逐条修复以上问题，每修完一个问题用 run_preview 验证\n"
                "- 优先使用 edit_file 做局部修改；如果 edit_file 连续失败 2 次，改用 write_file 重写文件\n"
                "- 对于大文件（>300行），用 read_file 的 start_line/end_line 参数定位到问题所在行\n"
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
                result, ensure_ascii=False
            )

        logger.info(
            f"[Verify] 评估完成: verdict={verdict}, "
            f"score={overall_score}, findings={len(findings)}"
        )

    except Exception as e:
        logger.warning(f"[Verify] 评估异常: {e}，保守判定为 NEEDS_WORK")
        return {"verify_passed": False, "current_step": "verify_done",
                "error": f"评估异常: {e}"}

    # verify_node 原地修改了 state（dialogue_history, role_outputs 等），
    # 只返回变更字段，避免 add reducer 重复拼接 dialogue_history
    return {"verify_passed": state.get("verify_passed", False), "current_step": state.get("current_step", "verify_done")}
