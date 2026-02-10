"""
GlobalID V2 缓存服务

基于 Redis 的分布式缓存
"""

import asyncio
import hashlib
import json
from typing import Any, Optional

import redis.asyncio as redis

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)


class CacheService:
    """Redis 缓存服务"""

    def __init__(self):
        self.config = get_config()
        self._redis: Optional[redis.Redis] = None

    async def connect(self) -> None:
        """连接 Redis"""
        if self._redis is None:
            try:
                self._redis = await redis.from_url(
                    self.config.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=5,
                )
                # 测试连接
                await self._redis.ping()
                logger.info(f"Redis connected: {self.config.redis_url}")
            except Exception as e:
                logger.error(f"Redis connection failed: {e}")
                self._redis = None
                raise

    async def disconnect(self) -> None:
        """断开 Redis 连接"""
        if self._redis:
            await self._redis.close()
            logger.info("Redis disconnected")
            self._redis = None

    @staticmethod
    def _make_key(key: str, prefix: str = "globalid") -> str:
        """生成缓存 key"""
        return f"{prefix}:{key}"

    @staticmethod
    def _hash_data(data: Any) -> str:
        """
        生成数据的 hash（用于缓存 key）

        将任意数据序列化为JSON并计算MD5
        """
        try:
            json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(json_str.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash data: {e}")
            return hashlib.md5(str(data).encode()).hexdigest()

    async def get(self, key: str) -> Optional[Any]:
        """
        获取缓存

        Args:
            key: 缓存键

        Returns:
            缓存的值，不存在返回 None
        """
        if not self.config.enable_ai_cache:
            return None

        await self.connect()

        if not self._redis:
            return None

        cache_key = self._make_key(key)

        try:
            value = await self._redis.get(cache_key)
            if value:
                logger.debug(f"Cache hit: {key}")
                return json.loads(value)
            logger.debug(f"Cache miss: {key}")
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    async def set(
        self, key: str, value: Any, ttl: Optional[int] = None
    ) -> bool:
        """
        设置缓存

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），默认使用配置中的值

        Returns:
            是否成功
        """
        if not self.config.enable_ai_cache:
            return False

        await self.connect()

        if not self._redis:
            return False

        cache_key = self._make_key(key)
        ttl = ttl or self.config.cache_ttl

        try:
            json_value = json.dumps(value, ensure_ascii=False)
            await self._redis.set(cache_key, json_value, ex=ttl)
            logger.debug(f"Cache set: {key}, TTL: {ttl}s")
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        await self.connect()

        if not self._redis:
            return False

        cache_key = self._make_key(key)

        try:
            await self._redis.delete(cache_key)
            logger.debug(f"Cache deleted: {key}")
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False

    async def clear(self, pattern: str = "*") -> int:
        """
        清空匹配的缓存

        Args:
            pattern: 匹配模式

        Returns:
            删除的缓存数量
        """
        await self.connect()

        if not self._redis:
            return 0

        cache_pattern = self._make_key(pattern)

        try:
            keys = []
            async for key in self._redis.scan_iter(match=cache_pattern):
                keys.append(key)

            if keys:
                deleted = await self._redis.delete(*keys)
                logger.info(f"Cache cleared: {deleted} keys deleted (pattern: {pattern})")
                return deleted
            return 0
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """检查缓存是否存在"""
        await self.connect()

        if not self._redis:
            return False

        cache_key = self._make_key(key)

        try:
            return await self._redis.exists(cache_key) > 0
        except Exception as e:
            logger.error(f"Cache exists check error: {e}")
            return False

    async def get_ttl(self, key: str) -> int:
        """获取缓存剩余存活时间（秒）"""
        await self.connect()

        if not self._redis:
            return -2

        cache_key = self._make_key(key)

        try:
            return await self._redis.ttl(cache_key)
        except Exception as e:
            logger.error(f"Cache TTL check error: {e}")
            return -2


# 全局缓存实例
_cache_service: Optional[CacheService] = None


def get_cache() -> CacheService:
    """获取缓存服务单例"""
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service
