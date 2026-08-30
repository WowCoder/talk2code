# -*- coding: utf-8 -*-
"""
IntentRouter —— 前置意图分类器

在 TeamLeader 之前对用户输入做轻量分类，将请求分流到四条路径：
- QUICK:    常识问答/代码解释/问候 → LLM 直接回答
- SEARCH:   需要实时信息 → Web 搜索后回答
- TASK:     软件开发任务 → 进入 TeamLeader → FrontendEngineer 流程
- AMBIGUOUS: 需求模糊 → 生成澄清问题

设计原则：
1. 分类调用极轻量（max_tokens=20, timeout=10s），失败时默认走 TASK
2. 分类规则写在 system prompt 中，无需硬编码关键词匹配
3. Chat 模式下的分类逻辑略有不同（区分"提问"和"修改指令"）
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

from llm.client import get_client
from harness.observability.logger import get_logger
from harness.instructions.prompts import load_prompt

logger = get_logger(__name__)


class IntentType(Enum):
    QUICK = "quick"
    SEARCH = "search"
    TASK = "task"
    AMBIGUOUS = "ambiguous"
    SKILL = "skill"


@dataclass
class IntentResult:
    """意图分类结果"""
    intent: IntentType
    confidence: float = 0.8  # 置信度 0-1
    quick_answer: str = ""   # QUICK/SEARCH 的预生成答案（可选）
    skill_name: str = ""     # SKILL 意图命中时，匹配到的工作流技能名


# ==================== 分类 Prompt（从 .md 文件加载）====================

INTENT_CLASSIFY_SYSTEM = load_prompt("intent/classify.md")
INTENT_CLASSIFY_CHAT_SYSTEM = load_prompt("intent/classify_chat.md")
QUICK_ANSWER_SYSTEM = load_prompt("intent/quick_answer.md")
QUICK_ANSWER_CHAT_SYSTEM = load_prompt("intent/quick_answer_chat.md")


class IntentRouter:
    """前置意图分类器 —— 轻量 LLM 调用做路由决策"""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_client()
        return self._client

    def classify(self, requirement: str, is_chat: bool = False,
                 history: list = None) -> IntentResult:
        """
        分类用户意图。

        Args:
            requirement: 用户输入文本
            is_chat: 是否为 Chat 模式（对已有代码的操作）
            history: 对话历史（可选，chat 模式提供更多上下文）

        Returns:
            IntentResult，失败时默认返回 TASK
        """
        # 截断过长输入，分类不需要完整文本
        short_text = requirement[:500] if len(requirement) > 500 else requirement

        # 确定性 SKILL 匹配：若需求命中某个工作流技能的 trigger，直接路由到 SKILL，
        # 不依赖 LLM，避免误判；返回最高优先级的命中技能名。
        try:
            from harness.instructions.skill_loader import get_skill_loader
            wf = get_skill_loader().match_workflow_skills(short_text)
            if wf:
                logger.info(
                    f"[IntentRouter] 命中工作流技能: {wf[0].name}，路由 SKILL"
                )
                return IntentResult(
                    intent=IntentType.SKILL,
                    confidence=0.95,
                    skill_name=wf[0].name,
                )
        except Exception as e:
            logger.warning(f"[IntentRouter] SKILL 预匹配异常（忽略）: {e}")

        system_prompt = INTENT_CLASSIFY_CHAT_SYSTEM if is_chat else INTENT_CLASSIFY_SYSTEM

        # Chat 模式下，如果有代码上下文，拼接到输入后面帮助分类
        prompt = short_text
        if is_chat and history:
            # 提取最近的几条消息作为上下文
            recent = [m for m in history[-4:] if m.get('role') in ('user', 'agent', 'assistant')]
            if recent:
                context_hint = "\n\n## 对话上下文（辅助判断）\n"
                for m in recent:
                    context_hint += f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}\n"
                prompt = short_text + context_hint

        try:
            response = self.client.chat(
                prompt=prompt,
                system_prompt=system_prompt,
                use_memory=False,
                max_tokens=100,
                timeout=15,
            )

            if response.is_error or not response.content:
                logger.warning(f"[IntentRouter] 分类失败，默认走 TASK: {response.error}")
                return IntentResult(intent=IntentType.TASK, confidence=0.5)

            raw = response.content.strip().upper()

            # 解析分类结果：先精确单标签，再按词边界取最早出现的标签
            # （避免 "这是 TASK，不需要 search" 被先命中 SEARCH）
            for intent_type in IntentType:
                if raw == intent_type.value.upper():
                    logger.info(f"[IntentRouter] 分类结果: {intent_type.value} (raw={raw})")
                    return IntentResult(intent=intent_type, confidence=0.95)

            import re as _re
            matches = []
            for intent_type in IntentType:
                label = intent_type.value.upper()
                m = _re.search(rf"\b{label}\b", raw)
                if m:
                    matches.append((m.start(), intent_type))
            if matches:
                matches.sort(key=lambda x: x[0])
                winner = matches[0][1]
                logger.info(f"[IntentRouter] 分类结果: {winner.value} (raw={raw})")
                return IntentResult(intent=winner, confidence=0.7)

            # 无法解析，默认 TASK
            logger.warning(f"[IntentRouter] 无法解析分类结果: {raw}，默认 TASK")
            return IntentResult(intent=IntentType.TASK, confidence=0.5)

        except Exception as e:
            logger.warning(f"[IntentRouter] 分类异常: {e}，默认 TASK")
            return IntentResult(intent=IntentType.TASK, confidence=0.5)

    def handle_quick(self, requirement: str, history: list = None,
                     code_context: str = "", is_chat: bool = False) -> str:
        """
        处理 QUICK 意图：LLM 直接回答。

        Args:
            requirement: 用户问题
            history: 对话历史
            code_context: 代码上下文（文件列表 + 内容概要，chat 模式提供）
            is_chat: 是否为 Chat 模式

        Returns:
            LLM 回答文本
        """
        system_prompt = QUICK_ANSWER_CHAT_SYSTEM if is_chat else QUICK_ANSWER_SYSTEM

        # 组装 prompt
        parts = [requirement]

        if code_context:
            parts.append(f"\n\n## 当前项目代码上下文\n{code_context}")

        if history:
            recent = [m for m in history[-6:] if m.get('role') in ('user', 'agent', 'assistant')]
            if recent:
                parts.append("\n\n## 对话历史\n")
                for m in recent:
                    parts.append(f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:300]}")

        prompt = "\n".join(parts)

        try:
            response = self.client.chat(
                prompt=prompt,
                system_prompt=system_prompt,
                use_memory=False,
                max_tokens=2000,
                timeout=30,
            )

            if response.is_error:
                return f"抱歉，回答问题时出错了：{response.error}"

            return response.content or "抱歉，我暂时无法回答这个问题。"

        except Exception as e:
            logger.error(f"[IntentRouter] Quick 回答失败: {e}")
            return f"抱歉，处理你的问题时遇到了错误：{e}"
