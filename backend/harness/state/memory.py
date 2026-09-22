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
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from models import SessionLocal, AgentMemoryV2, MemoryHit
from sqlalchemy import func
from harness.state.memory_retriever import create_retriever
from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== 注入预算 ====================
#
# 背景：记忆以 few-shot 形式追加在 system prompt 末尾，直接挤占主任务的
# 上下文与注意力。经验口径是"注意点越多，模型注意力越碎，主任务越差"，
# 所以这里必须设硬上限，而不是任由记忆增长。
# 预算值参考业界分层注入的常用配比（顶层摘要 ≤500、事实层 ≤800）。

INJECT_MAX_ITEMS = 6            # 单次最多注入的记忆条数
INJECT_MAX_TOKENS = 1200        # 注入文本的总预算（token）
INJECT_ITEM_MAX_TOKENS = 200    # 单条记忆的预算（token）

# ==================== 相关性门禁 ====================
#
# 背景：探针（tmp/verify_ab_probe.py）发现，开启记忆后几乎所有 eval 任务都
# 注入了不相关的记忆（名片页任务被注入了贪吃蛇游戏经验）——因为 search 返回的
# 相似度分数此前被直接丢弃、top_k 全入选、没有任何相关性门槛。这正是方案文档
# 反复诊断的"信噪比"问题：注入不相关记忆比不注入更糟。
#
# 两层门禁（score 是余弦相似度，约 0-1）：
#  - 绝对下限 MIN_RELEVANCE：连最相关的候选都不够相关，整体不注入。
#  - 相对门槛 REL_RELEVANCE_RATIO：只保留与最相关结果相似度达到其一定比例的项，
#    避免 top_k 全入选导致的同质化注入（记忆库小、语义稀疏时尤其明显）。
# 两层配合，对 BGE-M3 / TF-IDF 等后端都鲁棒。阈值偏保守——宁可少注入，
# 也不把不相关记忆硬塞进无关任务。
MIN_RELEVANCE = 0.25
REL_RELEVANCE_RATIO = 0.5

