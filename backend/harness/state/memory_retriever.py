# -*- coding: utf-8 -*-
"""
BGEM3Retriever —— BGE-M3 混合检索器（Dense + Sparse）

使用 BAAI/bge-m3 模型进行稠密语义检索 + 稀疏词法检索。
支持降级回退（sentence-transformers 未安装时自动切 TF-IDF）。

用法:
    retriever = BGEM3Retriever()
    retriever.index(["需求1文本", "需求2文本", ...])
    results = retriever.search("新需求文本", top_k=5)
    # → [(0, 0.92), (3, 0.85), ...]   (doc_index, combined_score)
"""

import math
import re
import threading
from collections import Counter
from typing import Optional

import numpy as np

from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== 降级回退：TF-IDF 匹配器 ====================

class _TFIDFFallback:
    """纯 Python TF-IDF + 余弦相似度（bge-m3 不可用时的降级方案）"""

    def __init__(self):
        self._docs: list[str] = []
        self._idf: dict[str, float] = {}
        self._tfidf_vectors: list[dict] = []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = []
        # 中文 2-gram + 单字
        chinese = re.findall(r'[一-鿿]+', text)
        for seq in chinese:
            for i in range(len(seq)):
                if i + 1 < len(seq):
                    tokens.append(seq[i:i + 2])
                tokens.append(seq[i])
        # 英文单词（含驼峰拆分）
        en_words = re.findall(r'[a-zA-Z]{2,}', text.lower())
        for w in en_words:
            tokens.append(w)
            # 驼峰拆分: localStorage → local, storage
            parts = re.findall(r'[a-z]+', w)
            tokens.extend(p for p in parts if len(p) >= 2)
        # 数字
        tokens.extend(re.findall(r'\d+', text))
        return tokens

    def fit(self, documents: list[str]):
        self._docs = documents
        N = len(documents)
        if N == 0:
            return

        df = Counter()
        doc_tokens = [set(self._tokenize(d)) for d in documents]
        for tokens in doc_tokens:
            df.update(tokens)

        self._idf = {
            w: math.log((N + 1) / (c + 1)) + 1
            for w, c in df.items()
        }

        self._tfidf_vectors = []
        for tokens in doc_tokens:
            tf = Counter(tokens)
            max_tf = max(tf.values()) if tf else 1
            self._tfidf_vectors.append({
                w: (c / max_tf) * self._idf.get(w, 0)
                for w, c in tf.items()
            })

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        if not self._docs:
            return []

        qtokens = set(self._tokenize(query))
        if not qtokens:
            return []

        tf = Counter(qtokens)
        max_tf = max(tf.values()) if tf else 1
        qvec = {w: (c / max_tf) * self._idf.get(w, 0) for w, c in tf.items()}

        scores = []
        for i, dvec in enumerate(self._tfidf_vectors):
            dot = sum(qvec.get(k, 0) * dvec.get(k, 0) for k in set(qvec) | set(dvec))
            na = math.sqrt(sum(v ** 2 for v in qvec.values()))
            nb = math.sqrt(sum(v ** 2 for v in dvec.values()))
            sim = dot / (na * nb) if na > 0 and nb > 0 else 0.0
            if sim > 0.05:
                scores.append((i, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ==================== BM25 稀疏检索 ====================

class _BM25:
    """简易 BM25 词法检索（纯 Python，零依赖）"""

    K1 = 1.5
    B = 0.75

    def __init__(self):
        self._docs: list[list[str]] = []
        self._avgdl: float = 0
        self._df: Counter = Counter()
        self._N: int = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # 中文 2-gram
        chinese = re.findall(r'[一-鿿]+', text)
        tokens = []
        for seq in chinese:
            tokens.extend(seq[i:i + 2] for i in range(len(seq) - 1))
        # 英文（含驼峰拆分）
        for w in re.findall(r'[a-zA-Z]{2,}', text.lower()):
            tokens.append(w)
            tokens.extend(re.findall(r'[a-z]+', w))
        return tokens

    def fit(self, documents: list[str]):
        self._docs = [self._tokenize(d) for d in documents]
        self._N = len(self._docs)
        self._df = Counter()
        for doc in self._docs:
            self._df.update(set(doc))
        self._avgdl = sum(len(d) for d in self._docs) / max(self._N, 1)

    def score(self, query: str) -> list[float]:
        if self._N == 0:
            return []

        qtokens = self._tokenize(query)
        scores = []
        for doc in self._docs:
            dl = len(doc)
            s = 0.0
            for t in qtokens:
                if t not in doc:
                    continue
                tf = doc.count(t)
                df = self._df.get(t, 0)
                idf = math.log((self._N - df + 0.5) / (df + 0.5) + 1)
                s += idf * (tf * (self.K1 + 1)) / (tf + self.K1 * (1 - self.B + self.B * dl / self._avgdl))
            scores.append(s)
        return scores


# ==================== BGE-M3 混合检索器 ====================

class BGEM3Retriever:
    """
    BGE-M3 混合检索器：Dense 语义 + Sparse 词法

    特性:
    - 稠密向量 (1024维): 捕获中英文语义相似度
    - BM25 词法: 精确匹配技术术语 (localStorage, JSON.stringify)
    - 混合打分: 0.6 * dense + 0.4 * sparse
    - 指令感知编码: 查询侧添加前缀
    - 降级回退: sentence-transformers 不可用时自动切 TF-IDF
    - 延迟加载: 首次使用时才下载模型 (1.9GB)
    """

    QUERY_INSTRUCTION = "为这个需求找到相关的历史经验："
    MODEL_NAME = "BAAI/bge-m3"
    DENSE_WEIGHT = 0.6
    SPARSE_WEIGHT = 0.4

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._bm25 = _BM25()
        self._fallback = _TFIDFFallback()
        self._documents: list[str] = []        # 文档文本列表
        self._dense_embeddings: Optional[np.ndarray] = None  # (N, 1024)
        self._dirty: bool = False
        self._use_fallback: bool = False

    @property
    def model(self):
        """延迟加载 BGE-M3 模型（线程安全）"""
        if self._model is None and not self._use_fallback:
            with self._model_lock:
                if self._model is None and not self._use_fallback:
                    try:
                        from sentence_transformers import SentenceTransformer
                        logger.info(f"加载 BGE-M3 模型: {self.MODEL_NAME} ...")
                        self._model = SentenceTransformer(self.MODEL_NAME)
                        logger.info("BGE-M3 模型加载完成")
                    except ImportError:
                        logger.warning(
                            "sentence-transformers 未安装，使用 TF-IDF 降级方案。"
                            "安装: pip install sentence-transformers>=2.7.0"
                        )
                        self._use_fallback = True
                    except Exception as e:
                        logger.warning(f"BGE-M3 加载失败 ({e})，使用 TF-IDF 降级方案")
                        self._use_fallback = True
        return self._model

    # ---- 索引管理 ----

    def index(self, documents: list[str]):
        """
        建立/更新索引。

        Args:
            documents: 文档文本列表（每条记忆的 to_text() 结果）
        """
        self._documents = documents
        if not documents:
            self._dense_embeddings = None
            self._bm25 = _BM25()
            self._fallback = _TFIDFFallback()
            return

        if self._use_fallback:
            self._fallback.fit(documents)
        else:
            self._bm25.fit(documents)
            # 延迟编码 dense（在首次 search 时）
            self._dense_embeddings = None
            self._dirty = True

    def _ensure_dense(self):
        """确保 dense embeddings 已计算"""
        if self._dirty and not self._use_fallback and self._documents:
            model = self.model
            if model and self._documents:
                logger.debug(f"编码 {len(self._documents)} 条文档的 dense embeddings ...")
                # 文档侧不加前缀（BGE 约定）
                self._dense_embeddings = model.encode(
                    self._documents,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                logger.debug(f"编码完成: shape={self._dense_embeddings.shape}")
            self._dirty = False

    # ---- 检索 ----

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        """
        混合检索 (Dense + Sparse)。

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            [(doc_index, combined_score), ...]  按分数降序
        """
        if not self._documents:
            return []

        if self._use_fallback:
            return self._fallback.search(query, top_k)

        # Dense 语义相似度
        dense_scores = self._dense_search(query)

        # Sparse 词法匹配
        sparse_scores = self._bm25.score(query)

        # Hybrid 加权合并
        combined = []
        for i in range(len(self._documents)):
            score = (
                self.DENSE_WEIGHT * dense_scores[i] +
                self.SPARSE_WEIGHT * sparse_scores[i]
            )
            if score > 0.01:
                combined.append((i, float(score)))

        combined.sort(key=lambda x: x[1], reverse=True)
        return combined[:top_k]

    def _dense_search(self, query: str) -> list[float]:
        """Dense 语义检索"""
        self._ensure_dense()
        model = self.model
        if model is None or self._dense_embeddings is None:
            return [0.0] * len(self._documents)

        qvec = model.encode(
            self.QUERY_INSTRUCTION + query,
            normalize_embeddings=True,
        )
        # 余弦相似度 (已归一化 → 点积)
        similarities = np.dot(self._dense_embeddings, qvec)
        return [float(s) for s in similarities]

    def _bm25_score(self, query: str) -> list[float]:
        """Sparse 词法匹配"""
        return self._bm25.score(query)

    # ---- 状态 ----

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def clear(self):
        """清空索引（测试用）"""
        self._documents = []
        self._dense_embeddings = None
        self._bm25 = _BM25()
        self._fallback = _TFIDFFallback()
        self._dirty = False
