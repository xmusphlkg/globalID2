"""Overview failed stages are current task failures, not historical stage rows."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from dashboard.api.routers.collection import (
    list_unresolved_failed_stages,
    public_stage_error,
)
from src.domain import Base, Task, TaskStatus, TaskType
from src.domain.collection import RunStage
from src.control_plane.overview import ControlPlaneOverviewService
from src.control_plane.runtime import REQUIRED_RUNTIME_SERVICES, runtime_registry

STARTED = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
FINISHED = datetime(2026, 10, 30, 12, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize("change,recovered", [
    ({}, True),
    ({"input_data": {"process": True, "source": "who_flunet_weekly", "country_code": "BN"}}, True),
    ({"input_data": {"source": "who_flunet_weekly", "country_code": "BN", "process": False}}, False),
    ({"input_data": {"source": "who_flunet_weekly", "country_code": "BN", "process": True, "start_year": 2025}}, False),
    ({"input_data": {"source": "other_source", "country_code": "BN", "process": True}}, False),
    ({"country_id": 2}, False),
    ({"status": TaskStatus.RUNNING}, False),
    ({"status": TaskStatus.FAILED}, False),
    ({"status": TaskStatus.CANCELLED}, False),
    ({"task_type": TaskType.PROCESS_DATA}, False),
    ({"id": 5}, False),
    ({"task_name": "[smoke] BN import"}, False),
    ({"tags": ["test"]}, False),
])
async def test_renamed_success_resolves_only_identical_collection_inputs(monkeypatch, change, recovered):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, RunStage.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(runtime_registry, "list_services", AsyncMock(return_value=(
        [{"service": name} for name in REQUIRED_RUNTIME_SERVICES], True)))
    inputs = {"source": "who_flunet_weekly", "country_code": "BN", "process": True}
    try:
        async with maker() as db:
            failed = Task(id=10, task_uuid="original-import", task_name="BN full history import",
                          task_type=TaskType.CRAWL_DATA, country_id=1,
                          status=TaskStatus.FAILED, input_data=inputs)
            db.add(failed)
            await db.flush()
            db.add(RunStage(task_uuid=failed.task_uuid, stage="store", attempt=1,
                            status="failed", error="Availability assertion missing",
                            started_at=STARTED, finished_at=FINISHED))
            fields = dict(task_uuid="verified-import", task_name="BN verified import",
                          task_type=TaskType.CRAWL_DATA, country_id=1,
                          status=TaskStatus.COMPLETED, input_data=inputs)
            fields.update(change)
            db.add(Task(**fields))
            await db.commit()
            stages = await list_unresolved_failed_stages(db)
            actions = await ControlPlaneOverviewService(db).action_items()
            assert ("original-import" not in [row["task_uuid"] for row in stages]) is recovered
            assert ("original-import" not in [row["resource_id"] for row in actions]) is recovered
            await db.refresh(failed)
            assert failed.status == TaskStatus.FAILED  # History is never rewritten.
    finally:
        await engine.dispose()


def test_public_stage_error_drops_the_trailing_json_document():
    error = 'Disease series observation quality gate failed: {"issue_count": 3}'
    assert public_stage_error(error) == "Disease series observation quality gate failed"


@pytest.mark.asyncio
async def test_unresolved_failed_stages_ignore_recovered_and_orphan_rows():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[Task.__table__, RunStage.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            db.add_all(
                [
                    Task(
                        task_uuid="still-failed",
                        task_name="Crawl CA Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.FAILED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="recovered",
                        task_name="Refresh GIDS Research Radar",
                        task_type=TaskType.SYNC_LITERATURE,
                        status=TaskStatus.COMPLETED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="cancelled",
                        task_name="Crawl US Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.CANCELLED,
                        input_data={},
                    ),
                ]
            )
            db.add_all(
                [
                    RunStage(
                        task_uuid="still-failed",
                        stage="store",
                        attempt=1,
                        status="failed",
                        error='Quality gate failed: {"issue_count": 3}',
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="still-failed",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="SSLError",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="still-failed",
                        stage="collect",
                        attempt=2,
                        status="completed",
                        started_at=FINISHED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="recovered",
                        stage="organize",
                        attempt=1,
                        status="failed",
                        error="CancelledError",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="cancelled",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="CancelledError",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="missing-task",
                        stage="store",
                        attempt=1,
                        status="failed",
                        error="sqlite3.OperationalError: no such table",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                ]
            )
            await db.commit()
            rows = await list_unresolved_failed_stages(db)
        assert [row["task_uuid"] for row in rows] == ["still-failed"]
        assert rows[0]["task_name"] == "Crawl CA Data"
        assert rows[0]["task_type"] == "crawl_data"
        assert rows[0]["task_status"] == "failed"
        assert rows[0]["stage"] == "store"
        assert rows[0]["attempt"] == 1
        assert rows[0]["error"] == "Quality gate failed"
        assert str(rows[0]["finished_at"]).startswith("2026-10-30T12:05:00")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unresolved_failed_stages_follow_the_latest_run_of_each_job():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[Task.__table__, RunStage.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            db.add_all(
                [
                    Task(
                        task_uuid="us-old",
                        task_name="Crawl US Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.FAILED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="us-new",
                        task_name="Crawl US Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.COMPLETED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="br-old",
                        task_name="Crawl BR Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.FAILED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="br-new",
                        task_name="Crawl BR Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.FAILED,
                        input_data={},
                    ),
                    Task(
                        task_uuid="hk",
                        task_name="Crawl HK Data",
                        task_type=TaskType.CRAWL_DATA,
                        status=TaskStatus.FAILED,
                        input_data={},
                    ),
                ]
            )
            db.add_all(
                [
                    RunStage(
                        task_uuid="us-old",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="IncompleteRead",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="br-old",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="old file",
                        started_at=STARTED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="br-new",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="current file",
                        started_at=FINISHED,
                        finished_at=FINISHED,
                    ),
                    RunStage(
                        task_uuid="hk",
                        stage="collect",
                        attempt=1,
                        status="failed",
                        error="live collection failed",
                        started_at=FINISHED,
                        finished_at=datetime(2026, 10, 30, 12, 6, tzinfo=timezone.utc),
                    ),
                ]
            )
            await db.commit()
            rows = await list_unresolved_failed_stages(db)
        assert [row["task_uuid"] for row in rows] == ["hk", "br-new"]
        assert [row["error"] for row in rows] == ["live collection failed", "current file"]
    finally:
        await engine.dispose()
