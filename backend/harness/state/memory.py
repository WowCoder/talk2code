# -*- coding: utf-8 -*-
"""
MemoryManager —— 统一的记忆管理核心

整合了原来的 ExperiencePool + FeedbackLoop + MemoryStore 的功能：
- 任务前: BGE-M3 检索 + LLM 校验 → 注入 few-shot
- 任务后: LLM 3 问反思 → 结构化存储
- 定期: 合并相似记忆、清理过时记忆

持久化到 agent_memories_v2 表，跨进程重启保留。
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from models import SessionLocal, AgentMemoryV2
from harness.state.memory_retriever import BGEM3Retriever
from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== Memory 数据类 ====================

@dataclass
class Memory:
    """一条结构化记忆 —— LLM 反思后的产物"""
    id: Optional[int] = None
    user_id: int = 0
    requirement: str = ""
    complexity: str = "S"
    code_summary: str = ""
    rating: float = 7.0

    reflection: str = ""
    lesson: str = ""
    reusable_pattern: str = ""

    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    access_count: int = 0
    created_at: float = 0.0

    merged_from: list[int] = field(default_factory=list)
    superseded: bool = False

    def to_text(self) -> str:
        """拼接为检索编码文本"""
        parts = [self.requirement]
        if self.lesson:
            parts.append(f"关键教训: {self.lesson}")
        if self.reusable_pattern:
            parts.append(f"可复用模式: {self.reusable_pattern}")
        if self.tags:
            parts.append(f"标签: {', '.join(self.tags)}")
        return " | ".join(parts)

    @classmethod
    def from_orm(cls, row: AgentMemoryV2) -> "Memory":
        ts = row.created_at.timestamp() if row.created_at else 0.0
        return cls(
            id=row.id, user_id=row.user_id,
            requirement=row.requirement, complexity=row.complexity,
            code_summary=row.code_summary or "", rating=row.rating or 7.0,
            reflection=row.reflection or "", lesson=row.lesson or "",
            reusable_pattern=row.reusable_pattern or "",
            tags=row.tags or [], importance=row.importance or 0.5,
            access_count=row.access_count or 0, created_at=ts,
            merged_from=row.merged_from or [], superseded=row.superseded or False,
        )


# ==================== 反思 Prompt ====================

REFLECTION_SYSTEM = """你是资深前端工程师，刚完成一个代码生成任务。请复盘并回答 3 个问题。

回答要简洁（每条 1-2 句），聚焦可复用经验。"""

REFLECTION_PROMPT = """## 任务回顾

用户需求: {requirement}

代码产出: {code_summary}

QA 评分: {rating}/10

## 请回答以下 3 个问题

1. **reflection**: 这次实现过程中有什么和预期不同的？为什么会出现这种情况？
2. **lesson**: 如果下次有人提出类似需求，你最重要的一个经验教训是什么？
3. **reusable_pattern**: 这次有没有产生可以复用的代码模式/组件？（没有就说"无"）

另外:
- tags: 为这段记忆打 2-4 个标签（如 localStorage, CRUD, 表单, 响应式）
- importance: 重要性 0.0-1.0（会反复遇到的模式给高分，一次性的给低分）

只返回 JSON，不要其他文字。格式:
{{"reflection": "...", "lesson": "...", "reusable_pattern": "...", "tags": [...], "importance": 0.0}}"""


# ==================== 校验 Prompt (L2) ====================

VERIFY_SYSTEM = """你是经验筛选助手。从候选记忆中选出对当前任务真正有用的。"""

VERIFY_PROMPT = """当前需求: {query}

候选经验:
{candidates}

请选出对当前任务真正有帮助的经验（最多 3 条），返回它们的序号列表。

选择标准:
- 技术栈相同或相似（如都用 localStorage）
- 功能模式可以复用（如 CRUD、表单验证模式）
- 包含需要避免的坑（如"localStorage 必须 JSON.stringify"）

不需要选的经验:
- 技术栈完全不同
- 仅因为关键词巧合匹配
- 已经是基本常识

只返回 JSON 数组: [0, 2]"""


# ==================== 合并 Prompt ====================

CONSOLIDATE_SYSTEM = """你是经验整理助手。将相似的经验合并，标记过时的建议。"""

CONSOLIDATE_PROMPT = """以下是最近积累的经验，请整理：

{memories}

请检查:
1. 有没有多条经验在说同一个事情？如果有，指出需要合并的组（最多合并为一条）
2. 有没有已经过时的建议？（如"用 X 代替 Y"，但 X 本身已不推荐）
3. 有没有互相矛盾的经验？

返回 JSON:
{{
  "merge_groups": [[0, 3], [1, 5]],
  "deprecate": [2],
  "summary": "一句话描述本次整理的变更"
}}

