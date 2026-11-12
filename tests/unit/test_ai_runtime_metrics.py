"""Durable model occupancy includes every dispatcher and excludes stale leases."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")

from src.ai import runtime_metrics as metrics
from src.domain import Base
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.domain.ai_work_item import AIWorkItem


def route(model_id, key, provider_id=10, *, capacity=4, provider_capacity=8):
    return {
        "model_id": model_id,
        "model_key": key,
        "model_name": key.split(":")[-1],
        "provider_id": provider_id,
        "provider_key": key.split(":")[0],
        "provider_name": "custom",
        "api_style": "openai_compatible",
        "available_for_routing": True,
        "runtime_model_capacity": capacity,
        "runtime_model_inflight": 99,
        "runtime_provider_capacity": provider_capacity,
        "runtime_provider_max_capacity": provider_capacity,
        "runtime_provider_inflight": 99,
    }


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            AIProviderConfig.__table__, AIModelConfig.__table__, AIWorkItem.__table__,
        ]))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_database():
        async with maker() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(metrics, "get_database", get_database)
    async with maker() as db:
        db.add(AIProviderConfig(id=10, provider_key="provider", provider_name="custom", display_name="Provider"))
        for model_id, name, enabled in [(1, "first", True), (2, "second", True), (3, "disabled", False)]:
            db.add(AIModelConfig(id=model_id, provider_id=10, model_key=f"provider:{name}",
                                 model_name=name, display_name=name, is_enabled=enabled))
        await db.commit()
    yield maker
    await engine.dispose()


async def test_shared_occupancy_counts_other_processes_and_disabled_models(database):
    now = datetime.now(timezone.utc)
    async with database() as db:
        for key, status, delta, capability, owner in [
            ("provider:first", "leased", 30, "text", "main"),
            ("provider:first", "leased", 30, "text", "auxiliary"),
            ("provider:second", "leased", -1, "text", "expired"),
            ("provider:second", "queued", 30, "text", "queued"),
            ("provider:second", "succeeded", 30, "text", "done"),
            ("provider:disabled", "leased", 30, "text", "auxiliary"),
            ("removed:model", "leased", 30, "text", "unknown"),
            ("health:probe", "leased", 30, "health", "probe"),
        ]:
            db.add(AIWorkItem(parent_task_uuid=owner, line="review", priority="normal", status=status,
                              capability=capability, route_key=key, payload={}, lease_expires_at=now + timedelta(seconds=delta)))
        await db.commit()

    routes = [route(1, "provider:first"), route(2, "provider:second")]
    snapshot = await metrics.read_runtime_metrics(routes, now=now)
    assert snapshot.models["1"]["runtime_model_inflight"] == 2
    assert snapshot.models["2"]["runtime_model_inflight"] == 0
    assert snapshot.models["3"] == {"runtime_model_capacity": 0, "runtime_model_inflight": 1}
    assert snapshot.providers["10"]["runtime_provider_inflight"] == 3
    assert snapshot.unassigned_leases == 1
    assert snapshot.route_leases == {"provider:first": 2, "provider:disabled": 1, "removed:model": 1}
    applied = snapshot.apply_to_routes(routes)
    assert applied[0]["runtime_model_capacity"] == 4
    assert applied[0]["runtime_model_inflight"] == 2
    assert routes[0]["runtime_model_inflight"] == 99  # Shared catalogue is untouched.
    metadata = snapshot.heartbeat_metadata()
    assert metadata["ai_model_capacities"]["1"]["runtime_model_inflight"] == 2
    assert metadata["ai_provider_capacities"]["10"]["runtime_provider_inflight"] == 3
    assert metadata["ai_capacity_source"] == "database_leases"
    assert metadata["ai_capacity_sampled_at"] == now.isoformat()


async def test_runtime_api_uses_database_counts_without_worker_heartbeat(database, monkeypatch):
    from dashboard.api.routers import ai

    now = datetime.now(timezone.utc)
    async with database() as db:
        db.add(AIWorkItem(parent_task_uuid="auxiliary", line="review", priority="normal", status="leased",
                          route_key="provider:first", payload={}, lease_expires_at=now + timedelta(minutes=1)))
        await db.commit()
    monkeypatch.setattr(ai, "get_runtime_routes", AsyncMock(return_value=[route(1, "provider:first")]))
    result = await ai.list_runtime_routes()
    assert len(result) == 1
    assert result[0].runtime_model_inflight == 1
    assert result[0].runtime_provider_inflight == 1
    assert result[0].runtime_model_capacity == 4


async def test_runtime_api_preserves_zero_admitted_capacity(database, monkeypatch):
    from dashboard.api.routers import ai
    unavailable = route(1, "provider:first", capacity=0, provider_capacity=0)
    unavailable["available_for_routing"] = False
    monkeypatch.setattr(ai, "get_runtime_routes", AsyncMock(return_value=[unavailable]))
    result = await ai.list_runtime_routes()
    assert result[0].runtime_model_capacity == 0
    assert result[0].runtime_provider_capacity == 0
    assert result[0].runtime_provider_max_capacity == 0


async def test_monitor_preserves_last_observation_timestamp_on_failure(monkeypatch):
    stop = asyncio.Event()
    now = datetime.now(timezone.utc)
    expected = metrics.RuntimeCapacitySnapshot(now, {"1": {"runtime_model_inflight": 2}}, {}, {})
    calls = 0

    async def read(_routes):
        nonlocal calls
        calls += 1
        if calls == 1:
            return expected
        stop.set()
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(metrics, "read_runtime_metrics", read)
    monitor = metrics.RuntimeMetricsMonitor(AsyncMock(return_value=[]), interval_seconds=0.001)
    assert monitor.heartbeat_metadata()["ai_capacity_source"] == "unavailable"
    await asyncio.wait_for(monitor.run(stop), 1)
    assert monitor.snapshot is expected
    assert monitor.heartbeat_metadata()["ai_capacity_sampled_at"] == now.isoformat()
    assert monitor.heartbeat_metadata()["ai_model_capacities"]["1"]["runtime_model_inflight"] == 2
