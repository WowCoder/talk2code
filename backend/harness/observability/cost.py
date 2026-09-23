# -*- coding: utf-8 -*-
"""
CostTracker —— Token 用量和成本统计
"""

from dataclasses import dataclass, field
from typing import Optional

from harness.observability.log_context import current_trace_id


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
    """Token 用量和成本统计"""

    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "qwen-plus": {"input": 0.50, "output": 2.00},
        "qwen-max": {"input": 2.00, "output": 8.00},
        "claude-opus-4-5": {"input": 15.00, "output": 75.00},
        "deepseek-v3": {"input": 0.27, "output": 1.10},
        "deepseek-r1": {"input": 0.55, "output": 2.19},
        # 注意：deepseek 系列按模型名前缀匹配（下方 record 中做前缀回退）
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

    def record(self, trace_id: str, input_tokens: int, output_tokens: int,
               model: str = "", cached_tokens: Optional[int] = None):
        # 显式传入优先；未传（None）时取 extract_usage 在本 trace 留下的命中量。
        # 直调 record 的旧代码/测试不传 → 拿不到就归 0，行为与之前完全一致。
        if cached_tokens is None:
            cached_tokens = self._pending_cache.pop(trace_id, 0)
        pricing = self.PRICING.get(model)
        if pricing is None:
            # 前缀回退：deepseek-v4-* 等未知版本按 deepseek-v3 计价，避免落到任意默认价
            if model.startswith("deepseek-"):
                pricing = self.PRICING.get("deepseek-v3", {"input": 0.27, "output": 1.10})
            elif model.startswith("qwen-"):
                pricing = self.PRICING.get("qwen-plus", {"input": 0.50, "output": 2.00})
            elif model.startswith("gpt-4o"):
                pricing = self.PRICING.get("gpt-4o", {"input": 2.50, "output": 10.00})
            else:
                pricing = {"input": 1.0, "output": 4.0}
        cost = (input_tokens / 1_000_000) * pricing["input"] + \
               (output_tokens / 1_000_000) * pricing["output"]

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

    def extract_cache_hit(self, response_usage: dict) -> int:
        """从 usage 中提取 KV-cache 命中的输入 token 数

        兼容三种供应商格式（实测本项目走第一条）：
        - OpenAI 兼容：    usage.prompt_tokens_details.cached_tokens
        - DeepSeek：       usage.prompt_cache_hit_tokens
        - Anthropic 兼容： usage.cache_read_input_tokens

        取不到时返回 0：命中率会因此被**低估**，但绝不会高估。
        宁可低估，也不要拿一个看起来很漂亮的假数字。
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
