# -*- coding: utf-8 -*-
"""
ContextAssembler —— 上下文组装（委托给 skills.SkillLoader 做渐进式披露）
"""

from dataclasses import dataclass, field


@dataclass
class AssembledContext:
    """组装后的上下文"""
    system_prompt: str
    skill_instructions: str = ""
    long_term_memories: str = ""
    craft_rules: str = ""
    user_prompt: str = ""
    metadata: dict = field(default_factory=dict)


class ContextAssembler:
    """上下文组装器 —— 委托 skills.SkillLoader 按需注入规则"""

    def __init__(self, memory_manager=None):
        self.memory_manager = memory_manager

    def assemble(self, requirement: str, user_id: int, metadata: dict = None) -> AssembledContext:
        """组装完整的 LLM 上下文"""
        if metadata is None:
            metadata = {}

        # 渐进式加载设计规则 + 领域技能
        try:
            from harness.instructions.prompts.skills import load_for_task
            rules_text = load_for_task(requirement)
        except Exception:
            rules_text = ""

        # 检索长期记忆
        memories = self._load_memories(requirement, user_id)

        return AssembledContext(
            system_prompt="你是一位资深前端工程师，专注于生成高质量、可直接运行的 HTML/CSS/JS 代码。",
            skill_instructions="",
            long_term_memories=memories,
            craft_rules=rules_text,
            user_prompt=requirement,
        )

    def _load_memories(self, requirement: str, user_id: int) -> str:
        """从 MemoryManager 检索相关记忆"""
        if not self.memory_manager:
            return ""
        try:
            recalled = self.memory_manager._get_active_memories()
            if not recalled:
                return ""
            self.memory_manager._retriever.index([m.to_text() for m in recalled])
            results = self.memory_manager._retriever.search(requirement, top_k=5)
            if not results:
                return ""
            selected = [recalled[i] for i, _ in results]
            lines = [
                f"- [{getattr(m, 'memory_type', '')}] {getattr(m, 'fact', getattr(m, 'requirement', ''))}"
                for m in selected
            ]
            return "\n\n## 用户偏好与项目背景\n" + "\n".join(lines) if lines else ""
        except Exception:
            return ""
