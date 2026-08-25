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
from harness.state.memory_retriever import create_retriever
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


from harness.instructions.prompts import load_prompt

# ==================== 反思 Prompt（从 .md 文件加载）====================

REFLECTION_SYSTEM = load_prompt("memory/reflection_system.md")
REFLECTION_PROMPT = load_prompt("memory/reflection_prompt.md")

# ==================== 校验 Prompt (L2) ====================

VERIFY_SYSTEM = load_prompt("memory/verify_system.md")
VERIFY_PROMPT = load_prompt("memory/verify_prompt.md")

# ==================== 合并 Prompt ====================

CONSOLIDATE_SYSTEM = load_prompt("memory/consolidate_system.md")
CONSOLIDATE_PROMPT = load_prompt("memory/consolidate_prompt.md")


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
        self._retriever = create_retriever()
        self._lock = threading.Lock()
        self._new_since_consolidate = 0

        # 启动时从数据库加载所有记忆，建立索引
        self._rebuild_index()

    # ==================== 公有 API ====================

    def before_task(self, requirement: str, system_prompt: str, user_id: int = 0) -> str:
        """
        任务前: 检索相关记忆，注入 System Prompt。

        两阶段检索:
        L1: BGE-M3 混合检索 → Top 5
        L2: LLM 校验排序 → 精选 2-3 条

        Args:
            requirement: 用户需求文本
            system_prompt: 原始系统提示词
            user_id: 当前用户 ID（记忆隔离，避免跨用户注入）

        Returns:
            增强后的系统提示词（追加 few-shot 示例）
        """
        try:
            memories = self._get_active_memories(user_id)
            if not memories:
                return system_prompt

            # L1: 混合检索（BGE-M3 或 pgvector）
            # 与 _store 的 _index_memories 互斥：检索器内部索引非线程安全，
            # 并发 index/search 会破坏索引一致性
            with self._lock:
                self._index_memories(memories)
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

    def _index_memories(self, memories: list[Memory]):
        """构建检索索引，兼容 PGVectorRetriever 和 BGEM3Retriever

        PGVectorRetriever 需要 memory_ids 来:
        1. 增量 upsert 向量到 pgvector（只编码新文档）
        2. search 时将 pgvector 结果映射回 doc_index

        BGEM3Retriever 只需要 documents（忽略 memory_ids 参数）
        """
        documents = [m.to_text() for m in memories]
        memory_ids = [m.id for m in memories if m.id is not None]
        try:
            self._retriever.index(documents, memory_ids=memory_ids)
        except TypeError:
            # BGEM3Retriever.index() 不接受 memory_ids 参数
            self._retriever.index(documents)

    def _rebuild_index(self):
        """启动时从数据库加载所有活跃记忆，建立检索索引"""
        try:
            memories = self._get_active_memories()
            if memories:
                self._index_memories(memories)
                logger.info(f"[MemoryManager] 初始索引构建完成: {len(memories)} 条记忆")
            else:
                logger.info("[MemoryManager] 记忆库为空，等待首次任务完成")
        except Exception as e:
            logger.warning(f"[MemoryManager] 索引构建失败（降级为空库）: {e}")

    def _get_active_memories(self, user_id: int = None) -> list[Memory]:
        """从数据库加载活跃（未被淘汰）的记忆；传 user_id 时按用户隔离"""
        try:
            db = SessionLocal()
            query = db.query(AgentMemoryV2).filter_by(superseded=False)
            if user_id is not None:
                query = query.filter(AgentMemoryV2.user_id == user_id)
            rows = query.all()
            db.close()
            return [Memory.from_orm(r) for r in rows]
        except Exception as e:
            logger.warning(f"[MemoryManager] 数据库加载失败: {e}")
            return []

    def _store(self, memory: Memory):
        """存储一条新记忆到数据库（含去重逻辑，去重仅限同一用户）"""
        try:
            # 去重: 如果同一用户已有高度相似的需求，标记旧记忆为 superseded。
            # 必须按 user_id 过滤——否则用户 A 的新需求会把用户 B 的相似记忆淘汰掉。
            memories = self._get_active_memories(user_id=memory.user_id)
            superseded_ids = set()
            for existing in memories:
                if self._jaccard_similarity(memory.requirement, existing.requirement) > 0.6:
                    self._mark_superseded(existing.id)
                    superseded_ids.add(existing.id)
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

            # 更新检索索引（增量构建，避免二次全量查库）
            active = [m for m in memories if m.id not in superseded_ids] + [memory]
            self._index_memories(active)

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
                thinking='enabled',
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
                thinking='enabled',
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
        self._index_memories(active)
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
            thinking='enabled',
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
