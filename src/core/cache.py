"""GlobalID V2 缓存服务"""

import asyncio
import json
from typing import Any, Optional

import redis.asyncio as redis
from redis.exceptions import RedisError

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)


class CacheService:
    """Redis 缓存服务"""

    def __init__(self):
        self.config = get_config()
        self._redis: Optional[redis.Redis] = None
        self._connection_lock = asyncio.Lock()

    async def connect(self) -> None:
        """连接 Redis"""
        async with self._connection_lock:
            if self._redis is not None:
                return
            client = redis.from_url(
                self.config.redis.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            try:
                await client.ping()
            except BaseException:
                # The candidate is not shared until it is ready. Also close it
                # when initialization is cancelled, before releasing the lock.
                try:
                    await client.aclose()
                except Exception as exc:
                    logger.warning("Failed to close uninitialized cache client: {}", exc)
                raise
            self._redis = client
            logger.info("Redis connected")

    async def disconnect(self) -> None:
        """断开连接"""
        async with self._connection_lock:
            client = self._redis
            self._redis = None
            if client is not None:
                await client.aclose()
                logger.info("Redis disconnected")

    async def _execute(self, command: str, *args: Any, default: Any, **kwargs: Any) -> Any:
        """Treat unavailable cache storage as a miss, including initial connect."""
        try:
            await self.connect()
            client = self._redis
            if client is None:
                return default
            return await getattr(client, command)(*args, **kwargs)
        except (RedisError, OSError) as exc:
            logger.warning("Cache {} unavailable: {}", command, exc)
            return default

    @staticmethod
    def _make_key(key: str, prefix: str = "globalid") -> str:
        """生成缓存 key"""
        return f"{prefix}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if not self.config.ai.enable_cache:
            return None

        value = await self._execute("get", self._make_key(key), default=None)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid cached JSON for {}: {}", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置缓存"""
        if not self.config.ai.enable_cache:
            return False

        ttl = self.config.ai.cache_ttl * 3600 if ttl is None else ttl
        if type(ttl) is not int or ttl <= 0:
            logger.warning("Cache TTL must be a positive integer number of seconds")
            return False
        try:
            json_value = json.dumps(value, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            logger.warning("Cache value cannot be serialized for {}: {}", key, exc)
            return False
        return bool(await self._execute(
            "set", self._make_key(key), json_value, ex=ttl, default=False,
        ))

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        result = await self._execute("delete", self._make_key(key), default=None)
        return result is not None

    async def exists(self, key: str) -> bool:
        """检查缓存是否存在"""
        return bool(await self._execute("exists", self._make_key(key), default=False))

    async def get_ttl(self, key: str) -> int:
        """获取缓存剩余存活时间"""
        return await self._execute("ttl", self._make_key(key), default=-2)


_cache_service: Optional[CacheService] = None


def get_cache() -> CacheService:
    """获取缓存服务单例"""
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service
