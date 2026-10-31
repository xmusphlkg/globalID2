"""A cancelled province worker must drain before its stage becomes terminal."""
import asyncio
from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests

from src.services.exceptions import TaskCancelledError
from src.services.ingestion.countries import cn_province


@pytest.mark.parametrize("today,force,start_year,expected", [
    (date(2026, 10, 30), False, None, [(2026, 7), (2026, 8), (2026, 9)]),
    (date(2026, 10, 30), True, None, [(2026, m) for m in range(1, 10)]),
    (date(2026, 1, 10), False, None, [(2025, m) for m in (10, 11, 12)]),
    (date(2026, 10, 30), True, 2025, [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 10)]),
])
def test_datacenter_never_requests_future_or_open_month(monkeypatch, today, force, start_year, expected):
    called = []
    class Center:
        config = {"disease_ids": ["pertussis"]}
        def __init__(self, **kwargs): pass
        def available_years(self): return [2025, 2026, 2027]
        def _fetch_report(self, year, disease, month):
            called.append((year, month))
            return []
    monkeypatch.setattr(cn_province, "ProvinceDataCenterCrawler", Center)
    plan = cn_province._collect(None, country_code="CN-BJ", source="cn_province_datacenter",
                               force=force, start_year=start_year, recent_months=3, today=today)
    assert called == expected
    assert [(y, m) for y, _, m in plan["datacenter"]] == expected


@pytest.mark.asyncio
async def test_cancel_waits_for_thread_exit_before_session_close_or_plan_write(monkeypatch):
    entered, release, exited = Event(), Event(), Event()
    closed = []
    writer = SimpleNamespace(write=AsyncMock(), artifacts=[])
    def collect_in_thread(session, *, cancel_event, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            cn_province._check_cancelled(cancel_event)
            raise AssertionError("cancelled worker must not continue")
        finally:
            exited.set()
    monkeypatch.setattr(cn_province, "_collect", collect_in_thread)
    monkeypatch.setattr(cn_province._RecordingSession, "close", lambda self: closed.append(exited.is_set()))
    task = asyncio.create_task(cn_province.collect(writer, source="cn_province_datacenter", country_code="CN-BJ"))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # repeated cancellation must not bypass draining
    await asyncio.sleep(0)
    assert not task.done()
    assert not closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()
    assert closed == [True]
    writer.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_recording_session_does_not_write_response_arriving_after_cancel(monkeypatch):
    stop = Event()
    writer = SimpleNamespace(write=AsyncMock())
    def get(self, url, **kwargs):
        stop.set()
        response = requests.Response()
        response.status_code = 200
        response._content = b"data"
        return response
    monkeypatch.setattr(requests.Session, "get", get)
    session = cn_province._RecordingSession(writer, asyncio.get_running_loop(), stop)
    with pytest.raises(TaskCancelledError):
        await asyncio.to_thread(session.get, "https://official.example/report")
    writer.write.assert_not_awaited()
