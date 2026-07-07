# -*- coding: utf-8 -*-
"""
记忆检索评估集

评估 BGE-M3 / TF-IDF / BM25 三种检索方案在代码生成记忆场景下的表现。

指标:
- Recall@K:    前 K 条中命中了多少条相关记忆 (越高越好)
- Precision@K: 前 K 条中有多少条真正相关 (越高越好)
- MRR:         Mean Reciprocal Rank — 第一条相关记忆的排名倒数均值 (越高越好)
- NDCG@K:      归一化折损累计增益 (越高越好)

使用方式:
    cd backend && python tests/eval/test_memory_retrieval.py
    cd backend && python tests/eval/test_memory_retrieval.py --compare  # 对比三种方法
"""

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# 评估数据集
# 每条: (query, relevant_indices, description)
# relevant_indices 是预期应该匹配的记忆索引（在 memories 列表中的位置）


@dataclass
class EvalCase:
    """一条评估用例"""
    query: str                          # 用户新需求
    relevant_indices: list[int]         # 预期匹配的记忆索引
    description: str                    # 用例描述
    tags: list[str] = field(default_factory=list)  # 分类标签


# ==================== 记忆库 ====================

MEMORIES = [
    # 0: localStorage 持久化
    "做一个待办清单应用，支持添加、删除、标记完成，数据用 localStorage 持久化，刷新不丢失。"
    "关键教训: localStorage 存复杂对象必须先 JSON.stringify，否则变成 [object Object]"
    "可复用模式: CRUD 标准流程 — 读→渲染→事件→修改→持久化→重渲染",

    # 1: 表单验证
    "做一个用户注册表单页面，包含用户名、邮箱、密码、确认密码，带前端验证和错误提示。"
    "关键教训: 表单验证要同时处理 blur 和 submit 事件，只在一个地方验证会有遗漏"
    "可复用模式: 表单验证器模式 — 每个字段定义 rules 数组，统一 validate() 遍历",

    # 2: CSS 动画
    "做一个产品展示页面，卡片 hover 时有放大和阴影动画，进入视口时淡入。"
    "关键教训: CSS transform 动画性能远优于修改 width/height，后者会触发 reflow"
    "可复用模式: IntersectionObserver + CSS class toggle 实现视口触发动画",

    # 3: 暗黑模式
    "给现有应用添加暗黑模式切换功能，用户偏好保存到 localStorage，刷新后保持。"
    "关键教训: 系统级 prefers-color-scheme 和用户手动选择需要分层处理"
    "可复用模式: CSS 变量 + data-theme 属性切换全局配色方案",

    # 4: 数据表格
    "做一个数据管理表格，支持排序、筛选、分页，数据从 localStorage 读取。"
    "关键教训: 排序和筛选要同时作用于原始数据的副本，不能修改原始数据"
    "可复用模式: 数据管道模式 — 原始数据 → 筛选 → 排序 → 分页 → 渲染",

    # 5: 搜索功能
    "给列表页添加实时搜索功能，输入关键词即时筛选列表项。"
    "关键教训: 中文搜索需要处理拼音和模糊匹配，简单的 includes() 不够用"
    "可复用模式: 防抖输入 + 高亮匹配文本的搜索组件",

    # 6: 图片上传预览
    "做一个图片上传组件，支持拖拽上传、预览、裁剪。"
    "关键教训: FileReader.readAsDataURL 对大图片很慢，用 URL.createObjectURL 更快"
    "可复用模式: 拖拽区域 + 隐藏 input[type=file] + 预览列表的通用上传组件",

    # 7: 倒计时
    "做一个番茄钟倒计时应用，25 分钟工作 + 5 分钟休息，有声音提醒。"
    "关键教训: setInterval 在页面后台运行时会降频，倒计时应基于 Date.now() 差值计算"
    "可复用模式: 剩余时间 = 结束时间戳 - Date.now()，用 requestAnimationFrame 驱动更新",

    # 8: 拖拽排序
    "给任务列表添加拖拽排序功能，排序结果持久化到 localStorage。"
    "关键教训: 移动端 touch 事件和桌面端 mouse 事件需要统一抽象处理"
    "可复用模式: dragstart/dragover/drop 事件 + 数据索引交换的通用拖拽组件",

    # 9: 图表展示
    "做一个数据统计 Dashboard，用 Chart.js 展示柱状图、饼图、折线图。"
    "关键教训: Chart.js 需要 Canvas 容器有明确的宽高，否则渲染异常"
    "可复用模式: 配置对象工厂模式生成不同类型图表的通用配置",
]


