# -*- coding: utf-8 -*-
"""
ContextCompactor —— P0-P3 分层上下文压缩
"""


class ContextCompactor:
    """
    上下文压缩器

    Token 预算模型:
    P0 (永远保留): System Prompt + Skill 指令 + Craft 规则
    P1 (压缩保留): 技术决策、文件清单、数据模型
    P2 (滑动窗口): 最近 N 轮对话
    P3 (摘要替代): 旧对话 → LLM 生成摘要
    """

    COMPACTION_THRESHOLD = 0.85  # 上下文占用 > 85% 预算时触发压缩

    def __init__(self, budget: int = 56000):
        self.budget = budget  # 总 token 预算

    def maybe_compact(self, messages: list) -> list:
        """
        检查是否需要压缩，需要则分层压缩

        Args:
            messages: 消息列表 [{"role": "...", "content": "..."}]

        Returns:
            压缩后的消息列表
        """
        estimated = self._estimate_tokens(messages)
        if estimated < self.budget * self.COMPACTION_THRESHOLD:
            return messages

        # P3 层：压缩旧对话
        return self._compact_old_dialogues(messages)

    def _compact_old_dialogues(self, messages: list) -> list:
        """压缩旧对话：保留 system + 最近 20 条消息"""
        kept = []
        for m in messages:
            if m.get("role") == "system":
                kept.append(m)

        # 保留最近 20 条非 system 消息
        non_system = [m for m in messages if m.get("role") != "system"]
        kept.extend(non_system[-20:])

        return kept

    def _estimate_tokens(self, messages: list) -> int:
        """粗略估算 token 数"""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3  # 粗略：中文约 1.5 char/token，英文约 4 char/token
        return total

    def summarize_old_messages(self, old_messages: list) -> str:
        """将旧消息概括为摘要（调用 LLM）"""
        if not old_messages:
            return ""
        content = " ".join(
            m.get("content", "")[:200]
            for m in old_messages
            if isinstance(m.get("content"), str)
        )
        return f"[历史摘要] {content[:500]}..."
