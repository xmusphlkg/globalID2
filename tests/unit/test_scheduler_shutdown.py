import asyncio
from types import SimpleNamespace

import pytest

from src.services.automation_service import AutomationService
from src.services.data_release_service import DataReleaseService


@pytest.mark.asyncio
@pytest.mark.parametrize("service_class", [AutomationService, DataReleaseService])
async def test_idle_scheduler_stops_without_waiting_for_poll_interval(
    monkeypatch, service_class
):
    service = service_class()
    service._stop_event = asyncio.Event()

    monkeypatch.setattr(
        service, "_config", lambda: SimpleNamespace(poll_interval_seconds=60)
    )

    async def no_jobs():
        return []

    monkeypatch.setattr(service, "load_jobs", no_jobs)
    service._task = asyncio.create_task(service._run_loop())
    await asyncio.sleep(0)

    await asyncio.wait_for(service.stop(), timeout=1)

    assert service._task is None
    assert service._stop_event is None
