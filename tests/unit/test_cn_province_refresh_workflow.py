"""Province refresh must expose partial coverage without weakening identity gates."""
from contextlib import asynccontextmanager
from datetime import date
import asyncio
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from scripts import update_cn_provinces as cli
from src.data.crawlers.cn_provinces import MonthlyReportLink
from src.services.ingestion.countries import cn_province as connector


def row(month=9, cases=3, **changes):
    return {"Date": f"2026-{month:02d}-01", "Cases": cases,
            "JurisdictionCode": "CN-BJ", "GeographyKey": "country:CN-BJ:national",
            "SourceID": "SRC_CN_PROV_MONTHLY_REPORT", "SourceDiseaseCode": "hepatitis_a",
            "RawDiseaseLabel": "甲型肝炎", "DefinitionVersion": "CN_PROVINCE_MONTHLY_REPORT_V1",
            "SourceURL": "https://official.example/report", "Raw": {}, **changes}


def link(month, suffix=""):
    return MonthlyReportLink("CN-BJ", date(2026, month, 1), "report", f"https://official.example/{month}{suffix}")


def package(path, rows, audit=None):
    path.write_text(json.dumps({"schema_version": 1, "kind": "cn_province_rows", "rows": rows,
                               "rows_sha256": cli._rows_digest(rows), "collection_audit": audit or {}}))
    return path


def test_revision_window_retains_three_distinct_months_and_their_revisions():
    links = [link(7), link(8), link(9), link(10), link(10, "-revision"), link(11), link(12)]
    selected = connector.select_monthly_links(links, recent_months=3, today=date(2026, 11, 16))
    assert [(item.report_date.month, item.url) for item in selected] == [
        (8, link(8).url), (9, link(9).url), (10, link(10).url), (10, link(10, "-revision").url)]
    assert len(connector.select_monthly_links(links, start_year=2026, today=date(2026, 11, 16))) == 5


def test_bounded_historical_gap_selection_keeps_revisions_and_rotates_after_cooldown():
    links = [link(month) for month in range(1, 11)] + [link(4, "-revision")]
    selected = connector.select_monthly_links(
        links, recent_months=3, fill_missing=True,
        known_case_months={"2026-01"}, recently_attempted_months={"2026-02"},
        today=date(2026, 11, 16),
    )
    assert [(item.report_date.month, item.url) for item in selected] == [
        (3, link(3).url), (4, link(4).url), (4, link(4, "-revision").url),
        (5, link(5).url), (8, link(8).url), (9, link(9).url), (10, link(10).url),
    ]
    assert len({item.report_date.month for item in selected if item.report_date.month < 8}) == 3


def test_conflicting_revision_quarantines_month_only_including_deaths():
    rows, errors = connector.merge_report_rows([
        ({"url": "first"}, [row(8), row(9, Deaths=0)]),
        ({"url": "revision"}, [row(9, Deaths=1), row(10)]),
    ])
    assert [item["Date"] for item in rows] == ["2026-08-01", "2026-10-01"]
    assert len(errors) == 1 and errors[0]["stage"] == "reconcile"


def test_compatible_case_duplicate_retains_explicit_death_observation():
    rows, errors = connector.merge_report_rows([({}, [row()]), ({}, [row(Deaths=0)])])
    assert not errors and len(rows) == 1 and rows[0]["Deaths"] == 0


def test_older_table_does_not_hide_newer_sparse_month():
    rows = [row(8), row(9, Raw={"explicit_narrative_value": "甲型肝炎无病例"})]
    assert connector.collection_status(rows, []) == "partial"


def test_death_only_source_is_not_marked_as_complete_cases():
    death = row(8, Measure="deaths", SourceDiseaseCode="hepatitis_a__deaths")
    assert connector.collection_status([death], []) == "death_only"
    assert connector.collection_status([death], [{"error": "another report failed"}]) == "partial"


def test_month_failure_does_not_abort_other_months_or_datacenter(monkeypatch):
    class Monthly:
        def __init__(self, **kwargs): pass
        def discover(self, code): return [link(8), link(9), link(10)]
        def fetch(self, item):
            if item.report_date.month == 9:
                raise ValueError("unknown disease contract")
            return SimpleNamespace(rows=[row(item.report_date.month)])

    center_calls = []
    class Center:
        config = {"disease_ids": [10]}
        def __init__(self, **kwargs): pass
        def available_years(self): return [2021]
        def _fetch_report(self, *args): center_calls.append(args); return []

    monkeypatch.setattr(connector, "ProvinceMonthlyReportCrawler", Monthly)
    monkeypatch.setattr(connector, "ProvinceDataCenterCrawler", Center)
    plan = connector._collect(None, country_code="CN-BJ", source="all", force=False,
                              start_year=None, recent_months=3, today=date(2026, 11, 16))
    assert [item["date"] for item in plan["reports"]] == ["2026-08-01", "2026-10-01"]
    assert len(center_calls) == 12
    assert plan["errors"][0]["date"] == "2026-09-01"


