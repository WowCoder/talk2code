# -*- coding: utf-8 -*-
"""
ContextAssembler —— 根据需求类型动态组装 LLM 上下文
"""

from dataclasses import dataclass, field
from typing import Optional


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
    """根据需求类型动态组装 LLM 上下文"""

    def __init__(self, memory_store=None, memory_manager=None):
        self.memory_store = memory_store       # deprecated, 保留兼容
        self.memory_manager = memory_manager   # 新的 MemoryManager

    def assemble(self, requirement: str, user_id: int, metadata: dict = None) -> AssembledContext:
        if metadata is None:
            metadata = {}

        # 1. 加载通用 Skill
        skill = self._load_generic_skill()

        # 2. 按需选择 Craft 规则
        required_crafts = self._select_crafts(requirement)

        # 3. 检索长期记忆（优先用 MemoryManager，降级到 MemoryStore）
        memories = ""
        if self.memory_manager:
            # MemoryManager 的 before_task 直接注入 few-shot 格式
            # 这里只需要检索原始记忆列表用于组装
            recalled = self._recall_from_manager(requirement, user_id)
            if recalled:
                memories = "\n".join(
                    f"- [domain_knowledge] {m.get('requirement', m.get('fact', ''))}"
                    for m in recalled
                )
                if memories:
                    memories = f"\n\n## 用户偏好与项目背景\n{memories}"
        elif self.memory_store:
            recalled = self.memory_store.recall(requirement, user_id, top_k=5)
            if recalled:
                memories = "\n".join(
                    f"- [{m.get('memory_type', '')}] {m.get('fact', '')}"
                    for m in recalled
                )
                if memories:
                    memories = f"\n\n## 用户偏好与项目背景\n{memories}"

        # 4. 加载 Craft 规则
        craft_rules_text = ""
        if required_crafts:
            craft_rules_text = self._load_craft_rules_text(required_crafts)

        return AssembledContext(
            system_prompt=self._build_system_prompt(metadata.get("visual_style", "")),
            skill_instructions=skill.get("body", "") if skill else "",
            long_term_memories=memories,
            craft_rules=craft_rules_text,
            user_prompt=requirement,
            metadata={
                "crafts": required_crafts,
                "memories_count": len(recalled) if memories else 0,
            }
        )

    def _load_generic_skill(self) -> Optional[dict]:
        """加载通用 Skill"""
        try:
            from harness.instructions.craft_loader import load_skill
            return load_skill("generic")
        except Exception:
            return None

    def _select_crafts(self, requirement: str) -> list:
        """按需选择 Craft 规则"""
        selected = []
        features = self._analyze_features(requirement)
        if features.get('has_ui'):
            selected.extend(['typography', 'color'])
        if features.get('has_form'):
            selected.append('accessibility-baseline')
        if features.get('has_content'):
            selected.append('anti-ai-slop')
        return selected or ['typography', 'color', 'accessibility-baseline', 'anti-ai-slop']

    def _analyze_features(self, requirement: str) -> dict:
        """简单特征检测"""
        ui_keywords = ['页面', '界面', '显示', '按钮', '列表', '表单', '样式', '颜色', '布局', 'UI']
        form_keywords = ['输入', '表单', '提交', '登录', '注册', '搜索']
        content_keywords = ['内容', '文章', '笔记', '博客', '文本', '写作']
        return {
            'has_ui': any(k in requirement for k in ui_keywords),
            'has_form': any(k in requirement for k in form_keywords),
            'has_content': any(k in requirement for k in content_keywords),
        }

    def _build_system_prompt(self, visual_style: str = "") -> str:
        """构建系统提示词"""
        prompt = "你是一位资深前端工程师，专注于生成高质量、可直接运行的 HTML/CSS/JS 代码。"
        if visual_style:
            prompt += f"\n\n视觉风格偏好: {visual_style}"
        return prompt

    def _load_craft_rules_text(self, craft_names: list) -> str:
        """加载 Craft 规则文本"""
        try:
            from harness.instructions.craft_loader import load_craft_rules
            return load_craft_rules(craft_names) or ""
        except Exception:
            return ""

    def _recall_from_manager(self, requirement: str, user_id: int) -> list:
        """从 MemoryManager 检索记忆（返回兼容 MemoryStore 格式的 dict 列表）"""
        try:
            memories = self.memory_manager._get_active_memories()
            if not memories:
                return []

            self.memory_manager._retriever.index([m.to_text() for m in memories])
            results = self.memory_manager._retriever.search(requirement, top_k=5)
            if not results:
                return []

            selected = [memories[i] for i, _ in results]
            return [m.to_dict() if hasattr(m, 'to_dict') else {
                "memory_type": "domain_knowledge",
                "fact": m.requirement,
                "importance": m.importance,
            } for m in selected]
        except Exception:
            return []
