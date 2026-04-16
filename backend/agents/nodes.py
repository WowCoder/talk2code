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
from prompts import (
    RESEARCHER_PROMPT, PRODUCT_MANAGER_PROMPT,
    ARCHITECT_PROMPT, ENGINEER_PROMPT,
    # Deprecated imports kept for backward compatibility
    RESEARCHER_SYSTEM_PROMPT, RESEARCHER_USER_PROMPT,
    PRODUCT_MANAGER_SYSTEM_PROMPT, PRODUCT_MANAGER_USER_PROMPT,
    ARCHITECT_SYSTEM_PROMPT, ARCHITECT_USER_PROMPT,
    ENGINEER_SYSTEM_PROMPT, ENGINEER_USER_PROMPT,
    generate_fallback_code
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ==================== 辅助函数 ====================

def create_agent_output(agent_name: str, success: bool, output: str, error: str = None) -> dict:
    """创建智能体输出格式"""
    return {
        'agent_name': agent_name,
        'success': success,
        'output': output,
        'error': error
    }


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


def compress_outputs(agent_outputs: List[dict]) -> str:
    """压缩智能体输出，提取关键信息"""
    if not agent_outputs:
        return ""

    keywords = ['功能清单', '核心功能', '技术栈', '数据结构', '组件设计', '页面结构', '交互逻辑']
    compressed = []

    for output in agent_outputs:
        content = output.get('output', '')
        extracted_lines = []

        for line in content.split('\n'):
            line = line.strip()
            if line and any(kw in line for kw in keywords):
                extracted_lines.append(line)
            elif line.startswith('-') or (line and line[0].isdigit()):
                extracted_lines.append(line)

        if len('\n'.join(extracted_lines)) < 200:
            extracted = content[:500] + '...' if len(content) > 500 else content
        else:
            extracted = '\n'.join(extracted_lines[:30])

        compressed.append(f"{output['agent_name']}: {extracted}")

    return '\n\n'.join(compressed)


# ==================== 智能体节点 ====================

def researcher_node(state: AgentState) -> Dict[str, Any]:
    """
    研究员节点：分析市场需求和可行性
    """
    logger.info(f"[研究员] 开始分析需求：{state['requirement_id']}")

    try:
        client = get_client()
        # Use ChatPromptTemplate.format_messages() for LangChain-compatible message formatting
        messages = RESEARCHER_PROMPT.format_messages(requirement=state['requirement_content'])
        # Extract system and user prompts for custom client compatibility
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=45
        )

        output = f"【市场与需求分析】\n\n{response.content}" if response.content else '[错误] 无响应'

        if response.is_error:
            raise Exception(response.error)

        return {
            'agent_outputs': [create_agent_output('研究员', True, output)],
            'current_step': 'researcher_done',
            'dialogue_history': [{
                'role': 'agent',
                'name': '研究员',
                'content': output,
                'status': 'completed'
            }],
            'metadata': {'researcher_success': True}
        }

    except Exception as e:
        logger.error(f"[研究员] 执行失败：{e}")
        fallback = f"【市场与需求分析】\n\n需求分析：{state['requirement_content'][:100]}...（简化版）"

        return {
            'agent_outputs': [create_agent_output('研究员', False, fallback, str(e))],
            'current_step': 'researcher_failed',
            'error': f"研究员失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': '研究员',
                'content': fallback,
                'status': 'failed'
            }],
            'metadata': {'researcher_success': False}
        }


def product_manager_node(state: AgentState) -> Dict[str, Any]:
    """
    产品经理节点：拆解需求，生成功能清单
    """
    logger.info(f"[产品经理] 开始规划功能：{state['requirement_id']}")

    try:
        client = get_client()

        # 获取研究员输出作为上下文
        context = compress_outputs(state.get('agent_outputs', []))

        # Use ChatPromptTemplate.format_messages() for LangChain-compatible message formatting
        messages = PRODUCT_MANAGER_PROMPT.format_messages(
            requirement=state['requirement_content'],
            context=context if context else '无'
        )
        # Extract system and user prompts for custom client compatibility
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=45
        )

        output = f"【产品功能规划】\n\n{response.content}" if response.content else '[错误] 无响应'

        if response.is_error:
            raise Exception(response.error)

        return {
            'agent_outputs': [create_agent_output('产品经理', True, output)],
            'current_step': 'pm_done',
            'dialogue_history': [{
                'role': 'agent',
                'name': '产品经理',
                'content': output,
                'status': 'completed'
            }],
            'metadata': {'pm_success': True}
        }

    except Exception as e:
        logger.error(f"[产品经理] 执行失败：{e}")
        fallback = f"【产品功能规划】\n\n核心功能：基于需求「{state['requirement_content'][:50]}...」实现基本功能"

        return {
            'agent_outputs': [create_agent_output('产品经理', False, fallback, str(e))],
            'current_step': 'pm_failed',
            'error': f"产品经理失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': '产品经理',
                'content': fallback,
                'status': 'failed'
            }],
            'metadata': {'pm_success': False}
        }


