# -*- coding: utf-8 -*-
"""
LangGraph 智能体节点函数（从 agents/nodes.py 迁移到 harness/instructions/nodes.py）
- team_leader_node: 需求分析 + 结构化 Plan
- tool_coder_node: 内部执行 ToolCallLoop 完成所有工具调用
"""

import json
import re
from typing import Dict, Any

from harness.state.agent_state import AgentState
from llm.client import get_client
from harness.instructions.prompts import TL_ANALYSIS_PROMPT
from harness.observability.logger import get_logger
from harness.harness_context import (
    get_role_executor, get_role_registry, get_tool_loop, get_workspace
)

logger = get_logger(__name__)


def _get_role_components(state):
    """从 state metadata 或模块级缓存获取角色执行组件"""
    return (
        get_role_executor(state),
        get_role_registry(state),
        get_tool_loop(state),
        get_workspace(state),
    )


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
    """生成澄清问题——LLM 根据已有信息自主判断还缺什么

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
    "极简白 — 白色背景，灰黑文字，大量留白，功能优先",
    "暖柔风格 — 暖色调、圆角卡片、柔和阴影 (默认)",
    "暗黑科技 — 深色背景、霓虹强调色、终端风格",
    "活泼多彩 — 明亮渐变、大色块、趣味性设计",
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
        messages = TL_ANALYSIS_PROMPT.format_messages(requirement=requirement)
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt, system_prompt=system_prompt,
            use_memory=False, max_tokens=2000, timeout=45
        )

        if response.is_error:
            raise Exception(response.error)

        # 清理 LLM 响应中的非法控制字符
        clean_content = response.content
        if clean_content:
            clean_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', clean_content)

        try:
            plan = json.loads(clean_content)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', clean_content, re.DOTALL)
            plan = json.loads(match.group()) if match else {}

        visual_style = state.get('visual_style', '') or \
            state.get('metadata', {}).get('visual_style', '')

        # 提取复杂度评级（默认 S）
        complexity = plan.get('complexity', 'S') if isinstance(plan, dict) else 'S'
        if complexity not in ('XS', 'S', 'M', 'L'):
            complexity = 'S'

        return {
            'plan': plan,
            'current_step': 'team_leader_done',
            'dialogue_history': [{
                'role': 'agent', 'name': 'Leon（负责人）',
                'content': '已完成需求分析和架构设计',
                'status': 'completed'
            }],
            'metadata': {
                'team_leader_success': True,
                'visual_style': visual_style,
                'complexity': complexity,
            },
            # 任务分解字段（三期：逐文件编码）
            'tasks': plan.get('tasks', []) if isinstance(plan, dict) else [],
            'interfaces': plan.get('interfaces', {}) if isinstance(plan, dict) else {},
            'implementation_order': plan.get('implementation_order', []) if isinstance(plan, dict) else [],
        }

    except Exception as e:
        logger.error(f"[TeamLeader] 执行失败：{e}")
        return {
            'plan': {},
            'current_step': 'team_leader_failed',
            'error': f"TeamLeader 失败：{e}",
            'dialogue_history': [{
                'role': 'agent', 'name': 'Leon（负责人）',
                'content': f"分析失败: {requirement[:50]}...",
                'status': 'failed'
            }],
            'metadata': {'team_leader_success': False}
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
                'role': 'agent', 'name': 'Henry（开发）',
                'content': f'生成过程出错: {e}',
                'status': 'failed'
            }],
        }


# ==================== 多角色 LangGraph 节点（三期） ====================


def pm_node(state: AgentState) -> Dict[str, Any]:
    """
    ProductManager 节点：生成 PRD。

    复用 RoleExecutor 执行 text 角色，产出 PRD 文档。
    """
    role_executor, role_registry, _tl, _ws = _get_role_components(state)

    if not role_executor or not role_registry:
        logger.warning("[PM Node] RoleExecutor/RoleRegistry 未注入，跳过")
        return {"current_step": "pm_skipped"}

    role = role_registry.get("ProductManager")
    if not role:
        return {"current_step": "pm_skipped"}

    requirement = state.get("requirement_content", "")
    plan = state.get("plan") or {}
    features = plan.get("features", []) if isinstance(plan, dict) else []
    feature_list = "\n".join(f"- {f}" for f in features) if features else requirement

    task_package = (
        f"分析以下需求并生成 PRD 文档：\n\n"
        f"## 用户需求\n{requirement}\n\n"
        f"## 预分析的功能点\n{feature_list}\n\n"
        f"按照你的 PRD 模板输出完整的分析文档。"
    )

    logger.info("[PM Node] 启动 ProductManager")
    result = role_executor.execute(role, state, task_package=task_package)

    dialogue_add = [{
        "role": "agent", "name": "Catherine（产品经理）",
        "content": f"PRD 生成完成 ({len(result.content)} 字符)" if result.success else f"PRD 生成失败: {result.error}",
        "status": "completed" if result.success else "failed",
    }]

    role_outputs = dict(state.get("role_outputs") or {})
    if result.success and result.content:
        role_outputs["ProductManager"] = result.content

    return {
        "role_outputs": role_outputs,
        "dialogue_history": dialogue_add,
        "current_step": "pm_done",
    }


def architect_node(state: AgentState) -> Dict[str, Any]:
    """
    Architect 节点：生成架构设计。

    复用 RoleExecutor 执行 text 角色，基于 PRD 产出架构设计文档。
    """
    role_executor, role_registry, _tl, _ws = _get_role_components(state)

    if not role_executor or not role_registry:
        logger.warning("[Architect Node] RoleExecutor/RoleRegistry 未注入，跳过")
        return {"current_step": "architect_skipped"}

    role = role_registry.get("Architect")
    if not role:
        return {"current_step": "architect_skipped"}

    requirement = state.get("requirement_content", "")
    role_outputs = state.get("role_outputs") or {}
    pm_output = role_outputs.get("ProductManager", "")

    task_package = (
        f"基于以下 PRD 设计前端架构：\n\n"
        f"## 用户需求\n{requirement}\n\n"
        f"## PRD\n{pm_output[:3000] if pm_output else '(PRD 待生成)'}\n\n"
        f"按照你的架构设计模板输出完整的技术方案。"
    )

    # 将 PM 产出作为额外上下文
    extra_context = f"## ProductManager 产出\n{pm_output[:3000]}" if pm_output else ""

    logger.info("[Architect Node] 启动 Architect")
    result = role_executor.execute(
        role, state,
        task_package=task_package,
        extra_context=extra_context,
    )

    dialogue_add = [{
        "role": "agent", "name": "Bob（架构师）",
        "content": f"架构设计完成 ({len(result.content)} 字符)" if result.success else f"架构设计失败: {result.error}",
        "status": "completed" if result.success else "failed",
    }]

    role_outputs = dict(state.get("role_outputs") or {})
    if result.success and result.content:
        role_outputs["Architect"] = result.content

    return {
        "role_outputs": role_outputs,
        "dialogue_history": dialogue_add,
        "current_step": "architect_done",
    }


def qa_node(state: AgentState) -> Dict[str, Any]:
    """
    QAReviewer 节点：代码审查。

    使用逐文件审查模式（对于有 implementation_order 的情况），
    或回退到整体审查（旧行为兼容）。
    """
    role_executor, role_registry, _tl, _ws = _get_role_components(state)

    if not role_executor or not role_registry:
        logger.warning("[QA Node] RoleExecutor/RoleRegistry 未注入，跳过")
        state["qa_passed"] = True
        return {"qa_passed": True, "current_step": "qa_skipped"}

    role = role_registry.get("QAReviewer")
    if not role:
        state["qa_passed"] = True
        return {"qa_passed": True, "current_step": "qa_skipped"}

    requirement = state.get("requirement_content", "")
    implementation_order = state.get("implementation_order") or []

    # 如果有 implementation_order，逐文件审查
    if implementation_order:
        result = _qa_per_file_review(state, role_executor, role, requirement, implementation_order)
    else:
        # 回退到整体审查
        logger.info("[QA Node] 使用整体审查模式")
        result = role_executor.execute(
            role, state,
            task_package=f"审查以下需求的代码实现质量：\n\n## 用户需求\n{requirement}\n\n检查代码是否完整实现需求，是否有安全和质量问题。",
        )

    if result.success and result.structured_output:
        qa_data = result.structured_output
        passed = qa_data.get("passed", True)
        rating = qa_data.get("overall_rating", 7)

        state["qa_passed"] = passed and rating >= 6
        logger.info(f"[QA Node] 审查完成: passed={state['qa_passed']}, rating={rating}")

        role_outputs = dict(state.get("role_outputs") or {})
        role_outputs["QAReviewer"] = json.dumps(qa_data, ensure_ascii=False)
        return {
            "qa_passed": state["qa_passed"],
            "role_outputs": role_outputs,
            "current_step": "qa_done",
        }
    else:
        state["qa_passed"] = True  # 审查失败时默认通过
        return {"qa_passed": True, "current_step": "qa_done"}


def _qa_per_file_review(state, role_executor, role, requirement, implementation_order):
    """逐文件 QA 审查（非一次性读全部）"""
    # 从 tool_loop 获取 workspace
    tool_loop = get_tool_loop(state)
    workspace = tool_loop.workspace if tool_loop else None

    if not workspace:
        # fallback to overall review
        return role_executor.execute(
            role, state,
            task_package=f"审查代码实现质量：{requirement}",
        )

    # 对每个文件收集简要摘要，然后做整体评估
    file_summaries = []
    for fname in implementation_order:
        try:
            content = workspace.read(fname)
            line_count = content.count('\n') + 1
            file_summaries.append({
                "file": fname,
                "lines": line_count,
                "preview": '\n'.join(content.split('\n')[:50]),
            })
        except Exception:
            file_summaries.append({"file": fname, "error": "无法读取"})

    # 构建审查任务
    files_text = "\n\n".join(
        f"### {fs['file']} ({fs.get('lines', '?')} 行)\n```\n{fs.get('preview', fs.get('error', ''))[:2000]}\n```"
        for fs in file_summaries
    )

    task_package = (
        f"审查以下需求的代码实现质量：\n\n"
        f"## 用户需求\n{requirement}\n\n"
        f"## 代码文件\n{files_text}\n\n"
        f"检查代码是否完整实现需求，是否有安全和质量问题。"
    )

    return role_executor.execute(role, state, task_package=task_package)


def repair_node(state: AgentState) -> Dict[str, Any]:
    """
    Repair 节点：接收 QA/Summarize 的问题反馈，定向修复特定文件。

    将问题列表注入对话历史，然后调用 ToolCallLoop 执行修复。
    修复轮次由 metadata.repair_count 追踪。
    """
    _executor, _reg, tool_loop, _ws = _get_role_components(state)
    if not tool_loop:
        state["current_step"] = "repair_error"
        return state

    repair_count = state.get("metadata", {}).get("repair_count", 0)
    state.setdefault("metadata", {})["repair_count"] = repair_count + 1

    # 收集修复任务
    issues = []

    # 从 QA 审查结果中提取问题
    role_outputs = state.get("role_outputs") or {}
    qa_raw = role_outputs.get("QAReviewer", "")
    if qa_raw:
        try:
            qa_data = json.loads(qa_raw) if isinstance(qa_raw, str) else qa_raw
            issues.extend(qa_data.get("critical_issues", []))
        except (json.JSONDecodeError, TypeError):
            pass

    # 从 Summarize 审查结果中提取问题
    summarize_raw = role_outputs.get("Summarize", "")
    if summarize_raw:
        try:
            summarize_data = json.loads(summarize_raw) if isinstance(summarize_raw, str) else summarize_raw
            if summarize_data.get("verdict") == "FAIL":
                issues.extend(summarize_data.get("issues", []))
        except (json.JSONDecodeError, TypeError):
            pass

    if not issues:
        logger.info("[Repair] 没有问题需要修复")
        state["current_step"] = "repair_done"
        return state

    logger.info(f"[Repair] 第 {repair_count + 1} 轮修复: {len(issues)} 个问题")

    # 构建修复提示
    repair_prompt = (
        f"## 代码审查发现以下问题，请立即修复（第 {repair_count + 1} 轮）\n\n"
        + "\n".join(f"- [{i+1}] {issue}" for i, issue in enumerate(issues))
        + "\n\n## 修复要求\n"
        + "- 用 edit_file 局部修改已有文件，不要重写整个文件\n"
        + "- 如果问题涉及未创建的文件，用 write_file 创建\n"
        + '- 修复完成后立即停止，告诉我“任务完成”\n'
    )

    state.setdefault("dialogue_history", []).append({
        "role": "user",
        "name": "System",
        "content": repair_prompt,
    })

    # 设置角色
    state.setdefault("metadata", {})["coder_name"] = "Henry（开发）"
    state["metadata"]["thinking_name"] = "Henry（开发）"

    # 限制修复迭代次数
    saved_max = tool_loop.MAX_ITERATIONS
    tool_loop.MAX_ITERATIONS = 8  # 修复轮次给多一些迭代

    try:
        result = tool_loop.run(state)
        state["dialogue_history"] = result.get("dialogue_history", [])
        state["current_step"] = "repair_done"
    except Exception as e:
        logger.error(f"[Repair] 修复失败: {e}")
        state["current_step"] = "repair_error"
        state["error"] = str(e)
    finally:
        tool_loop.MAX_ITERATIONS = saved_max

    return state
