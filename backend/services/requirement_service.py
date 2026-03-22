# -*- coding: utf-8 -*-
"""
需求管理服务
封装 AI 多智能体协同处理需求的业务逻辑
"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from models import SessionLocal, Requirement
from utils.logger import get_logger
from utils import get_current_timestamp, SSEMessage
from services.sse_manager import sse_manager
from agents.workflow import get_workflow
from agents.state import AgentState

logger = get_logger(__name__)


class RequirementService:
    """需求管理服务"""

    def __init__(self):
        # LangGraph 工作流实例（单例）
        self.workflow = get_workflow()
        # 进度映射
        self._progress_map = {
            'researcher': 25,
            'product_manager': 50,
            'architect': 75,
            'engineer': 90
        }

    def _execute_workflow_with_stream(self, requirement_id: int, initial_state: AgentState) -> Optional[AgentState]:
        """
        流式执行 LangGraph 工作流，并推送 SSE 消息

        Args:
            requirement_id: 需求 ID
            initial_state: 初始状态

        Returns:
            最终状态，如果执行失败返回 None
        """
        final_state = None

        # 使用 stream 方法流式执行
        for event in self.workflow.stream(initial_state, stream_mode='updates'):
            # event 是一个字典，key 是节点名，value 是节点返回的状态更新
            for node_name, node_output in event.items():
                logger.info(f"节点 {node_name} 执行完成")

                # 发送进度更新
                progress = self._progress_map.get(node_name, 0)
                agent_name_map = {
                    'researcher': '研究员',
                    'product_manager': '产品经理',
                    'architect': '架构师',
                    'engineer': '工程师'
                }
                agent_name = agent_name_map.get(node_name, node_name)
                self._send_progress(requirement_id, agent_name, progress)

                # 发送对话更新
                if 'dialogue_history' in node_output and node_output['dialogue_history']:
                    for dialogue in node_output['dialogue_history']:
                        self._send_dialogue(requirement_id, dialogue.get('name', agent_name), dialogue.get('content', ''))

                # 发送代码更新
                if node_name == 'engineer' and 'code_files' in node_output:
                    code_files = node_output.get('code_files', [])
                    for file_data in code_files:
                        filename = file_data.get('filename', 'unknown.txt')
                        content = file_data.get('content', '')
                        self._send_code(requirement_id, filename, content)

                # 检查错误
                if node_output.get('error'):
                    logger.error(f"节点 {node_name} 执行错误：{node_output['error']}")

            # 保留最终状态
            final_state = node_output

        return final_state

    def process_requirement(self, requirement_id: int) -> bool:
        """
        处理需求：执行 LangGraph 多智能体协同流程

        Args:
            requirement_id: 需求 ID

        Returns:
            是否成功完成
        """
        db = SessionLocal()
        try:
            requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
            if not requirement:
                logger.error(f"需求不存在：{requirement_id}")
                return False

            # 如果需求已经完成或正在处理中，跳过
            if requirement.status in ['finished', 'processing']:
                logger.info(f"需求 {requirement_id} 状态为 {requirement.status}，跳过处理")
                return False

            # 更新状态为处理中
            requirement.status = 'processing'
            db.commit()
            logger.info(f"需求 {requirement_id} 开始处理")

            # 构建 LangGraph 初始状态
            initial_state: AgentState = {
                'requirement_id': requirement_id,
                'requirement_content': requirement.content,
                'agent_outputs': [],
                'current_step': 'starting',
                'code_files': None,
                'error': None,
                'dialogue_history': requirement.dialogue_history or [],
                'metadata': {}
            }

            # 发送开始通知
            self._send_progress(requirement_id, '开始', 0)

            # 执行 LangGraph 工作流（流式处理，支持 SSE 推送）
            logger.info("LangGraph 工作流开始执行...")
            final_state = self._execute_workflow_with_stream(requirement_id, initial_state)

            if final_state is None:
                return False

            # 处理最终状态
            return self._process_final_state(db, requirement, requirement_id, final_state)

        except Exception as e:
            logger.error(f"处理需求时发生异常：{e}", exc_info=True)
            try:
                requirement.status = 'failed'
                db.commit()
            except:
                pass
            return False

        finally:
            db.close()

    def _process_final_state(self, db, requirement: Requirement, requirement_id: int, final_state: AgentState) -> bool:
        """处理 LangGraph 工作流的最终状态"""
        try:
            # 检查是否有错误
            if final_state.get('error'):
                logger.error(f"工作流执行错误：{final_state['error']}")
                requirement.status = 'failed'
                db.commit()
                return False

            # 保存对话历史
            dialogue_history = final_state.get('dialogue_history', [])
            if dialogue_history:
                requirement.dialogue_history = dialogue_history
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(requirement, 'dialogue_history')

            # 处理代码文件
            code_files = final_state.get('code_files', [])
            if code_files and isinstance(code_files, list):
                requirement.code_files = code_files
                logger.info(f"保存了 {len(code_files)} 个代码文件")
                # 发送代码 SSE 消息
                for file_data in code_files:
                    filename = file_data.get('filename', 'unknown.txt')
                    content = file_data.get('content', '')
                    self._send_code(requirement_id, filename, content)

            # 更新状态为完成
            requirement.status = 'finished'
            db.commit()
            logger.info(f"需求 {requirement_id} 处理完成")

            # 发送完成通知
            self._send_complete(requirement_id)

            return True

        except Exception as e:
            logger.error(f"处理最终状态时发生异常：{e}", exc_info=True)
            requirement.status = 'failed'
            db.commit()
            return False

    def _send_progress(self, requirement_id: int, agent_name: str, progress: int):
        """发送进度更新消息"""
        message = SSEMessage.progress_message(agent_name, progress, 'processing')
        sse_manager.broadcast(str(requirement_id), message)

    def _send_dialogue(self, requirement_id: int, name: str, content: str):
        """发送对话消息"""
        message = SSEMessage.dialogue_message('agent', name, content, get_current_timestamp())
        sse_manager.broadcast(str(requirement_id), message)

    def _send_code(self, requirement_id: int, filename: str, content: str):
        """发送代码消息"""
        message = SSEMessage.code_message(filename, content, 0, True)
        sse_manager.broadcast(str(requirement_id), message)

    def _send_complete(self, requirement_id: int):
        """发送完成通知"""
        message = SSEMessage.complete_message(requirement_id)
        sse_manager.broadcast(str(requirement_id), message)


# 全局服务实例
requirement_service = RequirementService()


def process_requirement_async(requirement_id: int):
    """
    异步处理需求（在线程中执行）

    Args:
        requirement_id: 需求 ID
    """
    service = RequirementService()
    return service.process_requirement(requirement_id)
