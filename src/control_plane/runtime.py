"""TTL-based service heartbeats for the dashboard runtime view."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import socket
import threading
import time
from typing import Any, Callable
from uuid import uuid4

import redis as redis_sync
import redis.asyncio as redis

from src.core.config import get_config
from src.core.logging import get_logger

logger = get_logger(__name__)


class ThreadedRuntimeGuard:
    """Renew worker ownership outside an event loop that legacy crawlers block."""

    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str,
        service: str,
        instance_id: str,
        lease_ttl_seconds: int,
        heartbeat_ttl_seconds: int,
        metadata: dict[str, Any] | None,
        on_lease_lost: Callable[[], None],
    ) -> None:
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.service = service
        self.instance_id = instance_id
        self.lease_ttl_seconds = lease_ttl_seconds
        self.heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self.metadata = metadata or {}
        self.on_lease_lost = on_lease_lost
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"{service}-runtime-guard",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _heartbeat_payload(self) -> str:
        return json.dumps(
            {
                "service": self.service,
                "instance_id": self.instance_id,
                "status": "healthy",
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            default=str,
        )

    def _run(self) -> None:
        lease_key = f"{self.key_prefix}:lease:{self.service}"
        heartbeat_key = f"{self.key_prefix}:{self.service}:{self.instance_id}"
        renew_script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        interval = max(
            5,
            min(
                15,
                self.lease_ttl_seconds // 3,
                self.heartbeat_ttl_seconds // 3,
            ),
        )
        last_renewed = time.monotonic()
        client: redis_sync.Redis | None = None
        try:
            while not self._stop.is_set():
                try:
                    if client is None:
                        client = redis_sync.from_url(
                            self.redis_url,
                            encoding="utf-8",
                            decode_responses=True,
                            socket_connect_timeout=2,
                            socket_timeout=2,
                        )
                        client.ping()
                    renewed = client.eval(
                        renew_script,
                        1,
                        lease_key,
                        self.instance_id,
                        self.lease_ttl_seconds,
                    )
                    if not renewed:
                        logger.error(
                            "Runtime lease '{}' ownership was lost in threaded guard",
                            self.service,
                        )
                        self.on_lease_lost()
                        return
                    client.set(
                        heartbeat_key,
                        self._heartbeat_payload(),
                        ex=self.heartbeat_ttl_seconds,
                    )
                    last_renewed = time.monotonic()
                except Exception as exc:
                    logger.warning("Threaded runtime guard Redis failure: {}", exc)
                    if client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass
                        client = None
                    if time.monotonic() - last_renewed >= self.lease_ttl_seconds:
                        self.on_lease_lost()
                        return
                self._stop.wait(interval)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


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
        ttl_seconds: int | None = None,
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
                ex=ttl_seconds or self.ttl_seconds,
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
        ttl_seconds: int | None = None,
    ) -> None:
        heartbeat_ttl = ttl_seconds or self.ttl_seconds
        while not stop_event.is_set():
            await self.heartbeat(
                service,
                instance_id,
                metadata=metadata,
                ttl_seconds=heartbeat_ttl,
            )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=max(5, heartbeat_ttl // 3),
                )
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

    async def service_is_live(self, service: str) -> tuple[bool, bool]:
        """Return ``(is_live, registry_available)`` for a runtime service kind."""
        services, available = await self.list_services()
        return any(item.get("service") == service for item in services), available

    async def wait_for_service(
        self,
        service: str,
        stop_event: asyncio.Event,
        *,
        timeout_seconds: float,
        poll_seconds: float = 2,
    ) -> bool:
        """Wait for a service heartbeat without hiding registry outages."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not stop_event.is_set():
            live, available = await self.service_is_live(service)
            if live:
                return True
            if not available:
                return False
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=min(poll_seconds, remaining)
                )
            except asyncio.TimeoutError:
                pass
        return False

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

    async def lease_owner(self, name: str) -> tuple[str | None, bool]:
        """Return the exact singleton owner and registry availability."""
        client = await self._client()
        if client is None:
            return None, False
        try:
            value = await client.get(f"{self.key_prefix}:lease:{name}")
            return (str(value) if value else None), True
        except Exception as exc:
            logger.warning("Runtime lease inspection failed: {}", exc)
            self._redis = None
            return None, False

    async def release_stopped_instance(self, name: str, expected_owner: str) -> bool:
        """Atomically remove one verified dead owner's lease and heartbeat.

        This is intentionally compare-and-delete. Maintenance tooling must
        verify the owner PID is dead before calling it, so a newly acquired
        lease can never be removed by a delayed cleanup step.
        """
        client = await self._client()
        if client is None:
            return False
        lease_key = f"{self.key_prefix}:lease:{name}"
        heartbeat_key = f"{self.key_prefix}:{name}:{expected_owner}"
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          redis.call('del', KEYS[2])
          return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            return bool(
                await client.eval(
                    script,
                    2,
                    lease_key,
                    heartbeat_key,
                    expected_owner,
                )
            )
        except Exception as exc:
            logger.warning("Stopped runtime instance cleanup failed: {}", exc)
            return False

    async def remove_heartbeat(self, service: str, instance_id: str) -> bool:
        """Remove one exact runtime heartbeat during graceful shutdown."""
        client = await self._client()
        if client is None:
            return False
        try:
            return bool(
                await client.delete(
                    f"{self.key_prefix}:{service}:{instance_id}"
                )
            )
        except Exception as exc:
            logger.warning("Runtime heartbeat cleanup failed: {}", exc)
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
        lease_lost_event: asyncio.Event | None = None,
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
                if lease_lost_event is not None:
                    lease_lost_event.set()
                stop_event.set()
                return

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def start_threaded_guard(
        self,
        service: str,
        instance_id: str,
        *,
        lease_ttl_seconds: int,
        heartbeat_ttl_seconds: int,
        metadata: dict[str, Any] | None,
        on_lease_lost: Callable[[], None],
    ) -> ThreadedRuntimeGuard:
        guard = ThreadedRuntimeGuard(
            redis_url=get_config().redis.url,
            key_prefix=self.key_prefix,
            service=service,
            instance_id=instance_id,
            lease_ttl_seconds=lease_ttl_seconds,
            heartbeat_ttl_seconds=heartbeat_ttl_seconds,
            metadata=metadata,
            on_lease_lost=on_lease_lost,
        )
        guard.start()
        return guard


runtime_registry = RuntimeRegistry()

__all__ = ["RuntimeRegistry", "ThreadedRuntimeGuard", "runtime_registry"]