_ESTIMATE_CJK = re.compile(r'[\u4e00-\u9fff]')


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    口径与 harness/instructions/compactor.py 的 _estimate_text_tokens 保持一致
    （中文约 1.5 字/token，英文与符号约 4 字/token），避免两处估算漂移。
    """
    if not text:
        return 0
    chinese = len(_ESTIMATE_CJK.findall(text))
    other = max(len(text) - chinese, 0)
    return int(chinese / 1.5 + other / 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """按 token 预算截断文本，超出部分以省略号标记。

    用二分而非逐字累加，长文本下开销可忽略。
    """
    if not text or max_tokens <= 0:
        return ""
    if _estimate_tokens(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


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


def _has_injectable_content(m: "Memory") -> bool:
    """记忆至少要有 lesson 或 reusable_pattern 之一有真实内容，才值得注入。

    完全空（二者皆空或仅占位符"无"）的记忆注入后只是空块，浪费预算且是噪声；
    其 rating 往往来自 LLM 自评、不可信（见 P1 #8）。
    宽松策略：保留"有 lesson 但缺 pattern"的记忆，不在这里砍（那是 P1 #8 治理决策）。
    """
    lesson = (m.lesson or "").strip()
    pattern = (m.reusable_pattern or "").strip()
    return (bool(lesson) and lesson != "无") or (bool(pattern) and pattern != "无")


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

        # 当前索引对应的活跃记忆快照（全局视图，检索时按 user_id 过滤结果）。
        # None 表示索引尚未建立或已失效，下次检索前会重建。
        self._indexed_memories: Optional[list[Memory]] = None

        # 启动时从数据库加载所有记忆，建立索引
        self._rebuild_index()

    # ==================== 公有 API ====================

    def _select_and_render(self, requirement: str, user_id: int = 0):
        """检索 → 校验 → 渲染，返回 (block, injected_items)。只渲染，不记账。"""
        try:
            with self._lock:
                memories = self._ensure_index()
                if not memories:
                    return "", []
                results = self._retriever.search(requirement, top_k=5)
            if not results:
                return "", []

            # 防御：部分检索后端（BGE-M3 / PGVector）的 search 结果未按 score 排序，
            # 门禁依赖"最相关"判断，这里统一排序以保证正确。
            results.sort(key=lambda x: x[1], reverse=True)

            # 相关性门禁（绝对层）：连最相关的候选都不够相关，整体不注入。
            # 避免把不相关记忆硬塞进无关任务——探针实证：名片页任务被注入了
            # 贪吃蛇游戏经验。score 是余弦相似度（约 0-1），<MIN_RELEVANCE 视为无关。
            top_score = results[0][1]
            if top_score < MIN_RELEVANCE:
                logger.info(
                    f"[MemoryManager] 最相关记忆相似度 {top_score:.2f} < {MIN_RELEVANCE}，"
                    f"跳过注入（避免不相关噪声）for: {requirement[:50]}...")
                return "", []

            # 相关性门禁（相对层）：只保留与最相关结果相似度达到其一定比例的项，
            # 避免 top_k 全入选导致的同质化注入（记忆库小、语义稀疏时尤其明显）。
            rel_floor = top_score * REL_RELEVANCE_RATIO

            # 用户隔离：索引是全局视图，检索结果按 user_id 过滤。
            # 不能反过来用单用户记忆去建索引，那会让索引退化成单用户视图，
            # 污染其他用户的检索结果。
            # 同时丢弃完全无内容的记忆（lesson 与 reusable_pattern 皆空/占位符），
            # 这类记忆注入后只是空块、纯噪声（见 _has_injectable_content）。
            candidates = []
            for i, score in results:
                if i >= len(memories):
                    continue
                m = memories[i]
                if m.user_id != user_id:
                    continue
                if not _has_injectable_content(m):
                    continue
                if score < rel_floor:
                    continue
                candidates.append(m)
            if not candidates:
                return "", []

            # L2: LLM 校验 + 排序
            selected = self._llm_verify(requirement, candidates)
            if not selected:
                # LLM 不可用或返回空 → 直接用 L1 的 top 2
                selected = candidates[:2]

            self._touch_memories([m.id for m in selected if m.id])

            block, injected_items = self._render_few_shot(selected)
            if block:
                logger.info(
                    f"[MemoryManager] 注入 {len(injected_items)} 条记忆 "
                    f"(用户 {user_id} 候选 {len(candidates)}/{len(memories)} 条) "
                    f"for: {requirement[:50]}...")
            return block, injected_items

        except Exception as e:
            logger.warning(f"[MemoryManager] 记忆检索异常（降级跳过）: {e}")
            return "", []

    def build_memory_block(self, requirement: str, user_id: int = 0) -> str:
        """检索相关记忆，渲染为可注入的 few-shot 文本块（已完成 token 预算裁剪）。

        与 before_task 的区别：本方法只负责"算出该注入什么"，不负责拼接。
        调用方应在一个任务开始时调用一次并缓存结果 —— 注入内容在任务内
        不会变化，而 system prompt 是每个 LLM turn 重建一次的，不缓存就
        意味着每个 turn 都要全表查库并重建检索索引。

        不记账：记账需要 requirement_id / run_id 才能归因，本方法拿不到这些
        上下文。需要度量时用 inject_with_receipt()。

        Returns:
            few-shot 文本块；无可用记忆或检索失败时返回空串（调用方可安全拼接）。
        """
        block, _ = self._select_and_render(requirement, user_id)
        return block

    def inject_with_receipt(self, requirement: str, user_id: int = 0,
                            requirement_id: Optional[int] = None,
                            run_id: Optional[str] = None):
        """检索 + 渲染 + 记账（"注入即记账"），返回 (block, hit_ids)。

        hit_ids 是本次注入写下的记账行主键，任务结束后用 resolve_hits()
        回填结果，于是每条记忆都能算出"被注入 N 次、其中 M 次任务通过"。

        归因粒度的边界（避免过度解读）：一条记忆被注入到通过的任务里，
        不代表任务通过是它的功劳 —— 这里记的是相关性，不是因果性。
        要证明因果必须靠 A/B（见 eval --with-memory）。

        记账失败不影响注入：会计挂了不代表不该卖货，block 照常返回。
        """
        block, injected_items = self._select_and_render(requirement, user_id)
        if not block or not injected_items:
            return block, []
        hit_ids = self._record_hits(injected_items, user_id, requirement_id, run_id)
        return block, hit_ids

    def before_task(self, requirement: str, system_prompt: str, user_id: int = 0) -> str:
        """
        任务前: 检索相关记忆，注入 System Prompt。

        提示：优先在任务开始时调用一次 build_memory_block() 缓存结果，再自行
        拼接。反复调用本方法会导致每个 LLM turn 都重新检索一遍。

        Args:
            requirement: 用户需求文本
            system_prompt: 原始系统提示词
            user_id: 当前用户 ID（记忆隔离，避免跨用户注入）

        Returns:
            增强后的系统提示词（追加 few-shot 示例）
        """
        block = self.build_memory_block(requirement, user_id)
        return system_prompt + block if block else system_prompt

    def after_task(self, requirement: str, complexity: str,
                   code_files: list, qa_result: dict = None,
                   user_id: int = 0):
        """
        任务后: LLM 反思 + 存储记忆（正负经验都沉淀，审查报告 Phase 4.2）。

        失败任务（passed=False 或低分）显式标记为负样本：
        - reflection prompt 会收到失败上下文，引导提取"为什么没做成"
        - 记忆打上 failure 标签，before_task 注入时作为 ⚠️ 警示案例呈现

        Args:
            requirement: 用户需求
            complexity: XS/S/M/L
            code_files: 生成的代码文件列表
            qa_result: QA/Summarize 审查结果（含 score, verdict, issues）
            user_id: 用户 ID
        """
        rating = self._extract_rating(qa_result)
        code_summary = self._build_code_summary(code_files)

        # 失败判定：显式 passed 标记优先，其次按评分阈值
        qa_passed = qa_result.get("passed") if isinstance(qa_result, dict) else None
        is_failure = (qa_passed is False) or (qa_passed is None and rating < 6.0)

        # LLM 反思（失败任务注入失败上下文，引导提取负面教训）
        reflection_data = {}
        if self._llm:
            try:
                reflection_data = self._reflect(
                    requirement, code_summary, rating,
                    failure_context=self._build_failure_context(qa_result) if is_failure else "",
                )
            except Exception as e:
                logger.warning(f"[MemoryManager] LLM 反思失败，使用默认值: {e}")

        tags = list(reflection_data.get("tags", []))
        if is_failure and "failure" not in tags:
            tags.append("failure")

        memory = Memory(
            user_id=user_id,
            requirement=requirement,
            complexity=complexity,
            code_summary=code_summary,
            rating=rating,
            reflection=reflection_data.get("reflection", ""),
            lesson=reflection_data.get("lesson", ""),
            reusable_pattern=reflection_data.get("reusable_pattern", ""),
            tags=tags,
            importance=max(reflection_data.get("importance", 0), 0.7) if is_failure
            else reflection_data.get("importance", rating / 10.0),
            created_at=time.time(),
        )

        # 去重 + 存储
        with self._lock:
            self._store(memory)
            self._new_since_consolidate += 1
            logger.info(
                f"[MemoryManager] 存储记忆: rating={rating}, "
                f"failure={is_failure}, tags={memory.tags}, "
                f"total_new_since_maintain={self._new_since_consolidate}"
            )

        # 定期维护
        if self._new_since_consolidate >= self.CONSOLIDATE_INTERVAL:
            self._maintain()

    @staticmethod
    def _build_failure_context(qa_result: dict | None) -> str:
        """把失败证据压缩成反思 prompt 的上下文块"""
        if not isinstance(qa_result, dict):
            return ""
        issues = qa_result.get("critical_issues") or []
        lines = []
        for issue in issues[:5]:
            lines.append(f"- {str(issue)[:150]}")
        return "\n".join(lines)

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

    def _ensure_index(self) -> list[Memory]:
        """确保检索索引与数据库一致，返回当前活跃记忆（全局视图）。

        索引始终覆盖全部用户的活跃记忆，检索命中后再按 user_id 过滤。
        不能反过来只用单个用户的记忆去建索引 —— 那会让索引退化成单用户
        视图，把其他用户可检索的记忆挤掉。

        只在活跃记忆数量变化时重建：稳定期每次检索仅一次 COUNT 查询，
        避免"每轮全表 SELECT + 重建索引"的开销。

        注意：调用方必须已持有 self._lock，本方法内部不再加锁。
        """
        db_count = self._count_active_memories()
        if self._indexed_memories is None or db_count != len(self._indexed_memories):
            self._indexed_memories = self._get_active_memories()
            self._index_memories(self._indexed_memories)
            logger.info(
                f"[MemoryManager] 检索索引已重建: {len(self._indexed_memories)} 条活跃记忆"
            )
        return self._indexed_memories

    def _invalidate_index(self):
        """标记索引失效（写入或淘汰后调用），下次检索前自动重建"""
        self._indexed_memories = None

    def _count_active_memories(self) -> int:
        """活跃记忆条数。查询失败返回 -1，以强制下次重建索引"""
        db = None
        try:
            db = SessionLocal()
            return db.query(AgentMemoryV2).filter_by(superseded=False).count()
        except Exception as e:
            logger.warning(f"[MemoryManager] 统计活跃记忆失败: {e}")
            return -1
        finally:
            if db is not None:
                db.close()

    def _rebuild_index(self):
        """启动时从数据库加载所有活跃记忆，建立检索索引"""
        try:
            memories = self._get_active_memories()
            self._indexed_memories = memories
            if memories:
                self._index_memories(memories)
                logger.info(f"[MemoryManager] 初始索引构建完成: {len(memories)} 条记忆")
            else:
                logger.info("[MemoryManager] 记忆库为空，等待首次任务完成")
        except Exception as e:
            self._indexed_memories = None
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
        db = None
        try:
            # 去重: 如果同一用户已有高度相似的需求，标记旧记忆为 superseded。
            # 必须按 user_id 过滤——否则用户 A 的新需求会把用户 B 的相似记忆淘汰掉。
            memories = self._get_active_memories(user_id=memory.user_id)
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

            # 索引失效，下次检索前重建。
            # 注意：不能在这里用 memories（单用户视图）去重建索引，那会把全局
            # 索引覆盖成单用户视图，让其他用户的记忆无从检索。
            self._invalidate_index()

        except Exception as e:
            logger.warning(f"[MemoryManager] 存储记忆失败: {e}")
            self._invalidate_index()
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _mark_superseded(self, mem_id: int):
        """标记一条记忆已被替代"""
        db = None
        try:
            db = SessionLocal()
            row = db.query(AgentMemoryV2).filter_by(id=mem_id).first()
            if row:
                row.superseded = True
                db.commit()
                self._invalidate_index()
        except Exception as e:
            logger.warning(f"[MemoryManager] 标记 superseded 失败: {e}")
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _touch_memories(self, memory_ids: list[int]):
        """把"这些记忆被注入过"这件事真正写进数据库。

        此前只在内存对象上自增、从不 commit，access_count 恒为 0，导致所有
        基于使用频率的治理（零引用归档、按热度淘汰）都拿不到事实依据。

        待办：last_accessed_at 需要给 agent_memories_v2 加列，而项目当前只用
        create_all（不会给已存在的表补列），故 P0 阶段先落 access_count，
        时间戳随 P1 的迁移一并补上。
        """
        if not memory_ids:
            return
        db = None
        try:
            db = SessionLocal()
            db.query(AgentMemoryV2).filter(AgentMemoryV2.id.in_(memory_ids)).update(
                {AgentMemoryV2.access_count: AgentMemoryV2.access_count + 1},
                synchronize_session=False,
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[MemoryManager] 记录记忆命中失败: {e}")
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _record_hits(self, injected_items: list, user_id: int,
                     requirement_id: Optional[int] = None,
                     run_id: Optional[str] = None) -> list[int]:
        """把"这些记忆被注入了"批量写成 pending 记账行，返回行主键列表。

        只记真正进入 prompt 的条目（injected_items 来自 _render_few_shot，
        已经过预算裁剪），候选但未入选的不记 —— 否则命中率会被凭空抬高。

        任何失败都返回空列表：记账是旁路，绝不能阻断记忆注入本身。
        """
        if not injected_items:
            return []
        db = None
        try:
            db = SessionLocal()
            rows = []
            for mem, position, tokens in injected_items:
                if not getattr(mem, "id", None):
                    continue
                row = MemoryHit(
                    memory_id=mem.id,
                    user_id=user_id,
                    requirement_id=requirement_id,
                    run_id=run_id,
                    inject_position=position,
                    inject_tokens=tokens,
                    outcome="pending",
                )
                db.add(row)
                rows.append(row)
            db.commit()
            return [r.id for r in rows if r.id]
        except Exception as e:
            logger.warning(f"[MemoryManager] 记忆注入记账失败（不阻断注入）: {e}")
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            return []
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def resolve_hits(self, hit_ids: list[int], passed: bool):
        """任务结束后回填记账结果：passed=True 记 pass，否则记 fail。

        任务异常中断时这些行会一直是 pending —— 这是有意的：pending 既不
        算命中也不算未命中，不会污染统计，同时留出了"任务没跑完"的线索。
        """
        if not hit_ids:
            return
        outcome = "pass" if passed else "fail"
        db = None
        try:
            db = SessionLocal()
            db.query(MemoryHit).filter(MemoryHit.id.in_(hit_ids)).update(
                {MemoryHit.outcome: outcome,
                 MemoryHit.resolved_at: func.now()},
                synchronize_session=False,
            )
            db.commit()
            logger.info(f"[MemoryManager] 记账回填 {len(hit_ids)} 条 → {outcome}")
        except Exception as e:
            logger.warning(f"[MemoryManager] 记账回填失败: {e}")
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _reflect(self, requirement: str, code_summary: str, rating: float,
                 failure_context: str = "") -> dict:
        """LLM 3 问自答（失败任务附带失败上下文引导负面教训提取）"""
        if not self._llm:
            return {}

        prompt = REFLECTION_PROMPT.format(
            requirement=requirement[:300],
            code_summary=code_summary[:500],
            rating=rating,
            failure_context=(
                f"\n\n## ⚠️ 本次任务未通过验收，以下是确定性缺陷证据\n{failure_context}\n"
                f"请重点分析这些缺陷的成因——下次遇到同类需求时如何从架构层面避免。"
                if failure_context else ""
            ),
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
        # 重建索引：维护后活跃集合已变化。检索索引非线程安全，必须持锁。
        active = self._get_active_memories()
        with self._lock:
            self._indexed_memories = active
            self._index_memories(active)
        logger.info(f"[MemoryManager] 维护完成: {len(active)} 条活跃记忆")

    def _llm_consolidate(self, memories: list[Memory]):
        """LLM 驱动的记忆合并

        LLM 返回的 merge_groups/deprecate 索引都相对于展示给它的
        最近 40 条窗口；本方法真正执行合并——每组保留第一条为代表，
        其余标记 superseded，并把权重让渡给代表记忆。
        """
        window = memories[-40:]  # 只看最近 40 条，索引以此窗口为准
        memory_text = "\n".join(
            f"[{i}] 需求: {m.requirement[:80]} | 评分: {m.rating} | "
            f"标签: {m.tags} | 教训: {m.lesson[:100]}"
            for i, m in enumerate(window)
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

            # 执行合并：每组 [a, b, c] 保留 a 为代表，b/c 淘汰，
            # 代表记忆 importance 上调（吸收了被合并条目的价值）
            merge_groups = plan.get("merge_groups", [])
            merged_count = 0
            for group in merge_groups:
                if not isinstance(group, list):
                    continue
                valid = [i for i in group
                         if isinstance(i, int) and 0 <= i < len(window)]
                if len(valid) < 2:
                    continue
                representative = window[valid[0]]
                for idx in valid[1:]:
                    self._mark_superseded(window[idx].id)
                    merged_count += 1
                if merged_count:
                    self._boost_importance(representative.id, 0.05 * len(valid[1:]))
            if merged_count:
                logger.info(f"[MemoryManager] 合并完成: {merged_count} 条相似记忆被代表记忆吸收")

            # 执行淘汰
            deprecate_ids = plan.get("deprecate", [])
            for idx in deprecate_ids:
                if isinstance(idx, int) and 0 <= idx < len(window):
                    self._mark_superseded(window[idx].id)

        except Exception as e:
            logger.warning(f"[MemoryManager] 解析合并计划失败: {e}")

    def _boost_importance(self, mem_id: int, delta: float):
        """合并后上调代表记忆的重要性（封顶 1.0）"""
        if not mem_id or delta <= 0:
            return
        try:
            db = SessionLocal()
            row = db.query(AgentMemoryV2).filter_by(id=mem_id).first()
            if row:
                row.importance = min(1.0, (row.importance or 0.5) + delta)
                db.commit()
            db.close()
        except Exception as e:
            logger.warning(f"[MemoryManager] 上调代表记忆重要性失败: {e}")

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
    def _format_few_shot(memories: list[Memory],
                         max_items: int = INJECT_MAX_ITEMS,
                         max_tokens: int = INJECT_MAX_TOKENS) -> str:
        """将选中的记忆格式化为 few-shot 注入文本（受 token 预算硬约束）

        仅返回文本。需要记账时用 _render_few_shot()，它额外返回实际注入的条目。
        """
        block, _ = MemoryManager._render_few_shot(memories, max_items, max_tokens)
        return block

    @staticmethod
    def _render_few_shot(memories: list[Memory],
                         max_items: int = INJECT_MAX_ITEMS,
                         max_tokens: int = INJECT_MAX_TOKENS):
        """格式化 + 记账回执，返回 (block, injected_items)。

        injected_items: [(Memory, position, tokens), ...] —— 真正进入 block 的
        记忆。必须与 block 严格一致：预算裁剪会丢弃尾部候选项，记账若按
        "候选列表"记，就会把被筛掉的记忆也算成命中，凭空抬高命中率。
        """
        if not memories:
            return "", []

        header = "\n\n## 参考案例（历史经验，含成功与失败教训）"
        parts = [header]
        used = _estimate_tokens(header)
        injected = 0
        injected_items = []

        for i, m in enumerate(memories, 1):
            if injected >= max_items or used >= max_tokens:
                break

            is_failure = "failure" in (m.tags or []) or m.rating < 6.0
            badge = "⚠️ 失败案例（务必避免重蹈覆辙）" if is_failure else "✅ 成功案例"
            head = (f"### 案例 {i}：{m.requirement[:80]} (评分: {m.rating}/10)\n"
                    f"{badge} | 复杂度: {m.complexity}\n")
            head_cost = _estimate_tokens(head)

            # 单条预算：标题优先，正文按剩余额度截断。
            # 注意 lesson 与 reusable_pattern 共享同一份 body 预算，
            # 否则两者各自吃满额度，单条实际会翻倍（2 × 200 token）。
            body_budget = INJECT_ITEM_MAX_TOKENS - head_cost
            body_parts = []
            if body_budget > 0 and m.lesson:
                lesson = _truncate_to_tokens(m.lesson, body_budget)
                body_parts.append(f"**关键教训**: {lesson}")
                body_budget -= _estimate_tokens(lesson)
            if (body_budget > 0 and not is_failure
                    and m.reusable_pattern and m.reusable_pattern != "无"):
                body_parts.append(
                    f"**可复用模式**: {_truncate_to_tokens(m.reusable_pattern, body_budget)}")

            block = head + "\n\n".join(body_parts)
            cost = _estimate_tokens(block)

            if used + cost > max_tokens:
                # 总预算装不下整条 → 退化为只保留标题行；标题也装不下就停止
                if used + head_cost > max_tokens:
                    break
                block, cost = head, head_cost

            parts.append(block)
            used += cost
            injected += 1
            injected_items.append((m, injected, cost))

        if injected == 0:
            return "", []

        if injected < len(memories):
            logger.info(
                f"[MemoryManager] 注入预算裁剪: {len(memories)} 条候选中注入 "
                f"{injected} 条，约 {used} token"
            )
        return "\n\n".join(parts), injected_items
