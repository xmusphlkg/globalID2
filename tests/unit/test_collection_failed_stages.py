"""Overview failed stages are current task failures, not historical stage rows."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from dashboard.api.routers.collection import (
    list_unresolved_failed_stages,
    public_stage_error,
)
from src.domain import Base, Task, TaskStatus, TaskType
from src.domain.collection import RunStage

STARTED = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
FINISHED = datetime(2026, 10, 30, 12, 5, tzinfo=timezone.utc)


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
