from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from scripts import import_wpp_population
from src.domain import Base, Country, PopulationRecord, Task
from src.domain.collection import RunStage, PublishDirty
from src.services.ingestion import manual_runs

STAMP = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            Country.__table__, PopulationRecord.__table__, Task.__table__, RunStage.__table__, PublishDirty.__table__,
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
    monkeypatch.setattr(import_wpp_population,"get_db",scope)
    async with scope() as db:
        db.add_all([Country(id=1,code="CN",name="China",name_en="China"),Country(id=2,code="JP",name="Japan",name_en="Japan")])
        db.add(PopulationRecord(country_id=1,year=2025,population=1404890000,source="NBS",metadata_={"official":True},created_at=STAMP,updated_at=STAMP))
        for country,year,value in [(1,2026,100),(2,2025,123),(2,2026,122)]:
            db.add(PopulationRecord(country_id=country,year=year,population=value,source="WPP",metadata_={"wpp_snapshot":"old.csv"},created_at=STAMP,updated_at=STAMP))
    yield scope
    await engine.dispose()


def source_file(tmp_path):
    source = tmp_path / "wpp.csv"
    source.write_text(
        "Iso2,Time,Value,Sex,Age,AgeStart,AgeEnd,IndicatorName\n"
        "CN,2025,1400000000,Both sexes,Total,0,-1,Total population by sex\n"
        "CN,2026,1390000000,Both sexes,Total,0,-1,Total population by sex\n"
        "JP,2025,123,Both sexes,Total,0,-1,Total population by sex\n"
        "JP,2026,122,Both sexes,Total,0,-1,Total population by sex\n"
    )
    return source


async def snapshot(database):
    async with database() as db:
        population = {(r.country_id,r.year):(r.population,r.source,r.metadata_,r.created_at,r.updated_at) for r in (await db.execute(select(PopulationRecord))).scalars()}
        return population, list((await db.execute(select(Task))).scalars()),list((await db.execute(select(RunStage))).scalars()),list((await db.execute(select(PublishDirty))).scalars())


async def test_wpp_refresh_preserves_official_population_and_fills_other_years(database,tmp_path):
    before = await snapshot(database)
    source=source_file(tmp_path)
    result=await import_wpp_population.ensure_wpp_population(source)
    values,tasks,stages,dirty=await snapshot(database)
    assert values[(1,2025)] == before[0][(1,2025)]
    assert values[(1,2026)][0:2] == (1390000000,"WPP")
    assert values[(2,2025)] == before[0][(2,2025)] and values[(2,2026)] == before[0][(2,2026)]
    assert result["changed_country_codes"] == ["CN"] and result["changed_rows"] == 1
    assert result["skipped_official_rows"] == 1
    assert len(tasks)==1 and len(stages)==3
    assert [(d.line,d.scope_key,d.source_task_uuid,d.published_at) for d in dirty] == [("surveillance","CN",tasks[0].task_uuid,None)]
    renamed=tmp_path/"new-snapshot.csv"
    renamed.write_text(source.read_text()+"\n")
    repeated=await import_wpp_population.ensure_wpp_population(renamed)
    after=await snapshot(database)
    assert repeated["input_sha256"] != result["input_sha256"]
    assert repeated["changed_country_codes"] == [] and repeated["task_uuid"] is None
    assert after[0] == values and len(after[1]) == len(after[3]) == 1
    explicit=await import_wpp_population.import_rows(import_wpp_population.load_rows(renamed),False,input_path=renamed)
    assert explicit["changed_country_codes"] == []
    assert explicit["input_sha256"] == repeated["input_sha256"]
    assert (await snapshot(database))[0] == values


@pytest.mark.parametrize("official_source",["NBS","ABS","IBGE"])
async def test_explicit_wpp_import_never_overwrites_official_denominator(database,official_source):
    async with database() as db:
        record=(await db.execute(select(PopulationRecord).where(PopulationRecord.country_id==1,PopulationRecord.year==2025))).scalar_one()
        record.source=official_source
    before=await snapshot(database)
    result=await import_wpp_population.import_rows([import_wpp_population.PopulationRow("CN",2025,1)],False)
    after=await snapshot(database)
    assert after[0] == before[0]
    assert result["changed_rows"]==0 and result["skipped_official_rows"]==1
    assert after[1] == after[2] == after[3] == []


@pytest.mark.parametrize("entrypoint",["rows","snapshot"])
async def test_wpp_dirty_failure_rolls_back_flushed_population_and_audit(database,monkeypatch,tmp_path,entrypoint):
    before=await snapshot(database)
    async def fail(db,**kwargs):
        await db.flush()
        raise RuntimeError("dirty write failed")
    monkeypatch.setattr(manual_runs,"mark_dirty",fail)
    with pytest.raises(RuntimeError,match="dirty write failed"):
        if entrypoint=="rows":
            await import_wpp_population.import_rows([import_wpp_population.PopulationRow("CN",2026,999)],False)
        else:
            await import_wpp_population.ensure_wpp_population(source_file(tmp_path))
    after=await snapshot(database)
    assert after[0]==before[0]
    assert after[1]==after[2]==after[3]==[]