def test_gap_attempts_and_failed_report_diagnostics_survive_collection(monkeypatch):
    class Monthly:
        discovery_diagnostics = []
        report_diagnostics = []

        def __init__(self, **kwargs):
            pass

        def discover(self, code):
            return [link(month) for month in range(1, 11)]

        def fetch(self, item):
            self.report_diagnostics = ([{"status": "summary_only", "month": "2026-03"}]
                                       if item.report_date.month == 3 else [])
            if item.report_date.month == 3:
                raise ValueError("No explicit disease values")
            return SimpleNamespace(rows=[row(item.report_date.month)])

    monkeypatch.setattr(connector, "ProvinceMonthlyReportCrawler", Monthly)
    plan = connector._collect(None, country_code="CN-BJ", source="cn_province_monthly_report",
                              force=False, start_year=None, recent_months=3, fill_missing=True,
                              known_case_months={"2026-01"}, recently_attempted_months={"2026-02"},
                              today=date(2026, 11, 16))
    assert plan["history_backfill"]["selected_months"] == ["2026-03", "2026-04", "2026-05"]
    assert plan["history_backfill"]["limit"] == 3
    assert plan["errors"][0]["diagnostics"] == [{"status": "summary_only", "month": "2026-03"}]


def test_no_monthly_entry_does_not_block_datacenter_in_all(monkeypatch):
    monkeypatch.setattr(connector, "ProvinceMonthlyReportCrawler", lambda **kwargs: SimpleNamespace(discover=lambda code: []))
    center = SimpleNamespace(config={"disease_ids": [10]}, available_years=lambda: [2021], _fetch_report=lambda *args: [])
    monkeypatch.setattr(connector, "ProvinceDataCenterCrawler", lambda **kwargs: center)
    plan = connector._collect(None, country_code="CN-XZ", source="all", force=False,
                              start_year=None, recent_months=3, today=date(2026, 11, 16))
    assert len(plan["datacenter"]) == 12 and len(plan["errors"]) == 1


async def test_http_retry_records_error_and_success_and_replays_success(monkeypatch):
    responses = []
    for status, content in [(429, b"rate limited"), (200, b"official table")]:
        response = requests.Response()
        response.status_code = status
        response.url = "https://official.example/report"
        response._content = content
        responses.append(response)
    get = Mock(side_effect=responses)
    monkeypatch.setattr(requests.Session, "get", get)
    monkeypatch.setattr("src.data.crawlers.cn_provinces.time.sleep", lambda delay: None)
    captured = []
    async def write(content, *, source_url, kind, metadata):
        captured.append(SimpleNamespace(content=content, source_url=source_url, kind=kind, metadata_=metadata))
    session = connector._RecordingSession(SimpleNamespace(write=write), asyncio.get_running_loop(), Event())
    crawler = connector.ProvinceMonthlyReportCrawler(session=session)
    response = await asyncio.to_thread(crawler._get, "https://official.example/report")
    assert response.status_code == 200 and get.call_count == 2
    assert [item.metadata_["status_code"] for item in captured] == [429, 200]
    monkeypatch.setattr(connector, "read_artifact", lambda item: item.content)
    replay = connector._ReplaySession(captured)
    assert replay.get("https://official.example/report").content == b"official table"
    with pytest.raises(RuntimeError, match="No captured"):
        connector._ReplaySession(captured[:1]).get("https://official.example/report")


def test_organize_preserves_collection_errors_in_source_audit(monkeypatch):
    plan = {"country_code": "CN-BJ", "source": "all", "reports": [{"date": "2026-09-01", "url": link(9).url, "title": "report"}],
            "datacenter": [], "errors": [{"source": "cn_province_datacenter", "error": "upstream unavailable"}]}
    artifact = SimpleNamespace(kind="province_plan", metadata_={})
    monkeypatch.setattr(connector, "read_artifact", lambda item: json.dumps(plan).encode())
    monkeypatch.setattr(connector, "ProvinceMonthlyReportCrawler", lambda **kwargs: SimpleNamespace(fetch=lambda link: SimpleNamespace(rows=[row()])))
    result = connector.organize([artifact], updater=None, source="all", country_code="CN-BJ")
    assert result.rows == [row()]
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["errors"] == plan["errors"]


