"""Source-window facts, revision history and dirty markers share a real transaction.

Opt in with TEST_SNAPSHOT_DATABASE_URL pointing to a disposable PostgreSQL DB.
Every test uses a unique schema; no existing database rows are read or modified.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.data.storage.series_observation_store import (
    SeriesObservationBuildResult, SeriesObservationQualityPolicy,
    SeriesObservationQuarantinedError, SeriesObservationStore,
)
from src.domain import (
    DiseaseSeriesObservation, DiseaseSeriesObservationRevision,
    DiseaseSurveillanceSeries, PublishDirty, SourceDiseaseCategory,
)
from src.services.ingestion.stages import mark_observation_changes_dirty


@pytest.fixture
async def window_database():
    url = os.environ.get("TEST_SNAPSHOT_DATABASE_URL")
    if not url:
        pytest.skip("TEST_SNAPSHOT_DATABASE_URL is required")
    schema = "test_source_windows_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as connection:
        # Only parent keys are needed; registry, holding, facts, revisions and
        # dirty markers use their actual ORM table constraints and defaults.
        for name, key in (("countries", "code"), ("standard_diseases", "disease_id"), ("country_scopes", "scope_code")):
            await connection.execute(text(f"CREATE TABLE {name} ({key} varchar(100) PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO countries (code) VALUES ('CA'), ('FR')"))
        for table in (DiseaseSurveillanceSeries.__table__, SourceDiseaseCategory.__table__,
                      DiseaseSeriesObservation.__table__, DiseaseSeriesObservationRevision.__table__, PublishDirty.__table__):
            await connection.run_sync(table.create)
    writes = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lstrip().upper()
        if normalized.startswith(("INSERT INTO DISEASE_SERIES_OBSERVATIONS ", "UPDATE DISEASE_SERIES_OBSERVATIONS ", "DELETE FROM DISEASE_SERIES_OBSERVATIONS ")):
            writes.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), writes
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


def point(code="S1", year=2024, value=10.0, **overrides):
    return {
        "time": datetime(year, 1, 1, tzinfo=timezone.utc), "series_code": code,
        "geography_key": "country:CA:national", "dimension_key": "all",
        "dimensions": {}, "value": value, "unit": "count", "suppressed": False,
        "suppression_reason": None, "quality_status": "final", "raw_data": {},
        "metadata": {"source_id": "SRC_TEST", "frequency": "annual",
                     "authoritative_revision": True, "allow_equal_quality_overwrite": True},
        **overrides,
    }


class PreparedStore(SeriesObservationStore):
    """Exercise the complete DB path with already-normalized candidate fixtures."""
    invalid = 0

    def build_observations(self, rows, *_args, **_kwargs):
        return SeriesObservationBuildResult(deepcopy(rows), 0, 0, self.invalid)

    async def _build_holding_observations(self, *_args, **_kwargs):
        return [], 0


async def register(db, points, country="CA", source="SRC_TEST"):
    codes = {row["series_code"] for row in points}
    if codes:
        await db.execute(pg_insert(DiseaseSurveillanceSeries.__table__).on_conflict_do_nothing(
            index_elements=["series_code"],
        ), [{"series_code": code, "country_code": country, "source_system": source,
             "source_series_code": code, "source_label": code,
             "target_group_code": "G_UNMAPPED_SOURCE_CATEGORIES"} for code in codes])


async def save(db, rows, *, store=None, policy=None, start_year=2023):
    result = await (store or PreparedStore()).save_rows(
        db, rows, "CA", source_id="SRC_TEST", geography_key="country:CA:national",
        quality_policy=policy or SeriesObservationQualityPolicy(mode="off"),
        authoritative_window_start=datetime(start_year, 1, 1, tzinfo=timezone.utc),
    )
    await mark_observation_changes_dirty(db, result=result, scope_key="CA", task_uuid="window-test")
    return result


async def snapshot(db, table=DiseaseSeriesObservation.__table__):
    return [dict(row) for row in (await db.execute(select(table).order_by(table.c.id))).mappings()]


@pytest.mark.parametrize("country", ["CA", "FR", "CA_CAPTURE"])
async def test_captured_source_repeat_has_zero_fact_writes_and_dirty(window_database, tmp_path, monkeypatch, country):
    sessions, writes = window_database
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures/ingestion"
    capture_path = None
    start_year = 2020
    if country == "CA_CAPTURE":
        if not os.environ.get("TEST_CA_RAW_ARTIFACT_DIR"):
            pytest.skip("TEST_CA_RAW_ARTIFACT_DIR enables replay of an actual complete CA capture")
        capture_path = Path(os.environ["TEST_CA_RAW_ARTIFACT_DIR"])
        country, start_year = "CA", 1924
    if country == "CA":
        from src.data.crawlers.ca_national import CanadaCNDSSNationalCrawler
        from src.data.processors.ca_national import CanadaCNDSSAnnualUpdater
        updater = CanadaCNDSSAnnualUpdater()
        if capture_path is not None:
            payloads = [json.loads(path.read_text()) for path in capture_path.glob("*.json")]
            describe = next(payload for payload in payloads if "records" not in payload)
            raw = next(payload for payload in payloads if "records" in payload)
        else:
            describe = json.loads((fixture_root / "CA/describe.json").read_text())
            raw = json.loads((fixture_root / "CA/raw.json").read_text())
        first, last = CanadaCNDSSNationalCrawler._validate_describe(describe)
        rows = CanadaCNDSSNationalCrawler._rows_from_payload(
            raw, first_year=first, last_year=last,
        )
        # This parser fixture includes a synthetic 1924 anthrax cell before
        # the registry's 1930 validity boundary. Replay the complete 2020+
        # window; out-of-contract history must retain its quality rejection.
        if capture_path is None:
            rows = [row for row in rows if row["Date"] >= "2020-01-01"]
    else:
        from src.data.processors.ecdc import ECDCAnnualUpdater
        from src.services.ingestion.countries import ecdc
        from tests.contract.test_ecdc_observations import fixture_requests, save_artifact
        updater = ECDCAnnualUpdater("FR")
        monkeypatch.chdir(tmp_path)
        artifacts = [save_artifact(item["payload"], {"endpoint": item["endpoint"], "params": item["params"]}, i)
                     for i, item in enumerate(fixture_requests("FR"))]
        fetched = ecdc.organize(artifacts, updater=updater, start_year=2020)
        assert fetched.authoritative_window_start_year == 2020
        rows = fetched.rows
    store = SeriesObservationStore()
    built = store.build_observations(rows, country, source_id=updater.ontology_source_id, geography_key=updater.series_geography_key)
    assert built.observations and not (built.skipped_invalid or built.skipped_unmatched or built.skipped_ambiguous)
    options = dict(source_id=updater.ontology_source_id, geography_key=updater.series_geography_key,
                   authoritative_window_start=datetime(start_year, 1, 1, tzinfo=timezone.utc))
    async with sessions.begin() as db:
        await register(db, built.observations, country, updater.ontology_source_id)
        first_result = await store.save_rows(db, rows, country, **options)
        assert first_result.upserted == len(built.observations)
        await mark_observation_changes_dirty(db, result=first_result, scope_key=country, task_uuid="first")
    async with sessions() as db:
        before = await snapshot(db)
        revisions = await snapshot(db, DiseaseSeriesObservationRevision.__table__)
        dirty = await snapshot(db, PublishDirty.__table__)
    refreshed = [{**row, "RetrievedAt": "2099-01-01T00:00:00Z", "SourceArtifactPath": "another-capture.json"} for row in rows]
    writes.clear()
    async with sessions.begin() as db:
        result = await store.save_rows(db, refreshed, country, **options)
        await mark_observation_changes_dirty(db, result=result, scope_key=country, task_uuid="repeat")
        assert result.upserted == 0 and result.publication_changes == ()
        assert result.window_change_counts == {
            "input": len(built.observations), "inserted": 0, "updated": 0,
            "deleted": 0, "unchanged": len(built.observations), "rejected": 0,
        }
    assert writes == []
    async with sessions() as db:
        assert await snapshot(db) == before
        assert await snapshot(db, DiseaseSeriesObservationRevision.__table__) == revisions
        assert await snapshot(db, PublishDirty.__table__) == dirty


async def test_correction_withdrawal_reappearance_and_scope_isolation(window_database):
    sessions, writes = window_database
    initial = [point(), point("S2"), point("S1", year=2020)]
    other = [point("OTHER"), point("S1", geography_key="country:CA-ON:national")]
    async with sessions.begin() as db:
        await register(db, initial)
        await register(db, [other[0]], source="SRC_OTHER")
        await save(db, initial)
        await db.execute(insert(DiseaseSeriesObservation.__table__), other)
    writes.clear()
    async with sessions.begin() as db:
        result = await save(db, initial)
        assert result.publication_changes == ()
        assert result.window_change_counts["unchanged"] == len(initial)
    assert writes == []
    async with sessions.begin() as db:
        result = await save(db, [point(value=12.0)])
        assert result.upserted == 1
        assert {(item["series_code"], item["operation"]) for item in result.publication_changes} == {("S1", "upsert"), ("S2", "delete")}
        current = await snapshot(db)
        assert len(current) == 4
        assert any(row["series_code"] == "OTHER" for row in current)
        assert any(row["geography_key"] == "country:CA-ON:national" for row in current)
        assert any(row["time"].year == 2020 for row in current)
        history = await snapshot(db, DiseaseSeriesObservationRevision.__table__)
        assert [row["value"] for row in history if row["series_code"] == "S1" and row["time"].year == 2024] == [10.0, 12.0]
        assert any(row["series_code"] == "S2" for row in history)
    async with sessions.begin() as db:
        result = await save(db, [point(value=12.0), point("S2")])
        assert [(row["series_code"], row["operation"]) for row in result.publication_changes] == [("S2", "upsert")]
    # A validated empty source snapshot withdraws only its complete window.
    async with sessions.begin() as db:
        result = await save(db, [])
        assert len(result.publication_changes) == 2
        assert {row["operation"] for row in result.publication_changes} == {"delete"}
        assert len(await snapshot(db)) == 3
    writes.clear()
    async with sessions.begin() as db:
        assert (await save(db, [])).publication_changes == ()
    assert writes == []


async def test_quality_and_incomplete_candidates_roll_back_without_withdrawal(window_database):
    sessions, writes = window_database
    async with sessions.begin() as db:
        await register(db, [point(), point("S2")])
        await save(db, [point(), point("S2")])
    async with sessions() as db:
        before = await snapshot(db)
        dirty = await snapshot(db, PublishDirty.__table__)
    writes.clear()
    candidate = point(value=0.0)
    candidate["metadata"]["authoritative_revision"] = False
    with pytest.raises(SeriesObservationQuarantinedError):
        async with sessions.begin() as db:
            await save(db, [candidate], policy=SeriesObservationQualityPolicy(mode="quarantine"))
    invalid_store = PreparedStore()
    invalid_store.invalid = 1
    with pytest.raises(ValueError, match="incomplete observation coverage"):
        async with sessions.begin() as db:
            await save(db, [point(value=11.0)], store=invalid_store)
    assert writes == []
    async with sessions() as db:
        assert await snapshot(db) == before
        assert await snapshot(db, PublishDirty.__table__) == dirty


async def test_unmarked_provisional_cannot_overwrite_final(window_database):
    sessions, _ = window_database
    async with sessions.begin() as db:
        await register(db, [point()])
        await save(db, [point()])
    candidate = point(value=11.0, quality_status="provisional")
    candidate["metadata"]["authoritative_revision"] = False
    async with sessions.begin() as db:
        result = await save(db, [candidate])
        assert result.upserted == 0 and result.publication_changes == ()
        assert (await snapshot(db))[0]["value"] == 10.0


async def test_second_sql_batch_failure_rolls_back_facts_revisions_and_dirty(window_database, monkeypatch):
    sessions, _ = window_database
    async with sessions.begin() as db:
        await register(db, [point(), point("S2"), point("S3"), point("OLD")])
        await save(db, [point("OLD")])
    async with sessions() as db:
        before = await snapshot(db)
        history = await snapshot(db, DiseaseSeriesObservationRevision.__table__)
        dirty = await snapshot(db, PublishDirty.__table__)
    monkeypatch.setattr("src.data.storage.series_observation_store._WRITE_BATCH_SIZE", 2)
    with pytest.raises(Exception, match="ck_disease_series_observation_unit"):
        async with sessions.begin() as db:
            await save(db, [point(), point("S2"), point("S3", unit="")])
    async with sessions() as db:
        assert await snapshot(db) == before
        assert await snapshot(db, DiseaseSeriesObservationRevision.__table__) == history
        assert await snapshot(db, PublishDirty.__table__) == dirty


async def test_same_country_serialization_observes_committed_predecessor(window_database):
    sessions, _ = window_database
    async with sessions.begin() as db:
        await register(db, [point()])
    async with sessions() as first:
        await first.begin()
        await save(first, [point()])
        started = asyncio.Event()

        async def next_refresh():
            async with sessions.begin() as second:
                started.set()
                return await save(second, [point()])

        work = asyncio.create_task(next_refresh())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            done, _ = await asyncio.wait([work], timeout=0.1)
            assert not done
            await first.commit()
            result = await asyncio.wait_for(work, timeout=5)
            assert result.upserted == 0 and result.publication_changes == ()
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    async with sessions() as db:
        assert len(await snapshot(db)) == 1
        assert len(await snapshot(db, PublishDirty.__table__)) == 1


async def test_optional_unmapped_old_history_cannot_authorize_old_deletion(window_database):
    from src.data.crawlers.ca_national import CanadaCNDSSNationalCrawler
    from src.data.processors.ca_national import CanadaCNDSSAnnualUpdater
    sessions, writes = window_database
    fixture = Path(__file__).resolve().parents[1] / "fixtures/ingestion/CA"
    first, last = CanadaCNDSSNationalCrawler._validate_describe(json.loads((fixture / "describe.json").read_text()))
    rows = CanadaCNDSSNationalCrawler._rows_from_payload(
        json.loads((fixture / "raw.json").read_text()), first_year=first, last_year=last,
    )
    store, updater = SeriesObservationStore(), CanadaCNDSSAnnualUpdater()
    options = dict(source_id=updater.ontology_source_id, geography_key=updater.series_geography_key,
                   quality_policy=SeriesObservationQualityPolicy(mode="report", registry_coverage="required"))
    built = store.build_observations(rows, "CA", source_id=updater.ontology_source_id, geography_key=updater.series_geography_key)
    assert built.skipped_unmatched == 1  # Synthetic 1924 fixture is before registry validity.
    old = {**built.observations[0], "time": datetime(1924, 1, 1, tzinfo=timezone.utc)}
    async with sessions.begin() as db:
        await register(db, built.observations, "CA", updater.ontology_source_id)
        await db.execute(insert(DiseaseSeriesObservation.__table__), [old])
    async with sessions.begin() as db:
        result = await store.save_rows(db, rows, "CA", **options,
            authoritative_window_start=datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert result.upserted == 2 and result.deleted == 0
        assert result.publication_changes is None  # Unexplained historical mapping stays conservative.
        assert any(row["time"].year == 1924 for row in await snapshot(db))
    writes.clear()
    with pytest.raises(ValueError, match="incomplete observation coverage"):
        async with sessions.begin() as db:
            await store.save_rows(db, rows, "CA", **options,
                authoritative_window_start=datetime(1924, 1, 1, tzinfo=timezone.utc))
    assert writes == []


async def test_holding_creation_repeat_and_new_mapping_remain_atomic(window_database, monkeypatch):
    sessions, writes = window_database
    store = SeriesObservationStore()
    rows = [{"Date": "2024-01-01", "Cases": "10", "SourceDiseaseCode": "unreviewed",
             "RawDiseaseLabel": "Unreviewed condition", "Frequency": "annual", "AuthoritativeRevision": True}]
    options = dict(source_id="SRC_TEST", geography_key="country:CA:national",
                   quality_policy=SeriesObservationQualityPolicy(registry_coverage="required"),
                   authoritative_window_start=datetime(2023, 1, 1, tzinfo=timezone.utc))
    now = datetime.now(timezone.utc)
    async with sessions.begin() as db:
        db.add(SourceDiseaseCategory(
            category_key="SRC_TEST:unreviewed", country_code="CA", source_id="SRC_TEST",
            source_code="unreviewed", canonical_source_label="Unreviewed condition",
            normalized_label="unreviewed condition", first_seen_at=now, last_seen_at=now,
        ))
        await db.flush()
        result = await store.save_rows(db, rows, "CA", **options)
        assert result.holding_observations == 1 and result.skipped_unmatched == 0
        await mark_observation_changes_dirty(db, result=result, scope_key="CA", task_uuid="holding")
        holding_code = (await snapshot(db))[0]["series_code"]
        assert holding_code.startswith("HOLD_CA_")
    writes.clear()
    async with sessions.begin() as db:
        result = await store.save_rows(db, rows, "CA", **options)
        assert result.publication_changes == ()
    assert writes == []
    async with sessions.begin() as db:
        await register(db, [point("MAPPED")])
    monkeypatch.setattr(store, "_resolve_series", lambda **_kwargs: [{
        "id": "MAPPED", "source_id": "SRC_TEST", "measure": "case_notifications",
        "frequency": "annual", "rollup_policy": "no_auto_rollup", "unit": "count", "status": "active",
    }])
    async with sessions.begin() as db:
        result = await store.save_rows(db, rows, "CA", **options)
        assert {(change["series_code"], change["operation"]) for change in result.publication_changes} == {
            (holding_code, "delete"), ("MAPPED", "upsert"),
        }
        assert [row["series_code"] for row in await snapshot(db)] == ["MAPPED"]
        assert {row["series_code"] for row in await snapshot(db, DiseaseSeriesObservationRevision.__table__)} == {holding_code, "MAPPED"}

    # A later failure also rolls back a newly created holding registry row.
    failed_rows = [{**rows[0], "SourceDiseaseCode": "rollback", "RawDiseaseLabel": "Rollback condition"}]
    before = None
    async with sessions() as db:
        before = set((await db.execute(select(DiseaseSurveillanceSeries.series_code))).scalars())
    with pytest.raises(RuntimeError, match="abort after holding"):
        async with sessions.begin() as db:
            db.add(SourceDiseaseCategory(
                category_key="SRC_TEST:rollback", country_code="CA", source_id="SRC_TEST",
                source_code="rollback", canonical_source_label="Rollback condition",
                normalized_label="rollback condition", first_seen_at=now, last_seen_at=now,
            ))
            await db.flush()
            result = await SeriesObservationStore().save_rows(db, failed_rows, "CA", **options)
            await mark_observation_changes_dirty(db, result=result, scope_key="CA", task_uuid="rollback")
            raise RuntimeError("abort after holding")
    async with sessions() as db:
        assert set((await db.execute(select(DiseaseSurveillanceSeries.series_code))).scalars()) == before
        assert [row["series_code"] for row in await snapshot(db)] == ["MAPPED"]
        assert len(await snapshot(db, PublishDirty.__table__)) == 1