def architect_node(state: AgentState) -> Dict[str, Any]:
    """
    架构师节点：设计技术方案
    """
    logger.info(f"[架构师] 开始设计架构：{state['requirement_id']}")

    try:
        client = get_client()

        # 获取前面所有智能体的输出
        context = compress_outputs(state.get('agent_outputs', []))

        # Use ChatPromptTemplate.format_messages() for LangChain-compatible message formatting
        messages = ARCHITECT_PROMPT.format_messages(
            requirement=state['requirement_content'],
            context=context if context else '无'
        )
        # Extract system and user prompts for custom client compatibility
        system_prompt = next((m.content for m in messages if m.type == 'system'), None)
        user_prompt = next((m.content for m in messages if m.type == 'human'), None)

        response = client.chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
            use_memory=False,
            max_tokens=2000,
            timeout=45
        )

        output = f"【技术架构设计】\n\n{response.content}" if response.content else '[错误] 无响应'

        if response.is_error:
            raise Exception(response.error)

        return {
            'agent_outputs': [create_agent_output('架构师', True, output)],
            'current_step': 'architect_done',
            'dialogue_history': [{
                'role': 'agent',
                'name': '架构师',
                'content': output,
                'status': 'completed'
            }],
            'metadata': {'architect_success': True}
        }

    except Exception as e:
        logger.error(f"[架构师] 执行失败：{e}")
        fallback = f"【技术架构设计】\n\n技术栈：HTML5 + CSS3 + JavaScript + LocalStorage"

        return {
            'agent_outputs': [create_agent_output('架构师', False, fallback, str(e))],
            'current_step': 'architect_failed',
            'error': f"架构师失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': '架构师',
                'content': fallback,
                'status': 'failed'
            }],
            'metadata': {'architect_success': False}
        }


def engineer_node(state: AgentState) -> Dict[str, Any]:
    """
    工程师节点：生成代码
    """
    logger.info(f"[工程师] 开始生成代码：{state['requirement_id']}")

    try:
        client = get_client()

        # 获取前面所有智能体的输出作为上下文
        context = compress_outputs(state.get('agent_outputs', []))

        # Use ChatPromptTemplate.format_messages() for LangChain-compatible message formatting
        messages = ENGINEER_PROMPT.format_messages(
            requirement=state['requirement_content'],
            context=context if context else '请根据需求生成代码'
        )
        # Extract system and user prompts for custom client compatibility
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
                'agent_outputs': [create_agent_output('工程师', True, response.content)],
                'code_files': code_files,
                'current_step': 'engineer_done',
                'dialogue_history': [{
                    'role': 'agent',
                    'name': '工程师',
                    'content': '代码生成完成',
                    'status': 'completed'
                }],
                'metadata': {'engineer_success': True}
            }
        else:
            # JSON 解析失败，使用 fallback
            code_files = generate_fallback_code(state['requirement_content'])
            return {
                'agent_outputs': [create_agent_output('工程师', False, response.content, result)],
                'code_files': code_files,
                'current_step': 'engineer_fallback',
                'error': f"代码解析失败：{result}",
                'dialogue_history': [{
                    'role': 'agent',
                    'name': '工程师',
                    'content': '代码生成完成（使用模板）',
                    'status': 'completed'
                }],
                'metadata': {'engineer_success': False}
            }

    except Exception as e:
        logger.error(f"[工程师] 执行失败：{e}")
        code_files = generate_fallback_code(state['requirement_content'])

        return {
            'agent_outputs': [create_agent_output('工程师', False, '使用 fallback 代码', str(e))],
            'code_files': code_files,
            'current_step': 'engineer_failed',
            'error': f"工程师失败：{e}",
            'dialogue_history': [{
                'role': 'agent',
                'name': '工程师',
                'content': '代码生成完成（使用模板）',
                'status': 'fallback'
            }],
            'metadata': {'engineer_success': False}
        }
