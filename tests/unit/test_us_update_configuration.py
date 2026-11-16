"""US range changes must retain the requested half-hour checks and pauses."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from scripts import configure_us_updates as setup


def fake_database(monkeypatch, rows):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: rows))), add=Mock(), commit=AsyncMock())
    @asynccontextmanager
    async def get_db():
        yield db
    monkeypatch.setattr(setup, "get_db", get_db)
    monkeypatch.setattr(setup, "automation_service", SimpleNamespace(reschedule_job=AsyncMock()))
    return db


def test_us_jobs_keep_half_hour_checks_and_default_to_incremental_ranges():
    for job in setup.desired_jobs().values():
        assert job["interval_minutes"] == 30 and job["daily_time"] is None
        assert not job["fill_missing"] and not job["force"]


async def test_configure_preserves_pauses_and_existing_job_history(monkeypatch):
    values = setup.desired_jobs()["us-daily"]
    row = SimpleNamespace(job_id="us-daily", **{**values, "enabled": False, "fill_missing": True})
    row.last_task_uuid = "existing-history"
    db = fake_database(monkeypatch, [row])
    await setup.configure(apply=True)
    assert row.enabled is False and row.fill_missing is False and row.interval_minutes == 30
    assert row.last_task_uuid == "existing-history"
    db.commit.assert_awaited_once()
    assert setup.automation_service.reschedule_job.await_count == 2


async def test_preview_is_nonmutating_and_reapply_is_idempotent(monkeypatch):
    rows = [SimpleNamespace(job_id=key, **values) for key, values in setup.desired_jobs().items()]
    rows[0].fill_missing = True
    db = fake_database(monkeypatch, rows)
    await setup.configure()
    assert rows[0].fill_missing is True
    db.commit.assert_not_called()
    rows[0].fill_missing = False
    changes = await setup.configure(apply=True)
    assert all(change.action == "unchanged" for change in changes)
    setup.automation_service.reschedule_job.assert_not_awaited()
