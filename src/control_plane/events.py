"""Cross-process control-plane events backed by a bounded Redis stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

import redis.asyncio as redis

from src.core.config import get_config
from src.core.logging import get_logger

logger = get_logger(__name__)


class ControlPlaneEventBus:
    """Publish and consume dashboard events without coupling them to the API process."""

    stream_key = "gids:control-plane:events"
    max_events = 2_000

    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._connect_lock = asyncio.Lock()
        self._fallback_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def _client(self) -> redis.Redis | None:
        if self._redis is not None:
            return self._redis
        async with self._connect_lock:
            if self._redis is not None:
                return self._redis
            try:
                client = redis.from_url(
                    get_config().redis.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=20,
                )
                await client.ping()
                self._redis = client
            except Exception as exc:
                logger.warning("Control-plane Redis event stream unavailable: {}", exc)
                self._redis = None
        return self._redis

    async def publish(
        self,
        event_type: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "event_id": str(uuid4()),
            "type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "data": data or {},
        }
        client = await self._client()
        if client is not None:
            try:
                stream_id = await client.xadd(
                    self.stream_key,
                    {"payload": json.dumps(payload, ensure_ascii=False, default=str)},
                    maxlen=self.max_events,
                    approximate=True,
                )
                payload["stream_id"] = stream_id
            except Exception as exc:
                logger.warning("Control-plane event publish failed: {}", exc)
                self._redis = None

        for queue in tuple(self._fallback_subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        return str(payload.get("stream_id") or payload["event_id"])

    async def publish_task_event(self, raw: dict[str, Any]) -> None:
        event_name = str(raw.get("event") or "updated").replace("task_", "")
        task_uuid = str(raw.get("task_uuid") or "") or None
        await self.publish(
            f"task.{event_name}",
            resource_type="task",
            resource_id=task_uuid,
            data=raw,
        )

    async def subscribe(self, last_event_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield persisted Redis events, falling back to an in-process queue."""

        client = await self._client()
        if client is not None:
            cursor = last_event_id if last_event_id and "-" in last_event_id else "$"
            while True:
                try:
                    rows = await client.xread({self.stream_key: cursor}, block=15_000, count=100)
                    if not rows:
                        yield {"type": "heartbeat", "occurred_at": datetime.now(timezone.utc).isoformat()}
                        continue
                    for _stream, messages in rows:
                        for stream_id, fields in messages:
                            cursor = stream_id
                            payload = json.loads(fields.get("payload") or "{}")
                            payload["stream_id"] = stream_id
                            yield payload
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Control-plane event subscription degraded: {}", exc)
                    self._redis = None
                    break

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._fallback_subscribers.add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "occurred_at": datetime.now(timezone.utc).isoformat()}
        finally:
            self._fallback_subscribers.discard(queue)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


control_plane_events = ControlPlaneEventBus()

__all__ = ["ControlPlaneEventBus", "control_plane_events"]
