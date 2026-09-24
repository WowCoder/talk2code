# -*- coding: utf-8 -*-
"""Agent 执行明细日志（开发排查用）—— 完整 LLM 参数 / 返回值 + 工具调用。

定位：**开发视角**。前端只展示用户能看懂的「Agent 做了什么」；而「LLM 到底收发了
什么」（messages / tools / thinking / 原始 response / usage）属于排查信息，写成本地
JSONL 供开发翻查，**默认关闭**（`AGENT_EXEC_LOG=1` 打开），避免生产环境无界增长。

输出：`<AGENT_EXEC_LOG_DIR>/<requirement_id>.jsonl`，每行一条 JSON。
- `kind=llm_turn`：一轮 LLM 调用（请求参数 + 原始返回）
- `kind=tool_call`：一次工具调用（name / arguments / result / blocked）

失败静默：日志绝不阻断主流程。
"""

from __future__ import annotations

import json
import os
import threading
import time

from harness.observability.logger import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
# 缓存的开关与目录（首次读取后固定；进程内一致）
_flags = None


def _load_flags() -> tuple:
    """读取 (enabled, dir)。优先 settings，缺失时退回环境变量。"""
    global _flags
    if _flags is not None:
        return _flags
    enabled, base = False, "logs/agent_exec"
    try:
        from config import settings
        enabled = bool(settings.AGENT_EXEC_LOG)
        base = settings.AGENT_EXEC_LOG_DIR or base
        if not os.path.isabs(base):
            base = str(settings.BACKEND_DIR / base)
    except Exception:
        enabled = os.getenv("AGENT_EXEC_LOG", "").strip().lower() in ("1", "true", "yes", "on")
        base = os.getenv("AGENT_EXEC_LOG_DIR", base)
    _flags = (enabled, base)
    return _flags


def is_enabled() -> bool:
    return _load_flags()[0]


def _write(requirement_id, record: dict):
    try:
        enabled, base = _load_flags()
        if not enabled:
            return
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, f"{requirement_id if requirement_id is not None else 'unknown'}.jsonl")
        record.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        logger.debug(f"[ExecLog] 写入失败（不阻断）: {e}")


def log_llm_turn(requirement_id, iteration, model, messages, tools, response,
                 thinking=None, latency_ms=None):
    """记录一轮 LLM 调用：完整请求参数 + 原始返回值。"""
    tool_calls = getattr(response, "tool_calls", None) or []
    _write(requirement_id, {
        "kind": "llm_turn",
        "iteration": iteration,
        "model": model,
        "thinking": thinking,
        "latency_ms": latency_ms,
        "request": {
            "messages": messages,
            "tools": tools,
        },
        "response": {
            "content": getattr(response, "content", None),
            "reasoning_content": getattr(response, "reasoning_content", None),
            "tool_calls": [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in tool_calls
            ],
            "usage": getattr(response, "usage", None),
            "is_error": getattr(response, "is_error", None),
            "error": getattr(response, "error", None),
        },
    })


def log_tool_call(requirement_id, iteration, tool_name, arguments, result):
    """记录一次工具调用：入参 + 结果（含阻断原因）。"""
    _write(requirement_id, {
        "kind": "tool_call",
        "iteration": iteration,
        "tool": tool_name,
        "arguments": arguments,
        "success": getattr(result, "success", None),
        "blocked": getattr(result, "blocked", None),
        "content": getattr(result, "content", None),
        "error": getattr(result, "error", None),
    })
