# -*- coding: utf-8 -*-
"""
日志系统 —— 从 utils/logger.py 迁移，支持 4 类日志文件
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

from harness.observability.log_context import install_record_factory


def _make_routing_filter(include=(), exclude=()):
    """按 logger 名前缀把记录路由到**唯一**一个文件通道

    此前三个文件 handler 全部挂在 root 上且无分流，导致 app/agent/llm 三个
    文件的内容完全相同（实测 md5 一致）——分类形同虚设，磁盘占用却是 3 倍。
    """
    prefixes_include = tuple(include)
    prefixes_exclude = tuple(exclude)

    class _RoutingFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            name = record.name
            for p in prefixes_exclude:
                if name == p or name.startswith(p + "."):
                    return False
            if prefixes_include:
                for p in prefixes_include:
                    if name == p or name.startswith(p + "."):
                        return True
                return False
            return True

    return _RoutingFilter()


def _resolve_log_settings(log_dir, level):
    """解析日志配置：显式参数优先，否则读 config.settings，再失败回退内置默认。

    修复两个历史问题：
    1. backupCount 此前硬编码 30，config 里的 *_RETENTION_DAYS 从未被读取（死配置）；
    2. log_dir 相对路径随 CWD 漂移，与 llm/client.py 的绝对路径分裂成两个目录 ——
       统一锚定 BACKEND_DIR（backend/logs），llm/client.py 同步改为同一锚点。
    """
    agent_days, app_days, fallback_dir, fallback_level = 30, 90, "logs", "INFO"
    try:
        from config import settings
        resolved_dir = log_dir or str(settings.BACKEND_DIR / settings.LOG_DIR)
        resolved_level = level or settings.LOG_LEVEL
        agent_days = getattr(settings, "AGENT_LOG_RETENTION_DAYS", agent_days)
        app_days = getattr(settings, "APP_LOG_RETENTION_DAYS", app_days)
        return resolved_dir, resolved_level, agent_days, app_days
    except Exception:
        # config 不可用（极端单测环境）：按 __file__ 反推 backend 目录
        backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return (log_dir or os.path.join(backend, fallback_dir),
                level or fallback_level, agent_days, app_days)


def setup_logging(log_dir: str = None, level: str = None):
    """初始化日志系统，配置 3 类日志文件（互斥分流，不再是三份副本）

    注：config 里还有一个 LOG_FILE_MAX_SIZE_MB（按大小轮转），与按天轮转
    二选一，当前选按天 —— 大小上限未实现，改配置不生效，勿误以为有保护。
    """
    log_dir, level, agent_days, app_days = _resolve_log_settings(log_dir, level)
    os.makedirs(log_dir, exist_ok=True)

    # 给每条日志补 req_id / trace_id（幂等）
    install_record_factory()

    log_level = getattr(logging, level.upper(), logging.INFO)

    # 根 logger
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | req=%(req_id)s trace=%(trace_id)s | '
        '%(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台：不分流，看全部（分流只影响落盘）
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件日志（按天轮转，三者互斥；保留天数真正读自 config 的 RETENTION_DAYS）
    #   harness.* → agent.log（Agent 执行链路）
    #   llm.*     → llm.log（LLM 调用；llm.traffic 另有独立通道，不在此列）
    #   其余      → app.log（Web 层、服务层、基础设施）
    log_files = [
        ("agent", ("harness",), (), agent_days),
        ("llm", ("llm",), (), agent_days),
        ("app", (), ("harness", "llm"), app_days),
    ]

    for name, include, exclude, keep_days in log_files:
        handler = TimedRotatingFileHandler(
            os.path.join(log_dir, f"{name}.log"), when="midnight", interval=1,
            backupCount=keep_days, encoding="utf-8"
        )
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        handler.addFilter(_make_routing_filter(include, exclude))
        root.addHandler(handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """获取 logger 实例（继承 root 配置，无需重复挂 handler）"""
    return logging.getLogger(name)


# 向后兼容：setup_logger 支持旧版调用方式
def setup_logger(
    name: str,
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    format_string: Optional[str] = None
) -> logging.Logger:
    """
    设置并返回一个日志记录器（兼容旧 utils/logger.py 接口）

    Args:
        name: 日志器名称（通常是 __name__）
        level: 日志级别
        log_file: 日志文件路径（可选）
        format_string: 日志格式字符串

    Returns:
        配置好的 Logger 对象
    """
    if format_string is None:
        format_string = (
            "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
        )

    formatter = logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件处理器（可选）
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def set_global_level(level: int):
    """
    设置全局日志级别

    Args:
        level: logging 模块定义的级别
    """
    logging.root.setLevel(level)


# 预定义的日志级别别名
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


# 快捷函数
def debug(msg: str, *args, **kwargs):
    logging.getLogger("app").debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs):
    logging.getLogger("app").info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs):
    logging.getLogger("app").warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs):
    logging.getLogger("app").error(msg, *args, **kwargs)
