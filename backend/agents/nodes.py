# -*- coding: utf-8 -*-
"""
LangGraph 智能体节点函数
每个节点接收 AgentState，返回部分状态更新
"""

import json
import re
from typing import Dict, Any, List, Union, Tuple

from agents.state import AgentState
from llm.client import get_client
from prompts import PLANNER_PROMPT, ENGINEER_PROMPT, generate_fallback_code
from utils.logger import get_logger

logger = get_logger(__name__)


# ==================== 辅助函数 ====================

def extract_json_from_response(content: str) -> Tuple[bool, Union[list, str]]:
    """从响应中提取 JSON"""
    try:
        # 尝试直接解析
        return True, json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取 JSON 块
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            try:
                return True, json.loads(match.group())
            except:
                pass
        return False, "JSON 解析失败"


# ==================== 智能体节点 ====================

def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    Planner 节点：合并研究员、产品经理、架构师职责，产出结构化 Plan
    """
    logger.info(f"[Planner] 开始分析需求：{state['requirement_id']}")

    try:
        client = get_client()
        messages = PLANNER_PROMPT.format_messages(requirement=state['requirement_content'])
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=45
        )

        if response.is_error:
            raise Exception(response.error)

        # 解析 JSON
        plan = extract_json_from_response(response.content)
        if not plan[0]:  # 解析失败
            plan_result = {'features': [], 'tech_stack': {}, 'data_model': [], 'file_structure': [], 'implementation_notes': []}
        else:
            plan_result = plan[1] if plan[0] else {}

        return {
            'plan': plan_result,
            'current_step': 'planner_done',
            'dialogue_history': [{
                'role': 'agent',
                'name': 'Planner',
                'content': '已完成需求分析和架构设计',
                'status': 'completed'
            }],
            'metadata': {'planner_success': True}
        }

    except Exception as e:
        logger.error(f"[Planner] 执行失败：{e}")
        return {
            'plan': {},
            'current_step': 'planner_failed',
            'error': f"Planner 失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': 'Planner',
                'content': f"分析失败，使用简化版：{state['requirement_content'][:50]}...",
                'status': 'failed'
            }],
            'metadata': {'planner_success': False}
        }


def engineer_node(state: AgentState) -> Dict[str, Any]:
    """
    工程师节点：根据 Planner 的 Plan 生成代码
    """
    logger.info(f"[工程师] 开始生成代码：{state['requirement_id']}")

    try:
        client = get_client()

        # 使用 Planner 输出的 plan 作为上下文
        plan = state.get('plan', {})
        plan_context = json.dumps(plan, ensure_ascii=False, indent=2) if plan else '请根据需求生成代码'

        messages = ENGINEER_PROMPT.format_messages(
            requirement=state['requirement_content'],
            context=plan_context
        )
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=4000,
            timeout=60
        )

        if not response.content or response.is_error:
            raise Exception(response.error or "无响应")

        # 解析 JSON
        success, result = extract_json_from_response(response.content)

        if success:
            code_files = result if isinstance(result, list) else []
            return {
                'code_files': code_files,
                'current_step': 'engineer_done',
                'dialogue_history': [{
                    'role': 'agent',
                    'name': 'Coder',
                    'content': '代码生成完成',
                    'status': 'completed'
                }],
                'metadata': {'engineer_success': True}
            }
        else:
            # JSON 解析失败，使用 fallback
            code_files = generate_fallback_code(state['requirement_content'])
            return {
                'code_files': code_files,
                'current_step': 'engineer_fallback',
                'error': f"代码解析失败：{result}",
                'dialogue_history': [{
                    'role': 'agent',
                    'name': 'Coder',
                    'content': '代码生成完成（使用模板）',
                    'status': 'completed'
                }],
                'metadata': {'engineer_success': False}
            }

    except Exception as e:
        logger.error(f"[工程师] 执行失败：{e}")
        code_files = generate_fallback_code(state['requirement_content'])

        return {
            'code_files': code_files,
            'current_step': 'engineer_failed',
            'error': f"工程师失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': 'Coder',
                'content': '代码生成完成（使用模板）',
                'status': 'fallback'
            }],
            'metadata': {'engineer_success': False}
        }