async def test_explicit_wpp_dry_run_creates_no_population_task_or_dirty(database):
    before=await snapshot(database)
    result=await import_wpp_population.import_rows([import_wpp_population.PopulationRow("CN",2026,999)],True)
    after=await snapshot(database)
    assert result["dry_run"] is True
    assert after[0]==before[0] and after[1]==after[2]==after[3]==[]


async def test_missing_population_schema_fails_instead_of_creating_runtime_tables(database):
    from sqlalchemy.exc import OperationalError
    async with database() as db:
        await db.execute(text("DROP TABLE population_records"))
    with pytest.raises(OperationalError,match="population_records"):
        await import_wpp_population.import_rows([import_wpp_population.PopulationRow("CN",2026,999)],False)
    async with database() as db:
        assert (await db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='population_records'"))).all()==[]


def test_default_wpp_input_prefers_existing_history_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    history = tmp_path / "history.csv"
    processed = tmp_path / "processed.csv"
    history.write_text("history", encoding="utf-8")
    processed.write_text("processed", encoding="utf-8")
    monkeypatch.setattr(
        import_wpp_population,
        "DEFAULT_INPUT_CANDIDATES",
        (history, processed),
    )

    assert import_wpp_population.resolve_default_input() == history


def test_wpp_parser_keeps_all_supported_country_denominators(tmp_path: Path) -> None:
    source = tmp_path / "wpp.csv"
    source.write_text(
        "Iso2,Time,Value,Sex,Age,AgeStart,AgeEnd,IndicatorName\n"
        "JP,2026,122000000,Both sexes,Total,0,-1,Total population by sex\n"
        "KR,2026,51000000,Both sexes,Total,0,-1,Total population by sex\n"
        "BR,2026,213000000,Both sexes,Total,0,-1,Total population by sex\n"
        "CH,2026,9000000,Both sexes,Total,0,-1,Total population by sex\n"
        "HK,2026,7500000,Both sexes,Total,0,-1,Total population by sex\n"
        "TW,2026,23000000,Both sexes,Total,0,-1,Total population by sex\n",
        encoding="utf-8",
    )

    rows = import_wpp_population.load_rows(source)

    assert {row.iso2 for row in rows} == {"JP", "KR", "BR", "CH", "HK", "TW"}


def test_population_plan_automatically_includes_new_database_countries() -> None:
    rows = [
        import_wpp_population.PopulationRow("JP", 2025, 123.0),
        import_wpp_population.PopulationRow("JP", 2026, 122.0),
        import_wpp_population.PopulationRow("GB", 2025, 68.0),
        import_wpp_population.PopulationRow("GB", 2026, 69.0),
    ]

    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"JP": 1, "UK": 2},
    )

    import_wpp_population.validate_population_import_plan(plan)
    assert plan["mapped_country_codes"] == ["JP", "UK"]
    assert len(plan["rows"]) == 4
    assert {(row.country_code, row.wpp_iso2) for row in plan["rows"]} == {
        ("JP", "JP"),
        ("UK", "GB"),
    }


def test_population_plan_rejects_unmatched_or_incomplete_new_country() -> None:
    rows = [
        import_wpp_population.PopulationRow("JP", 2025, 123.0),
        import_wpp_population.PopulationRow("JP", 2026, 122.0),
        import_wpp_population.PopulationRow("KR", 2025, 51.0),
    ]
    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"JP": 1, "KR": 2, "ZZ": 3},
    )

    with pytest.raises(ValueError, match="no WPP ISO2 match: ZZ") as error:
        import_wpp_population.validate_population_import_plan(plan)

    assert "KR missing 1 year" in str(error.value)


def test_population_plan_excludes_subdivision_without_using_parent_denominator() -> (
    None
):
    rows = [import_wpp_population.PopulationRow("CA", 2026, 40000000.0)]
    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"CA": 1, "CA-ON": 2},
        excluded_location_codes={"CA-ON"},
    )

    import_wpp_population.validate_population_import_plan(plan)
    assert plan["mapped_country_codes"] == ["CA"]
    assert plan["excluded_location_codes"] == ["CA-ON"]
    assert all(row.country_code != "CA-ON" for row in plan["rows"])
    assert (
        import_wpp_population.is_wpp_population_target(
            {"location_type": "subdivision", "iso_subdivision_code": "CA-ON"}
        )
        is False
    )
