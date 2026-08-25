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
                        self._model = SentenceTransformer(self.MODEL_NAME, device='cpu')
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
        """确保 dense embeddings 已计算（模型未就绪时降级到 TF-IDF）"""
        if self._dirty and self._documents:
            # 触发延迟加载：必须访问 self.model（property），而非裸属性 self._model，
            # 否则模型永远不加载、被永久降级为 TF-IDF
            model = self.model
            if model is not None and not self._use_fallback:
                logger.debug(f"编码 {len(self._documents)} 条文档的 dense embeddings ...")
                self._dense_embeddings = model.encode(
                    self._documents,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                logger.debug(f"编码完成: shape={self._dense_embeddings.shape}")
            else:
                # 模型加载失败（property 已置 _use_fallback=True）→ TF-IDF
                logger.info("BGE-M3 模型不可用，本次检索使用 TF-IDF 降级方案")
                self._fallback.fit(self._documents)
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


# ==================== pgvector 向量检索器 ====================

class PGVectorRetriever:
    """PostgreSQL + pgvector 向量检索器

    特性:
    - 向量持久化在 PG agent_memory_vectors 表（HNSW 索引），重启不丢失
    - 增量 upsert（不再全量重建索引）
    - Dense 检索走 pgvector SQL（ORDER BY embedding <=> query_vec）
    - BM25 Sparse 仍用内存（轻量）
    - 混合打分: 0.6 * dense + 0.4 * sparse
    - 降级回退: PG/pgvector 不可用时回退到 BGEM3Retriever
    - 接口和 BGEM3Retriever 完全一致（index + search）
    """

    QUERY_INSTRUCTION = "为这个需求找到相关的历史经验："
    MODEL_NAME = "BAAI/bge-m3"
    DENSE_WEIGHT = 0.6
    SPARSE_WEIGHT = 0.4

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._bm25 = _BM25()
        self._documents: list[str] = []  # 内存中的文档文本（用于 BM25）
        self._memory_ids: list[int] = []  # 对应的 memory_id（用于映射 doc_index → memory_id）
        self._use_fallback = False
        self._fallback = BGEM3Retriever()

    @property
    def model(self):
        """延迟加载 BGE-M3 模型（线程安全）"""
        if self._model is None and not self._use_fallback:
            with self._model_lock:
                if self._model is None and not self._use_fallback:
                    try:
                        from sentence_transformers import SentenceTransformer
                        logger.info(f"加载 BGE-M3 模型: {self.MODEL_NAME} ...")
                        self._model = SentenceTransformer(self.MODEL_NAME, device='cpu')
                        logger.info("BGE-M3 模型加载完成")
                    except ImportError:
                        logger.warning(
                            "sentence-transformers 未安装，pgvector 检索降级到 TF-IDF"
                        )
                        self._use_fallback = True
                    except Exception as e:
                        logger.warning(f"BGE-M3 加载失败 ({e})，降级到 TF-IDF")
                        self._use_fallback = True
        return self._model

    def index(self, documents: list[str], memory_ids: list[int] = None, user_id: int = 0):
        """建立/更新索引

        pgvector 模式下:
        - 如果 memory_ids 不为空,执行增量 upsert（只编码新文档）
        - 如果 memory_ids 为空,执行全量重建（从 DB 加载）

        Args:
            documents: 文档文本列表
            memory_ids: 对应的 memory_id（可选,增量 upsert 时必须）
            user_id: 用户 ID（用于过滤）
        """
        self._documents = documents
        if memory_ids:
            self._memory_ids = memory_ids
        else:
            self._memory_ids = []

        if not documents:
            self._bm25 = _BM25()
            return

        # BM25 内存索引（轻量,每次都重建）
        self._bm25.fit(documents)

        # pgvector 增量 upsert（只处理新 memory_id）
        if memory_ids and self.model is not None:
            self._upsert_vectors(documents, memory_ids)

    def _upsert_vectors(self, documents: list[str], memory_ids: list[int]):
        """将文档向量增量 upsert 到 pgvector"""
        try:
            from models import SessionLocal, AgentMemoryVector
            from sqlalchemy import text

            db = SessionLocal()
            for doc, mem_id in zip(documents, memory_ids):
                # 检查是否已存在（embedding_text 为 ORM 映射列，PG/SQLite 通用）
                existing = db.query(AgentMemoryVector).filter_by(memory_id=mem_id).first()
                if existing and existing.embedding_text is not None:
                    continue  # 已有向量,跳过

                # 编码文档
                vec = self.model.encode([doc], normalize_embeddings=True, show_progress_bar=False)[0]
                vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

                if existing:
                    # 更新已有记录的向量（CAST 避免 CAST(:vec AS vector) 被 SQLAlchemy 误解析）
                    db.execute(
                        text(
                            "UPDATE agent_memory_vectors "
                            "SET embedding = CAST(:vec AS vector), embedding_text = :vec "
                            "WHERE id = :id"
                        ),
                        {"vec": vec_str, "id": existing.id}
                    )
                else:
                    # 插入新记录
                    db.execute(
                        text(
                            "INSERT INTO agent_memory_vectors "
                            "(memory_id, user_id, embedding, embedding_text) "
                            "VALUES (:mid, :uid, CAST(:vec AS vector), :vec)"
                        ),
                        {"mid": mem_id, "uid": 0, "vec": vec_str}
                    )
            db.commit()
            db.close()
            logger.debug(f"pgvector upsert 完成: {len(documents)} 条文档")

        except Exception as e:
            logger.warning(f"pgvector upsert 失败（BM25 仍可用）: {e}")

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        """混合检索 (Dense + Sparse)

        Returns:
            [(doc_index, combined_score), ...]  按分数降序
            doc_index 对应 index() 传入的 documents 列表索引
        """
        if not self._documents:
            return []

        # 降级模式
        if self._use_fallback or self.model is None:
            return self._fallback_search(query, top_k)

        # Dense 检索: 走 pgvector SQL
        dense_results = self._dense_search_pg(query, top_k)

        # Sparse 检索: BM25 内存
        sparse_scores = self._bm25.score(query)

        # 合并打分
        # dense_results: {memory_id: score} 需要映射回 doc_index
        dense_by_index = {}
        if self._memory_ids and dense_results:
            for mem_id, score in dense_results.items():
                if mem_id in self._memory_ids:
                    idx = self._memory_ids.index(mem_id)
                    dense_by_index[idx] = score

        combined = []
        for i in range(len(self._documents)):
            dense_score = dense_by_index.get(i, 0.0)
            sparse_score = sparse_scores[i] if i < len(sparse_scores) else 0.0
            score = self.DENSE_WEIGHT * dense_score + self.SPARSE_WEIGHT * sparse_score
            if score > 0.01:
                combined.append((i, float(score)))

        combined.sort(key=lambda x: x[1], reverse=True)
        return combined[:top_k]

    def _dense_search_pg(self, query: str, top_k: int) -> dict:
        """pgvector Dense 检索

        Returns:
            {memory_id: score} 字典
        """
        try:
            from models import engine
            from sqlalchemy import text

            qvec = self.model.encode(
                self.QUERY_INSTRUCTION + query,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            vec_str = "[" + ",".join(f"{x:.8f}" for x in qvec) + "]"

            with engine.connect() as conn:
                # 只搜索当前 memory_ids 对应的向量
                if self._memory_ids:
                    id_list = ",".join(str(mid) for mid in self._memory_ids)
                    result = conn.execute(text(f"""
                        SELECT memory_id, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                        FROM agent_memory_vectors
                        WHERE memory_id IN ({id_list})
                        ORDER BY embedding <=> CAST(:vec AS vector)
                        LIMIT :top_k
                    """), {"vec": vec_str, "top_k": top_k})
                else:
                    result = conn.execute(text("""
                        SELECT memory_id, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                        FROM agent_memory_vectors
                        ORDER BY embedding <=> CAST(:vec AS vector)
                        LIMIT :top_k
                    """), {"vec": vec_str, "top_k": top_k})

                return {row[0]: float(row[1]) for row in result}

        except Exception as e:
            logger.warning(f"pgvector 检索失败（降级到 BM25）: {e}")
            return {}

    def _fallback_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """降级到 BGEM3Retriever 内存检索"""
        self._fallback.index(self._documents)
        return self._fallback.search(query, top_k)

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def clear(self):
        """清空内存索引（不删 DB 数据）"""
        self._documents = []
        self._memory_ids = []
        self._bm25 = _BM25()


# ==================== 检索器工厂 ====================

def create_retriever():
    """根据配置创建检索器

    - PostgreSQL 模式: PGVectorRetriever（增量 upsert + 持久化）
    - SQLite 模式: BGEM3Retriever（内存全量重建，原行为）
    """
    try:
        from config import settings
        if settings.IS_POSTGRES:
            logger.info("使用 PGVectorRetriever（pgvector 增量检索）")
            return PGVectorRetriever()
        else:
            logger.info("使用 BGEM3Retriever（内存检索）")
            return BGEM3Retriever()
    except Exception as e:
        logger.warning(f"检索器创建失败，降级到 BGEM3Retriever: {e}")
        return BGEM3Retriever()
