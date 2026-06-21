# -*- coding: utf-8 -*-
from __future__ import annotations
"""
MemoryStore —— LLM 驱动的长期记忆提取和两阶段检索
"""

import time
from typing import Optional


class MemoryStore:
    """
    长期记忆管理

    提取：LLM 扫描对话记录，提取值得长期记忆的事实
    检索：≤10 条纯 LLM 判读，>10 条 embedding 初筛 + LLM 精排
    衰减：时间衰减 + 30 天未访问自动清理
    """

    def __init__(self, db_session=None, llm_client=None):
        self._db = db_session
        self._llm = llm_client
        self._cache: dict[int, list[dict]] = {}  # user_id → [memories]

    def extract_memories(self, dialogue_context: list, requirement_id: int, user_id: int) -> list:
        """用 LLM 扫描对话，提取值得长期记忆的事实"""
        if not self._llm or not dialogue_context:
            return []

        formatted = self._format_dialogues(dialogue_context[-10:])
        prompt = f"""以下是用户与 AI 编程助手的对话记录。请判断哪些信息值得作为"长期记忆"保存。

值得记住的信息（重要性 0.7-1.0）：
1. 用户偏好: 明确表达的技术/设计偏好
2. 项目背景: 项目目标、领域、约束
3. 重要决策: 用户确认的设计/技术决策
4. 错误经验: 用户纠正过的错误方案

不应记住的信息：
- 单次任务的执行细节
- 已在代码文件中体现的信息
- LLM 能从对话自行推理出的信息

对话记录：
{formatted}

返回 JSON 数组，每条: {{"fact": "一句话描述", "type": "user_preference|domain_knowledge|agent_lesson|user_feedback", "importance": 0.0-1.0, "reason": "为什么值得记住"}}
如果没有值得长期记忆的内容，返回空数组 []。"""

        try:
            response = self._llm.chat(prompt, use_memory=False, max_tokens=500, timeout=20)
            import json
            items = json.loads(response.content) if response.content else []
            for item in items:
                self.remember(user_id, item.get("fact", ""), item.get("type", "domain_knowledge"),
                              item.get("importance", 0.5), requirement_id)
            return items
        except Exception:
            return []

    def recall(self, query: str, user_id: int, top_k: int = 5) -> list[dict]:
        """
        两阶段检索：
        - ≤10 条: LLM 直接判读筛选
        - >10 条: 简单关键词初筛 + LLM 精排
        """
        memories = self._get_user_memories(user_id)
        if not memories:
            return []

        if len(memories) <= 10:
            return self._llm_filter(memories, query, top_k)
        else:
            candidates = self._keyword_filter(memories, query, limit=10)
            return self._llm_filter(candidates, query, top_k)

    def remember(self, user_id: int, fact: str, memory_type: str,
                 importance: float = 0.5, requirement_id: int = None):
        """写入新记忆，处理冲突"""
        existing = self._find_similar(user_id, fact)
        if existing:
            existing["importance"] = min(1.0, existing.get("importance", 0.5) + 0.05)
            existing["last_accessed_at"] = time.time()
        else:
            if user_id not in self._cache:
                self._cache[user_id] = []
            self._cache[user_id].append({
                "fact": fact,
                "memory_type": memory_type,
                "importance": importance,
                "requirement_id": requirement_id,
                "created_at": time.time(),
                "last_accessed_at": time.time(),
                "access_count": 0,
            })

    def decay(self):
        """时间衰减 + 清理"""
        now = time.time()
        for user_id in list(self._cache.keys()):
            kept = []
            for m in self._cache[user_id]:
                days = max((now - m.get("last_accessed_at", now)) / 86400, 1)
                m["importance"] *= 0.95 ** max(days / 7, 1)
                if m["importance"] >= 0.1:
                    kept.append(m)
            self._cache[user_id] = kept

    def _get_user_memories(self, user_id: int) -> list:
        return self._cache.get(user_id, [])

    def _find_similar(self, user_id: int, fact: str) -> Optional[dict]:
        """简单相似匹配（基于关键词重叠）"""
        memories = self._get_user_memories(user_id)
        fact_words = set(fact.lower().split())
        for m in memories:
            m_words = set(m["fact"].lower().split())
            if len(fact_words & m_words) / max(len(fact_words | m_words), 1) > 0.5:
                return m
        return None

    def _llm_filter(self, memories: list, query: str, top_k: int) -> list:
        """用 LLM 从候选记忆中筛选最相关的"""
        if not self._llm or len(memories) <= top_k:
            return memories[:top_k]

        memory_list = "\n".join(
            f"{i}: [{m['memory_type']}] {m['fact']} (重要性: {m.get('importance', 0.5):.2f})"
            for i, m in enumerate(memories)
        )
        prompt = f"""以下是用户的历史偏好信息。请判断哪些与当前需求相关，返回相关记忆的序号列表。

当前需求: {query}

历史记忆:
{memory_list}

只返回相关的记忆序号 JSON 数组，如 [0, 3, 5]。不相关的不返回。最多返回 {top_k} 条。"""

        try:
            response = self._llm.chat(prompt, use_memory=False, max_tokens=200, timeout=15)
            import json
            indices = json.loads(response.content) if response.content else []
            return [memories[i] for i in indices if 0 <= i < len(memories)][:top_k]
        except Exception:
            return memories[:top_k]

    def _keyword_filter(self, memories: list, query: str, limit: int) -> list:
        """简单关键词初筛"""
        query_words = set(query.lower().split())
        scored = []
        for m in memories:
            m_words = set(m["fact"].lower().split())
            score = len(query_words & m_words)
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]] or memories[:limit]

    def _format_dialogues(self, dialogues: list) -> str:
        lines = []
        for d in dialogues:
            role = d.get("role", "unknown")
            content = str(d.get("content", ""))[:300]
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)
