# -*- coding: utf-8 -*-
"""
CostTracker —— Token 用量和成本统计
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from harness.observability.log_context import current_trace_id

logger = logging.getLogger(__name__)

# 已经为「未登记模型」告过警的名字，避免每次调用刷屏（见 price_for）。
_WARNED_MODELS: set = set()


@dataclass
class CostReport:
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # KV-cache 命中的输入 token 数（这部分按缓存价计费，是「省了多少钱」的度量，
    # 也是验证 L1「稳定前缀 + 可变尾段」优化是否真的生效的唯一指标）
    cached_tokens: int = 0
    total_cost: float = 0.0
    by_model: dict = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率 = 命中 token / 输入 token"""
        if not self.input_tokens:
            return 0.0
        return round(self.cached_tokens / self.input_tokens, 4)


class CostTracker:
    """Token 用量和成本统计

    计价口径（三处必须一致，否则「谁的数字都不敢信」）：
      本文件的 record()、estimate_cost_usd()，以及可观测性后台的展示。
    """

    # 缓存命中部分的输入单价 = 普通输入价的 10%。
    # DeepSeek 与 Agnes 的官方计费规则相同（缓存命中/未命中是两个价目），
    # 这不是估算系数而是厂商公布的比例。个别模型若公布别的比例，
    # 在 PRICING 里给它单独写 "cached" 覆盖即可。
    CACHED_INPUT_RATIO = 0.1

    # 单价单位：美元 / 百万 token。键 "cached" 可省略，省略即按
    # CACHED_INPUT_RATIO × input 计。
    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "qwen-plus": {"input": 0.50, "output": 2.00},
        "qwen-max": {"input": 2.00, "output": 8.00},
        "claude-opus-4-5": {"input": 15.00, "output": 75.00},
        "deepseek-v3": {"input": 0.27, "output": 1.10},
        "deepseek-r1": {"input": 0.55, "output": 2.19},
        # agnes 系列（本项目当前主力模型）。**必须显式登记**：此前缺这一项，
        # 前缀回退三个分支全不匹配 → 落到最末的兜底价 $1/$4，比官方刊例
        # （输入 $0.05 / 输出 $0.15）整整贵 21 倍，后台报 $2.48 而实际花费 $0。
        "agnes-3.0-flash": {"input": 0.05, "output": 0.15, "cached": 0.005},
        "agnes-3.0-pro": {"input": 0.30, "output": 0.90, "cached": 0.03},
        # 注意：deepseek / qwen / agnes / claude 系列按模型名前缀匹配
        #（见 price_for 的前缀回退），版本后缀换名不必逐个登记。
    }

    def __init__(self):
        self._usage: dict[str, CostReport] = {}  # trace_id → CostReport
        # trace_id → 待入库的缓存命中量。由 extract_usage 写入、record 取走。
        # 设计动机：cache 采集原本要改 runtime.py 的调用点，但该文件正被
        # 另一分支占用，实测会造成合并冲突（git apply --check 失败）。
        # runtime 的调用序列是同一同步代码块内 extract_usage → record，
        # 且二者处于同一需求执行线程（trace_id 已绑定），因此按 trace_id
        # 中转是安全的；trace_id 全局唯一，不会跨 trace 串台。
        self._pending_cache: dict[str, int] = {}

    # 未登记模型的兜底价。宁可偏高也不要偏低（低估成本会让人以为优化有效），
    # 但每次命中都要打警告 —— 「按兜底价算出来的成本」和「真实成本」是两回事，
    # 静默兜底会让后台的数字看起来精确、实际没有依据。
    FALLBACK_PRICING = {"input": 1.0, "output": 4.0}

    @classmethod
    def price_for(cls, model: str) -> dict:
        """取模型单价（美元 / 百万 token）。

        未登记的模型走前缀回退，不落到任意默认价 —— 各家模型的
        版本后缀很多，逐个登记不现实，但也不能因此按最贵的算。

        返回值一定是含 input / output / cached 三个键的完整价目：cached 由
        CACHED_INPUT_RATIO 补齐，调用方不必各自判断缺键。
        """
        model = model or ""
        pricing = cls.PRICING.get(model)
        if pricing is None:
            for prefix, key in (
                ("deepseek-", "deepseek-v3"),
                ("qwen-", "qwen-plus"),
                ("gpt-4o", "gpt-4o"),
                ("agnes-", "agnes-3.0-flash"),
                ("claude-", "claude-opus-4-5"),
            ):
                if model.startswith(prefix):
                    pricing = cls.PRICING.get(key)
                    break
        if pricing is None:
            # 每个模型名只警告一次：price_for 是每次 LLM 调用的必经之路，
            # 不去重会变成刷屏，反而没人看。
            if model and model not in _WARNED_MODELS:
                _WARNED_MODELS.add(model)
                logger.warning(
                    "[CostTracker] 未登记模型 %r，按兜底价 %s 计 —— 该模型的实际"
                    "单价请补进 PRICING，否则后台成本只是量级参考",
                    model, cls.FALLBACK_PRICING,
                )
            pricing = cls.FALLBACK_PRICING
        if "cached" not in pricing:
            pricing = dict(pricing)
            pricing["cached"] = pricing["input"] * cls.CACHED_INPUT_RATIO
        return pricing

    @staticmethod
    def _cost_of(pricing: dict, input_tokens: int, output_tokens: int,
                 cached_tokens: int = 0) -> float:
        """按价目算一次调用的美元成本（缓存命中部分单独折价）。

        ⚠️ `input_tokens` 是**含**命中量的总量（供应商口径就是 prompt_tokens），
        所以命中部分必须先从全价里扣出来、再按缓存价计。此前直接
        `input × 价 + output × 价`，把已经命中的部分也按全价收了一遍 ——
        实测命中率 46% 时，输入成本因此高估约 41%。
        """
        cached = max(0, min(int(cached_tokens or 0), int(input_tokens or 0)))
        fresh = max(0, int(input_tokens or 0) - cached)
        cached_price = pricing.get("cached")
        if cached_price is None:  # 价目没写 cached → 按统一比例，与 price_for 一致
            cached_price = pricing["input"] * CostTracker.CACHED_INPUT_RATIO
        return ((fresh / 1_000_000) * pricing["input"]
                + (cached / 1_000_000) * cached_price
                + (int(output_tokens or 0) / 1_000_000) * pricing["output"])

    def record(self, trace_id: str, input_tokens: int, output_tokens: int,
               model: str = "", cached_tokens: Optional[int] = None):
        # 显式传入优先；未传（None）时取 extract_usage 在本 trace 留下的命中量。
        # 直调 record 的旧代码/测试不传 → 拿不到就归 0，行为与之前完全一致。
        if cached_tokens is None:
            cached_tokens = self._pending_cache.pop(trace_id, 0)
        # 命中量是 input 的子集：供应商若上报了该字段但数值异常（> input），
        # 按 input 封顶，否则计价里会出现负的"未命中部分"，成本被算成负数。
        cached_tokens = max(0, min(int(cached_tokens or 0), int(input_tokens or 0)))
        # 计价口径统一走 price_for：可观测性后台的单次成本与这里的累计成本
        # 必须是同一套算法，否则两边数字对不上、谁都不敢信。
        pricing = self.price_for(model)
        cost = self._cost_of(pricing, input_tokens, output_tokens, cached_tokens)

        if trace_id not in self._usage:
            self._usage[trace_id] = CostReport()

        report = self._usage[trace_id]
        report.total_tokens += input_tokens + output_tokens
        report.input_tokens += input_tokens
        report.output_tokens += output_tokens
        report.cached_tokens += cached_tokens
        report.total_cost += cost

        if model:
            if model not in report.by_model:
                report.by_model[model] = {"tokens": 0, "cost": 0.0, "cached": 0}
            report.by_model[model]["tokens"] += input_tokens + output_tokens
            report.by_model[model]["cached"] += cached_tokens
            report.by_model[model]["cost"] += cost

    def get_report(self, trace_id: str) -> CostReport:
        return self._usage.get(trace_id, CostReport())

    def clear(self, trace_id: str = None):
        """清理用量记录。trace_id 为 None 时清空全部。

        建议在 trace 结束时（end_trace）随 tracer 联动调用，
        避免 _usage 按 trace_id 无界累积。
        """
        if trace_id is None:
            self._usage.clear()
            self._pending_cache.clear()
        else:
            self._usage.pop(trace_id, None)
            self._pending_cache.pop(trace_id, None)

    def extract_usage(self, response_usage: dict, provider: str) -> tuple:
        """从 LLM API 响应中提取 usage

        顺手把 KV-cache 命中量挂到 _pending_cache[当前 trace]，由随后的
        record() 自动取走 —— 这样 cache 采集**不需要改 runtime.py**。
        无 trace 上下文时（单测直调）跳过缓存，行为与原先一致。
        """
        if not response_usage:
            return 0, 0
        if provider == "anthropic_compatible":
            result = (
                response_usage.get("input_tokens", 0),
                response_usage.get("output_tokens", 0),
            )
        else:
            result = (
                response_usage.get("prompt_tokens", 0),
                response_usage.get("completion_tokens", 0),
            )

        trace_id = current_trace_id()
        if trace_id != "-":
            self._pending_cache[trace_id] = self.extract_cache_hit(response_usage)
        return result

    @staticmethod
    def extract_cache_hit(response_usage: dict) -> int:
        """从 usage 中提取 KV-cache 命中的输入 token 数

        兼容三种供应商格式（实测本项目走第一条）：
        - OpenAI 兼容：    usage.prompt_tokens_details.cached_tokens
        - DeepSeek：       usage.prompt_cache_hit_tokens
        - Anthropic 兼容： usage.cache_read_input_tokens

        取不到时返回 0：命中率会因此被**低估**，但绝不会高估。
        宁可低估，也不要拿一个看起来很漂亮的假数字。

        staticmethod：可观测性后台（trace_writer）要按同一套规则落库，
        必须是同一个实现 —— 两边各写一份，展示的命中量与累计成本里的
        命中量迟早会不一致。
        """
        if not response_usage:
            return 0

        details = response_usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        if cached:
            return int(cached)

        hit = response_usage.get("prompt_cache_hit_tokens")
        if hit:
            return int(hit)

        read = response_usage.get("cache_read_input_tokens")
        if read:
            return int(read)

        return 0


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int,
                      cached_tokens: int = 0) -> float:
    """单次 LLM 调用的估算成本（美元）。

    CostTracker.record 只给累计值，而可观测性后台要的是「这次调用花多少」，
    因此把计价单独开放出来 —— 两处共用 price_for 与 _cost_of，口径不会走偏。
    `cached_tokens` 必须透传：不传的话后台的单次成本会把缓存命中部分按全价算，
    与累计成本（record）对不上。
    """
    if not model or (not input_tokens and not output_tokens):
        return 0.0
    pricing = CostTracker.price_for(model)
    return CostTracker._cost_of(pricing, input_tokens, output_tokens, cached_tokens)


# 进程级共享实例。
#
# 为什么需要它：成本要在**所有** LLM 调用上累计（编码 / 规划 / 验收 / 修复 /
# AC 翻译），而记账的唯一收口点是 `trace_writer.record_llm_turn`（每条调用都
# 经过它）。它在调用栈深处，拿不到「某个需求自己 new 出来的」CostTracker 实例。
# 于是改为按 trace_id 键共享一个实例：trace_id 全局唯一，不会串台，
# 每个 trace 结束时由 tracer.end_trace 调 clear(trace_id) 回收。
_SHARED: Optional[CostTracker] = None


def shared_cost_tracker() -> CostTracker:
    """取进程级共享的 CostTracker（懒加载）。"""
    global _SHARED
    if _SHARED is None:
        _SHARED = CostTracker()
    return _SHARED
