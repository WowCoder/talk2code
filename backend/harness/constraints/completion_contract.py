# -*- coding: utf-8 -*-
"""
CompletionContract —— Default-FAIL 检查清单

基于 TeamLeader 输出的 implementation_order 自动生成 .task/contract.json，
所有目标文件初始 created: false。PreToolUse Hook 在 write_file 成功后自动标记完成，
在全部完成前阻断 task_complete 声明。

设计原则:
- 文件系统状态不受 LangGraph 节点替换影响
- Hook 可以不经过 LLM 确定性更新（零 token 消耗）
- 崩溃恢复时直接读取 contract.json 即可知道进度
"""

import json
import os
from typing import List, Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)


class CompletionContract:
    """管理 .task/contract.json 检查清单文件"""

    def __init__(self, workspace):
        """
        Args:
            workspace: WorkspaceFS 实例，用于读写工作区文件
        """
        self._workspace = workspace
        self._contract_path = ".task/contract.json"

    # ---- 初始化 ----

    def initialize(self, implementation_order: List[str]) -> bool:
        """从 implementation_order 生成 contract.json

        Args:
            implementation_order: TeamLeader 输出的文件创建顺序列表

        Returns:
            True 如果成功创建 contract，False 如果 order 为空
        """
        if not implementation_order:
            logger.warning("[CompletionContract] implementation_order 为空，跳过 contract 创建")
            return False

        contract = {}
        for file_path in implementation_order:
            contract[file_path] = {
                "created": False,
                "validated": False,
            }

        self._write_contract(contract)
        logger.info(
            f"[CompletionContract] 已初始化 contract: "
            f"{len(contract)} 个文件, 全部 marked created=false"
        )
        return True

    def initialize_incremental(self, implementation_order: List[str]) -> bool:
        """增量初始化：新文件添加 created=false，已有文件保持原状态

        用于修复循环重入 Coder 节点时避免重置已有的写入进度。
        与 initialize() 的区别：不覆盖已存在文件的 created/validated 状态。

        Args:
            implementation_order: TeamLeader 输出的文件创建顺序列表

        Returns:
            True 如果成功，False 如果 order 为空
        """
        if not implementation_order:
            return False

        existing = self._read_contract()
        new_files = 0
        for file_path in implementation_order:
            if file_path not in existing:
                existing[file_path] = {"created": False, "validated": False}
                new_files += 1

        if new_files > 0:
            self._write_contract(existing)
            logger.info(
                f"[CompletionContract] 增量初始化: 补充 {new_files} 个新文件, "
                f"总文件数 {len(existing)}, 保留已有文件的 created/validated 状态"
            )
        else:
            logger.info(
                f"[CompletionContract] 增量初始化: 无新文件, "
                f"已有 {len(existing)} 个文件状态保持不变"
            )
        return True

    # ---- 状态查询 ----

    def _read_contract(self) -> dict:
        """读取 contract.json，文件不存在时返回空 dict"""
        try:
            content = self._workspace.read(self._contract_path)
            return json.loads(content) if content.strip() else {}
        except Exception:
            return {}

    def _write_contract(self, data: dict):
        """写入 contract.json 到工作区"""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        self._workspace.write(self._contract_path, content)

    def is_created(self, file_path: str) -> bool:
        """检查文件是否已标记为创建"""
        contract = self._read_contract()
        if file_path not in contract:
            return False
        return contract[file_path].get("created", False)

    def mark_created(self, file_path: str) -> bool:
        """标记文件为已创建

        Args:
            file_path: 被写入的文件路径

        Returns:
            True 如果文件在 contract 中且状态已更新，False 如果不在 contract 中
        """
        contract = self._read_contract()
        if file_path not in contract:
            return False  # 不在 contract 中，非目标文件

        if contract[file_path].get("created"):
            return True  # 已经是 created，幂等

        contract[file_path]["created"] = True
        self._write_contract(contract)
        logger.info(f"[CompletionContract] {file_path} → created=true")
        return True

    def add_file(self, file_path: str, created: bool = False, validated: bool = False) -> bool:
        """动态添加文件到 contract（用于 write_file 创建了 plan 之外的文件时）

        Args:
            file_path: 文件路径
            created: 初始 created 状态（默认 False）
            validated: 初始 validated 状态（默认 False）

        Returns:
            True 如果文件被新增，False 如果已存在
        """
        contract = self._read_contract()
        if file_path in contract:
            return False
        contract[file_path] = {"created": created, "validated": validated}
        self._write_contract(contract)
        logger.info(f"[CompletionContract] add_file: {file_path} created={created}")
        return True

    def mark_validated(self, file_path: str) -> bool:
        """标记文件为已验证（Evaluator 确认通过后调用）"""
        contract = self._read_contract()
        if file_path not in contract:
            return False

        contract[file_path]["validated"] = True
        self._write_contract(contract)
        return True

    def all_completed(self) -> bool:
        """检查所有文件是否都已创建"""
        contract = self._read_contract()
        if not contract:
            return True  # 没有 contract = 无需检查
        return all(
            info.get("created", False)
            for info in contract.values()
        )

    def pending_files(self) -> List[str]:
        """返回尚未创建的文件列表"""
        contract = self._read_contract()
        return sorted(
            f for f, info in contract.items()
            if not info.get("created", False)
        )

    def total_files(self) -> int:
        """返回 contract 中的文件总数"""
        return len(self._read_contract())

    def completed_count(self) -> int:
        """返回已创建的文件数"""
        contract = self._read_contract()
        return sum(1 for info in contract.values() if info.get("created", False))

    def get_progress(self) -> dict:
        """返回进度摘要"""
        return {
            "total": self.total_files(),
            "completed": self.completed_count(),
            "pending": self.pending_files(),
            "all_done": self.all_completed(),
        }

    def exists(self) -> bool:
        """检查 contract 文件是否存在"""
        try:
            self._workspace.read(self._contract_path)
            return True
        except Exception:
            return False

    def clear(self):
        """清除 contract（任务完成后）"""
        try:
            self._workspace.delete_file(self._contract_path)
            logger.info("[CompletionContract] contract 已清除")
        except Exception as e:
            logger.warning(f"[CompletionContract] 清除 contract 失败: {e}")
