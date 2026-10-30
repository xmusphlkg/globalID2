"""Migrated ingestion runner: durable stages, atomic store, attempts and I/O bounds."""
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import threading

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.domain import Base, CrawlRun
from src.domain.collection import RunStage, SourceArtifact, PublishDirty
from src.services.ingestion import runner


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingestion.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[CrawlRun.__table__, RunStage.__table__, SourceArtifact.__table__, PublishDirty.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_maker", maker)
    monkeypatch.setattr(runner.task_manager, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(runner.task_manager, "update_task_progress", AsyncMock())
    monkeypatch.chdir(tmp_path)
    yield maker
    await engine.dispose()


def inputs(failure=None):
    task = SimpleNamespace(task_uuid=str(uuid4()), input_data={})
    updater = SimpleNamespace(country_code="US", get_db_latest_date=AsyncMock(return_value=date(2025, 1, 1)))
    main_thread = threading.get_ident()

    async def collect(writer, **kwargs):
        if failure == "collect":
            raise RuntimeError("upstream unavailable")
        await writer.write(b"Date,Cases\n2026-01-01,3\n", source_url="https://example.com/source", kind="csv_page")
        return writer.artifacts

    def organize(artifacts, **kwargs):
        assert threading.get_ident() != main_thread
        if failure == "organize":
            raise ValueError("invalid source")
        return SimpleNamespace(rows=[{"Cases": "3"}], series_rows=[{"Cases": "3", "ReportingArea": "US RESIDENTS"}], latest_date=date(2026, 1, 1))

    async def save(session, updater, rows, **kwargs):
        assert kwargs["series_rows"][0]["ReportingArea"] == "US RESIDENTS"
        if failure == "store":
            raise RuntimeError("store failed")
        return SimpleNamespace(inserted_or_updated=3)

    service = SimpleNamespace(_import_rows_with_series=AsyncMock(side_effect=save), _finish_crawl_run=AsyncMock())
    connector = SimpleNamespace(collect=collect, organize=organize)
    return dict(service=service, task=task, updater=updater, connector=connector, source="all", force=False, process=True)


async def test_success_is_three_stages_and_retry_keeps_uuid_and_attempt_history(db):
    params = inputs()
    for _ in range(2):
        result = await runner.run_surveillance(**params)
        assert (result.new_reports, result.processed_reports, result.total_records) == (3, 1, 3)
    async with db() as session:
        stages = list((await session.execute(select(RunStage))).scalars())
        artifacts = list((await session.execute(select(SourceArtifact))).scalars())
        dirty = list((await session.execute(select(PublishDirty))).scalars())
        assert len(stages) == 6
        assert {row.task_uuid for row in stages} == {params["task"].task_uuid}
        for attempt in (1, 2):
            assert {(row.stage, row.status) for row in stages if row.attempt == attempt} == {("collect", "completed"), ("organize", "completed"), ("store", "completed")}
        assert all(row.sha256 and row.byte_size and row.relative_path.startswith("data/raw/us/") for row in artifacts)
        assert len(dirty) == 2 and all(row.published_at is None and row.scope_key == "US" for row in dirty)


@pytest.mark.parametrize("stage,error", [("collect", RuntimeError), ("organize", ValueError), ("store", RuntimeError)])
async def test_failure_retains_all_three_stages_without_dirty(db, stage, error):
    params = inputs(stage)
    with pytest.raises(error):
        await runner.run_surveillance(**params)
    async with db() as session:
        rows = list((await session.execute(select(RunStage))).scalars())
        assert len(rows) == 3
        assert next(row for row in rows if row.stage == stage).status == "failed"
        assert not list((await session.execute(select(PublishDirty))).scalars())
    params["service"]._finish_crawl_run.assert_not_awaited()


async def test_process_disabled_collects_artifacts_without_formal_import(db):
    params = inputs()
    params["process"] = False
    result = await runner.run_surveillance(**params)
    assert result.total_records == result.processed_reports == 0
    params["service"]._import_rows_with_series.assert_not_awaited()
    async with db() as session:
        assert not list((await session.execute(select(PublishDirty))).scalars())
        assert list((await session.execute(select(SourceArtifact))).scalars())


async def test_operator_cancellation_stops_before_source_access(db, monkeypatch):
    from src.services.exceptions import TaskCancelledError
    monkeypatch.setattr(runner.task_manager, "is_cancel_requested", AsyncMock(return_value=True))
    params = inputs()
    with pytest.raises(TaskCancelledError, match="Cancellation requested"):
        await runner.run_surveillance(**params)
    params["service"]._import_rows_with_series.assert_not_awaited()
    async with db() as session:
        assert not list((await session.execute(select(SourceArtifact))).scalars())
        assert not list((await session.execute(select(PublishDirty))).scalars())
