"""A completed scheduled task can still have incomplete source coverage."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Task, TaskStatus, TaskType
from src.domain.collection import RunStage
from src.services import automation_service as module
from src.services.automation_service import AutomationJobConfig, AutomationService
from src.services.ingestion_health import latest_source_audit_statuses


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, RunStage.__table__],
        ))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def stage(task_uuid, attempt, status, *, name="organize", audit_status=None):
    audit = {"status": audit_status} if audit_status is not None else {}
    return RunStage(
        task_uuid=task_uuid, stage=name, attempt=attempt, status=status,
        started_at=datetime.now(timezone.utc),
        metrics={"source_audit": audit},
    )


async def test_only_current_attempt_completed_organize_audit_is_projected(sessions):
    async with sessions() as db:
        db.add_all([
            stage("recovered", 1, "completed", audit_status="partial"),
            stage("recovered", 2, "completed", audit_status="validated"),
            stage("collect-only", 1, "completed", audit_status="partial"),
            stage("collect-only", 2, "failed", name="collect"),
            stage("organize-failed", 1, "failed", audit_status="partial"),
            stage("legacy", 1, "completed"),
            stage("outside-request", 1, "completed", audit_status="partial"),
        ])
        await db.commit()
        result = await latest_source_audit_statuses(
            db, ["recovered", "collect-only", "organize-failed", "legacy"],
        )
    assert result == {"recovered": "validated"}


@pytest.mark.parametrize("audit_status,expected", [
    ("partial", "partial"),
    ("partial_year", "partial"),
    ("parsed_with_gaps", "partial"),
    ("needs_review", "needs_review"),
    ("no_verified_observations", "needs_review"),
    ("numerical_contract_unverified", "needs_review"),
    ("source_access_blocked", "needs_review"),
    ("documents_only", "needs_review"),
    ("validated", "healthy"),
    ("parsed", "healthy"),
    ("fresh", "healthy"),
    (None, "healthy"),
])
def test_source_quality_is_distinct_from_task_completion(audit_status, expected):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(status=TaskStatus.COMPLETED, completed_at=now, metadata_={})
    job = AutomationJobConfig(job_id="th-monthly", name="TH monthly", country_code="TH")
    result = module._ingestion_job_health(
        job, task, task, now=now, source_audit_status=audit_status,
    )
    assert result["health_status"] == expected
    if expected != "healthy":
        assert audit_status in result["health_reason"]


@pytest.mark.parametrize("status,enabled,expected", [
    (TaskStatus.COMPLETED, False, "disabled"),
    (TaskStatus.PENDING, True, "active"),
    (TaskStatus.QUEUED, True, "active"),
    (TaskStatus.RUNNING, True, "active"),
    (TaskStatus.RETRYING, True, "recovering"),
    (TaskStatus.FAILED, True, "failed"),
    (TaskStatus.CANCELLED, True, "failed"),
])
def test_lifecycle_verdict_precedes_source_quality(status, enabled, expected):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(status=status, completed_at=now, metadata_={})
    job = AutomationJobConfig(
        job_id="th-monthly", name="TH monthly", country_code="TH", enabled=enabled,
    )
    result = module._ingestion_job_health(
        job, task, task, now=now, source_audit_status="partial",
    )
    assert result["health_status"] == expected


def test_validated_audit_does_not_hide_stale_or_missing_completion():
    now = datetime.now(timezone.utc)
    job = AutomationJobConfig(
        job_id="th-monthly", name="TH monthly", country_code="TH", interval_minutes=30,
    )
    task = SimpleNamespace(
        status=TaskStatus.COMPLETED, completed_at=now - timedelta(hours=2), metadata_={},
    )
    assert module._ingestion_job_health(
        job, task, task, now=now, source_audit_status="validated",
    )["health_status"] == "stale"
    assert module._ingestion_job_health(
        job, task, None, now=now, source_audit_status="validated",
    )["health_status"] == "unknown"


async def test_snapshot_batches_current_source_quality_and_preserves_public_projection(
    sessions, monkeypatch,
):
    now = datetime.now(timezone.utc)
    jobs = [AutomationJobConfig(
        job_id=job_id, name=job_id, country_code="TH", source=source,
        interval_minutes=30, timezone="UTC",
    ) for job_id, source in [
        ("monthly", "th_doe506_monthly"), ("weekly", "th_dds_weekly"),
    ]]
    async with sessions() as db:
        for job_id, task_uuid, created_at in [
            ("monthly", "previous-monthly", now - timedelta(hours=1)),
            ("monthly", "current-monthly", now),
            ("weekly", "current-weekly", now),
        ]:
            db.add(Task(
                task_uuid=task_uuid, task_name=task_uuid, task_type=TaskType.CRAWL_DATA,
                status=TaskStatus.COMPLETED, created_at=created_at,
                started_at=created_at, completed_at=created_at,
                input_data={"automation_job_id": job_id},
            ))
        db.add_all([
            stage("previous-monthly", 1, "completed", audit_status="validated"),
            stage("current-monthly", 1, "completed", audit_status="partial"),
            stage("current-weekly", 1, "completed", audit_status="partial"),
            stage("current-weekly", 2, "completed", audit_status="validated"),
        ])
        await db.commit()

    @asynccontextmanager
    async def database():
        async with sessions() as db:
            yield db

    statements = []
    engine = sessions.kw["bind"]

    def record_statement(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    service = AutomationService()
    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(module.system_settings_service, "build_smtp_status",
                        lambda: {"admin_emails": [], "alerting_ready": False})
    monkeypatch.setattr(service, "ensure_storage", AsyncMock())
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=jobs))
    monkeypatch.setattr(service, "_refresh_persisted_states", AsyncMock())
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=True, timezone="UTC", poll_interval_seconds=60, default_retry_threshold=3,
    ))
    snapshot = await service.snapshot_async()
    by_job = {job["job_id"]: job for job in snapshot["jobs"]}
    assert by_job["monthly"]["health_status"] == "partial"
    assert by_job["monthly"]["last_task_uuid"] == "current-monthly"
    assert by_job["weekly"]["health_status"] == "healthy"
    assert len(statements) == 3  # Two latest-task queries plus one shared scalar projection.
    assert "JSON_EXTRACT" in statements[-1]

    from dashboard.api.routers.sources import _automation_job_out
    from dashboard.api.schemas.sources import AutomationConfigOut

    assert AutomationConfigOut(**snapshot).jobs[0].health_status == "partial"
    api_job = SimpleNamespace(**vars(jobs[0]), notes=None)
    assert _automation_job_out(api_job, by_job["monthly"]).health_status == "partial"
