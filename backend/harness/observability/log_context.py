# -*- coding: utf-8 -*-
"""日志上下文 —— 把 requirement_id / trace_id 注入每一条日志

核心取舍是：
**按需求「聚合」而不是按需求「分流」**。

实测过：单日 3884 行日志里只有 46 行（1.2%）能被归因到需求，其余是
SSE 断连、任务调度、启动、DB 这类**不属于任何需求**的模块级流水。
把它们强行按需求分文件，只会让它们无处可写；而全局共因（LLM 端点不可达、
代理漂移、DB 池耗尽）恰恰要靠**跨需求视图**才能识别。
所以：运行期保持单流追加，需求维度的聚合交给事后查询（`grep 'req=76'`）。

为什么用 contextvar 而不是 threading.local：
contextvar 是线程池 + asyncio 混合场景下唯一的官方解法。但要注意
**ThreadPoolExecutor 不会自动继承提交者的 context**，必须在线程入口
显式绑定 —— 见 `services/task_queue.py::_run_task`。

为什么用 LogRecordFactory 而不是 logging.Filter：
Filter 挂在 logger 上，只对**直接经由该 logger** 产生的记录生效，子 logger
propagate 上来的记录不会经过它；挂在 handler 上则要逐个 handler 维护。
LogRecordFactory 在记录创建时就补字段，对所有 logger / handler 一视同仁，
且天然不会出现格式串取不到属性的 KeyError。
"""

from __future__ import annotations

import contextvars
import logging
from typing import Optional

_EMPTY = "-"

_req_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "t2c_requirement_id", default=_EMPTY
)
_trace_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "t2c_trace_id", default=_EMPTY
)


def bind_requirement(requirement_id) -> None:
    """绑定需求 ID（在需求执行线程入口调用一次即可）"""
    _req_var.set(str(requirement_id) if requirement_id is not None else _EMPTY)


def bind_trace_id(trace_id) -> None:
    """绑定 trace ID（由 Tracer.start_trace 自动调用）"""
    _trace_var.set(str(trace_id) if trace_id else _EMPTY)


def clear() -> None:
    """清理上下文。

    ThreadPoolExecutor 的线程是**复用**的：不清理的话，上一个需求的
    req_id 会残留到该线程执行的下一个任务上，产生张冠李戴的日志。
    """
    _req_var.set(_EMPTY)
    _trace_var.set(_EMPTY)


def current_requirement_id() -> str:
    return _req_var.get()


def current_trace_id() -> str:
    return _trace_var.get()


def install_record_factory() -> None:
    """给每一条 LogRecord 补上 req_id / trace_id 字段（幂等）"""
    if getattr(logging, "_t2c_record_factory_installed", False):
        return

    old_factory = logging.getLogRecordFactory()

    def _factory(*args, **kwargs) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        # 在记录创建时读取 contextvar —— 这是唯一正确的时机，
        # 晚于此刻读取会拿不到日志产生当时的上下文。
        record.req_id = _req_var.get()
        record.trace_id = _trace_var.get()
        return record

    logging.setLogRecordFactory(_factory)
    # 幂等标记：setup_logging 可能被重复调用（测试 / 热重载），
    # 不设标记会形成 factory 嵌套链，每条日志多跑 N 次。
    logging._t2c_record_factory_installed = True
