# -*- coding: utf-8 -*-
"""
ExperiencePool —— 经验池 (DEPRECATED)

⚠️  已废弃！请使用 harness.state.memory.MemoryManager 替代。

MemoryManager 提供:
- BGE-M3 混合检索（语义 + 词法）替代 TF-IDF
- LLM 3 问反思（reflection + lesson + reusable_pattern）
- 持久化到 agent_memories_v2 表，跨进程重启保留
- 定期 LLM 合并和淘汰

旧的 ExperiencePool + FeedbackLoop 仍可导入，但不会再有新功能。

缓存成功的 (需求特征, 代码方案) 对，相似需求直接复用或作为 few-shot 示例注入 Prompt。

使用纯 Python TF-IDF + 余弦相似度，零额外依赖，适合 Demo 项目规模。
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from harness.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Experience:
    """一条经验记录"""
    requirement: str                  # 原始需求文本
    complexity: str                   # XS/S/M/L
    code_summary: str                 # 代码方案简述（文件名 + 行数 + 功能摘要）
    rating: float = 7.0               # QA 评分 (0-10)
    tech_stack: str = ""              # "tailwind + localStorage"
    file_count: int = 0               # 生成文件数
    total_lines: int = 0              # 总代码行数
    metadata: dict = field(default_factory=dict)


class TFIDFMatcher:
    """纯 Python TF-IDF + 余弦相似度匹配器

    无需额外依赖（不需要 scikit-learn 或 sentence-transformers）。
    适合 Demo 项目规模（<10000 条经验）。
    """

    def __init__(self):
        self._docs: list[str] = []           # 文档列表
        self._idf: dict[str, float] = {}     # 词 → IDF 值
        self._tfidf_vectors: list[dict] = [] # 每条文档的 TF-IDF 向量

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文+英文混合分词"""
        # 提取中文字符序列、英文单词、数字
        tokens = []
        # 中文按单字+词组切分
        chinese_chars = re.findall(r'[一-鿿]+', text)
        for seq in chinese_chars:
            # 按 2-gram 切分中文
            for i in range(len(seq)):
                if i + 1 < len(seq):
                    tokens.append(seq[i:i+2])
                tokens.append(seq[i])
        # 英文单词
        english_words = re.findall(r'[a-zA-Z]{2,}', text.lower())
        tokens.extend(english_words)
        # 数字
        numbers = re.findall(r'\d+', text)
        tokens.extend(numbers)
        return tokens

    def fit(self, documents: list[str]):
        """构建 TF-IDF 索引"""
        self._docs = documents
        N = len(documents)
        if N == 0:
            return

        # 计算文档频率
        df = Counter()
        doc_tokens = []
        for doc in documents:
            tokens = set(self._tokenize(doc))
            doc_tokens.append(tokens)
            df.update(tokens)

        # 计算 IDF
        self._idf = {
            word: math.log((N + 1) / (count + 1)) + 1
            for word, count in df.items()
        }

        # 计算每条文档的 TF-IDF 向量
        self._tfidf_vectors = []
        for tokens in doc_tokens:
            tf = Counter(tokens)
            max_tf = max(tf.values()) if tf else 1
            vec = {
                word: (count / max_tf) * self._idf.get(word, 0)
                for word, count in tf.items()
            }
            self._tfidf_vectors.append(vec)

    def search(self, query: str, top_k: int = 3) -> list[tuple[int, float]]:
        """搜索最相似的 top_k 条文档，返回 [(doc_index, similarity_score), ...]"""
        if not self._docs:
            return []

        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []

        # 查询 TF 向量
        tf = Counter(query_tokens)
        max_tf = max(tf.values()) if tf else 1
        query_vec = {
            word: (count / max_tf) * self._idf.get(word, 0)
            for word, count in tf.items()
        }

        # 计算余弦相似度
        scores = []
        for i, doc_vec in enumerate(self._tfidf_vectors):
            sim = self._cosine_similarity(query_vec, doc_vec)
            if sim > 0.1:  # 过滤完全不相关的结果
                scores.append((i, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    @staticmethod
    def _cosine_similarity(vec1: dict, vec2: dict) -> float:
        """余弦相似度"""
        dot = sum(vec1.get(k, 0) * vec2.get(k, 0) for k in set(vec1) | set(vec2))
        norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)


class ExperiencePool:
    """经验池 —— 缓存 + 检索成功的代码方案

    用法：
    ```python
    pool = ExperiencePool()
    pool.store("做一个待办清单", "S", "index.html(120行)...", rating=8.5)
    results = pool.recall("做一个任务管理工具", top_k=2)
    ```
    """

    MAX_EXPERIENCES = 500  # 经验池上限（防止无限增长）

    def __init__(self):
        self._experiences: list[Experience] = []
        self._matcher = TFIDFMatcher()
        self._dirty = True  # 索引是否需要重建

    def store(self, requirement: str, complexity: str,
              code_summary: str, rating: float = 7.0,
              tech_stack: str = "", file_count: int = 0,
              total_lines: int = 0, metadata: dict = None) -> Experience:
        """
        存储一条经验。

        Args:
            requirement: 用户原始需求
            complexity: XS/S/M/L
            code_summary: 代码方案简述
            rating: QA 评分 (0-10)
            tech_stack: 技术栈
            file_count: 文件数量
            total_lines: 总代码行数
            metadata: 附加信息

        Returns:
            存储的 Experience 对象
        """
        exp = Experience(
            requirement=requirement,
            complexity=complexity,
            code_summary=code_summary,
            rating=rating,
            tech_stack=tech_stack,
            file_count=file_count,
            total_lines=total_lines,
            metadata=metadata or {},
        )
        self._experiences.append(exp)
        self._dirty = True

        # 超过上限时淘汰评分最低的旧经验
        if len(self._experiences) > self.MAX_EXPERIENCES:
            # 保留最近 100 条 + 评分最高 400 条
            sorted_by_rating = sorted(
                self._experiences[:-100],
                key=lambda e: e.rating, reverse=True
            )
            self._experiences = sorted_by_rating[:400] + self._experiences[-100:]
            self._dirty = True

        logger.debug(f"[ExperiencePool] 存储经验: {requirement[:50]}... (rating={rating})")
        return exp

    def store_failure(self, requirement: str, error_pattern: str):
        """存储失败案例（负面样本），后续相似需求时注入警告"""
        exp = Experience(
            requirement=requirement,
            complexity="N/A",
            code_summary=f"FAILURE: {error_pattern}",
            rating=2.0,
            metadata={"type": "negative", "error_pattern": error_pattern},
        )
        self._experiences.append(exp)
        self._dirty = True

    def recall(self, requirement: str, top_k: int = 3,
               min_rating: float = 5.0) -> list[Experience]:
        """
        检索最相似的 K 条成功经验。

        Args:
            requirement: 用户需求文本
            top_k: 返回数量
            min_rating: 最低评分阈值（过滤低质量经验）

        Returns:
            相似经验列表（按相似度降序）
        """
        if not self._experiences:
            return []

        # 重建索引（如有新数据）
        if self._dirty:
            docs = [e.requirement + " " + e.code_summary for e in self._experiences]
            self._matcher.fit(docs)
            self._dirty = False

        results = self._matcher.search(requirement, top_k * 2)  # 多取一些再过滤

        experiences = []
        for idx, sim in results:
            exp = self._experiences[idx]
            if exp.rating >= min_rating:
                experiences.append(exp)
            if len(experiences) >= top_k:
                break

        if experiences:
            logger.info(
                f"[ExperiencePool] 检索到 {len(experiences)} 条经验 "
                f"(query: {requirement[:50]}...)"
            )

        return experiences

    def recall_warnings(self, requirement: str, top_k: int = 2) -> list[str]:
        """检索相关的失败案例，返回警告文本列表"""
        if not self._experiences:
            return []

        if self._dirty:
            docs = [e.requirement + " " + e.code_summary for e in self._experiences]
            self._matcher.fit(docs)
            self._dirty = False

        results = self._matcher.search(requirement, top_k * 2)
        warnings = []
        for idx, sim in results:
            exp = self._experiences[idx]
            if exp.rating < 3.0 and exp.metadata.get("type") == "negative":
                pattern = exp.metadata.get("error_pattern", "")
                if pattern and pattern not in warnings:
                    warnings.append(pattern)
            if len(warnings) >= top_k:
                break
        return warnings

    def get_few_shot_text(self, requirement: str, n_examples: int = 2) -> str:
        """
        组装 few-shot 示例文本，直接注入 Prompt。

        返回格式：
        ## 参考案例（历史成功经验）
        ### 案例 1：待办清单 (评分: 9/10)
        文件: index.html(120行), style.css(80行), script.js(200行)
        方案: 使用 Tailwind CSS + localStorage 实现增删改查...
        """
        experiences = self.recall(requirement, top_k=n_examples)
        if not experiences:
            return ""

        parts = ["## 参考案例（历史成功经验）"]
        for i, exp in enumerate(experiences, 1):
            parts.append(
                f"### 案例 {i}：{exp.requirement[:80]} (评分: {exp.rating}/10)\n"
                f"复杂度: {exp.complexity} | 文件数: {exp.file_count} | "
                f"总行数: {exp.total_lines}\n"
                f"方案: {exp.code_summary[:500]}"
            )
        return "\n\n".join(parts)

    def get_warnings_text(self, requirement: str) -> str:
        """
        组装警告文本，注入 Prompt 帮助避免重复错误。

        返回格式：
        ## 避免以下错误（历史教训）
        - ❌ localStorage 未做 JSON.parse 异常处理
        - ❌ 删除操作未加确认弹窗
        """
        warnings = self.recall_warnings(requirement, top_k=2)
        if not warnings:
            return ""

        return "## 避免以下错误（历史教训）\n" + "\n".join(
            f"- ❌ {w}" for w in warnings
        )

    def stats(self) -> dict:
        """经验池统计信息"""
        if not self._experiences:
            return {"total": 0, "avg_rating": 0}

        ratings = [e.rating for e in self._experiences if e.rating > 0]
        complexities = Counter(e.complexity for e in self._experiences)
        return {
            "total": len(self._experiences),
            "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
            "by_complexity": dict(complexities),
            "failures": sum(1 for e in self._experiences if e.rating < 3.0),
        }

    def clear(self):
        """清空经验池"""
        self._experiences.clear()
        self._matcher = TFIDFMatcher()
        self._dirty = True
