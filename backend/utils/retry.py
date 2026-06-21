# -*- coding: utf-8 -*-
"""
重试工具模块
实现指数退避重试机制
"""

import time
import random
from typing import Callable, Any, Optional, Tuple, Type
from functools import wraps

from harness.observability.logger import get_logger

logger = get_logger(__name__)


class RetryError(Exception):
    """重试失败异常"""

    def __init__(self, message: str, last_exception: Optional[Exception] = None, attempts: int = 1):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger_func: Optional[Callable] = None
):
    """
    指数退避重试装饰器

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        exponential_base: 指数基数
        jitter: 是否添加随机抖动
        exceptions: 需要重试的异常类型
        logger_func: 日志记录函数

    Returns:
        装饰器函数

    Example:
        @retry_with_backoff(max_retries=3, exceptions=(requests.RequestException,))
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    if attempt >= max_retries:
                        # 超过最大重试次数，抛出 RetryError
                        error_msg = f"{func.__name__} 失败，已重试 {max_retries} 次"
                        logger_func = logger_func or logger.error
                        logger_func(error_msg)
                        raise RetryError(error_msg, last_exception, max_retries + 1)

                    # 计算延迟时间（指数退避）
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)

                    # 添加随机抖动（避免雷群效应）
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger_func = logger_func or logger.warning
                    logger_func(
                        f"{func.__name__} 失败：{str(e)}，"
                        f"将在 {delay:.2f}秒后重试 ({attempt + 1}/{max_retries})"
                    )

                    time.sleep(delay)

            # 不应该到达这里
            raise RetryError(f"{func.__name__} 未知错误", last_exception, max_retries + 1)

        return wrapper
    return decorator


async def retry_with_backoff_async(
    func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger_func: Optional[Callable] = None,
    **kwargs
) -> Any:
    """
    异步版本的指数退避重试

    Args:
        func: 异步函数
        *args: 函数参数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        exponential_base: 指数基数
        jitter: 是否添加随机抖动
        exceptions: 需要重试的异常类型
        logger_func: 日志记录函数
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果
    """
    import asyncio

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)

        except exceptions as e:
            last_exception = e

            if attempt >= max_retries:
                error_msg = f"{func.__name__} 失败，已重试 {max_retries} 次"
                logger_func = logger_func or logger.error
                logger_func(error_msg)
                raise RetryError(error_msg, last_exception, max_retries + 1)

            # 计算延迟时间
            delay = min(base_delay * (exponential_base ** attempt), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)

            logger_func = logger_func or logger.warning
            logger_func(
                f"{func.__name__} 失败：{str(e)}，"
                f"将在 {delay:.2f}秒后重试 ({attempt + 1}/{max_retries})"
            )

            await asyncio.sleep(delay)

    raise RetryError(f"{func.__name__} 未知错误", last_exception, max_retries + 1)


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """
        计算指定尝试次数的延迟时间

        Args:
            attempt: 当前尝试次数（从 0 开始）

        Returns:
            延迟时间（秒）
        """
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        return delay


class Retrier:
    """重试器类"""

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()

    def execute(
        self,
        func: Callable,
        *args,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        logger_func: Optional[Callable] = None,
        **kwargs
    ) -> Any:
        """
        执行函数并重试

        Args:
            func: 要执行的函数
            *args: 函数参数
            exceptions: 需要重试的异常类型
            logger_func: 日志记录函数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return func(*args, **kwargs)

            except exceptions as e:
                last_exception = e

                if attempt >= self.config.max_retries:
                    error_msg = f"{func.__name__} 失败，已重试 {self.config.max_retries} 次"
                    (logger_func or logger.error)(error_msg)
                    raise RetryError(error_msg, last_exception, self.config.max_retries + 1)

                delay = self.config.get_delay(attempt)
                (logger_func or logger.warning)(
                    f"{func.__name__} 失败：{str(e)}，"
                    f"将在 {delay:.2f}秒后重试 ({attempt + 1}/{self.config.max_retries})"
                )

                time.sleep(delay)

        raise RetryError(f"{func.__name__} 未知错误", last_exception, self.config.max_retries + 1)