如果没有需要合并或淘汰的，返回空数组。只返回 JSON。"""


# ==================== MemoryManager ====================

class MemoryManager:
    """统一的记忆管理器

    用法:
        mgr = MemoryManager()
        # 任务前
        enhanced_prompt = mgr.before_task(requirement, system_prompt)
        # 任务后
        mgr.after_task(requirement, "S", code_files, qa_result, user_id=1)
        # 查看统计
        stats = mgr.stats()
    """

    CONSOLIDATE_INTERVAL = 20  # 每 20 条新记忆触发一次合并

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM 客户端（可选，用于反思/校验/合并）。
                        不提供时退化为纯检索模式（无 LLM 反思和校验）。
        """
        self._llm = llm_client
        self._retriever = BGEM3Retriever()
        self._lock = threading.Lock()
        self._new_since_consolidate = 0

        # 启动时从数据库加载所有记忆，建立索引
        self._rebuild_index()

    # ==================== 公有 API ====================

    def before_task(self, requirement: str, system_prompt: str) -> str:
        """
        任务前: 检索相关记忆，注入 System Prompt。

        两阶段检索:
        L1: BGE-M3 混合检索 → Top 5
        L2: LLM 校验排序 → 精选 2-3 条

        Args:
            requirement: 用户需求文本
            system_prompt: 原始系统提示词

        Returns:
            增强后的系统提示词（追加 few-shot 示例）
        """
        try:
            memories = self._get_active_memories()
            if not memories:
                return system_prompt

            # L1: BGE-M3 混合检索
            self._retriever.index([m.to_text() for m in memories])
            results = self._retriever.search(requirement, top_k=5)
            if not results:
                return system_prompt

            candidates = [memories[i] for i, _ in results]

            # L2: LLM 校验 + 排序
            selected = self._llm_verify(requirement, candidates)
            if not selected:
                # LLM 不可用或返回空 → 直接用 L1 的 top 2
                selected = candidates[:2]

            # 更新访问计数
            for m in selected:
                m.access_count += 1

            # 组装 few-shot 注入文本
            few_shot = self._format_few_shot(selected)
            if few_shot:
                logger.info(
                    f"[MemoryManager] 注入了 {len(selected)} 条相关记忆 "
                    f"(共 {len(memories)} 条) for: {requirement[:50]}..."
                )
                return system_prompt + few_shot

        except Exception as e:
            logger.warning(f"[MemoryManager] before_task 异常（降级跳过）: {e}")

        return system_prompt

    def after_task(self, requirement: str, complexity: str,
                   code_files: list, qa_result: dict = None,
                   user_id: int = 0):
        """
        任务后: LLM 反思 + 存储记忆。

        Args:
            requirement: 用户需求
            complexity: XS/S/M/L
            code_files: 生成的代码文件列表
            qa_result: QA/Summarize 审查结果（含 score, verdict, issues）
            user_id: 用户 ID
        """
        rating = self._extract_rating(qa_result)
        code_summary = self._build_code_summary(code_files)

        # LLM 反思
        reflection_data = {}
        if self._llm:
            try:
                reflection_data = self._reflect(requirement, code_summary, rating)
            except Exception as e:
                logger.warning(f"[MemoryManager] LLM 反思失败，使用默认值: {e}")

        memory = Memory(
            user_id=user_id,
            requirement=requirement,
            complexity=complexity,
            code_summary=code_summary,
            rating=rating,
            reflection=reflection_data.get("reflection", ""),
            lesson=reflection_data.get("lesson", ""),
            reusable_pattern=reflection_data.get("reusable_pattern", ""),
            tags=reflection_data.get("tags", []),
            importance=reflection_data.get("importance", rating / 10.0),
            created_at=time.time(),
        )

        # 去重 + 存储
        with self._lock:
            self._store(memory)
            self._new_since_consolidate += 1
            logger.info(
                f"[MemoryManager] 存储记忆: rating={rating}, "
                f"tags={memory.tags}, total_new_since_maintain={self._new_since_consolidate}"
            )

        # 定期维护
        if self._new_since_consolidate >= self.CONSOLIDATE_INTERVAL:
            self._maintain()

    def stats(self) -> dict:
        """统计信息"""
        memories = self._get_active_memories()
        if not memories:
            return {"total": 0, "avg_rating": 0, "by_complexity": {}, "new_since_consolidate": self._new_since_consolidate}

        ratings = [m.rating for m in memories if m.rating > 0]
        from collections import Counter
        complexities = Counter(m.complexity for m in memories)
        return {
            "total": len(memories),
            "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
            "by_complexity": dict(complexities),
            "new_since_consolidate": self._new_since_consolidate,
        }

    # ==================== 内部方法 ====================

    def _rebuild_index(self):
        """启动时从数据库加载所有活跃记忆，建立检索索引"""
        try:
            memories = self._get_active_memories()
            if memories:
                self._retriever.index([m.to_text() for m in memories])
                logger.info(f"[MemoryManager] 初始索引构建完成: {len(memories)} 条记忆")
            else:
                logger.info("[MemoryManager] 记忆库为空，等待首次任务完成")
        except Exception as e:
            logger.warning(f"[MemoryManager] 索引构建失败（降级为空库）: {e}")

    def _get_active_memories(self) -> list[Memory]:
        """从数据库加载所有活跃（未被淘汰）的记忆"""
        try:
            db = SessionLocal()
            rows = db.query(AgentMemoryV2).filter_by(superseded=False).all()
            db.close()
            return [Memory.from_orm(r) for r in rows]
        except Exception as e:
            logger.warning(f"[MemoryManager] 数据库加载失败: {e}")
            return []

    def _store(self, memory: Memory):
        """存储一条新记忆到数据库（含去重逻辑）"""
        try:
            # 去重: 如果已有高度相似的需求，标记旧记忆为 superseded
            memories = self._get_active_memories()
            for existing in memories:
                if self._jaccard_similarity(memory.requirement, existing.requirement) > 0.6:
                    self._mark_superseded(existing.id)
                    logger.debug(f"[MemoryManager] 去重: 标记记忆 {existing.id} 为 superseded")
                    break

            # 写入新记忆
            db = SessionLocal()
            row = AgentMemoryV2(
                user_id=memory.user_id,
                requirement=memory.requirement,
                complexity=memory.complexity,
                code_summary=memory.code_summary,
                rating=memory.rating,
                reflection=memory.reflection,
                lesson=memory.lesson,
                reusable_pattern=memory.reusable_pattern,
                tags=memory.tags,
                importance=memory.importance,
            )
            db.add(row)
            db.commit()
            memory.id = row.id
            db.close()

            # 更新检索索引
            active = self._get_active_memories()
            self._retriever.index([m.to_text() for m in active])

        except Exception as e:
            logger.warning(f"[MemoryManager] 存储记忆失败: {e}")
            try:
                db.rollback()
                db.close()
            except Exception:
                pass

    def _mark_superseded(self, mem_id: int):
        """标记一条记忆已被替代"""
        try:
            db = SessionLocal()
            row = db.query(AgentMemoryV2).filter_by(id=mem_id).first()
            if row:
                row.superseded = True
                db.commit()
            db.close()
        except Exception as e:
            logger.warning(f"[MemoryManager] 标记 superseded 失败: {e}")

    def _reflect(self, requirement: str, code_summary: str, rating: float) -> dict:
        """LLM 3 问自答"""
        if not self._llm:
            return {}

        prompt = REFLECTION_PROMPT.format(
            requirement=requirement[:300],
            code_summary=code_summary[:500],
            rating=rating,
        )

        try:
            response = self._llm.chat(
                prompt=prompt,
                system_prompt=REFLECTION_SYSTEM,
                use_memory=False,
                max_tokens=400,
                timeout=20,
            )
            if response.is_error or not response.content:
                return {}

            content = response.content.strip()
            # 提取 JSON
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                import re
                m = re.search(r'\{[\s\S]*\}', content)
                if m:
                    return json.loads(m.group())
        except Exception:
            pass

        return {}

    def _llm_verify(self, query: str, candidates: list[Memory]) -> list[Memory]:
        """LLM 校验并筛选候选记忆"""
        if not self._llm or len(candidates) <= 2:
            return candidates[:2]

        candidates_text = "\n".join(
            f"[{i}] 需求: {m.requirement[:100]} | 评分: {m.rating} | "
            f"教训: {m.lesson[:100]} | 模式: {m.reusable_pattern[:100]}"
            for i, m in enumerate(candidates)
        )

        prompt = VERIFY_PROMPT.format(query=query[:300], candidates=candidates_text)

        try:
            response = self._llm.chat(
                prompt=prompt,
                system_prompt=VERIFY_SYSTEM,
                use_memory=False,
                max_tokens=100,
                timeout=15,
            )
            if response.is_error or not response.content:
                return candidates[:2]

            import re
            content = response.content.strip()
            m = re.search(r'\[[\d,\s]*\]', content)
            if m:
                indices = json.loads(m.group())
                return [candidates[i] for i in indices if 0 <= i < len(candidates)][:3]

        except Exception:
            pass

        return candidates[:2]

    def _maintain(self):
        """定期维护: LLM 合并相似记忆 + 清理过时记忆"""
        memories = self._get_active_memories()
        if len(memories) < 10:
            self._new_since_consolidate = 0
            return

        logger.info(f"[MemoryManager] 触发维护: {len(memories)} 条活跃记忆")

        if self._llm:
            try:
                self._llm_consolidate(memories)
            except Exception as e:
                logger.warning(f"[MemoryManager] LLM 合并失败: {e}")

        # 时间衰减: 上次合并前存在的旧低分记忆
        self._decay(memories)

        self._new_since_consolidate = 0
        # 重建索引
        active = self._get_active_memories()
        self._retriever.index([m.to_text() for m in active])
        logger.info(f"[MemoryManager] 维护完成: {len(active)} 条活跃记忆")

    def _llm_consolidate(self, memories: list[Memory]):
        """LLM 驱动的记忆合并"""
        memory_text = "\n".join(
            f"[{i}] 需求: {m.requirement[:80]} | 评分: {m.rating} | "
            f"标签: {m.tags} | 教训: {m.lesson[:100]}"
            for i, m in enumerate(memories[-40:])  # 只看最近 40 条
        )

        prompt = CONSOLIDATE_PROMPT.format(memories=memory_text)

        response = self._llm.chat(
            prompt=prompt,
            system_prompt=CONSOLIDATE_SYSTEM,
            use_memory=False,
            max_tokens=500,
            timeout=30,
        )

        if response.is_error or not response.content:
            return

        try:
            content = response.content.strip()
            import re
            m = re.search(r'\{[\s\S]*\}', content)
            if not m:
                return
            plan = json.loads(m.group())

            # 执行合并
            merge_groups = plan.get("merge_groups", [])
            if merge_groups:
                logger.info(f"[MemoryManager] 合并 {len(merge_groups)} 组相似记忆")

            # 执行淘汰
            deprecate_ids = plan.get("deprecate", [])
            for idx in deprecate_ids:
                if 0 <= idx < len(memories):
                    self._mark_superseded(memories[idx].id)

        except Exception as e:
            logger.warning(f"[MemoryManager] 解析合并计划失败: {e}")

    def _decay(self, memories: list[Memory]):
        """重要性衰减 + 清理低分旧记忆"""
        now = time.time()
        limit = max(500, len(memories))  # 保留上限

        if len(memories) <= limit:
            return

        # 按 (importance * rating) 排序，淘汰末尾
        scored = sorted(memories, key=lambda m: m.importance * m.rating)
        to_remove = scored[:len(memories) - limit]
        for m in to_remove:
            self._mark_superseded(m.id)
        logger.info(f"[MemoryManager] 淘汰 {len(to_remove)} 条低质旧记忆")

    # ==================== 工具方法 ====================

    @staticmethod
    def _extract_rating(qa_result: dict) -> float:
        """从 QA 结果中提取评分"""
        if not qa_result:
            return 7.0
        score = qa_result.get("score", qa_result.get("overall_rating", 7))
        try:
            return float(score)
        except (ValueError, TypeError):
            return 7.0

    @staticmethod
    def _build_code_summary(code_files: list) -> str:
        """构建代码方案摘要"""
        if not code_files:
            return "无代码文件"
        parts = []
        for f in code_files[:5]:
            fname = f.get("filename", "unknown")
            content = f.get("content", "")
            lines = content.count("\n") + 1 if content else 0
            preview = content[:100].replace("\n", " ").strip()
            parts.append(f"{fname}({lines}行): {preview}...")
        return " | ".join(parts)

    @staticmethod
    def _jaccard_similarity(a: str, b: str) -> float:
        """两段文本的 Jaccard 相似度"""
        import re
        def tokens(s):
            # 中文 2-gram + 英文词
            t = set()
            t.update(re.findall(r'[a-zA-Z]{2,}', s.lower()))
            cn = re.findall(r'[一-鿿]+', s)
            for seq in cn:
                t.update(seq[i:i + 2] for i in range(len(seq) - 1))
            return t

        ta, tb = tokens(a), tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    @staticmethod
    def _format_few_shot(memories: list[Memory]) -> str:
        """将选中的记忆格式化为 few-shot 注入文本"""
        if not memories:
            return ""

        parts = ["\n\n## 参考案例（历史成功经验）"]
        for i, m in enumerate(memories, 1):
            parts.append(
                f"### 案例 {i}：{m.requirement[:80]} (评分: {m.rating}/10)\n"
                f"复杂度: {m.complexity} | 文件数: {len(m.tags)} 个标签\n"
            )
            if m.lesson:
                parts.append(f"**关键教训**: {m.lesson}")
            if m.reusable_pattern and m.reusable_pattern != "无":
                parts.append(f"**可复用模式**: {m.reusable_pattern[:300]}")

        return "\n\n".join(parts)