def test_cli_distinguishes_sparse_rows_and_failed_pages(monkeypatch):
    sparse = row(Raw={"explicit_narrative_value": "甲型肝炎无病例"})
    class Crawler:
        discovery_diagnostics = [{"status": "fetched", "url": "index"}]
        report_diagnostics = [{"status": "partial_narrative"}]
        def __init__(self, **kwargs): pass
        def discover(self, code): return [link(8), link(9)]
        def fetch(self, item):
            if item.report_date.month == 8:
                raise ValueError("no table")
            return SimpleNamespace(rows=[sparse], source_url=item.url, artifact_sha256="a" * 64)
    monkeypatch.setattr(cli, "ProvinceMonthlyReportCrawler", Crawler)
    rows, audit = cli._select_monthly_rows("CN-BJ", 2026, None)
    assert rows == [sparse] and audit["status"] == "partial"
    assert audit["reports"][0]["status"] == "narrative_sparse"
    assert audit["reports"][0]["diagnostics"] == [{"status": "partial_narrative"}]


@pytest.mark.parametrize("changes,reason", [
    ({"GeographyKey": "country:CN:national"}, "geography"),
    ({"JurisdictionCode": "CN-ZZ", "GeographyKey": "country:CN-ZZ:national"}, "geography"),
    ({"Cases": -1}, "nonnegative integer"),
    ({"Cases": True}, "nonnegative integer"),
    ({"Date": "9999-01-01"}, "completed month"),
    ({"SourceDiseaseCode": "unregistered_new_condition", "RawDiseaseLabel": "unregistered_new_condition"}, "mapping preflight"),
])
def test_candidate_validation_rejects_bad_rows_before_any_import(changes, reason):
    with pytest.raises(ValueError, match=reason):
        cli.preflight_rows([row(), row(8, **changes)])


def test_candidate_preflight_covers_deaths_and_conflicts_across_chunks():
    checked = cli.preflight_rows([row(Deaths=1)])
    assert checked["expanded_metric_rows"] == checked["unique_observations"] == 2
    assert any("DEATH" in code for code in checked["required_series_codes"])
    with pytest.raises(ValueError, match="Conflicting source rows"):
        cli.preflight_rows([row(cases=3), row(cases=4)])


async def test_offline_preview_checks_digest_without_database_or_network(tmp_path, monkeypatch):
    candidate = package(tmp_path / "candidate.json", [row()])
    monkeypatch.setattr(cli, "preflight_database", AsyncMock(side_effect=AssertionError("database must remain offline")))
    monkeypatch.setattr(cli, "ProvinceMonthlyReportCrawler", Mock(side_effect=AssertionError("network must remain offline")))
    args = cli.build_parser().parse_args(["import", "--rows-file", str(candidate)])
    assert await cli._run(args) == 0
    payload = json.loads(candidate.read_text())
    payload["rows"][0]["Cases"] = 999
    candidate.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SHA-256"):
        await cli._run(args)


async def test_apply_requires_explicit_partial_flag_and_marks_committed_scope(tmp_path, monkeypatch):
    candidate = package(tmp_path / "candidate.json", [row(), row(8)], {
        "monthly_jurisdictions": [{"province": "CN-BJ", "status": "partial", "errors": [{"error": "old month unavailable"}]}]})
    monkeypatch.setattr(cli, "preflight_database", AsyncMock(return_value={"status": "ready"}))
    ensure = AsyncMock()
    dirty = AsyncMock()
    monkeypatch.setattr(cli, "_ensure_province_rows", ensure)
    monkeypatch.setattr(cli, "mark_dirty", dirty)
    imported = AsyncMock(return_value=SimpleNamespace(inserted_or_updated=1, skipped_rows=0,
        source_ids=("SRC_CN_PROV_MONTHLY_REPORT",), source_latest_date=date(2026, 9, 1)))
    monkeypatch.setattr(cli.CNProvinceUpdater, "import_rows", imported)
    db = SimpleNamespace(commit=AsyncMock())
    @asynccontextmanager
    async def database(): yield db
    monkeypatch.setattr(cli, "get_db", database)
    args = cli.build_parser().parse_args(["import", "--rows-file", str(candidate), "--apply", "--chunk-size", "1"])
    with pytest.raises(RuntimeError, match="allow-partial"):
        await cli._run(args)
    ensure.assert_not_awaited()
    imported.assert_not_awaited()
    args.allow_partial = True
    assert await cli._run(args) == 2
    assert imported.await_count == dirty.await_count == 2
    assert {call.kwargs["scope_key"] for call in dirty.await_args_list} == {"CN-BJ"}
    assert len({call.kwargs["task_uuid"] for call in dirty.await_args_list}) == 1
