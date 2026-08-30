"""Standalone scheduler process for all control-plane background loops."""

from __future__ import annotations

import asyncio
import signal

from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.config import get_config
from src.core.logging import get_logger
from src.core.task_manager import task_manager
from src.services.automation_service import automation_service
from src.services.data_release_service import data_release_service
from src.services.disease_mapping_automation_service import disease_mapping_automation_service
from src.services.literature_service import literature_service

logger = get_logger(__name__)


async def _monitor_worker(
    stop_event: asyncio.Event,
    worker_unavailable: asyncio.Event,
    *,
    grace_seconds: int,
) -> None:
    """Stop scheduling when the task consumer disappears beyond the grace window."""
    loop = asyncio.get_running_loop()
    missing_since: float | None = None
    while not stop_event.is_set():
        live, registry_available = await runtime_registry.service_is_live("worker")
        if live:
            missing_since = None
        else:
            missing_since = missing_since or loop.time()
            if not registry_available or loop.time() - missing_since >= grace_seconds:
                logger.error(
                    "Task worker heartbeat unavailable; stopping scheduler to prevent queue growth"
                )
                worker_unavailable.set()
                stop_event.set()
                return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


async def run_scheduler() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    instance_id = runtime_registry.new_instance_id("scheduler")
    if not await runtime_registry.acquire_lease("scheduler", instance_id):
        raise RuntimeError(
            "Another scheduler owns the singleton lease or Redis is unavailable"
        )
    worker_grace_seconds = get_config().task_worker.scheduler_worker_grace_seconds
    if not await runtime_registry.wait_for_service(
        "worker",
        stop_event,
        timeout_seconds=worker_grace_seconds,
    ):
        await runtime_registry.release_lease("scheduler", instance_id)
        await runtime_registry.close()
        raise RuntimeError(
            "Task worker did not become ready before scheduler startup deadline"
        )
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)
    worker_unavailable = asyncio.Event()
    lease_lost = asyncio.Event()
    heartbeat = asyncio.create_task(
        runtime_registry.run_heartbeat("scheduler", instance_id, stop_event),
        name="control-plane-scheduler-heartbeat",
    )
    lease_maintenance = asyncio.create_task(
        runtime_registry.maintain_lease(
            "scheduler",
            instance_id,
            stop_event,
            lease_lost_event=lease_lost,
        ),
        name="control-plane-scheduler-lease",
    )
    worker_monitor = asyncio.create_task(
        _monitor_worker(
            stop_event,
            worker_unavailable,
            grace_seconds=worker_grace_seconds,
        ),
        name="control-plane-scheduler-worker-watchdog",
    )

    logger.info("Control-plane scheduler starting")
    try:
        await automation_service.start()
        await data_release_service.start()
        await disease_mapping_automation_service.start()
        await literature_service.start()
        await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)
        await stop_event.wait()
    finally:
        await literature_service.stop()
        await disease_mapping_automation_service.stop()
        await data_release_service.stop()
        await automation_service.stop()
        stop_event.set()
        await worker_monitor
        await lease_maintenance
        await heartbeat
        await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
        await runtime_registry.release_lease("scheduler", instance_id)
        await runtime_registry.remove_heartbeat("scheduler", instance_id)
        task_manager.set_broadcast_hook(None)
        await runtime_registry.close()
        await control_plane_events.close()
        logger.info("Control-plane scheduler stopped")
    if worker_unavailable.is_set() or lease_lost.is_set():
        reason = "task worker became unavailable" if worker_unavailable.is_set() else "singleton lease was lost"
        raise RuntimeError(f"Scheduler stopped because its {reason}")


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()


__all__ = ["run_scheduler", "main"]
