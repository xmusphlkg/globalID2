"""Required scheduler loops must recover pool pressure or fail visibly."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from src.control_plane.scheduler import _wait_for_scheduler_tasks
from src.control_plane import scheduler as scheduler_module
from src.control_plane import schedule_state as schedule_state_module
from src.services import literature_service as literature_module
from src.services.literature_service import LiteratureService


async def test_literature_loop_retries_pool_timeout_and_dispatches_overdue_sync(monkeypatch):
    service = LiteratureService()
    service._stop_event = asyncio.Event()
    overdue = datetime.now(timezone.utc) - timedelta(hours=1)
    config = SimpleNamespace(
        schedule_enabled=True, catch_up_enabled=True, timezone="UTC",
        ai_enrichment_enabled=False, weekly_ai_review_enabled=False,
        ai_enrichment_schedule_enabled=False, gap_discovery_schedule_enabled=False,
        poll_interval_seconds=0.001,
    )
    load = AsyncMock(side_effect=[
        PoolTimeoutError("QueuePool limit reached"),
        {service.JOB_ID: {"next_run_at": overdue}},
    ])
    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_module.schedule_state_repository, "load", load)

    async def dispatch(job_id, *, manual):
        assert job_id == service.JOB_ID
        assert manual is False
        service._stop_event.set()

    trigger = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(service, "trigger_job", trigger)
    await asyncio.wait_for(service._run_loop(), timeout=1)
    assert load.await_count == 2
    trigger.assert_awaited_once()
    assert service._state.next_run_at == overdue


async def test_unexpected_literature_failure_propagates_to_process_supervisor(monkeypatch):
    service = LiteratureService()
    service._stop_event = asyncio.Event()
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(poll_interval_seconds=0.001))
    failure = ValueError("invalid schedule state")
    monkeypatch.setattr(service, "_run_tick", AsyncMock(side_effect=failure))
    with pytest.raises(ValueError, match="invalid schedule state"):
        await service._run_loop()


async def test_repository_pool_timeout_does_not_discard_in_memory_overdue_schedule(monkeypatch):
    service = LiteratureService()
    service._stop_event = asyncio.Event()
    overdue = datetime.now(timezone.utc) - timedelta(hours=1)
    service._state.next_run_at = overdue
    config = SimpleNamespace(
        schedule_enabled=True, catch_up_enabled=True, timezone="UTC",
        ai_enrichment_enabled=False, weekly_ai_review_enabled=False,
        ai_enrichment_schedule_enabled=False, gap_discovery_schedule_enabled=False,
        poll_interval_seconds=0.001,
    )

    @asynccontextmanager
    async def unavailable():
        raise PoolTimeoutError("QueuePool limit reached")
        yield  # pragma: no cover

    monkeypatch.setattr(schedule_state_module, "get_database", unavailable)
    monkeypatch.setattr(service, "_config", lambda: config)

    async def dispatch(*_args, **_kwargs):
        service._stop_event.set()

    trigger = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(service, "trigger_job", trigger)
    await asyncio.wait_for(service._run_loop(), timeout=1)
    trigger.assert_awaited_once_with(service.JOB_ID, manual=False)
    assert service._state.next_run_at == overdue


@pytest.mark.parametrize("outcome", ["error", "return", "cancel"])
async def test_process_supervisor_rejects_required_loop_exit(outcome):
    stop = asyncio.Event()
    failure = ValueError("schedule state corrupt")

    async def child():
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise asyncio.CancelledError

    task = asyncio.create_task(child(), name="globalid-literature-scheduler")
    with pytest.raises(RuntimeError, match="globalid-literature-scheduler") as caught:
        await asyncio.wait_for(_wait_for_scheduler_tasks(stop, [task]), timeout=1)
    assert caught.value.__cause__ is (failure if outcome == "error" else None)


async def test_process_supervisor_accepts_normal_shutdown_without_cancelling_child():
    stop = asyncio.Event()
    child = asyncio.create_task(stop.wait(), name="normal-scheduler")
    supervisor = asyncio.create_task(_wait_for_scheduler_tasks(stop, [child]))
    stop.set()
    await asyncio.wait_for(supervisor, timeout=1)
    await child
    assert not child.cancelled()


async def test_failed_child_stops_other_services_and_releases_process_lease(monkeypatch):
    class Service:
        def __init__(self, name):
            self.name = name
            self.stop_event = asyncio.Event()
            self.stopped = False
            self._task = None

        async def start(self):
            async def run():
                if self.name == "literature_service":
                    raise ValueError("broken literature loop")
                await self.stop_event.wait()
            self._task = asyncio.create_task(run(), name=self.name)

        async def stop(self):
            self.stopped = True
            self.stop_event.set()
            await self._task

    names = (
        "automation_service", "data_release_service", "disease_mapping_automation_service",
        "literature_service", "knowledge_automation_service", "idle_backlog_service",
        "situation_schedule_service", "report_schedule_service",
    )
    services = [Service(name) for name in names]
    for service in services:
        monkeypatch.setattr(scheduler_module, service.name, service)

    async def heartbeat(_service, _instance, stop):
        await stop.wait()

    async def maintain(_service, _instance, stop, **_kwargs):
        await stop.wait()

    async def worker_watch(stop, *_args, **_kwargs):
        await stop.wait()

    registry = SimpleNamespace(
        new_instance_id=Mock(return_value="test-owner"), acquire_lease=AsyncMock(return_value=True),
        wait_for_service=AsyncMock(return_value=True), run_heartbeat=heartbeat,
        maintain_lease=maintain, release_lease=AsyncMock(), remove_heartbeat=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(scheduler_module, "runtime_registry", registry)
    monkeypatch.setattr(scheduler_module, "_monitor_worker", worker_watch)
    monkeypatch.setattr(scheduler_module, "run_usage_collector", worker_watch)
    monkeypatch.setattr(scheduler_module, "control_plane_events", SimpleNamespace(
        publish=AsyncMock(), publish_task_event=AsyncMock(), close=AsyncMock(),
    ))
    monkeypatch.setattr(scheduler_module, "task_manager", SimpleNamespace(set_broadcast_hook=Mock()))
    monkeypatch.setattr(scheduler_module, "get_config", lambda: SimpleNamespace(
        task_worker=SimpleNamespace(scheduler_worker_grace_seconds=1),
    ))
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", Mock())

    with pytest.raises(RuntimeError, match="literature_service"):
        await asyncio.wait_for(scheduler_module.run_scheduler(), timeout=1)
    assert all(service.stopped for service in services)
    registry.release_lease.assert_awaited_once_with("scheduler", "test-owner")
    registry.remove_heartbeat.assert_awaited_once_with("scheduler", "test-owner")
    registry.close.assert_awaited_once()
