# -*- coding: utf-8 -*-
"""
CostTracker —— Token 用量和成本统计
"""

from dataclasses import dataclass, field


@dataclass
class CostReport:
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0
    by_model: dict = field(default_factory=dict)


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

    def record(self, trace_id: str, input_tokens: int, output_tokens: int, model: str = ""):
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
        report.total_cost += cost

        if model:
            if model not in report.by_model:
                report.by_model[model] = {"tokens": 0, "cost": 0.0}
            report.by_model[model]["tokens"] += input_tokens + output_tokens
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
        else:
            self._usage.pop(trace_id, None)

    def extract_usage(self, response_usage: dict, provider: str) -> tuple:
        """从 LLM API 响应中提取 usage"""
        if not response_usage:
            return 0, 0
        if provider == "anthropic_compatible":
            return (
                response_usage.get("input_tokens", 0),
                response_usage.get("output_tokens", 0),
            )
        else:
            return (
                response_usage.get("prompt_tokens", 0),
                response_usage.get("completion_tokens", 0),
            )
