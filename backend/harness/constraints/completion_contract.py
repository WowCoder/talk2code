# -*- coding: utf-8 -*-
"""
CompletionContract —— Default-FAIL 检查清单（v2：文件 + 验收条件两级）

v1 只追踪「文件存在」粒度（created/validated），AC 满足与否全流程无人写入。
v2 升级为两级契约：
1. 文件级：implementation_order 中每个文件的 created 状态（不变）
2. AC 级：acceptance_criteria 中每条的交互元素证据状态（evidence_found）

task_complete 声明前的 Hook 按 AC 清单做 DOM 级预检（静态启发式：
AC 文本与 index.html 的元素 id/class/name/aria-label 相互印证），
未找到证据的 AC 会阻断完成声明并给出缺口报告。

设计原则:
- 文件系统状态不受 LangGraph 节点替换影响
- Hook 可以不经过 LLM 确定性更新（零 token 消耗）
- 崩溃恢复时直接读取 contract.json 即可知道进度
"""

import json
import re
from typing import List, Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)

# contract.json 当前 schema 版本
CONTRACT_VERSION = 2


class CompletionContract:
    """管理 .task/contract.json 检查清单文件（v2 格式，兼容读取 v1）"""

    def __init__(self, workspace):
        """
        Args:
            workspace: WorkspaceFS 实例，用于读写工作区文件
        """
        self._workspace = workspace
        self._contract_path = ".task/contract.json"

    # ---- 初始化 ----

    def initialize(self, implementation_order: List[str],
                   acceptance_criteria: Optional[List[dict]] = None) -> bool:
        """从 implementation_order (+ 可选 AC 清单) 生成 contract.json

        Args:
            implementation_order: TeamLeader 输出的文件创建顺序列表
            acceptance_criteria: TL 输出的验收条件 [{id, label, how_to_verify}]

        Returns:
            True 如果成功创建 contract，False 如果 order 为空
        """
        if not implementation_order:
            logger.warning("[CompletionContract] implementation_order 为空，跳过 contract 创建")
            return False

        contract = {
            "version": CONTRACT_VERSION,
            "files": {
                file_path: {"created": False, "validated": False}
                for file_path in implementation_order
            },
            "acceptance_criteria": self._normalize_acs(acceptance_criteria),
        }

        self._write_contract(contract)
        logger.info(
            f"[CompletionContract] 已初始化 contract: "
            f"{len(contract['files'])} 个文件, "
            f"{len(contract['acceptance_criteria'])} 条验收条件, 全部 marked created=false"
        )
        return True

    def initialize_incremental(self, implementation_order: List[str],
                               acceptance_criteria: Optional[List[dict]] = None) -> bool:
        """增量初始化：新文件/新 AC 添加为未完成，已有条目保持原状态

        用于修复循环重入 Coder 节点时避免重置已有的写入进度。
        """
        if not implementation_order:
            return False

        data = self._read_contract()
        files = data.get("files", {}) if isinstance(data.get("files"), dict) else {}
        new_files = 0
        for file_path in implementation_order:
            if file_path not in files:
                files[file_path] = {"created": False, "validated": False}
                new_files += 1

        # 增量补充 AC（按 id 去重）
        acs = data.get("acceptance_criteria")
        if not isinstance(acs, list):
            acs = []
        known_ids = {a.get("id") for a in acs if isinstance(a, dict)}
        new_acs = 0
        for ac in self._normalize_acs(acceptance_criteria):
            if ac["id"] not in known_ids:
                acs.append(ac)
                new_acs += 1

        data["version"] = CONTRACT_VERSION
        data["files"] = files
        data["acceptance_criteria"] = acs

        if new_files or new_acs:
            self._write_contract(data)
            logger.info(
                f"[CompletionContract] 增量初始化: 补充 {new_files} 个文件 / {new_acs} 条 AC, "
                f"总计 {len(files)} 个文件 / {len(acs)} 条 AC, 保留已有状态"
            )
        else:
            logger.info(
                f"[CompletionContract] 增量初始化: 无新增, "
                f"已有 {len(files)} 个文件 / {len(acs)} 条 AC 状态保持不变"
            )
        return True

    @staticmethod
    def _normalize_acs(acceptance_criteria) -> List[dict]:
        """把 TL 的 AC 列表规整为契约内部格式。"""
        result = []
        for ac in acceptance_criteria or []:
            if not isinstance(ac, dict):
                continue
            ac_id = str(ac.get("id") or "").strip()
            if not ac_id:
                continue
            result.append({
                "id": ac_id,
                "label": str(ac.get("label") or "")[:120],
                "how_to_verify": str(ac.get("how_to_verify") or "")[:200],
                "evidence_found": False,
            })
        return result

    # ---- 兼容 v1 扁平格式的读写 ----

    def _read_contract(self) -> dict:
        """读取 contract.json，统一返回 v2 结构；文件不存在时返回空 dict"""
        try:
            content = self._workspace.read(self._contract_path)
            raw = json.loads(content) if content.strip() else {}
        except Exception:
            return {}

        if not isinstance(raw, dict):
            return {}
        if raw.get("version") == CONTRACT_VERSION and isinstance(raw.get("files"), dict):
            return raw
        # v1 扁平格式: {path: {created, validated}} → 包装为 v2
        return {
            "version": 1,
            "files": raw,
            "acceptance_criteria": [],
        }

    def _write_contract(self, data: dict):
        """写入 contract.json 到工作区（统一落盘为当前 schema 版本）"""
        data["version"] = CONTRACT_VERSION
        content = json.dumps(data, ensure_ascii=False, indent=2)
        self._workspace.write(self._contract_path, content)

    # ---- 文件级状态 ----

    def is_created(self, file_path: str) -> bool:
        """检查文件是否已标记为创建"""
        entry = self._read_contract()["files"].get(file_path)
        return bool(entry and entry.get("created"))

    def mark_created(self, file_path: str) -> bool:
        """标记文件为已创建

        Returns:
            True 如果文件在 contract 中且状态已更新，False 如果不在 contract 中
        """
        data = self._read_contract()
        files = data.get("files", {})
        entry = files.get(file_path)
        if entry is None:
            return False  # 不在 contract 中，非目标文件

        if entry.get("created"):
            return True  # 已经是 created，幂等

        entry["created"] = True
        data["files"] = files
        self._write_contract(data)
        logger.info(f"[CompletionContract] {file_path} → created=true")
        return True

    def add_file(self, file_path: str, created: bool = False, validated: bool = False) -> bool:
        """动态添加文件到 contract（用于 write_file 创建了 plan 之外的文件时）"""
        data = self._read_contract()
        files = data.setdefault("files", {})
        if file_path in files:
            return False
        files[file_path] = {"created": created, "validated": validated}
        data["version"] = CONTRACT_VERSION
        self._write_contract(data)
        logger.info(f"[CompletionContract] add_file: {file_path} created={created}")
        return True

    def mark_validated(self, file_path: str) -> bool:
        """标记文件为已验证（Evaluator 确认通过后调用）"""
        data = self._read_contract()
        entry = data.get("files", {}).get(file_path)
        if entry is None:
            return False
        entry["validated"] = True
        self._write_contract(data)
        return True

    def all_completed(self) -> bool:
        """检查所有文件是否都已创建"""
        files = self._read_contract().get("files", {})
        if not files:
            return True  # 没有 contract = 无需检查
        return all(info.get("created", False) for info in files.values())

    def pending_files(self) -> List[str]:
        """返回尚未创建的文件列表"""
        files = self._read_contract().get("files", {})
        return sorted(f for f, info in files.items() if not info.get("created", False))

    def total_files(self) -> int:
        """返回 contract 中的文件总数"""
        return len(self._read_contract().get("files", {}))

    def completed_count(self) -> int:
        """返回已创建的文件数"""
        files = self._read_contract().get("files", {})
        return sum(1 for info in files.values() if info.get("created", False))

    # ---- AC 级状态（v2 新增） ----

    def get_acceptance_criteria(self) -> List[dict]:
        """返回契约中的 AC 清单"""
        return self._read_contract().get("acceptance_criteria", [])

    def pending_acs(self) -> List[dict]:
        """返回尚无交互元素证据的 AC 列表"""
        return [ac for ac in self.get_acceptance_criteria() if not ac.get("evidence_found")]

    def mark_ac_checked(self, ac_id: str) -> bool:
        """标记某条 AC 已有满足证据（verify_node Playwright 通过后调用）"""
        data = self._read_contract()
        acs = data.get("acceptance_criteria", [])
        changed = False
        for ac in acs:
            if ac.get("id") == ac_id and not ac.get("evidence_found"):
                ac["evidence_found"] = True
                changed = True
        if changed:
            self._write_contract(data)
        return changed

    def ac_progress(self) -> dict:
        """返回 AC 进度摘要"""
        acs = self.get_acceptance_criteria()
        checked = sum(1 for ac in acs if ac.get("evidence_found"))
        return {
            "total": len(acs),
            "checked": checked,
            "pending": [ac["id"] for ac in acs if not ac.get("evidence_found")],
        }

    # ---- 通用 ----

    def get_progress(self) -> dict:
        """返回进度摘要"""
        ac_progress = self.ac_progress()
        return {
            "total": self.total_files(),
            "completed": self.completed_count(),
            "pending": self.pending_files(),
            "all_done": self.all_completed(),
            "acs_total": ac_progress["total"],
            "acs_checked": ac_progress["checked"],
            "acs_pending": ac_progress["pending"],
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
            self._workspace.delete(self._contract_path)
            logger.info("[CompletionContract] contract 已清除")
        except Exception as e:
            logger.warning(f"[CompletionContract] 清除 contract 失败: {e}")


# ==================== AC → DOM 静态预检（零 LLM） ====================

# 从 AC 文本中提取用于 DOM 印证的关键词（中文优先，过滤虚词）
_AC_STOPWORDS = {
    "用户", "可以", "能够", "页面", "显示", "出现", "点击", "后", "时",
    "并且", "然后", "应该", "需要", "支持", "进行", "使用", "看到",
}


def _extract_ac_keywords(text: str) -> list[str]:
    tokens = re.findall(r'[A-Za-z_][\w-]{2,}|[\u4e00-\u9fff]{2,6}', text or "")
    keywords = []
    for tok in tokens:
        low = tok.lower()
        if low in _AC_STOPWORDS or len(tok) < 2:
            continue
        keywords.append(low)
    return keywords[:8]


def find_ac_evidence(ac: dict, index_html: str) -> bool:
    """静态判断 index.html 中是否存在与该 AC 印证的交互元素线索。

    启发式匹配三路：
    1. AC 关键词出现在元素 id/class/name/aria-label/button 文本中
    2. how_to_verify 提到的控件类型（输入框/按钮/列表…）存在对应标签
    3. canvas 类 AC：页面存在 <canvas>（游戏类最低证据）

    这不是完整 DOM 验证——真正的验证由 verify_node 的 Playwright 承担；
    此处只在完成声明前拦截「连元素都没写」的明显缺陷。
    """
    if not index_html:
        return False
    haystack_parts = re.findall(
        r'id=["\']([^"\']+)["\']|class=["\']([^"\']+)["\']'
        r'|name=["\']([^"\']+)["\']|aria-label=["\']([^"\']+)["\']'
        r'|<button[^>]*>([^<]{1,60})<',
        index_html, re.IGNORECASE,
    )
    haystack = " ".join(
        part.lower()
        for groups in haystack_parts
        for part in groups if part
    )

    keywords = _extract_ac_keywords(f"{ac.get('label', '')} {ac.get('how_to_verify', '')}")
    if keywords and any(kw in haystack for kw in keywords):
        return True

    verify_text = (ac.get("how_to_verify") or "").lower()
    widget_map = [
        (("输入框", "input"), "<input"),
        (("按钮", "button"), "<button"),
        (("列表", "list"), ("<ul", "<ol", "list")),
        (("表格", "table"), "<table"),
        (("表单", "form"), "<form"),
        (("弹窗", "对话框", "modal"), ("modal", "dialog")),
        (("canvas", "画布", "画面"), "<canvas"),
    ]
    for triggers, needle in widget_map:
        if any(t in verify_text for t in triggers) and any(n in index_html.lower() for n in (needle if isinstance(needle, tuple) else (needle,))):
            return True
    return False
