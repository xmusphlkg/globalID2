from __future__ import annotations

import asyncio

import pytest

from src.control_plane.events import ControlPlaneEventBus
from src.control_plane.runtime import RuntimeRegistry


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, _key_count, key, owner, *args):
        if self.values.get(key) != owner:
            return 0
        if "expire" in script:
            return 1
        if "del" in script:
            del self.values[key]
            return 1
        return 0

    async def get(self, key):
        return self.values.get(key)

    async def scan_iter(self, *, match):
        prefix = match.removesuffix("*")
        for key in self.values:
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_scheduler_lease_is_single_owner_and_owner_safe() -> None:
    registry = RuntimeRegistry()
    registry._redis = FakeRedis()  # type: ignore[assignment]

    assert await registry.acquire_lease("scheduler", "owner-a") is True
    assert await registry.acquire_lease("scheduler", "owner-b") is False
    assert await registry.renew_lease("scheduler", "owner-a") is True
    assert await registry.renew_lease("scheduler", "owner-b") is False
    await registry.release_lease("scheduler", "owner-b")
    assert await registry.acquire_lease("scheduler", "owner-b") is False
    await registry.release_lease("scheduler", "owner-a")
    assert await registry.acquire_lease("scheduler", "owner-b") is True


@pytest.mark.asyncio
async def test_runtime_service_scan_ignores_leases_and_malformed_values() -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]
    fake_redis.values = {
        f"{registry.key_prefix}:lease:scheduler": "scheduler-owner",
        f"{registry.key_prefix}:api:api-1": (
            '{"service":"api","instance_id":"api-1","status":"healthy"}'
        ),
        f"{registry.key_prefix}:worker:broken": "not-json",
    }

    services, available = await registry.list_services()

    assert available is True
    assert services == [
        {"service": "api", "instance_id": "api-1", "status": "healthy"}
    ]


@pytest.mark.asyncio
async def test_event_stream_falls_back_to_local_delivery_when_redis_is_down(monkeypatch) -> None:
    bus = ControlPlaneEventBus()

    async def unavailable():
        return None

    monkeypatch.setattr(bus, "_client", unavailable)
    subscription = bus.subscribe()
    waiting = asyncio.create_task(anext(subscription))
    await asyncio.sleep(0)
    await bus.publish("task.status", resource_type="task", resource_id="task-1", data={"status": "queued"})
    event = await asyncio.wait_for(waiting, timeout=1)

    assert event["type"] == "task.status"
    assert event["resource_id"] == "task-1"
    await subscription.aclose()
