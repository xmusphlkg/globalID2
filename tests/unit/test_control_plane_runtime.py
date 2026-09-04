from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from src.control_plane.events import ControlPlaneEventBus
from src.control_plane import runtime as runtime_module
from src.control_plane.runtime import RuntimeRegistry, ThreadedRuntimeGuard


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int | None] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def eval(self, script, key_count, *args):
        key = args[0]
        owner = args[-1] if key_count == 2 else args[1]
        if self.values.get(key) != owner:
            return 0
        if "expire" in script:
            return 1
        if "del" in script:
            del self.values[key]
            if key_count == 2:
                self.values.pop(args[1], None)
            return 1
        return 0

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def scan_iter(self, *, match):
        prefix = match.removesuffix("*")
        for key in self.values:
            if key.startswith(prefix):
                yield key


class FakeSyncRedis:
    def __init__(self, *, owner_matches: bool = True) -> None:
        self.owner_matches = owner_matches
        self.heartbeat_expiry: int | None = None
        self.heartbeat_written = threading.Event()

    def ping(self):
        return True

    def eval(self, *_args):
        return 1 if self.owner_matches else 0

    def set(self, _key, _value, *, ex=None):
        self.heartbeat_expiry = ex
        self.heartbeat_written.set()
        return True

    def close(self):
        return None


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
async def test_stopped_instance_cleanup_is_exact_owner_scoped() -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]
    lease_key = f"{registry.key_prefix}:lease:worker"
    heartbeat_key = f"{registry.key_prefix}:worker:worker-1"
    fake_redis.values = {
        lease_key: "worker-1",
        heartbeat_key: '{"service":"worker"}',
    }

    assert await registry.lease_owner("worker") == ("worker-1", True)
    assert await registry.release_stopped_instance("worker", "worker-other") is False
    assert fake_redis.values[lease_key] == "worker-1"
    assert await registry.release_stopped_instance("worker", "worker-1") is True
    assert lease_key not in fake_redis.values
    assert heartbeat_key not in fake_redis.values


@pytest.mark.asyncio
async def test_dead_local_owner_lease_is_released_without_waiting_for_ttl(monkeypatch) -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]
    owner = f"worker-{socket.gethostname()}-987654-dead"
    lease_key = f"{registry.key_prefix}:lease:worker"
    heartbeat_key = f"{registry.key_prefix}:worker:{owner}"
    fake_redis.values = {lease_key: owner, heartbeat_key: '{"service":"worker"}'}

    def dead_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(runtime_module.os, "kill", dead_process)

    assert await registry.release_dead_local_instance_lease("worker") is True
    assert lease_key not in fake_redis.values
    assert heartbeat_key not in fake_redis.values


@pytest.mark.asyncio
async def test_graceful_heartbeat_cleanup_removes_only_exact_instance() -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]
    old_key = f"{registry.key_prefix}:worker:worker-old"
    new_key = f"{registry.key_prefix}:worker:worker-new"
    fake_redis.values = {old_key: "old", new_key: "new"}

    assert await registry.remove_heartbeat("worker", "worker-old") is True
    assert old_key not in fake_redis.values
    assert fake_redis.values[new_key] == "new"


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
    assert await registry.service_is_live("api") == (True, True)
    assert await registry.service_is_live("worker") == (False, True)


@pytest.mark.asyncio
async def test_wait_for_service_observes_new_heartbeat() -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]
    stop_event = asyncio.Event()

    waiter = asyncio.create_task(
        registry.wait_for_service(
            "worker", stop_event, timeout_seconds=1, poll_seconds=0.01
        )
    )
    await asyncio.sleep(0.02)
    await registry.heartbeat("worker", "worker-1")

    assert await waiter is True


@pytest.mark.asyncio
async def test_worker_heartbeat_can_use_a_longer_ttl_for_blocking_crawlers() -> None:
    registry = RuntimeRegistry()
    fake_redis = FakeRedis()
    registry._redis = fake_redis  # type: ignore[assignment]

    await registry.heartbeat("worker", "worker-1", ttl_seconds=120)

    key = f"{registry.key_prefix}:worker:worker-1"
    assert fake_redis.expirations[key] == 120


@pytest.mark.asyncio
async def test_lease_maintenance_requests_failure_restart_when_lease_is_lost() -> None:
    registry = RuntimeRegistry()
    registry._redis = FakeRedis()  # type: ignore[assignment]
    stop_event = asyncio.Event()
    lease_lost = asyncio.Event()

    await registry.maintain_lease(
        "worker",
        "missing-owner",
        stop_event,
        ttl_seconds=1,
        lease_lost_event=lease_lost,
    )

    assert stop_event.is_set()
    assert lease_lost.is_set()


def test_threaded_worker_guard_renews_outside_the_asyncio_loop(monkeypatch) -> None:
    client = FakeSyncRedis()
    lost = threading.Event()
    monkeypatch.setattr(
        "src.control_plane.runtime.redis_sync.from_url",
        lambda *_args, **_kwargs: client,
    )
    guard = ThreadedRuntimeGuard(
        redis_url="redis://example.invalid/0",
        key_prefix="test-runtime",
        service="worker",
        instance_id="worker-1",
        lease_ttl_seconds=60,
        heartbeat_ttl_seconds=45,
        metadata={"concurrency": 2},
        on_lease_lost=lost.set,
    )

    guard.start()
    deadline = time.monotonic() + 1
    while not client.heartbeat_written.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    guard.stop()

    assert client.heartbeat_written.is_set()
    assert client.heartbeat_expiry == 45
    assert not lost.is_set()


def test_threaded_worker_guard_fails_closed_when_ownership_changes(monkeypatch) -> None:
    client = FakeSyncRedis(owner_matches=False)
    lost = threading.Event()
    monkeypatch.setattr(
        "src.control_plane.runtime.redis_sync.from_url",
        lambda *_args, **_kwargs: client,
    )
    guard = ThreadedRuntimeGuard(
        redis_url="redis://example.invalid/0",
        key_prefix="test-runtime",
        service="worker",
        instance_id="worker-1",
        lease_ttl_seconds=60,
        heartbeat_ttl_seconds=45,
        metadata=None,
        on_lease_lost=lost.set,
    )

    guard.start()
    assert lost.wait(timeout=1)
    guard.stop()


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
