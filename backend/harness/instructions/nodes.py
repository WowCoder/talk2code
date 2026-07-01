# -*- coding: utf-8 -*-
"""
LangGraph 智能体节点函数（从 agents/nodes.py 迁移到 harness/instructions/nodes.py）
- planner_node: 需求分析 + 结构化 Plan
- tool_coder_node: 内部执行 ToolCallLoop 完成所有工具调用
"""

import json
import re
from typing import Dict, Any

from harness.state.agent_state import AgentState
from llm.client import get_client
from harness.instructions.prompts import PLANNER_PROMPT
from harness.observability.logger import get_logger

logger = get_logger(__name__)


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


def planner_node(state: AgentState) -> Dict[str, Any]:
    """Planner 节点：需求分析 → 澄清 / 结构化 Plan"""
    requirement = state['requirement_content']
    clarify_round = state.get('metadata', {}).get('clarify_round', 0)

    # SOP: 全新需求（clarify_round == 0）始终触发澄清表单
    # 但如果需求已包含 [用户补充说明]，说明用户刚提交过澄清，跳过
    if clarify_round < 1 and '[用户补充说明]' not in requirement:
        try:
            client = get_client()
            questions = _generate_clarify_questions(client, requirement)
            if not questions:
                # LLM 没生成问题，用兜底问题
                questions = [
                    {"id": "q1", "type": "radio", "label": "你想做什么类型的应用？",
                     "options": ["工具类应用", "展示类页面", "数据管理类", "小游戏"]},
                    {"id": "visual_style", "type": "radio", "label": "你偏好哪种视觉风格？",
                     "options": ["极简白", "暖柔风格", "暗黑科技", "活泼多彩", "无偏好"]},
                ]
            if questions:
                # 判断需求是否详细，调整提示文案
                is_detailed = not _is_vague_requirement(requirement)
                hint_text = (
                    '你的需求已经很详细了，确认以下信息后即可开始生成'
                    if is_detailed else
                    '需求不够明确，需要你补充一些信息'
                )
                # 将 question_form 嵌入 dialogue_history，确保通过 REST API 也能获取
                return {
                    'plan': {},
                    'current_step': 'needs_clarification',
                    'dialogue_history': [{
                        'role': 'system', 'name': 'Planner',
                        'content': hint_text,
                        'status': 'needs_clarification',
                        'question_form': {'questions': questions},
                    }],
                    'metadata': {
                        'planner_success': True,
                        'question_form': {'questions': questions},
                        'clarify_round': clarify_round
                    }
                }
        except Exception as e:
            logger.warning(f"[Planner] 澄清问题生成失败：{e}")

    try:
        client = get_client()
        messages = PLANNER_PROMPT.format_messages(requirement=requirement)
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

        return {
            'plan': plan,
            'current_step': 'planner_done',
            'dialogue_history': [{
                'role': 'agent', 'name': 'Planner',
                'content': '已完成需求分析和架构设计',
                'status': 'completed'
            }],
            'metadata': {
                'planner_success': True,
                'visual_style': visual_style,
            }
        }

    except Exception as e:
        logger.error(f"[Planner] 执行失败：{e}")
        return {
            'plan': {},
            'current_step': 'planner_failed',
            'error': f"Planner 失败：{e}",
            'dialogue_history': [{
                'role': 'agent', 'name': 'Planner',
                'content': f"分析失败: {requirement[:50]}...",
                'status': 'failed'
            }],
            'metadata': {'planner_success': False}
        }


def tool_coder_node(state: AgentState) -> Dict[str, Any]:
    """
    Coder 节点：内部执行完整的 ToolCallLoop

    不再依赖 LangGraph 的迭代机制，而是在本节点内一次性跑完所有工具调用。
    工作流简化为 planner → tool_coder → END，消除死循环。
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
                'role': 'agent', 'name': 'Coder',
                'content': f'生成过程出错: {e}',
                'status': 'failed'
            }],
        }
