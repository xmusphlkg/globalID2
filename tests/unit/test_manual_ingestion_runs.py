"""Maintenance mutations share a transaction with their real run and dirty rows."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.domain import Base, Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty, RunStage
from src.services.ingestion.manual_runs import record_manual_store


@pytest.mark.parametrize("rollback", [False, True])
async def test_manual_store_formal_rows_audit_and_dirty_are_atomic(rollback):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, RunStage.__table__, PublishDirty.__table__]
        ))
        await connection.execute(text("CREATE TABLE maintenance_fixture (value INTEGER)"))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            await db.execute(text("INSERT INTO maintenance_fixture VALUES (7)"))
            task = await record_manual_store(
                db, line="surveillance", scope_keys=["US", "AU-NSW", "US"],
                operation="reviewed_import", metrics={"upserted": 7},
            )
            # Deliberately roll back after flush, just as a later store failure
            # would: the helper must never commit its own audit independently.
            await db.flush()
            await (db.rollback() if rollback else db.commit())
        async with maker() as db:
            formal = (await db.execute(text("SELECT value FROM maintenance_fixture"))).scalars().all()
            tasks = list((await db.execute(select(Task))).scalars())
            stages = list((await db.execute(select(RunStage))).scalars())
            dirty = list((await db.execute(select(PublishDirty))).scalars())
            if rollback:
                assert formal == tasks == stages == dirty == []
            else:
                assert formal == [7]
                assert len(tasks) == 1
                assert tasks[0].task_uuid == task.task_uuid
                assert tasks[0].status == TaskStatus.COMPLETED
                assert tasks[0].task_type == TaskType.PROCESS_DATA
                assert {stage.stage: stage.status for stage in stages} == {
                    "collect": "skipped", "organize": "skipped", "store": "completed"
                }
                assert {stage.attempt for stage in stages} == {1}
                assert {row.source_task_uuid for row in dirty} == {task.task_uuid}
                assert {row.scope_key for row in dirty} == {"US", "AU-NSW"}
                assert all(row.published_at is None for row in dirty)
    finally:
        await engine.dispose()


async def test_au_maintenance_marks_its_actual_import_transaction(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scripts import update_au_subdivisions as maintenance
    from src.core import database
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import manual_runs

    session = SimpleNamespace()
    @asynccontextmanager
    async def database_scope():
        yield session
    marker = AsyncMock(return_value=SimpleNamespace(task_uuid="stored-audit"))
    importer = AsyncMock(return_value=SimpleNamespace(inserted_or_updated=1, skipped_unmapped=0))
    class ExistingInput:
        output_csv = tmp_path / "missing.csv"
        _validate_subdivision_rows = lambda self, rows: None
        _write_rows_to_output_csv = lambda self, rows: None
        _latest_row_date = lambda self, rows: "2025-01-01"
        get_db_latest_date = AsyncMock(return_value=None)
    monkeypatch.setattr(database, "get_db", database_scope)
    monkeypatch.setattr(database, "dispose_database", AsyncMock())
    monkeypatch.setattr("src.data.processors.au.AUMonthlyUpdater", lambda **kwargs: ExistingInput())
    monkeypatch.setattr(CrawlService, "_import_rows_with_series", importer)
    monkeypatch.setattr(manual_runs, "record_manual_store", marker)
    result = await maintenance.apply_rows({"AU-NSW": [{"Date": "2025-01-01", "RawDiseaseLabel": "Dengue"}]})
    assert importer.await_args.args[0] is session
    assert marker.await_args.args[0] is session
    assert marker.await_args.kwargs["scope_keys"] == ["AU-NSW"]
    assert result["AU-NSW"]["task_uuid"] == "stored-audit"


async def test_br_maintenance_marks_all_residence_scopes_in_import_transaction(monkeypatch):
    from types import SimpleNamespace
    from scripts import update_br_subdivisions as maintenance
    from src.core import database
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import manual_runs

    session = SimpleNamespace(execute=AsyncMock())
    @asynccontextmanager
    async def database_scope():
        yield session
    marker = AsyncMock(return_value=SimpleNamespace(task_uuid="stored-audit"))
    importer = AsyncMock(return_value=SimpleNamespace(inserted_or_updated=2, skipped_unmapped=0))
    monkeypatch.setattr(database, "get_db", database_scope)
    monkeypatch.setattr(database, "dispose_database", AsyncMock())
    monkeypatch.setattr(CrawlService, "_import_rows_with_series", importer)
    monkeypatch.setattr(manual_runs, "record_manual_store", marker)
    await maintenance.apply([
        {"Date": "2025-01-01", "CountryCode": code} for code in ["BR-SP", "BR-RJ"]
    ])
    assert importer.await_args.args[0] is marker.await_args.args[0] is session
    assert marker.await_args.kwargs["scope_keys"] == ["BR-RJ", "BR-SP"]


async def test_ecdc_replacement_and_dirty_precede_same_commit(monkeypatch):
    from types import SimpleNamespace
    from scripts import import_ecdc_baselines as maintenance
    from src.services.ingestion import manual_runs
    calls = []
    session = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")))
    @asynccontextmanager
    async def database_scope():
        yield session
    class Updater:
        def __init__(self, code):
            self.country_code = code
        get_db_latest_date = AsyncMock(return_value=None)
        delete_authoritative_window = AsyncMock(return_value=3)
    async def record(db, **kwargs):
        assert db is session
        assert kwargs["scope_keys"] == ["FR", "DE"]
        assert kwargs["metrics"]["replaced"] == 6
        calls.append("dirty")
    monkeypatch.setattr(maintenance, "get_database", database_scope)
    monkeypatch.setattr(maintenance, "ECDCAnnualUpdater", Updater)
    monkeypatch.setattr(maintenance, "_validate_rows", lambda updater: [{"Date": "2025-01-01"}])
    monkeypatch.setattr(maintenance, "ensure_standard_country_rows", AsyncMock())
    monkeypatch.setattr(maintenance.CrawlService, "_import_rows_with_series", AsyncMock(return_value=SimpleNamespace(inserted_or_updated=1)))
    monkeypatch.setattr(manual_runs, "record_manual_store", record)
    await maintenance.run(apply=True, countries=("FR", "DE"))
    assert calls == ["dirty", "commit"]