# ==================== 评估用例 ====================

EVAL_CASES = [
    # ---- 精确关键词匹配 ----
    EvalCase(
        query="做一个任务清单，需要 localStorage 存储数据",
        relevant_indices=[0],
        description="localStorage 精确匹配",
        tags=["keyword", "localStorage"],
    ),
    EvalCase(
        query="给表单添加实时验证",
        relevant_indices=[1],
        description="表单验证精确匹配",
        tags=["keyword", "表单"],
    ),

    # ---- 语义匹配（中文字面不同但语义相同） ----
    EvalCase(
        query="做一个日程管理工具，可以添加和删除事件，数据不能丢失",
        relevant_indices=[0, 8],  # 待办清单(CRUD+持久化) + 拖拽排序
        description="语义匹配: 日程管理 → 待办清单 + 拖拽",
        tags=["semantic", "CRUD"],
    ),
    EvalCase(
        query="做一个会员信息录入页面，需要验证手机号和邮箱格式",
        relevant_indices=[1],
        description="语义匹配: 会员录入 → 表单验证",
        tags=["semantic", "表单"],
    ),
    EvalCase(
        query="做一个卡片列表，滚动到可视区域时播放进入动画",
        relevant_indices=[2],
        description="语义匹配: 滚动动画 → CSS 视口动画",
        tags=["semantic", "动画"],
    ),

    # ---- 跨语言/技术术语 ----
    EvalCase(
        query="用 IndexedDB 代替 localStorage 存储大量数据",
        relevant_indices=[0, 4],  # localStorage 持久化经验 + 数据表格
        description="技术术语: IndexedDB → localStorage 相关记忆",
        tags=["technical", "storage"],
    ),
    EvalCase(
        query="怎么让页面支持跟随系统的明暗主题自动切换",
        relevant_indices=[3],
        description="技术术语: 明暗主题 → 暗黑模式",
        tags=["technical", "theme"],
    ),

    # ---- 多记忆匹配 ----
    EvalCase(
        query="做一个任务看板，卡片可以拖拽到不同列，数据保存到本地",
        relevant_indices=[0, 8, 4],  # CRUD+持久化 + 拖拽 + 数据表格
        description="复合需求: 任务看板(CRUD+拖拽+展示)",
        tags=["composite", "multiple"],
    ),
    EvalCase(
        query="做一个后台 Dashboard，有数据表格、搜索筛选、统计图表",
        relevant_indices=[4, 5, 9],  # 表格 + 搜索 + 图表
        description="复合需求: Dashboard(表格+搜索+图表)",
        tags=["composite", "multiple"],
    ),

    # ---- 不应匹配（负样本验证） ----
    EvalCase(
        query="给按钮添加 loading 状态，发送请求时显示加载动画",
        relevant_indices=[],  # 没有完全匹配的记忆（最近的可能是表单相关）
        description="无匹配: loading 状态（记忆库无相关）",
        tags=["negative"],
    ),
    EvalCase(
        query="实现一个 WebSocket 实时聊天功能",
        relevant_indices=[],  # 记忆库中没有 WebSocket 相关内容
        description="无匹配: WebSocket 聊天（技术栈不同）",
        tags=["negative"],
    ),
]


# ==================== 评估指标 ====================

def recall_at_k(relevant: set, retrieved: list, k: int) -> float:
    """Recall@K: 前 K 条中命中了多少比例的相关项"""
    if not relevant:
        return 1.0  # 没有相关项 → 完美（不应该召回任何）
    hits = len(set(retrieved[:k]) & relevant)
    return hits / len(relevant)


def precision_at_k(relevant: set, retrieved: list, k: int) -> float:
    """Precision@K: 前 K 条中有多少比例真正相关"""
    if k == 0:
        return 0.0
    hits = len(set(retrieved[:k]) & relevant)
    return hits / k


def mrr(relevant: set, ranked: list) -> float:
    """MRR: 第一条相关记忆的排名倒数"""
    if not relevant:
        return 1.0  # 没有相关项 → 完美
    for i, idx in enumerate(ranked):
        if idx in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(relevant: set, ranked: list, k: int) -> float:
    """NDCG@K: 归一化折损累计增益"""
    if not relevant or k == 0:
        return 1.0 if not relevant else 0.0

    # DCG
    dcg = 0.0
    for i, idx in enumerate(ranked[:k]):
        rel = 1.0 if idx in relevant else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 因为 log2(1)=0

    # IDCG (理想排序: 所有相关项排最前)
    idcg = 0.0
    for i in range(min(len(relevant), k)):
        idcg += 1.0 / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


