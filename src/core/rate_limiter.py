"""GlobalID V2 速率限制器"""

import asyncio
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
        self.max_requests = max_requests or self.config.ai.rate_limit
        self.window_seconds = window_seconds or 60  # 默认60秒窗口
        self.requests: Deque[float] = deque()
        logger.info(f"RateLimiter initialized: {self.max_requests} requests per {self.window_seconds}s")

    def _clean_old_requests(self) -> None:
        """清理过期的请求记录"""
        now = time.time()
        cutoff = now - self.window_seconds
        while self.requests and self.requests[0] < cutoff:
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
        wait = (oldest + self.window_seconds) - time.time()
        return max(0.0, wait)

    async def wait_if_needed(self) -> None:
        """如果达到速率限制则等待"""
        wait = self.wait_time()
        if wait > 0:
            logger.warning(f"Rate limit reached, waiting {wait:.2f}s")
            await asyncio.sleep(wait)

    def record_request(self) -> None:
        """记录一次请求"""
        if not self.config.ai.enable_rate_limiting:
            return
        self.requests.append(time.time())
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
