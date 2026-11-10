"""Worker cancellation drains parents and reaches dispatcher ownership cleanup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import TaskType
from src.services import task_worker as worker


@pytest.mark.parametrize("failure", ["cancel", "dispatcher", "startup"])
async def test_worker_failure_releases_runtime_resources(monkeypatch, failure):
    events = []
    entered = asyncio.Event()
    crash = asyncio.Event()

    async def release(*args):
        events.append("process-release")

    registry = SimpleNamespace(
        new_instance_id=lambda _: "test-worker", acquire_lease=AsyncMock(return_value=True),
        start_threaded_guard=lambda *args, **kwargs: SimpleNamespace(stop=lambda: events.append("guard-stop")),
        release_lease=AsyncMock(side_effect=release), remove_heartbeat=AsyncMock(), close=AsyncMock(),
    )
    monkeypatch.setattr(worker, "runtime_registry", registry)
    monkeypatch.setattr(worker, "control_plane_events", SimpleNamespace(
        publish=AsyncMock(), publish_task_event=AsyncMock(), close=AsyncMock(),
    ))
    monkeypatch.setattr(worker, "task_manager", SimpleNamespace(set_broadcast_hook=lambda _: None))

    class Dispatcher:
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
            pass
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
        assert tasks == [("organizer", TaskType.GENERATE_REPORT)] and owner == "test-worker"
        events.append("parent-requeue")
        return 1

    claim = AsyncMock(side_effect=[("organizer", TaskType.GENERATE_REPORT), None])
    async def claim_next(*args, **kwargs):
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
    monkeypatch.setattr("src.services.model_dispatch_ready.publish_ready", lambda: None)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    if failure == "startup":
        with pytest.raises(RuntimeError, match="startup failed"):
            await worker.run_worker()
        registry.release_lease.assert_awaited_once_with("worker", "test-worker")
        registry.close.assert_awaited_once()
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
    assert events.index("dispatcher-stop") < events.index("process-release")
    registry.release_lease.assert_awaited_once_with("worker", "test-worker")
    registry.close.assert_awaited_once()
    assert "guard-stop" in events
