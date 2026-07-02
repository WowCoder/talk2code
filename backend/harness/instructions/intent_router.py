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

logger = get_logger(__name__)


class IntentType(Enum):
    QUICK = "quick"
    SEARCH = "search"
    TASK = "task"
    AMBIGUOUS = "ambiguous"


@dataclass
class IntentResult:
    """意图分类结果"""
    intent: IntentType
    confidence: float = 0.8  # 置信度 0-1
    quick_answer: str = ""   # QUICK/SEARCH 的预生成答案（可选）


# ==================== 分类 Prompt ====================

INTENT_CLASSIFY_SYSTEM = """你是一个意图分类器。分析用户输入，只返回一个词：QUICK / SEARCH / TASK / AMBIGUOUS

## 分类标准

**QUICK** — 可以直接用文字回答的问题：
- 常识问答、概念解释（"什么是 CSS Grid""React 和 Vue 的区别"）
- 逻辑推理、数学计算
- 问候聊天（"你好""谢谢"）
- 简短代码片段示例（"写一个冒泡排序""Array.map 怎么用"）
- 对已有代码的提问（"这段代码为什么报错""这个函数做什么"）
- 纯咨询/知识类问题

**SEARCH** — 需要最新/实时信息：
- 最新版本特性（"React 19 有哪些新特性"）
- 时事新闻、天气
- 需要联网查询的问题

**TASK** — 需要创建完整应用或执行代码修改：
- 创建完整页面/应用（"做一个待办清单""帮我写一个个人主页"）
- 修改代码指令（"把背景色改成蓝色""给按钮加个点击事件"）
- 多步骤开发任务

**AMBIGUOUS** — 需求不清晰，无法确定具体产出：
- 过于模糊（"帮我优化一下""做个好东西"）
- 缺少关键信息（"做个网站" — 什么类型的网站？）
- 范围过大，无法判断用户真正想要什么

## 重要规则
- 如果用户描述了具体功能（有动词+功能点），即使简短也是 TASK，不是 AMBIGUOUS
  - "做个计算器" → TASK（功能明确）
  - "做个工具" → AMBIGUOUS（不明确什么工具）
- 如果用户在问"怎么做""是什么意思"，是 QUICK，不是 TASK
- 默认倾向：不确定时返回 TASK（宁可多生成，不要漏掉开发需求）

只返回一个词：QUICK / SEARCH / TASK / AMBIGUOUS"""

INTENT_CLASSIFY_CHAT_SYSTEM = """你是一个意图分类器，用于代码修改对话场景。用户已经有了一个应用，正在对它进行修改或提问。

分析用户输入，只返回一个词：QUICK / TASK / AMBIGUOUS

## 分类标准

**QUICK** — 对现有代码的提问，不需要修改代码：
- "这个按钮为什么是蓝色的""XX 功能是怎么实现的"
- "当前有哪些文件""index.html 里有什么"
- "为什么页面刷新后数据丢失了"

**TASK** — 需要修改代码的指令：
- "把背景色改成红色""给按钮加个 loading 状态"
- "添加一个删除功能""优化一下移动端适配"
- "修复 XX 的 bug"

**AMBIGUOUS** — 修改意图不明确：
- "帮我优化一下"（优化什么？性能？UI？代码结构？）
- "改好看点"（什么样的好看？）
- "加点功能"（什么功能？）

## 重要规则
- 包含具体动作（改/加/删/修/优化）+ 具体目标 → TASK
- 只是问为什么/是什么/怎么实现的 → QUICK
- 不确定时返回 TASK

只返回一个词：QUICK / TASK / AMBIGUOUS"""


# ==================== Quick 回答 Prompt ====================

QUICK_ANSWER_SYSTEM = """你是一个友好的 AI 编程助手。用户向你提问，请直接、准确地回答。

## 回答原则
- 直接回答问题，不要绕弯子
- 如果涉及代码，给出简洁的示例
- 如果用户只是打招呼，友好回应
- 回答保持简洁，不要展开不相关的内容
- 如果不知道答案，诚实说明，不要编造"""

QUICK_ANSWER_CHAT_SYSTEM = """你是一个友好的 AI 编程助手。用户正在开发一个前端应用，对现有代码有疑问。

## 回答原则
- 根据上下文（文件列表、代码内容）回答用户的问题
- 如果问题涉及具体代码，引用文件名和行数
- 给出实用、可操作的建议
- 保持简洁"""


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

            # 解析分类结果
            for intent_type in IntentType:
                if intent_type.value.upper() in raw:
                    logger.info(f"[IntentRouter] 分类结果: {intent_type.value} (raw={raw})")
                    return IntentResult(intent=intent_type, confidence=0.9)

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
