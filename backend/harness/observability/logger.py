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


def setup_logging(log_dir: str = "logs", level: str = "INFO"):
    """初始化日志系统，配置 4 类日志文件"""
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    # 根 logger
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件日志（按天轮转）
    log_files = {
        "app": os.path.join(log_dir, "app.log"),
        "agent": os.path.join(log_dir, "agent.log"),
        "llm": os.path.join(log_dir, "llm.log"),
    }

    for name, path in log_files.items():
        handler = TimedRotatingFileHandler(
            path, when="midnight", interval=1, backupCount=30, encoding="utf-8"
        )
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """获取 logger 实例（兼容旧接口）"""
    # 检查是否已配置过 handler
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
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
