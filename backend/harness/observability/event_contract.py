# -*- coding: utf-8 -*-
"""Agent 可观测性事件契约 —— 阶段与事件类型的**唯一定义处**。

为什么要有这个文件
------------------
此前「事件类型」被抄在了三个地方：trace_writer 的 KIND_* 常量、前端
KIND_LABEL / KIND_COLOR 两张硬编码 map、以及筛选 chip 的写死列表。
新增一个阶段要同步改三处，漏一处就是「后端写了、前端显示不出来」，
而前端那种 `?? k` 兜底会让它灰着躺在时间线上，看不出是新阶段还是脏数据。

现在：后端在这里定义，前端启动时从 `/api/admin/traces/contract` 拉。
新增类型只改这一个文件，前端自动拿到中文名、颜色和筛选入口。

向前兼容
--------
`KIND_*` 常量仍然从 trace_writer 导出（那边 re-export 本模块），
既有 `from harness.observability.trace_writer import KIND_MEMORY` 不用改。
"""

from __future__ import annotations

# ---------- 阶段 ----------
# 一条事件只属于一个阶段；阶段是时间线的粗粒度分组（规划 → 编码 → 验收 → 修复）。
STAGE_PLANNING = "planning"
STAGE_CODING = "coding"
STAGE_VERIFYING = "verifying"
STAGE_REPAIRING = "repairing"
STAGE_DELIVERING = "delivering"

# ---------- 事件类型 ----------
KIND_INTENT = "intent"
KIND_MEMORY = "memory"
KIND_CLARIFY = "clarify"
KIND_PLAN = "plan"
KIND_CONFIRM = "confirm"
KIND_CODING = "coding"
KIND_LLM_TURN = "llm_turn"
KIND_TOOL_CALL = "tool_call"
KIND_VERIFY = "verify"
KIND_REPAIR = "repair"
KIND_QUALITY_GATE = "quality_gate"
KIND_ROLLBACK = "rollback"
KIND_DELIVER = "deliver"

# ---------- 契约表 ----------
# color 用 oklch —— 与前端既有配色同一体系，深浅色模式下都稳定。
EVENT_KINDS = {
    KIND_INTENT: {"label": "意图", "color": "oklch(52% 0.15 250)", "stage": STAGE_PLANNING},
    KIND_MEMORY: {"label": "记忆", "color": "oklch(50% 0.15 300)", "stage": STAGE_PLANNING},
    KIND_CLARIFY: {"label": "澄清", "color": "oklch(62% 0.13 85)", "stage": STAGE_PLANNING},
    KIND_PLAN: {"label": "规划", "color": "oklch(55% 0.12 155)", "stage": STAGE_PLANNING},
    KIND_CONFIRM: {"label": "确认", "color": "oklch(55% 0.12 155)", "stage": STAGE_PLANNING},
    KIND_CODING: {"label": "编码", "color": "oklch(55% 0.12 155)", "stage": STAGE_CODING},
    KIND_LLM_TURN: {"label": "LLM", "color": "oklch(62% 0.13 65)", "stage": STAGE_CODING},
    KIND_TOOL_CALL: {"label": "工具", "color": "oklch(58% 0.1 230)", "stage": STAGE_CODING},
    KIND_VERIFY: {"label": "验收", "color": "oklch(55% 0.12 155)", "stage": STAGE_VERIFYING},
    KIND_REPAIR: {"label": "修复", "color": "oklch(52% 0.15 35)", "stage": STAGE_REPAIRING},
    KIND_QUALITY_GATE: {"label": "质量门禁", "color": "oklch(52% 0.15 35)", "stage": STAGE_VERIFYING},
    KIND_ROLLBACK: {"label": "回滚", "color": "oklch(52% 0.15 35)", "stage": STAGE_REPAIRING},
    KIND_DELIVER: {"label": "交付", "color": "oklch(55% 0.12 155)", "stage": STAGE_DELIVERING},
}

EVENT_STAGES = {
    STAGE_PLANNING: {"label": "规划", "color": "oklch(50% 0.15 300)"},
    STAGE_CODING: {"label": "编码", "color": "oklch(55% 0.12 155)"},
    STAGE_VERIFYING: {"label": "验收", "color": "oklch(55% 0.12 155)"},
    STAGE_REPAIRING: {"label": "修复", "color": "oklch(52% 0.15 35)"},
    STAGE_DELIVERING: {"label": "交付", "color": "oklch(55% 0.12 155)"},
}

# 前端筛选 chip：细粒度过滤入口。除过程性事件（LLM / 工具）外，
# 把三类**结论性**事件也放进来 —— 排查时最常见的问题是「验收为什么没过」
# 「第几轮修了什么」，能一键筛出来比在时间线里翻要快得多。
# 放进契约是为了新增类型时不必再回头改前端的 chip 列表。
FILTER_KINDS = [KIND_LLM_TURN, KIND_TOOL_CALL, KIND_PLAN, KIND_VERIFY,
                KIND_REPAIR, KIND_MEMORY]

# 未登记类型的兜底（前端同款）：显示原名 + 中性灰，而不是崩掉。
_FALLBACK_KIND = {"label": None, "color": "oklch(60% 0.02 70)", "stage": None}
_FALLBACK_STAGE = {"label": None, "color": "oklch(60% 0.02 70)"}


def kind_spec(kind: str) -> dict:
    """取事件类型定义；未登记的类型返回兜底，label 为 None 表示「显示原名」。"""
    return EVENT_KINDS.get(kind, _FALLBACK_KIND)


def stage_spec(stage: str) -> dict:
    return EVENT_STAGES.get(stage, _FALLBACK_STAGE)


def infer_stage_from_legacy(iteration) -> str:
    """旧日志（没有 stage 字段）的阶段推断。

    判据：编码循环（ToolCallLoop）的埋点传的是 `iteration + 1`（从 1 起），
    其余辅助链路（规划 / AC 翻译 / 验收 / 修复）不参与编码迭代 —— 历史上它们
    把 `0` 当哨兵传，现在统一改传 `None`。因此 iteration 为 0 或空的旧记录
    必然属于非编码链路（回填时一律归入验收阶段；辅助链路内部无法再细分，
    旧日志本就没有 stage 字段）。

    这个推断只用于回填历史数据 —— 新产生的日志一律自带 stage，不走这里。
    一旦 ToolCallLoop 改成从 0 起，存量数据会集体标错阶段，所以有守卫测试
    （test_event_contract.py）盯着那个调用形式。
    """
    return STAGE_CODING if (iteration or 0) >= 1 else STAGE_VERIFYING


def contract_payload() -> dict:
    """给前端的完整契约 —— /api/admin/traces/contract 的响应体。"""
    return {
        "kinds": {k: dict(v) for k, v in EVENT_KINDS.items()},
        "stages": {k: dict(v) for k, v in EVENT_STAGES.items()},
        "filter_kinds": list(FILTER_KINDS),
        "fallback_kind": dict(_FALLBACK_KIND),
        "fallback_stage": dict(_FALLBACK_STAGE),
    }


def unknown_kinds_in(kinds) -> list:
    """返回未在契约中登记的 kind —— 给守卫测试用，防止埋点写错名字。"""
    return sorted({k for k in kinds if k and k not in EVENT_KINDS})
