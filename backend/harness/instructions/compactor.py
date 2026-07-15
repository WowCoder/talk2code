# -*- coding: utf-8 -*-
"""
ContextCompactor —— P0-P3 分层上下文压缩

使用分层策略管理 LLM 上下文窗口，防止长对话导致上下文溢出。
支持 preserve 标记保护关键消息不被压缩。
"""

from harness.observability.logger import get_logger

logger = get_logger(__name__)


class ContextCompactor:
    """
    上下文压缩器

    Token 预算模型:
    P0 (永远保留): System Prompt + Skill 指令 + Craft 规则 + preserve=True 消息
    P1 (压缩保留): 技术决策、文件清单、数据模型
    P2 (滑动窗口): 最近 N 轮对话
    P3 (摘要替代): 旧对话 → LLM 生成摘要
    """

    COMPACTION_THRESHOLD = 0.85  # 上下文占用 > 85% 预算时触发压缩
    MAX_CONTEXT_MESSAGES = 30    # P2: 滑动窗口最大消息数

    def __init__(self, budget: int = 56000):
        self.budget = budget  # 总 token 预算

    def maybe_compact(self, messages: list) -> list:
        """
        检查是否需要压缩，需要则分层压缩。

        优先使用滑动窗口截断（P2），如果超出阈值则进一步压缩（P3）。
        标记 preserve=True 的消息不参与压缩。

        Args:
            messages: 消息列表 [{"role": "...", "content": "..."}]

        Returns:
            压缩后的消息列表
        """
        estimated = self._estimate_tokens(messages)

        if estimated < self.budget * self.COMPACTION_THRESHOLD:
            return messages

        # 分离保留消息（preserve=True）
        preserved = [m for m in messages if m.get("preserve") is True]
        compressible = [m for m in messages if m.get("preserve") is not True]

        preserved_tokens = self._estimate_tokens(preserved)
        compressible_budget = self.budget - preserved_tokens

        # 保留消息超额时记录 WARNING，但保留所有 preserve 消息
        if preserved_tokens > self.budget:
            logger.warning(
                f"[ContextCompactor] 保留消息 token 数 ({preserved_tokens}) "
                f"超过总预算 ({self.budget})，保留全部 preserve 消息，"
                f"非保留消息将被丢弃"
            )

        logger.info(
            f"[ContextCompactor] 触发压缩: estimated={estimated}, "
            f"budget={self.budget}, preserved={preserved_tokens}, "
            f"compressible_budget={compressible_budget}"
        )

        # P2 层：滑动窗口截断（仅压缩可压缩消息，保留 system 角色）
        compacted = self._compact_old_dialogues(
            compressible, max_budget=compressible_budget
        )

        # 合并保留消息和压缩后的消息
        result = preserved + compacted

        # 再次估算，如果还超则 P3 层压缩
        estimated2 = self._estimate_tokens(result)
        if estimated2 > self.budget * self.COMPACTION_THRESHOLD:
            logger.info(f"[ContextCompactor] P2 压缩后仍超限 ({estimated2})，进入 P3 层")
            result = preserved + self._compact_with_summary(compacted)

        return result

    def _compact_old_dialogues(self, messages: list, max_budget: int = None) -> list:
        """P2 层压缩：保留 system + 最近 N 条消息"""
        kept = []
        for m in messages:
            if m.get("role") == "system":
                kept.append(m)

        # 保留最近 N 条非 system 消息
        non_system = [m for m in messages if m.get("role") != "system"]
        kept.extend(non_system[-self.MAX_CONTEXT_MESSAGES:])

        return kept

    def _compact_with_summary(self, messages: list) -> list:
        """P3 层压缩：将截断的消息替换为摘要"""
        # 找到 system 消息和非 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.MAX_CONTEXT_MESSAGES:
            return messages

        # 保留最近的消息，对旧消息生成摘要
        kept = non_system[-self.MAX_CONTEXT_MESSAGES:]
        old = non_system[:-self.MAX_CONTEXT_MESSAGES]

        summary = self._generate_summary_stub(old)

        result = list(system_msgs)
        if summary:
            result.append({"role": "user", "content": summary})
        result.extend(kept)

        return result

    def _generate_summary_stub(self, old_messages: list) -> str:
        """生成本地摘要（不依赖 LLM，保证不增加调用成本）"""
        if not old_messages:
            return ""

        # 提取关键信息：文件操作、错误修复、设计决策
        key_items = []
        for m in old_messages:
            content = str(m.get("content", ""))
            role = m.get("role", "")

            # 提取工具调用信息
            if role == "tool_call":
                tool_name = m.get("name", "")
                if tool_name in ("write_file", "edit_file"):
                    args = m.get("arguments", {}) or {}
                    fname = args.get("filename", "")
                    if fname:
                        key_items.append(f"创建/修改文件: {fname}")
                elif tool_name in ("validate_html", "lint_css", "lint_js"):
                    args = m.get("arguments", {}) or {}
                    fname = args.get("filename", "")
                    if fname:
                        key_items.append(f"验证文件: {fname}")

            # 提取错误/修复信息
            if "修复" in content or "错误" in content or "修复" in content.lower():
                key_items.append(f"修复/错误: {content[:100]}")

        if not key_items:
            # 提取消息的关键句子
            for m in old_messages[-5:]:
                content = str(m.get("content", ""))[:200]
                if len(content) > 50:
                    key_items.append(content[:150])

        if not key_items:
            return ""

        summary_parts = ["[历史摘要] 之前的操作包括:"]
        for item in key_items[-10:]:  # 最多 10 条
            summary_parts.append(f"- {item}")

        return "\n".join(summary_parts)

    def _estimate_tokens(self, messages: list) -> int:
        """粗略估算 token 数"""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3  # 粗略：中文约 1.5 char/token，英文约 4 char/token
        return total

    def summarize_old_messages(self, old_messages: list, client=None) -> str:
        """
        将旧消息概括为摘要。

        如果提供了 LLM client，使用 LLM 生成高质量摘要；
        否则使用本地启发式方法。
        """
        if not old_messages:
            return ""

        content = "\n".join(
            f"[{m.get('name', m.get('role', ''))}]: {m.get('content', '')[:300]}"
            for m in old_messages[-30:]
        )

        if client:
            try:
                response = client.chat(
                    prompt=(
                        f"将以下对话历史概括为简洁摘要（保留关键技术决策、"
                        f"文件操作和错误修复信息）：\n\n{content}"
                    ),
                    max_tokens=500,
                    timeout=15,
                )
                if response and response.content:
                    return f"[历史摘要] {response.content[:500]}"
            except Exception as e:
                logger.debug(f"[ContextCompactor] LLM 摘要生成失败，使用本地摘要: {e}")

        # Fallback: 本地启发式摘要
        return self._generate_summary_stub(old_messages)
