"""Worker cancellation drains parents and reaches dispatcher ownership cleanup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import TaskType
from src.services import task_worker as worker


@pytest.mark.parametrize("role,failure", [
    ("ai", "cancel"), ("ai", "dispatcher"), ("ai", "startup"),
    ("ai", "ready"), ("general", "cancel"), ("general", "startup"),
])
async def test_worker_failure_releases_runtime_resources(monkeypatch, role, failure):
    monkeypatch.setattr(worker.TASK_WORKER_CONFIG, "role", role)
    service = worker.WORKER_SERVICES[role]
    task_type = TaskType.GENERATE_REPORT if role == "ai" else TaskType.SYNC_LITERATURE
    events = []
    heartbeats = []
    entered = asyncio.Event()
    crash = asyncio.Event()

    async def release(*args):
        events.append("process-release")

    def start_guard(name, owner, **kwargs):
        assert name == service and owner == "test-worker"
        heartbeats.append(kwargs["metadata"]())
        return SimpleNamespace(stop=lambda: events.append("guard-stop"))

    registry = SimpleNamespace(
        new_instance_id=lambda _: "test-worker", acquire_lease=AsyncMock(return_value=True),
        lease_owner=AsyncMock(return_value=(None, True)),
        start_threaded_guard=start_guard,
        release_lease=AsyncMock(side_effect=release), remove_heartbeat=AsyncMock(), close=AsyncMock(),
    )
    monkeypatch.setattr(worker, "runtime_registry", registry)
    monkeypatch.setattr(worker, "control_plane_events", SimpleNamespace(
        publish=AsyncMock(), publish_task_event=AsyncMock(), close=AsyncMock(),
    ))
    monkeypatch.setattr(worker, "task_manager", SimpleNamespace(set_broadcast_hook=lambda _: None))

    class Dispatcher:
        def __init__(self):
            assert role == "ai"
        async def run(self, stop):
            try:
                if failure == "dispatcher":
                    await crash.wait()
                    raise RuntimeError("dispatcher crashed")
                await stop.wait()
            finally:
                events.append("dispatcher-stop")

    class Metrics:
        def __init__(self, *_):
            assert role == "ai"
        async def run(self, stop):
            await stop.wait()
        def heartbeat_metadata(self):
            return {}

    async def recover(stop, owner):
        await stop.wait()

    async def heartbeat(uuid, owner, finished):
        await finished.wait()

    async def execute(uuid):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("parent-cancel")

    async def requeue(tasks, owner):
        assert tasks == [("organizer", task_type)] and owner == "test-worker"
        events.append("parent-requeue")
        return 1

    claim = AsyncMock(side_effect=[("organizer", task_type), None])
    async def claim_next(*args, **kwargs):
        assert kwargs["role"] == role
        return await claim(*args, **kwargs) if claim.await_count < 2 else None

    for name, value in {
        "AIWorkDispatcher": Dispatcher, "RuntimeMetricsMonitor": Metrics,
        "recover_expired_leases": AsyncMock(return_value=0),
        "recover_interrupted_tasks_on_startup": AsyncMock(
            side_effect=RuntimeError("startup failed") if failure == "startup" else None, return_value=0,
        ),
        "_load_runtime_routes": AsyncMock(return_value=[{"available_for_routing": True, "runtime_model_capacity": 1}]),
        "_resume_knowledge_repairs_after_model_recovery": AsyncMock(),
        "_recover_stale_tasks": recover, "_heartbeat_claimed_task": heartbeat,
        "_claim_next_task_safely": claim_next, "execute_task": execute,
        "_requeue_interrupted_tasks_for_restart": requeue, "_release_task_memory": lambda: None,
        "POLL_INTERVAL_SECONDS": .01,
    }.items():
        monkeypatch.setattr(worker, name, value)
    def publish_ready():
        assert role == "ai"
        if failure == "ready":
            raise RuntimeError("ready failed")
    monkeypatch.setattr("src.services.model_dispatch_ready.publish_ready", publish_ready)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    if failure in {"startup", "ready"}:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            await worker.run_worker()
        registry.release_lease.assert_awaited_once_with(service, "test-worker")
        registry.close.assert_awaited_once()
        assert "guard-stop" in events
        if failure == "ready":
            assert events.index("dispatcher-stop") < events.index("process-release")
        assert not entered.is_set()
        return
    run = asyncio.create_task(worker.run_worker())
    await asyncio.wait_for(entered.wait(), 1)
    if failure == "cancel":
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, 1)
    else:
        crash.set()
        with pytest.raises(RuntimeError, match="dispatcher crashed"):
            await asyncio.wait_for(run, 1)
    assert "parent-cancel" in events and "parent-requeue" in events
    if role == "ai":
        assert events.index("dispatcher-stop") < events.index("process-release")
    else:
        assert "dispatcher-stop" not in events
        worker.recover_expired_leases.assert_not_awaited()
        worker._load_runtime_routes.assert_not_awaited()
    registry.release_lease.assert_awaited_once_with(service, "test-worker")
    registry.close.assert_awaited_once()
    assert "guard-stop" in events
    assert heartbeats[0]["role"] == role
    if role == "general":
        assert heartbeats[0]["ai_concurrency_current"] == 0
        assert heartbeats[0]["ai_concurrency_max"] == 0