# ==================== 评估运行器 ====================

def evaluate_retriever(retriever, memories: list[str], cases: list[EvalCase],
                       top_k: int = 5, name: str = "Unknown") -> dict:
    """
    运行评估。

    Args:
        retriever: 有 .index(docs) 和 .search(query, top_k) 方法的检索器
        memories: 记忆文本列表
        cases: 评估用例列表
        top_k: 评估的 K 值
        name: 检索器名称

    Returns:
        指标汇总字典
    """
    retriever.index(memories)

    results = {
        "name": name,
        f"recall@{top_k}": [],
        f"precision@{top_k}": [],
        "mrr": [],
        f"ndcg@{top_k}": [],
        "latency_ms": [],
        "by_tag": defaultdict(lambda: {"recall": [], "mrr": []}),
        "case_details": [],
    }

    for case in cases:
        start = time.perf_counter()
        scored = retriever.search(case.query, top_k=top_k)
        elapsed = (time.perf_counter() - start) * 1000

        # 提取排名索引（去掉分数）
        ranked = [idx for idx, _ in scored]
        relevant = set(case.relevant_indices)

        r_k = recall_at_k(relevant, ranked, top_k)
        p_k = precision_at_k(relevant, ranked, top_k)
        m = mrr(relevant, ranked)
        n = ndcg_at_k(relevant, ranked, top_k)

        results[f"recall@{top_k}"].append(r_k)
        results[f"precision@{top_k}"].append(p_k)
        results["mrr"].append(m)
        results[f"ndcg@{top_k}"].append(n)
        results["latency_ms"].append(elapsed)

        for tag in case.tags:
            results["by_tag"][tag]["recall"].append(r_k)
            results["by_tag"][tag]["mrr"].append(m)

        results["case_details"].append({
            "query": case.query[:80],
            "description": case.description,
            "expected": sorted(case.relevant_indices),
            "got": ranked[:top_k],
            "recall": f"{r_k:.2f}",
            "mrr": f"{m:.2f}",
            "tags": case.tags,
        })

    return results


def summarize_results(results: dict) -> str:
    """生成可读的评估报告"""
    lines = [f"\n{'='*60}",
             f"  检索方案: {results['name']}",
             f"{'='*60}"]

    # 整体指标
    k = 5  # hardcoded for now
    lines.append(f"\n## 整体指标 (K={k})")
    for metric in [f"recall@{k}", f"precision@{k}", "mrr", f"ndcg@{k}"]:
        vals = results[metric]
        if vals:
            avg = sum(vals) / len(vals)
            lines.append(f"  {metric:15s}: {avg:.4f}  (n={len(vals)})")

    # 延迟
    lats = results["latency_ms"]
    if lats:
        lines.append(f"  {'延迟':15s}: avg={sum(lats)/len(lats):.1f}ms  "
                     f"p50={sorted(lats)[len(lats)//2]:.1f}ms  "
                     f"p99={sorted(lats)[int(len(lats)*0.99)]:.1f}ms")

    # 按标签分类
    lines.append(f"\n## 按场景分类")
    for tag, metrics in sorted(results["by_tag"].items()):
        r_avg = sum(metrics["recall"]) / len(metrics["recall"]) if metrics["recall"] else 0
        m_avg = sum(metrics["mrr"]) / len(metrics["mrr"]) if metrics["mrr"] else 0
        lines.append(f"  {tag:15s}: Recall@{k}={r_avg:.3f}  MRR={m_avg:.3f}  (n={len(metrics['recall'])})")

    # 逐用例详情
    lines.append(f"\n## 逐用例详情")
    for i, d in enumerate(results["case_details"]):
        flag = "✅" if float(d["recall"]) >= 0.5 else "⚠️ " if float(d["recall"]) > 0 else "❌"
        lines.append(
            f"  {flag} [{i}] {d['description']}"
        )
        lines.append(f"      期望: {d['expected']} → 实际: {d['got']}  "
                     f"R@{k}={d['recall']} MRR={d['mrr']}")

    return "\n".join(lines)


# ==================== 主程序 ====================

