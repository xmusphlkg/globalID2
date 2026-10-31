"""Real SQL checks: population deltas, audit and dirty scopes commit together."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from scripts import import_subdivision_population as importer
from src.core import database as database_module
from src.domain import Base, Country, DataReleaseJob, PopulationRecord, Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty, RunStage
from src.services.ingestion import manual_runs


STAMP = datetime(2020, 1, 1, tzinfo=timezone.utc)


def row(code="CN-GD", population=100, year=2024):
    return importer.SubdivisionPopulation(code, year, population, "NBS", f"{year}-12-31", "https://example.org/population", "year_end_resident_population")


@pytest.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            Country.__table__, PopulationRecord.__table__, Task.__table__, RunStage.__table__, PublishDirty.__table__, DataReleaseJob.__table__,
        ]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def scope():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    monkeypatch.setattr(database_module, "get_db", scope)
    monkeypatch.setattr(database_module, "dispose_database", AsyncMock())
    async with scope() as db:
        db.add_all([Country(id=1, code="CN-GD", name="Guangdong", name_en="Guangdong"), Country(id=2, code="CN-ZJ", name="Zhejiang", name_en="Zhejiang")])
        for identifier, data in [(1, row()), (2, row("CN-ZJ", 200))]:
            db.add(PopulationRecord(country_id=identifier, year=2024, population=data.population, source="NBS", metadata_={**importer._population_metadata(data), "input_sha256":"old-digest", "review_status":"reviewed"}, created_at=STAMP, updated_at=STAMP))
    yield scope
    await engine.dispose()


async def snapshot(database):
    async with database() as db:
        populations = {(p.country_id,p.year): (p.population,p.source,p.metadata_,p.created_at,p.updated_at) for p in (await db.execute(select(PopulationRecord))).scalars()}
        tasks = list((await db.execute(select(Task))).scalars())
        stages = list((await db.execute(select(RunStage))).scalars())
        dirty = list((await db.execute(select(PublishDirty))).scalars())
        return populations,tasks,stages,dirty


async def test_only_changed_geography_writes_timestamp_audit_and_dirty(database):
    before, *_ = await snapshot(database)
    result = await importer.apply_rows([row(), row("CN-ZJ", 210)], {"NBS":"new-whole-file-digest"})
    populations,tasks,stages,dirty = await snapshot(database)
    assert result["changed_country_codes"] == ["CN-ZJ"]
    assert result["changed_rows"] == result["unchanged_rows"] == 1
    assert populations[(1,2024)] == before[(1,2024)]
    changed = populations[(2,2024)]
    assert changed[0] == 210 and changed[3] == before[(2,2024)][3]
    assert changed[4] != before[(2,2024)][4]
    assert changed[2]["review_status"] == "reviewed"
    assert changed[2]["input_sha256"] == "new-whole-file-digest"
    assert len(tasks) == 1 and tasks[0].status == TaskStatus.COMPLETED
    assert tasks[0].input_data["input_sha256"] == {"NBS":"new-whole-file-digest"}
    assert tasks[0].task_uuid == result["task_uuid"]
    assert {stage.stage:stage.status for stage in stages} == {"collect":"skipped", "organize":"skipped", "store":"completed"}
    assert [(marker.line,marker.scope_key,marker.source_task_uuid,marker.published_at) for marker in dirty] == [("surveillance","CN-ZJ",result["task_uuid"],None)]
    stable = await snapshot(database)
    repeated = await importer.apply_rows([row(), row("CN-ZJ", 210)], {"NBS":"another-whole-file-digest"})
    after = await snapshot(database)
    assert repeated["changed_country_codes"] == [] and repeated["task_uuid"] is None
    assert repeated["input_sha256"] == {"NBS":"another-whole-file-digest"}
    assert after[0] == stable[0]
    assert len(after[1]) == len(after[3]) == 1


async def test_digest_only_refresh_is_a_complete_formal_noop(database):
    before = await snapshot(database)
    result = await importer.apply_rows([row(),row("CN-ZJ",200)], {"NBS":"different-digest"})
    after = await snapshot(database)
    assert result["changed_country_codes"] == [] and result["changed_rows"] == 0
    assert result["unchanged_rows"] == 2 and result["task_uuid"] is None
    assert before[0] == after[0]
    assert after[1] == after[2] == after[3] == []


@pytest.mark.parametrize("kind", ["population", "source", "reference_date", "basis", "source_url"])
async def test_all_denominator_semantic_changes_are_dirty(database,kind):
    data = row("CN-ZJ",200)
    changes = {"population":210, "source":"Official NBS", "reference_date":"2024-12-30", "basis":"revised_year_end", "source_url":"https://example.org/revised"}
    data = replace(data, **{kind:changes[kind]})
    result = await importer.apply_rows([data], {data.source:"reviewed-input"})
    populations,tasks,stages,dirty = await snapshot(database)
    assert result["changed_country_codes"] == ["CN-ZJ"]
    assert len(tasks) == 1 and len(stages) == 3
    assert [marker.scope_key for marker in dirty] == ["CN-ZJ"]


@pytest.mark.parametrize("new_year", [False,True])
async def test_dirty_failure_rolls_back_real_flushed_population_and_audit(database,monkeypatch,new_year):
    before = await snapshot(database)
    async def fail_after_real_flush(db, **kwargs):
        await db.flush()
        raise RuntimeError("publication marker unavailable")
    monkeypatch.setattr(manual_runs,"mark_dirty",fail_after_real_flush)
    data = row("CN-ZJ",210,2025 if new_year else 2024)
    with pytest.raises(RuntimeError,match="publication marker unavailable"):
        await importer.apply_rows([data], {"NBS":"input"})
    after = await snapshot(database)
    assert after[0] == before[0]
    assert after[1] == after[2] == after[3] == []


async def test_new_population_year_marks_exact_geography_and_inserts_json_metadata(database):
    result = await importer.apply_rows([row("CN-GD",110,2025)], {"NBS":"new-input"})
    populations,tasks,stages,dirty = await snapshot(database)
    assert result["changed_country_codes"] == ["CN-GD"]
    assert populations[(1,2025)][0] == 110
    assert populations[(1,2025)][2]["reference_date"] == "2025-12-31"
    assert [marker.scope_key for marker in dirty] == ["CN-GD"]


async def test_unregistered_geography_never_commits_partial_changes(database):
    before = await snapshot(database)
    with pytest.raises(ValueError,match="Unregistered subdivisions"):
        await importer.apply_rows([row("CN-ZJ",210),row("CN-XX",100)], {"NBS":"input"})
    after = await snapshot(database)
    assert after[0] == before[0]
    assert after[1] == after[2] == after[3] == []


async def test_population_dirty_is_consumed_as_exact_incremental_rate_scope(database,monkeypatch):
    from types import SimpleNamespace
    from src.services import data_release_service as releases
    await importer.apply_rows([row(),row("CN-ZJ",210)], {"NBS":"updated-snapshot"})
    service=releases.DataReleaseService()
    monkeypatch.setattr(releases,"get_database",database)
    monkeypatch.setattr(service,"_download_repo_branch",lambda job:"data")
    monkeypatch.setattr(service,"_download_repo_url",lambda:"https://example.org/data.git")
    monkeypatch.setattr(service,"_download_repo_raw_base",lambda job:"https://example.org/data")
    monkeypatch.setattr(service,"_cloudflare_project_name",lambda project:"fixture")
    monkeypatch.setattr(service,"_raw_archive_runtime",lambda:SimpleNamespace(enabled=False,branch="raw",repo_url="https://example.org/raw.git"))
    monkeypatch.setattr(service,"_automatic_retry_settings",lambda:(3,30,300))
    monkeypatch.setattr(releases.task_manager,"_broadcast",AsyncMock())
    result=await service._enqueue_release_task(releases.DataReleaseJobConfig("site-release","Fixture",include_git_push=False,include_cloudflare_deploy=False),manual=True,trigger="population-test",trigger_task_uuid=None)
    async with database() as db:
        release=(await db.execute(select(Task).where(Task.task_type==TaskType.EXPORT_DATA))).scalar_one()
        pending=(await db.execute(select(PublishDirty))).scalar_one()
        assert release.task_uuid==result["task_uuid"]
        assert release.input_data["full"] is False
        assert release.input_data["incremental_site"] is True
        assert release.input_data["incremental_country_codes"]==["CN-ZJ"]
        assert release.input_data["publish_dirty_ids"]==[pending.id]
        assert pending.published_at is None
