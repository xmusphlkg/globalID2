"""GlobalID V2 速率限制器"""

import asyncio
import math
import time
from collections import deque
from typing import Deque, Optional

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """滑动窗口速率限制器"""

    def __init__(self, max_requests: Optional[int] = None, window_seconds: Optional[int] = None):
        self.config = get_config()
        self.max_requests = self.config.ai.rate_limit if max_requests is None else max_requests
        self.window_seconds = 60 if window_seconds is None else window_seconds
        if type(self.max_requests) is not int or self.max_requests <= 0:
            raise ValueError("max_requests must be a positive integer")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        self.requests: Deque[float] = deque()
        logger.info(f"RateLimiter initialized: {self.max_requests} requests per {self.window_seconds}s")

    def _clean_old_requests(self) -> None:
        """清理过期的请求记录"""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        while self.requests and self.requests[0] <= cutoff:
            self.requests.popleft()

    def can_proceed(self) -> bool:
        """检查是否可以继续请求"""
        if not self.config.ai.enable_rate_limiting:
            return True
        self._clean_old_requests()
        return len(self.requests) < self.max_requests

    def wait_time(self) -> float:
        """计算需要等待的时间"""
        if not self.config.ai.enable_rate_limiting:
            return 0.0
        self._clean_old_requests()
        if len(self.requests) < self.max_requests:
            return 0.0
        oldest = self.requests[0]
        wait = (oldest + self.window_seconds) - time.monotonic()
        return max(0.0, wait)

    async def wait_if_needed(self) -> None:
        """如果达到速率限制则等待"""
        while (wait := self.wait_time()) > 0:
            logger.warning(f"Rate limit reached, waiting {wait:.2f}s")
            await asyncio.sleep(wait)

    async def acquire(self) -> None:
        """Wait and reserve one slot atomically within this event loop.

        There is no suspension between checking capacity and recording it;
        competing waiters recheck capacity after every wakeup.
        """
        while True:
            await self.wait_if_needed()
            if self.can_proceed():
                self.record_request()
                return

    def record_request(self) -> None:
        """记录一次请求"""
        if not self.config.ai.enable_rate_limiting:
            return
        self._clean_old_requests()
        self.requests.append(time.monotonic())
        current = len(self.requests)
        if current >= self.max_requests * 0.8:
            logger.warning(f"Rate limit warning: {current}/{self.max_requests}")
        else:
            logger.debug(f"Request recorded ({current}/{self.max_requests})")

    def reset(self) -> None:
        """重置计数器"""
        self.requests.clear()
        logger.info("RateLimiter reset")

    def get_stats(self) -> dict:
        """获取统计信息"""
        self._clean_old_requests()
        return {
            "current_requests": len(self.requests),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "usage_percent": round(len(self.requests) / self.max_requests * 100, 2),
            "can_proceed": self.can_proceed(),
            "wait_time": self.wait_time(),
        }
