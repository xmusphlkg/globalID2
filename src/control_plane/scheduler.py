"""Standalone scheduler process for all control-plane background loops."""

from __future__ import annotations

import asyncio
import signal

from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.logging import get_logger
from src.core.task_manager import task_manager
from src.services.automation_service import automation_service
from src.services.data_release_service import data_release_service
from src.services.disease_mapping_automation_service import disease_mapping_automation_service

logger = get_logger(__name__)


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
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)
    heartbeat = asyncio.create_task(
        runtime_registry.run_heartbeat("scheduler", instance_id, stop_event),
        name="control-plane-scheduler-heartbeat",
    )
    lease_maintenance = asyncio.create_task(
        runtime_registry.maintain_lease("scheduler", instance_id, stop_event),
        name="control-plane-scheduler-lease",
    )

    logger.info("Control-plane scheduler starting")
    try:
        await automation_service.start()
        await data_release_service.start()
        await disease_mapping_automation_service.start()
        await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)
        await stop_event.wait()
    finally:
        await disease_mapping_automation_service.stop()
        await data_release_service.stop()
        await automation_service.stop()
        stop_event.set()
        await lease_maintenance
        await heartbeat
        await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
        await runtime_registry.release_lease("scheduler", instance_id)
        task_manager.set_broadcast_hook(None)
        await runtime_registry.close()
        await control_plane_events.close()
        logger.info("Control-plane scheduler stopped")


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()


__all__ = ["run_scheduler", "main"]
