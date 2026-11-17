"""Province job setup is scoped, previewable, and never multiplies national downloads."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import configure_cn_province_jobs as setup


def fake_database(monkeypatch, rows):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))),
                         add=Mock(), commit=AsyncMock())
    @asynccontextmanager
    async def database(): yield db
    monkeypatch.setattr(setup, "get_database", database)
    monkeypatch.setattr(setup, "automation_service", SimpleNamespace(reschedule_job=AsyncMock()))
    return db


def test_31_weekly_monthly_jobs_and_no_per_province_datacenter_jobs():
    jobs = setup.desired_jobs({"CN-BJ", "CN-XZ"})
    assert len(jobs) == 31
    assert {job["source"] for job in jobs.values()} == {"cn_province_monthly_report"}
    assert {job["interval_minutes"] for job in jobs.values()} == {10080}
    assert {job["country_code"] for job in jobs.values() if job["enabled"]} == {"CN-BJ"}
    assert not any(job["enabled"] for job in setup.desired_jobs().values())
    assert all(not job["force"] and job["fill_missing"] and job["revision_window_months"] == 3 for job in jobs.values())


async def test_preview_does_not_mutate_or_reschedule(monkeypatch):
    paused = SimpleNamespace(job_id="cn-bj-monthly-weekly", **setup.desired_jobs()["cn-bj-monthly-weekly"])
    db = fake_database(monkeypatch, [paused])
    changes = await setup.configure(enable=True, verified_provinces={"CN-BJ"})
    assert next(change for change in changes if change.job_id == paused.job_id).enabled
    assert paused.enabled is False
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    setup.automation_service.reschedule_job.assert_not_awaited()


async def test_apply_preserves_operator_pause_and_enables_only_verified_sources(monkeypatch):
    paused = SimpleNamespace(job_id="cn-bj-monthly-weekly", **setup.desired_jobs()["cn-bj-monthly-weekly"])
    db = fake_database(monkeypatch, [paused])
    await setup.configure(apply=True, enable=True, verified_provinces={"CN-TJ", "CN-XZ"})
    assert paused.enabled is False
    additions = [call.args[0] for call in db.add.call_args_list]
    assert {job.country_code for job in additions if job.enabled} == {"CN-TJ"}
    assert len(additions) == 30
    db.commit.assert_awaited_once()


async def test_enable_requires_explicit_verification(monkeypatch):
    db = fake_database(monkeypatch, [])
    with pytest.raises(ValueError, match="requires verified"):
        await setup.configure(apply=True, enable=True)
    db.execute.assert_not_awaited()


async def test_repeat_maintenance_is_idempotent(monkeypatch):
    rows = [SimpleNamespace(job_id=job_id, **values) for job_id, values in setup.desired_jobs({"CN-BJ"}).items()]
    db = fake_database(monkeypatch, rows)
    changes = await setup.configure(apply=True)
    assert all(change.action == "unchanged" for change in changes)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    setup.automation_service.reschedule_job.assert_not_awaited()
