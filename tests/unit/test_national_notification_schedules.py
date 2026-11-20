"""Schedule setup must preserve operator pauses and have a nonmutating preview."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo


from scripts import configure_national_notifications as setup
from src.core.source_scopes import canonicalize_task_source

HALF_HOUR_JOB_IDS = {
    "kr-kdca-monthly-daily", "kr-kdca-region-annual-daily", "kr-kdca-death-annual-daily",
    "th-dds-tableau-daily", "th-dds-weekly-daily", "th-doe506-daily",
    "th-id-th-history-daily", "my-moh-std-daily", "nl-rivm-notifiable-daily",
    "my-idengue-daily", "my-who-flunet-daily", "my-who-gho-daily",
    "nl-rivm-virological-daily", "nl-rivm-respiratory-daily",
}



def fake_database(monkeypatch, rows):
    monkeypatch.setattr(setup, "automation_service", SimpleNamespace(reschedule_job=AsyncMock()))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))), add=Mock(), commit=AsyncMock())

    @asynccontextmanager
    async def get_database():
        yield db

    monkeypatch.setattr(setup, "get_database", get_database)
    return db


def job(job_id, **overrides):
    return SimpleNamespace(job_id=job_id, **{**setup.desired_jobs()[job_id], **overrides})


def test_jobs_have_distinct_sources_and_publication_checks():
    desired = setup.desired_jobs()
    assert set(desired) == {
        "kr-kdca-monthly-daily", "kr-kdca-region-annual-daily", "kr-kdca-death-annual-daily",
        "th-dds-tableau-daily", "th-dds-weekly-daily", "th-doe506-daily", "th-id-th-history-daily",
        "my-moh-std-daily", "nl-rivm-notifiable-daily", "my-idengue-daily", "my-who-flunet-daily",
        "my-who-gho-daily", "nl-rivm-virological-daily", "nl-rivm-respiratory-daily",
    }
    assert {value["daily_time"] for value in desired.values()} == {None}
    for values in desired.values():
        assert canonicalize_task_source(values["source"], country_code=values["country_code"]) == values["source"]
        assert ZoneInfo(values["timezone"])
        assert values["process"] and values["save_raw"]
        assert not values["fill_missing"] and not values["force"]


def test_all_managed_jobs_use_half_hour_control_plane_intervals():
    desired = setup.desired_jobs()
    for job_id in HALF_HOUR_JOB_IDS:
        assert desired[job_id]["interval_minutes"] == 30
        assert desired[job_id]["daily_time"] is None
        assert "30-Minute" in desired[job_id]["name"]



async def test_interval_migration_retains_job_identity_history_and_operator_pauses(monkeypatch):
    rows = [job(job_id) for job_id in setup.desired_jobs()]
    for row in rows:
        if row.job_id in HALF_HOUR_JOB_IDS:
            row.name = "Previous Daily Publication Check"
            row.interval_minutes = None
            row.daily_time = "18:00"
            row.enabled = False
            row.last_task_uuid = "existing-task-history"
    db = fake_database(monkeypatch, rows)
    changes = await setup.configure(apply=True)
    assert {c.job_id for c in changes if c.changed_fields} == HALF_HOUR_JOB_IDS
    for row in rows:
        if row.job_id in HALF_HOUR_JOB_IDS:
            assert row.interval_minutes == 30 and row.daily_time is None
            assert row.enabled is False and row.last_task_uuid == "existing-task-history"
    db.add.assert_not_called()
    db.commit.assert_awaited_once()
    assert {call.args[0] for call in setup.automation_service.reschedule_job.await_args_list} == HALF_HOUR_JOB_IDS


async def test_preview_does_not_mutate_paused_job_or_add_missing_jobs(monkeypatch):
    existing = job("my-moh-std-daily", enabled=False, daily_time="18:00")
    db = fake_database(monkeypatch, [existing])
    changes = await setup.configure()
    assert existing.daily_time == "18:00" and existing.enabled is False
    assert next(change for change in changes if change.job_id == existing.job_id).enabled is False
    db.add.assert_not_called()
    db.commit.assert_not_called()
    setup.automation_service.reschedule_job.assert_not_awaited()


async def test_apply_preserves_pause_and_does_not_touch_other_jobs(monkeypatch):
    existing = job("my-moh-std-daily", enabled=False, daily_time="18:00")
    db = fake_database(monkeypatch, [existing])
    changes = await setup.configure(apply=True)
    assert existing.daily_time is None and existing.interval_minutes == 30 and existing.enabled is False
    assert db.add.call_count == len(setup.desired_jobs()) - 1
    assert setup.automation_service.reschedule_job.await_count == len(setup.desired_jobs())
    db.commit.assert_awaited_once()
    assert all(change.job_id in setup.desired_jobs() for change in changes)


async def test_explicit_enable_resumes_paused_managed_job(monkeypatch):
    existing = job("nl-rivm-notifiable-daily", enabled=False)
    db = fake_database(monkeypatch, [existing])
    await setup.configure(apply=True, enable=True)
    assert existing.enabled is True
    assert setup.automation_service.reschedule_job.await_count == len(setup.desired_jobs())
    db.commit.assert_awaited_once()


async def test_repeat_apply_is_idempotent_and_keeps_paused_jobs(monkeypatch):
    rows = [job(job_id, enabled=not job_id.startswith("my-")) for job_id in setup.desired_jobs()]
    db = fake_database(monkeypatch, rows)
    changes = await setup.configure(apply=True)
    assert all(change.action == "unchanged" and not change.changed_fields for change in changes)
    db.add.assert_not_called()
    db.commit.assert_not_called()


async def test_unchanged_apply_does_not_reschedule(monkeypatch):
    rows = [job(job_id) for job_id in setup.desired_jobs()]
    fake_database(monkeypatch, rows)
    await setup.configure(apply=True)
    setup.automation_service.reschedule_job.assert_not_awaited()
