"""Panel and scheduler instances share persisted schedules, not memory caches."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import automation_service as module
from src.services.automation_service import (
    AutomationJobConfig,
    AutomationJobState,
    AutomationService,
)
from src.domain import TaskStatus


@pytest.fixture
def instances(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    job = AutomationJobConfig(
        job_id="my-source", name="MY source", country_code="MY",
        source="my_idengue_daily", interval_minutes=1440, timezone="UTC",
    )
    persisted = {
        job.job_id: {
            "next_run_at": Clock.current + timedelta(days=1),
            "last_started_at": Clock.current - timedelta(minutes=5),
            "last_finished_at": Clock.current - timedelta(minutes=4),
            "last_status": "queued",
            "last_error": None,
            "last_task_uuid": "previous-task",
        }
    }

    async def load(kind):
        assert kind == "ingestion"
        return deepcopy(persisted)

    async def reschedule(kind, job_id, next_run_at, *, only_if_missing=False):
        assert kind == "ingestion"
        if not only_if_missing:
            persisted[job_id]["next_run_at"] = next_run_at

    async def save_result(kind, job_id, state, *, expected_next_run_at):
        assert kind == "ingestion"
        row = persisted[job_id]
        for field in row:
            if field != "next_run_at":
                row[field] = getattr(state, field)
        if row["next_run_at"] == expected_next_run_at:
            row["next_run_at"] = state.next_run_at
        return row["next_run_at"]

    monkeypatch.setattr(module.schedule_state_repository, "load", load)
    monkeypatch.setattr(module.schedule_state_repository, "reschedule", reschedule)
    monkeypatch.setattr(module.schedule_state_repository, "save_result", save_result)
    enqueue = AsyncMock(return_value=SimpleNamespace(
        created=True, task=SimpleNamespace(task_uuid="new-task"),
    ))
    monkeypatch.setattr(module.crawl_task_service, "enqueue_crawl_task", enqueue)
    panel, scheduler = AutomationService(), AutomationService()
    for service in (panel, scheduler):
        monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC"))
        monkeypatch.setattr(service, "load_jobs", AsyncMock(side_effect=lambda: [replace(job)]))
        monkeypatch.setattr(service, "requeue_due_automatic_retries", AsyncMock())
    scheduler._states[job.job_id] = AutomationJobState(
        next_run_at=persisted[job.job_id]["next_run_at"],
        run_count=7, skipped_count=2,
    )
    panel._states[job.job_id] = AutomationJobState(last_task_uuid="stale-api-cache")
    return SimpleNamespace(
        clock=Clock, job=job, persisted=persisted, panel=panel,
        scheduler=scheduler, enqueue=enqueue,
    )


async def test_panel_cadence_change_is_observed_without_scheduler_restart(instances):
    env = instances
    env.job.interval_minutes = 30
    await env.panel.reschedule_job(env.job.job_id)

    scheduled_at = env.clock.current + timedelta(minutes=30)
    assert env.persisted[env.job.job_id]["next_run_at"] == scheduled_at
    assert env.persisted[env.job.job_id]["last_task_uuid"] == "previous-task"
    assert env.persisted[env.job.job_id]["last_status"] == "queued"

    env.clock.current = scheduled_at - timedelta(seconds=1)
    await env.scheduler._run_tick()
    env.enqueue.assert_not_awaited()
    state = env.scheduler._states[env.job.job_id]
    assert state.next_run_at == scheduled_at
    assert (state.run_count, state.skipped_count) == (7, 2)

    env.clock.current = scheduled_at
    await env.scheduler._run_tick()
    env.enqueue.assert_awaited_once()
    assert env.enqueue.call_args.kwargs["metadata"]["scheduled_trigger"] is True
    assert (state.run_count, state.skipped_count) == (8, 2)
    assert env.persisted[env.job.job_id]["next_run_at"] == scheduled_at + timedelta(minutes=30)
    assert env.persisted[env.job.job_id]["last_task_uuid"] == "new-task"
    await env.scheduler._run_tick()
    env.enqueue.assert_awaited_once()


def test_new_province_jobs_first_run_staggered_without_changing_later_cadence(instances):
    env = instances
    job = AutomationJobConfig(job_id="cn-bj-monthly-weekly", name="Beijing", country_code="CN-BJ",
                              source="cn_province_monthly_report", interval_minutes=10080, timezone="UTC")
    state = AutomationJobState()
    env.scheduler._sync_state_schedule(job, state, reset=True)
    assert state.next_run_at == env.clock.current + timedelta(minutes=12)
    state.last_task_uuid = "first-task"
    state.last_started_at = env.clock.current
    env.scheduler._sync_state_schedule(job, state, reset=True)
    assert state.next_run_at == env.clock.current + timedelta(days=7)


@pytest.mark.parametrize("stale_source", [False, True])
async def test_snapshot_ranks_latest_task_and_success_per_job(monkeypatch, stale_source):
    service = AutomationService()
    job = AutomationJobConfig(job_id="cn-ah-monthly-weekly", name="Anhui",
                              country_code="CN-AH", source="cn_province_monthly_report",
                              interval_minutes=10080, timezone="UTC")
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(task_uuid="ah-task", status=TaskStatus.COMPLETED,
                           input_data={"automation_job_id": job.job_id},
                           completed_at=now, started_at=now, last_error=None,
                           metadata_=({"province_ingestion": {"status": "parsed", "source_audit": {
                               "latest_report_month": (now - timedelta(days=180)).strftime("%Y-%m"),
                           }}} if stale_source else {}))
    statements = []

    async def execute(statement):
        statements.append(statement)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [task]))

    @asynccontextmanager
    async def database():
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(module.system_settings_service, "build_smtp_status",
                        lambda: {"admin_emails": [], "alerting_ready": False})
    monkeypatch.setattr(service, "ensure_storage", AsyncMock())
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[job]))
    monkeypatch.setattr(service, "_refresh_persisted_states", AsyncMock())
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=True, timezone="UTC", poll_interval_seconds=60, default_retry_threshold=3,
    ))
    snapshot = await service.snapshot_async()
    assert snapshot["jobs"][0]["last_task_uuid"] == "ah-task"
    expected = "stale_source" if stale_source else "healthy"
    assert snapshot["jobs"][0]["health_status"] == expected
    # Validate the actual service projection through both public response paths.
    from dashboard.api.schemas.sources import AutomationConfigOut
    from dashboard.api.routers.sources import _automation_job_out
    assert AutomationConfigOut(**snapshot).jobs[0].health_status == expected
    api_job = SimpleNamespace(**vars(job), notes=None)
    assert _automation_job_out(api_job, snapshot["jobs"][0]).health_status == expected
    assert len(statements) == 2
    assert all("row_number()" in str(statement).lower() for statement in statements)
    assert all(statement._limit_clause is None for statement in statements)


async def test_panel_pause_resume_does_not_reuse_stale_due_time(instances):
    env = instances
    env.scheduler._states[env.job.job_id].next_run_at = env.clock.current - timedelta(days=1)
    env.job.enabled = False
    await env.panel.reschedule_job(env.job.job_id)
    await env.scheduler._run_tick()
    env.enqueue.assert_not_awaited()
    assert env.scheduler._states[env.job.job_id].next_run_at is None

    env.job.enabled = True
    env.job.interval_minutes = 30
    await env.panel.reschedule_job(env.job.job_id)
    await env.scheduler._run_tick()
    env.enqueue.assert_not_awaited()
    assert env.scheduler._states[env.job.job_id].next_run_at == env.clock.current + timedelta(minutes=30)
    assert env.scheduler._states[env.job.job_id].run_count == 7
    env.clock.current += timedelta(minutes=30)
    await env.scheduler._run_tick()
    env.enqueue.assert_awaited_once()


@pytest.mark.parametrize("pause", [False, True])
@pytest.mark.parametrize("manual", [False, True])
async def test_panel_edit_while_enqueue_is_awaiting_preserves_new_schedule(instances, pause, manual):
    env = instances
    env.persisted[env.job.job_id]["next_run_at"] = env.clock.current - timedelta(seconds=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def enqueue(**_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(created=True, task=SimpleNamespace(task_uuid="new-task"))

    env.enqueue.side_effect = enqueue
    dispatch = asyncio.create_task(env.scheduler.trigger_job(env.job.job_id, manual=manual))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        env.job.enabled = not pause
        env.job.interval_minutes = 30
        await env.panel.reschedule_job(env.job.job_id)
        panel_next = None if pause else env.clock.current + timedelta(minutes=30)
        assert env.persisted[env.job.job_id]["next_run_at"] == panel_next
        # Rescheduling cannot erase evidence of the previous execution.
        assert env.persisted[env.job.job_id]["last_task_uuid"] == "previous-task"
    finally:
        release.set()
        result = await asyncio.wait_for(dispatch, timeout=1)

    assert result["status"] == "queued"
    assert env.persisted[env.job.job_id]["next_run_at"] == panel_next
    assert env.scheduler._states[env.job.job_id].next_run_at == panel_next
    assert env.persisted[env.job.job_id]["last_task_uuid"] == "new-task"
    assert env.scheduler._states[env.job.job_id].run_count == 8
    env.enqueue.assert_awaited_once()


@pytest.mark.parametrize("pause", [False, True])
async def test_panel_change_between_due_selection_and_enqueue_is_rechecked(instances, pause):
    env = instances
    env.persisted[env.job.job_id]["next_run_at"] = env.clock.current - timedelta(seconds=1)
    original_trigger = env.scheduler.trigger_job

    async def panel_edit_then_trigger(job_id, *, manual):
        env.job.enabled = not pause
        env.job.interval_minutes = 30
        await env.panel.reschedule_job(job_id)
        return await original_trigger(job_id, manual=manual)

    env.scheduler.trigger_job = panel_edit_then_trigger
    await env.scheduler._run_tick()
    env.enqueue.assert_not_awaited()
    assert env.persisted[env.job.job_id]["last_task_uuid"] == "previous-task"
    assert env.scheduler._states[env.job.job_id].run_count == 7


@pytest.mark.parametrize("manual", [False, True])
async def test_pause_after_job_snapshot_before_final_schedule_refresh(instances, monkeypatch, manual):
    env = instances
    env.persisted[env.job.job_id]["next_run_at"] = env.clock.current - timedelta(seconds=1)

    async def read_then_pause():
        snapshot = replace(env.job)
        env.job.enabled = False
        await env.panel.reschedule_job(env.job.job_id)
        return [snapshot]

    monkeypatch.setattr(env.scheduler, "load_jobs", read_then_pause)
    result = await env.scheduler.trigger_job(env.job.job_id, manual=manual)
    assert env.persisted[env.job.job_id]["next_run_at"] is None
    if manual:
        assert result["status"] == "queued"
        env.enqueue.assert_awaited_once()
    else:
        assert result["status"] == "not_due"
        env.enqueue.assert_not_awaited()
        assert env.persisted[env.job.job_id]["last_task_uuid"] == "previous-task"


async def test_explicit_manual_run_does_not_resume_a_paused_job(instances):
    env = instances
    env.job.enabled = False
    await env.panel.reschedule_job(env.job.job_id)
    result = await env.scheduler.trigger_job(env.job.job_id, manual=True)
    assert result["status"] == "queued"
    assert env.enqueue.call_args.kwargs["metadata"]["manual_trigger"] is True
    assert env.persisted[env.job.job_id]["next_run_at"] is None


@pytest.mark.parametrize("source", ["kdca_death_annual", "kdca_region_annual"])
@pytest.mark.parametrize("manual", [False, True])
async def test_missing_credentials_skip_dispatch_and_resume_when_supplied(instances, monkeypatch, source, manual):
    env = instances
    env.job.country_code = "KR"
    env.job.source = source
    env.job.interval_minutes = 30
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    monkeypatch.delenv("KDCA_EID_SERVICE_KEY", raising=False)
    env.persisted[env.job.job_id]["next_run_at"] = env.clock.current

    result = await env.scheduler.trigger_job(env.job.job_id, manual=manual)
    assert result["status"] == "skipped"
    assert "DATA_GO_KR_SERVICE_KEY" in result["reason"]
    env.enqueue.assert_not_awaited()
    state = env.scheduler._states[env.job.job_id]
    assert (state.run_count, state.skipped_count) == (7, 3)
    assert env.persisted[env.job.job_id]["last_task_uuid"] == "previous-task"
    assert env.persisted[env.job.job_id]["last_status"] == "skipped"
    assert env.persisted[env.job.job_id]["next_run_at"] == env.clock.current + timedelta(minutes=30)

    monkeypatch.setenv("KDCA_EID_SERVICE_KEY", "test-key")
    env.clock.current += timedelta(minutes=30)
    result = await env.scheduler.trigger_job(env.job.job_id, manual=manual)
    assert result["status"] == "queued"
    env.enqueue.assert_awaited_once()
    assert (state.run_count, state.skipped_count) == (8, 3)