def run_eval():
    """运行评估"""
    import argparse
    ap = argparse.ArgumentParser(description="记忆检索评估")
    ap.add_argument("--compare", action="store_true", help="对比 BGE-M3 / TF-IDF / BM25")
    ap.add_argument("--bge", action="store_true", help="强制使用 BGE-M3（下载模型）")
    args = ap.parse_args()

    results_all = []

    # ---- BM25 独立检索（仅 sparse） ----
    from harness.state.memory_retriever import _BM25

    class BM25OnlyRetriever:
        def __init__(self):
            self._bm25 = _BM25()
            self._docs = []

        def index(self, docs):
            self._docs = docs
            self._bm25.fit(docs)

        def search(self, query, top_k=5):
            scores = self._bm25.score(query)
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            return [(i, s) for i, s in ranked[:top_k] if s > 0]

    print("\n[1/4] 评估 BM25 (Sparse Only) ...")
    bm25_retriever = BM25OnlyRetriever()
    bm25_results = evaluate_retriever(bm25_retriever, MEMORIES, EVAL_CASES, name="BM25")
    results_all.append(bm25_results)

    # ---- TF-IDF（旧方案） ----
    from harness.state.memory_retriever import _TFIDFFallback

    class TFIDFOnlyRetriever:
        def __init__(self):
            self._tfidf = _TFIDFFallback()

        def index(self, docs):
            self._tfidf.fit(docs)

        def search(self, query, top_k=5):
            return self._tfidf.search(query, top_k)

    print("[2/4] 评估 TF-IDF (旧方案) ...")
    tfidf_retriever = TFIDFOnlyRetriever()
    tfidf_results = evaluate_retriever(tfidf_retriever, MEMORIES, EVAL_CASES, name="TF-IDF (旧方案)")
    results_all.append(tfidf_results)

    # ---- BGE-M3 混合（新方案） ----
    from harness.state.memory_retriever import BGEM3Retriever

    if args.bge:
        print("[3/4] 评估 BGE-M3 混合检索 (加载模型, 约 5-8s) ...")
        bge_retriever = BGEM3Retriever()
        # 确保不用降级
        assert not bge_retriever._use_fallback, "BGE-M3 不可用"
        bge_results = evaluate_retriever(bge_retriever, MEMORIES, EVAL_CASES, name="BGE-M3 Hybrid")
    else:
        print("[3/4] 模拟 BGE-M3 混合检索 (Dense 不可用, 自动降级 TF-IDF) ...")
        bge_retriever = BGEM3Retriever()
        bge_retriever._use_fallback = True
        bge_results = evaluate_retriever(bge_retriever, MEMORIES, EVAL_CASES,
                                         name="BGE-M3 Hybrid (降级 TF-IDF)")
    results_all.append(bge_results)

    # ---- 随机基线 ----
    import random

    class RandomRetriever:
        def __init__(self, seed=42):
            self._docs = []
            self._rng = random.Random(seed)

        def index(self, docs):
            self._docs = docs

        def search(self, query, top_k=5):
            indices = list(range(len(self._docs)))
            self._rng.shuffle(indices)
            return [(i, 0.0) for i in indices[:top_k]]

    print("[4/4] 评估 Random 基线 ...")
    random_retriever = RandomRetriever()
    random_results = evaluate_retriever(random_retriever, MEMORIES, EVAL_CASES, name="Random 基线")
    results_all.append(random_results)

    # ---- 打印报告 ----
    for r in results_all:
        print(summarize_results(r))

    # ---- 对比汇总 ----
    k = 5
    print(f"\n{'='*60}")
    print(f"  对比汇总 (Recall@{k} / MRR)")
    print(f"{'='*60}")
    print(f"  {'方案':<30s} {'Recall@5':>8s}  {'MRR':>8s}  {'P@5':>8s}  {'延迟':>8s}")
    print(f"  {'-'*60}")
    for r in results_all:
        rec = sum(r[f"recall@{k}"]) / len(r[f"recall@{k}"]) if r[f"recall@{k}"] else 0
        mr = sum(r["mrr"]) / len(r["mrr"]) if r["mrr"] else 0
        prec = sum(r[f"precision@{k}"]) / len(r[f"precision@{k}"]) if r[f"precision@{k}"] else 0
        lat = sum(r["latency_ms"]) / len(r["latency_ms"]) if r["latency_ms"] else 0
        print(f"  {r['name']:<30s} {rec:8.4f}  {mr:8.4f}  {prec:8.4f}  {lat:6.1f}ms")

    # ---- 结论 ----
    best = max(results_all, key=lambda r: sum(r[f"recall@{k}"]) / max(len(r[f"recall@{k}"]), 1))
    print(f"\n  最佳方案: {best['name']}")
    print(f"{'='*60}\n")

    return results_all


if __name__ == "__main__":
    run_eval()
