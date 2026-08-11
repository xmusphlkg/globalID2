"""TTL-based service heartbeats for the dashboard runtime view."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import socket
from typing import Any
from uuid import uuid4

import redis.asyncio as redis

from src.core.config import get_config
from src.core.logging import get_logger

logger = get_logger(__name__)


class RuntimeRegistry:
    key_prefix = "gids:control-plane:runtime"
    ttl_seconds = 45

    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._lock = asyncio.Lock()

    async def _client(self) -> redis.Redis | None:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is not None:
                return self._redis
            try:
                client = redis.from_url(
                    get_config().redis.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
                await client.ping()
                self._redis = client
            except Exception as exc:
                logger.warning("Runtime heartbeat Redis unavailable: {}", exc)
        return self._redis

    @staticmethod
    def new_instance_id(service: str) -> str:
        return f"{service}-{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"

    async def heartbeat(
        self,
        service: str,
        instance_id: str,
        *,
        status: str = "healthy",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        client = await self._client()
        if client is None:
            return
        payload = {
            "service": service,
            "instance_id": instance_id,
            "status": status,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        try:
            await client.set(
                f"{self.key_prefix}:{service}:{instance_id}",
                json.dumps(payload, ensure_ascii=False, default=str),
                ex=self.ttl_seconds,
            )
        except Exception as exc:
            logger.warning("Runtime heartbeat failed: {}", exc)
            self._redis = None

    async def run_heartbeat(
        self,
        service: str,
        instance_id: str,
        stop_event: asyncio.Event,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        while not stop_event.is_set():
            await self.heartbeat(service, instance_id, metadata=metadata)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

    async def list_services(self) -> tuple[list[dict[str, Any]], bool]:
        client = await self._client()
        if client is None:
            return [], False
        services: list[dict[str, Any]] = []
        try:
            async for key in client.scan_iter(match=f"{self.key_prefix}:*"):
                if key.startswith(f"{self.key_prefix}:lease:"):
                    continue
                raw = await client.get(key)
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Ignoring malformed runtime heartbeat key: {}", key)
                    continue
                if isinstance(payload, dict) and payload.get("service") and payload.get("instance_id"):
                    services.append(payload)
        except Exception as exc:
            logger.warning("Runtime heartbeat scan failed: {}", exc)
            self._redis = None
            return [], False
        services.sort(key=lambda item: (item.get("service", ""), item.get("instance_id", "")))
        return services, True

    async def acquire_lease(self, name: str, owner: str, *, ttl_seconds: int = 45) -> bool:
        """Acquire a fail-closed distributed lease used by singleton processes."""
        client = await self._client()
        if client is None:
            return False
        try:
            return bool(
                await client.set(
                    f"{self.key_prefix}:lease:{name}", owner, nx=True, ex=ttl_seconds
                )
            )
        except Exception as exc:
            logger.warning("Runtime lease acquisition failed: {}", exc)
            self._redis = None
            return False

    async def renew_lease(self, name: str, owner: str, *, ttl_seconds: int = 45) -> bool:
        client = await self._client()
        if client is None:
            return False
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        try:
            return bool(
                await client.eval(
                    script,
                    1,
                    f"{self.key_prefix}:lease:{name}",
                    owner,
                    ttl_seconds,
                )
            )
        except Exception as exc:
            logger.warning("Runtime lease renewal failed: {}", exc)
            self._redis = None
            return False

    async def release_lease(self, name: str, owner: str) -> None:
        client = await self._client()
        if client is None:
            return
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            await client.eval(script, 1, f"{self.key_prefix}:lease:{name}", owner)
        except Exception as exc:
            logger.warning("Runtime lease release failed: {}", exc)

    async def maintain_lease(
        self,
        name: str,
        owner: str,
        stop_event: asyncio.Event,
        *,
        ttl_seconds: int = 45,
    ) -> None:
        """Renew a lease and stop the owning process if exclusivity is lost."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(5, ttl_seconds // 3))
                continue
            except asyncio.TimeoutError:
                pass
            if not await self.renew_lease(name, owner, ttl_seconds=ttl_seconds):
                logger.error("Runtime lease '{}' was lost; requesting shutdown", name)
                stop_event.set()
                return

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


runtime_registry = RuntimeRegistry()

__all__ = ["RuntimeRegistry", "runtime_registry"]
