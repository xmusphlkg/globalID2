"""Persisted legacy keys, independently expected from immutable fixtures.

These tests complement the source/observation contracts: actual is read from
the database after commit, not from import counters or INSERT parameters.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.data.processors.us import USWeeklyUpdater
from src.data.processors.jp import JPWeeklyUpdater
from src.data.processors.au import AUMonthlyUpdater
from tests.contract.compare import assert_records_match
from tests.contract.legacy_records import (
    FIXTURES, CONTRACT_FIXTURES, contract_fixture, fixture_mapping,
    import_records, records_database, saved_artifact, stored_records,
)


CONTRACT = contract_fixture()
MONTHLY = {
    "NZ": ("nz", "NZMonthlyUpdater"),
    "TW": ("tw", "TWMonthlyUpdater"),
    "HK": ("hk", "HKMonthlyUpdater"),
    "FI": ("fi", "FIMonthlyUpdater"),
    "SE": ("se", "SEMonthlyUpdater"),
    "AT": ("at", "ATMonthlyUpdater"),
    "CA-ON-file": ("ca", "CAOntarioMonthlyUpdater"),
}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """A mistaken production DB or HTTP call must fail instead of escaping."""
    def forbidden(*args, **kwargs):
        pytest.fail("Legacy persistence contracts must not access PostgreSQL or HTTP")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("asyncpg.connect", forbidden)
    monkeypatch.setattr("asyncpg.create_pool", forbidden)
    monkeypatch.setattr("src.core.database.get_db", forbidden)


def monthly_updater(code, tmp_path):
    module, name = MONTHLY[code]
    cls = getattr(importlib.import_module(f"src.data.processors.{module}"), name)
    return cls(output_csv=tmp_path / f"{code}.csv")


def source_rows(code):
    return json.loads((FIXTURES / code / "golden.json").read_text())["rows"]


def us_artifacts():
    resource = {
        "id": "fixture", "name": "archive",
        "columns_name": ["Hep A, Current week"],
        "columns_field_name": ["measles"],
    }
    return [saved_artifact(
        (FIXTURES / "US" / filename).read_bytes(), country="US", filename=filename,
        kind=kind, metadata=metadata,
        url="https://fixture.invalid/nhss_2024.xlsx",
    ) for filename, kind, metadata in [
        ("current.csv", "csv_page", {}),
        ("archive.json", "archive_json", {"year": 2020, "resource": resource}),
        ("nhss_2024.xlsx", "nhss_xlsx", {}),
    ]]


async def test_us_fixture_bytes_persist_exact_national_and_state_keys(tmp_path, monkeypatch):
    from src.services.ingestion.countries.us import organize

    monkeypatch.chdir(tmp_path)
    artifacts = us_artifacts()
    expected = []
    async with records_database(CONTRACT["countries"]) as sessions:
        for code in ("US", "US-CA", "US-AL"):
            updater = USWeeklyUpdater(country_code=code)
            fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["US"])
            fetched = organize(artifacts, updater=updater, source="all")
            assert fetched.series_rows
            assert bool(fetched.rows) == (code == "US")
            for _ in range(2):
                await import_records(sessions, updater, fetched.rows, force=True)
            expected.extend(CONTRACT["expected"][code])
        # US RESIDENTS=2, TOTAL=3 and California=0 share a week and label.
        # A TOTAL or state row leaking into the national key changes this set.
        assert_records_match(await stored_records(sessions), expected)


@pytest.mark.parametrize("code", ["JP", "JP-01", "JP-13"])
async def test_jp_fixture_persists_only_national_legacy_keys(code, tmp_path, monkeypatch):
    from src.services.ingestion.countries.jp import organize

    monkeypatch.chdir(tmp_path)
    updater = JPWeeklyUpdater(country_code=code, output_csv=tmp_path / "weekly.csv")
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["JP"])
    artifact = saved_artifact(
        (FIXTURES / "JP/weekly.csv").read_bytes(), country="JP", filename="weekly.csv",
        kind="weekly_csv", metadata={"source_kind": "zensu"},
    )
    fetched = organize([artifact], updater=updater, source="jp_weekly", output_csv=updater.output_csv)
    assert fetched.rows, "Child fixtures must exercise nonempty prefecture source rows"
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            await import_records(sessions, updater, fetched.rows, force=True)
        assert_records_match(await stored_records(sessions), CONTRACT["expected"][code])


async def test_au_fixture_persists_each_jurisdiction_independently(tmp_path, monkeypatch):
    from src.services.ingestion.countries.au import organize

    monkeypatch.chdir(tmp_path)
    expected = []
    async with records_database(CONTRACT["countries"]) as sessions:
        for code in ("AU", "AU-ACT", "AU-NSW", "AU-VIC", "AU-QLD", "AU-SA", "AU-WA", "AU-TAS", "AU-NT"):
            updater = AUMonthlyUpdater(country_code=code, output_csv=tmp_path / f"{code}.csv")
            fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["AU"])
            artifact = saved_artifact(
                (FIXTURES / code / "location.json").read_bytes(), country=code,
                filename="location.json", kind="powerbi_json",
                metadata={"year": 2026, "month": 3, "disease": "Hepatitis (NEC)"},
            )
            fetched = organize([artifact], updater=updater, source="au", months=[(2026, 3)])
            assert fetched.rows
            for _ in range(2):
                # AU revisions must still write older months with force=False.
                await import_records(sessions, updater, fetched.rows, latest=date(2026, 6, 1))
            expected.extend(CONTRACT["expected"][code])
        assert_records_match(await stored_records(sessions), expected)


@pytest.mark.parametrize("code", list(MONTHLY))
async def test_monthly_frozen_source_rows_persist_exact_keys(code, tmp_path, monkeypatch):
    updater = monthly_updater(code, tmp_path)
    if code != "AT":
        fixture_mapping(monkeypatch, updater, CONTRACT["mappings"][updater.country_code])
    rows = source_rows(code)
    assert rows
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            await import_records(sessions, updater, rows, latest=date(2026, 12, 1))
        assert_records_match(await stored_records(sessions), CONTRACT["expected"][code])


@pytest.mark.parametrize("code", ["AU", "NZ", "TW", "HK", "FI", "SE", "AT"])
async def test_monthly_zero_missing_and_unmapped_legacy_policy(code, tmp_path, monkeypatch):
    updater = (AUMonthlyUpdater(output_csv=tmp_path / "au.csv") if code == "AU"
               else monthly_updater(code, tmp_path))
    if code != "AT":
        fixture_mapping(monkeypatch, updater, {"*": CONTRACT["edge_mapping"]})
    rows = deepcopy(CONTRACT["edge_rows"])
    for row in rows:
        row["GeographyKey"] = f"country:{code}:national"
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            await import_records(sessions, updater, rows, latest=date(2026, 12, 1))
        assert_records_match(await stored_records(sessions), CONTRACT["edge_expected"][code])
        for row in rows:
            if row["RawDiseaseLabel"] == "Mapped cases":
                row["Cases"] = "9"
        expected_revision = deepcopy(CONTRACT["edge_expected"][code])
        for row in expected_revision:
            if row["disease_id"] == 9004:
                row["cases"] = 9
        for _ in range(2):
            await import_records(sessions, updater, rows, latest=date(2026, 12, 1))
        assert_records_match(await stored_records(sessions), expected_revision)


async def test_se_county_run_cannot_overwrite_national_records(tmp_path, monkeypatch):
    from src.data.processors.se import SEMonthlyUpdater

    async with records_database(CONTRACT["countries"]) as sessions:
        for code in ("SE", "SE-AB"):
            updater = SEMonthlyUpdater(country_code=code, output_csv=tmp_path / f"{code}.csv")
            fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["SE"])
            await import_records(sessions, updater, source_rows("SE"))
            assert_records_match(await stored_records(sessions), CONTRACT["expected"]["SE"])


@pytest.mark.parametrize("code,expected_cases", [
    ("AU", 2), ("NZ", 2), ("TW", 5), ("HK", 5), ("SE", 5),
])
async def test_monthly_many_to_one_mappings_preserve_legacy_aggregation(
    code, expected_cases, tmp_path, monkeypatch,
):
    updater = (AUMonthlyUpdater(output_csv=tmp_path / "au.csv") if code == "AU"
               else monthly_updater(code, tmp_path))
    fixture_mapping(monkeypatch, updater, {"*": {"Source A": 9004, "Source B": 9004}})
    rows = [
        {"Date": "2026-01-01", "RawDiseaseLabel": "Source A", "Cases": "2", "GeographyKey": f"country:{code}:national"},
        {"Date": "2026-01-01", "RawDiseaseLabel": "Source B", "Cases": "3", "GeographyKey": f"country:{code}:national"},
    ]
    expected = [{"time": "2026-01-01T00:00:00+00:00", "disease_id": 9004,
                 "country_id": CONTRACT["countries"][code], "cases": expected_cases, "deaths": None}]
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            await import_records(sessions, updater, rows)
        assert_records_match(await stored_records(sessions), expected)


async def test_fi_distinct_source_groups_cannot_collapse_to_one_legacy_key(tmp_path, monkeypatch):
    from src.data.processors.fi import FIReportingGroupCollisionError

    updater = monthly_updater("FI", tmp_path)
    fixture_mapping(monkeypatch, updater, {"*": {"Tuberculosis, total": 7120, "Pulmonary tuberculosis": 7120}})
    async with records_database(CONTRACT["countries"]) as sessions:
        with pytest.raises(FIReportingGroupCollisionError, match="must not be aggregated"):
            await import_records(sessions, updater, source_rows("FI"))
        assert_records_match(await stored_records(sessions), [])


@pytest.mark.parametrize("missing", ["", None])
async def test_ontario_missing_case_batch_rejected_without_changing_committed_keys(missing, tmp_path, monkeypatch):
    updater = monthly_updater("CA-ON-file", tmp_path)
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["CA-ON"])
    rows = source_rows("CA-ON-file")
    async with records_database(CONTRACT["countries"]) as sessions:
        await import_records(sessions, updater, rows)
        rows[0]["Cases"] = missing
        with pytest.raises(ValueError, match="incomplete"):
            await import_records(sessions, updater, rows)
        assert_records_match(await stored_records(sessions), CONTRACT["expected"]["CA-ON-file"])


async def test_ontario_unmapped_and_suppressed_rows_have_no_legacy_projection(tmp_path, monkeypatch):
    updater = monthly_updater("CA-ON-file", tmp_path)
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["CA-ON"])
    rows = source_rows("CA-ON-file")
    rows[0]["Cases"] = "<5"
    rows.append({**rows[0], "RawDiseaseLabel": "Unmapped disease", "Cases": "777"})
    expected = [row for row in CONTRACT["expected"]["CA-ON-file"]
                if (row["time"], row["disease_id"]) != ("2026-01-01T00:00:00+00:00", 7140)]
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            result = await import_records(sessions, updater, rows)
            assert result.skipped_unmapped == 1
        assert_records_match(await stored_records(sessions), expected)


async def test_updater_commit_strategy_persists_rows_separately_from_series(tmp_path, monkeypatch):
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import strategies

    monkeypatch.chdir(tmp_path)
    from src.services.ingestion.countries.us import organize
    updater = USWeeklyUpdater()
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["US"])
    monkeypatch.setattr(updater, "get_db_latest_date", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.crawl_service.acquire_disease_data_mutation_lock", AsyncMock())
    save_series = AsyncMock(return_value=SimpleNamespace(upserted=100))
    monkeypatch.setattr(CrawlService, "_save_series_rows", save_series)
    dirty = AsyncMock()
    monkeypatch.setattr(strategies, "mark_dirty", dirty)
    fetched = organize(us_artifacts(), updater=updater, source="all")
    assert len(fetched.series_rows) > len(fetched.rows)
    async with records_database(CONTRACT["countries"]) as sessions:
        async with sessions.begin() as db:
            await strategies.UpdaterCommitStrategy().commit(
                db, service=CrawlService(), updater=updater, fetched=fetched,
                force=False, task_uuid="legacy-contract",
            )
        assert_records_match(await stored_records(sessions), CONTRACT["expected"]["US"])
    assert save_series.await_args.kwargs["series_rows"] == fetched.series_rows
    assert dirty.await_args.kwargs["scope_key"] == "US"


@pytest.mark.parametrize("force", [False, True])
async def test_us_source_specific_incremental_boundary_and_duplicate_upsert(force, monkeypatch):
    updater = USWeeklyUpdater()
    fixture_mapping(monkeypatch, updater, {"*": CONTRACT["edge_mapping"]})
    # Eight weeks before March 1 is January 4. NHSS annual revisions bypass
    # this weekly cutoff, even when the global latest date is much newer.
    monkeypatch.setattr(updater, "_get_source_latest_dates", AsyncMock(return_value={
        "US CDC NNDSS": date(2025, 3, 1), "US CDC NHSS": date(2024, 12, 31),
    }))
    scenario = CONTRACT["us_policy"]
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            result = await import_records(
                sessions, updater, scenario["rows"], latest=date(2026, 12, 31), force=force,
            )
            assert result.skipped_unmapped == 1
        expected = scenario["force_expected" if force else "incremental_expected"]
        assert_records_match(await stored_records(sessions), expected)
        if not force:
            revised = deepcopy(scenario["rows"])
            revised[2]["Cases"] = "10"
            await import_records(sessions, updater, revised, latest=date(2026, 12, 31))
            assert_records_match(await stored_records(sessions), scenario["revised_expected"])


@pytest.mark.parametrize("force,latest,expected_name", [
    (False, date(2025, 3, 23), "jp_incremental_expected"),
    (True, date(2025, 3, 30), "jp_edges_expected"),
])
async def test_jp_incremental_filter_keeps_zero_excludes_missing_and_unmapped(
    force, latest, expected_name, tmp_path, monkeypatch,
):
    from src.services.ingestion.countries.jp import organize

    monkeypatch.chdir(tmp_path)
    updater = JPWeeklyUpdater(output_csv=tmp_path / "jp.csv")
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["JP"])
    artifact = saved_artifact(
        (CONTRACT_FIXTURES / "jp_edges.csv").read_bytes(), country="JP",
        filename="edges.csv", kind="weekly_csv", metadata={"source_kind": "zensu"},
    )
    fetched = organize([artifact], updater=updater, source="jp_weekly", output_csv=updater.output_csv)
    assert not any(row["MMWRWeek"] == "14" for row in fetched.rows)
    async with records_database(CONTRACT["countries"]) as sessions:
        for _ in range(2):
            await import_records(
                sessions, updater, fetched.rows, latest=latest,
                source_latest=date(2025, 3, 30), force=force,
            )
        assert_records_match(await stored_records(sessions), CONTRACT[expected_name])


async def test_jp_equal_source_date_does_not_reimport(tmp_path, monkeypatch):
    updater = JPWeeklyUpdater(output_csv=tmp_path / "jp.csv")
    fixture_mapping(monkeypatch, updater, CONTRACT["mappings"]["JP"])
    rows = [{"MMWRYear": "2025", "MMWRWeek": "12", "ReportingArea": "総数",
             "RawDiseaseLabel": "AIDS", "Cases": "2"}]
    async with records_database(CONTRACT["countries"]) as sessions:
        await import_records(sessions, updater, rows, latest=date(2025, 3, 23), source_latest=date(2025, 3, 23))
        assert_records_match(await stored_records(sessions), [])


async def prepare_china(monkeypatch, tmp_path):
    """Stub mapping/series I/O; keep parsing, validation and RecordStore real."""
    from src.data.processors import cn as processor_module

    class Mapper:
        async def map_dataframe(self, frame, *, disease_col):
            frame = frame.copy()
            ids = {"甲型肝炎": "D007", "乙型肝炎": "D008", "丙型肝炎": "D009"}
            names = {"甲型肝炎": "Hepatitis A", "乙型肝炎": "Hepatitis B", "丙型肝炎": "Hepatitis C"}
            frame["disease_id"] = frame[disease_col].map(ids)
            frame["standard_name_en"] = frame[disease_col].map(names)
            frame["standard_name_zh"] = frame[disease_col]
            return frame

    monkeypatch.setattr(processor_module, "create_disease_mapper", AsyncMock(return_value=Mapper()))
    processor = processor_module.DataProcessor(output_dir=tmp_path / "processed")

    async def country(db, code):
        identity = (await db.execute(text("SELECT id FROM countries WHERE code = :code"), {"code": code})).scalar_one()
        return SimpleNamespace(id=identity)

    monkeypatch.setattr(processor._record_store, "_get_country", country)
    monkeypatch.setattr(processor._record_store, "_batch_load_diseases", AsyncMock(return_value={
        "D007": 7007, "D008": 7008, "D009": 7009,
    }))
    # These reports have no adjacent-day snapshots. PostgreSQL's ctid cleanup
    # cannot run on SQLite; its behavior is outside this contract's coverage.
    monkeypatch.setattr(processor._record_store, "_cleanup_adjacent_duplicate_snapshots", AsyncMock(return_value=0))
    monkeypatch.setattr(processor._series_observation_store, "save_rows", AsyncMock(return_value=SimpleNamespace(
        upserted=3, skipped_registry_not_synced=0, skipped_ambiguous=0,
    )))
    return processor


@pytest.mark.parametrize("edge_report", [False, True])
async def test_cn_report_commit_persists_zero_null_and_actual_deaths(edge_report, tmp_path, monkeypatch):
    from src.services.ingestion.countries import cn
    from src.services.ingestion import strategies

    monkeypatch.chdir(tmp_path)
    processor = await prepare_china(monkeypatch, tmp_path)
    path = CONTRACT_FIXTURES / "cn_edges.html" if edge_report else FIXTURES / "CN/report.html"
    artifact = saved_artifact(
        path.read_bytes(), country="CN", filename="report.html", kind="html_report",
        metadata={"title": "report", "date": "2025-02-01T00:00:00", "year_month": "2025 February",
                  "metadata": {"language": "zh", "source": "China CDC Weekly", "ontology_source_id": "SRC_CN_CDC"}},
    )
    reports, failed = await cn.organize([artifact], processor=processor)
    assert failed == 0 and len(reports) == 1
    assert len(reports[0][1]) == 3
    monkeypatch.setattr(strategies, "mark_dirty", AsyncMock())
    async with records_database(CONTRACT["countries"]) as sessions:
        @asynccontextmanager
        async def local_db():
            async with sessions.begin() as db:
                yield db
        monkeypatch.setattr("src.core.database.get_db", local_db)
        for _ in range(2):
            result = await strategies.ChinaReportCommitStrategy().commit_reports(
                reports, processor=processor, task_uuid="legacy-cn-contract",
            )
            assert result == (1, 3, 0)
        expected = CONTRACT["cn_edges_expected"] if edge_report else CONTRACT["expected"]["CN"]
        assert_records_match(await stored_records(sessions), expected)


async def test_cn_database_incremental_gate_excludes_old_report_before_parse(tmp_path, monkeypatch):
    from src.data.crawlers.cn import ChinaCDCCrawler
    from src.data.crawlers.base import CrawlerResult
    from src.services.ingestion.countries import cn
    from src.services.ingestion import strategies

    monkeypatch.chdir(tmp_path)
    processor = await prepare_china(monkeypatch, tmp_path)
    crawler = ChinaCDCCrawler()
    index = json.loads((FIXTURES / "CN/index.json").read_text())
    candidates = [CrawlerResult(
        **{**row, "date": datetime.fromisoformat(row["date"])},
        content="must not parse" if row["title"] == "old" else (FIXTURES / "CN/report.html").read_text(),
        metadata={"language": "zh", "source": "China CDC Weekly", "ontology_source_id": "SRC_CN_CDC"},
    ) for row in index]
    monkeypatch.setattr(crawler, "fetch_list", AsyncMock(return_value=candidates))
    monkeypatch.setattr(cn.task_manager, "is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(strategies, "mark_dirty", AsyncMock())
    writer = SimpleNamespace(task_uuid="legacy-cn-incremental", attempt=1, artifacts=[])

    async def write(payload, **kwargs):
        artifact = saved_artifact(payload, country="CN", filename="selected.html", **{
            "kind": kwargs["kind"], "metadata": kwargs["metadata"], "url": kwargs.get("source_url"),
        })
        writer.artifacts.append(artifact)
        return artifact
    writer.write = write
    seeds = [
        {"time": "2025-01-15T00:00:00+00:00", "disease_id": 7990, "country_id": 201, "cases": 1, "deaths": None},
        {"time": "2025-03-01T00:00:00+00:00", "disease_id": 7990, "country_id": 202, "cases": 99, "deaths": None},
    ]
    try:
        async with records_database(CONTRACT["countries"]) as sessions:
            @asynccontextmanager
            async def local_db():
                async with sessions.begin() as db:
                    yield db
            monkeypatch.setattr("src.data.crawlers.cn.get_db", local_db)
            monkeypatch.setattr("src.core.database.get_db", local_db)
            async with sessions.begin() as db:
                # ORM typed inserts match the ORM max-time query used by the gate.
                from src.domain import DiseaseRecord
                db.add_all([
                    DiseaseRecord(**{**row, "time": datetime.fromisoformat(row["time"])})
                    for row in seeds
                ])
            artifacts, failed = await cn.collect(writer, crawler=crawler, source="all", force=False, fill_missing=False)
            assert failed == 0 and len(artifacts) == 1
            assert artifacts[0].metadata_["title"] == "new"
            reports, failed = await cn.organize(artifacts, processor=processor)
            assert failed == 0 and len(reports) == 1
            await strategies.ChinaReportCommitStrategy().commit_reports(reports, processor=processor, task_uuid=writer.task_uuid)
            assert_records_match(await stored_records(sessions), seeds + CONTRACT["expected"]["CN"])
    finally:
        crawler.session.close()
