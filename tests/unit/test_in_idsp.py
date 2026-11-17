"""India IDSP keeps outbreak snapshots and S/P/L surveillance distinct."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers import in_idsp as source
from src.data.processors.in_idsp import IndiaIDSPUpdater
from src.services.ingestion.countries import in_idsp as connector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/IN"


@pytest.fixture(scope="module")
def archive():
    return (FIXTURES / "ncdc_weekly_outbreaks.html").read_bytes()


@pytest.fixture(scope="module")
def document():
    return (FIXTURES / "ncdc_2026_week37.pdf").read_bytes()


@pytest.fixture(scope="module")
def report(archive):
    return source.discover_reports(archive, start_year=2026)[-1]


@pytest.fixture(scope="module")
def parsed(document, report):
    return source.parse_report_pdf(document, report)


def test_real_official_archive_discovers_history_and_latest(archive):
    reports = source.discover_reports(archive)
    assert reports[0]["year"] == 2009
    assert (reports[-1]["year"], reports[-1]["week"]) == (2026, 37)
    assert len(source.discover_reports(archive, start_year=2026)) == 37
    assert len(source.discover_reports(archive, start_year=2025, end_year=2025)) == 52


def test_discovery_excludes_external_hosts_and_non_outbreak_pdfs():
    payload = b'''<a href="https://evil.example/uploads/weekly_outbreaks/2026/week37.pdf">PDF</a>
        <a href="/uploads/other/2026/week37.pdf">PDF</a>
        <a href="/uploads/weekly_outbreaks/2026/week37_1794379575.pdf">Week 37</a>'''
    reports = source.discover_reports(payload)
    assert len(reports) == 1
    assert reports[0]["pdf_url"].startswith("https://ncdc.mohfw.gov.in/")


def test_real_pdf_preserves_event_counts_deaths_and_source_dates(parsed):
    rows, audit = parsed
    event = [row for row in rows if json.loads(row["Dimensions"])["event_id"] == "AS/BAJ/2026/37/1492"]
    assert {row["Measure"]: row["Cases"] for row in event} == {"cases": "21", "deaths": "0"}
    assert event[0]["Date"] == "2026-09-13"
    assert event[0]["SourcePeriodStart"] == "2026-09-07"
    assert event[0]["RawDiseaseLabel"] == "Suspected Food Poisoning"
    assert event[0]["GeographyKey"] == "country:IN:source-state:AS:source-district:BAJ"
    assert audit["event_rows"] == 44
    assert audit["parsed_events"] == 44
    assert len(rows) == 88
    assert audit["issues"] == []


@pytest.mark.parametrize("year,week,filename,minimum_events", [
    (2020, 20, "ncdc_2020_week20.pdf", 5),
    (2015, 20, "ncdc_2015_week20.pdf", 30),
    (2009, 25, "ncdc_2009_week25.pdf", 15),
])
def test_historical_layouts_use_strict_text_fallback(year, week, filename, minimum_events):
    rows, audit = source.parse_report_pdf(
        (FIXTURES / filename).read_bytes(),
        {"year": year, "week": week, "pdf_url": "https://ncdc.mohfw.gov.in/fixture.pdf",
         "post_url": source.DIRECTORY_URL},
    )
    assert audit["parsed_events"] >= minimum_events
    assert len(rows) == audit["parsed_events"] * 2
    assert all(row["ReportingBasis"] == "outbreak_event_snapshot" for row in rows)
    assert all(row["SourceReportYear"] == str(year) for row in rows)
    assert all(row["SourcePeriodEnd"] >= row["SourcePeriodStart"] for row in rows)


def test_event_snapshots_do_not_become_routine_or_national_counts(parsed):
    rows, audit = parsed
    assert {row["ReportingBasis"] for row in rows} == {"outbreak_event_snapshot"}
    assert {row["PeriodType"] for row in rows} == {"snapshot"}
    assert {row["PublicReleaseEnabled"] for row in rows} == {"false"}
    assert all(row["GeographyKey"] != "country:IN:national" for row in rows)
    assert all(json.loads(row["Dimensions"])["surveillance_form"] == "not_reported" for row in rows)
    assert audit["routine_forms"] == {form: "not_available_from_outbreak_report" for form in ("S", "P", "L")}
    assert len(set(source.FORM_CASE_DEFINITIONS.values())) == 3
    assert IndiaIDSPUpdater.series_geography_from_rows is True


def test_late_annex_excluded_and_split_source_identifier_recovered(parsed):
    rows, audit = parsed
    identifiers = {json.loads(row["Dimensions"])["event_id"] for row in rows}
    assert "MH/GON/2026/37/1517" in identifiers
    assert "KL/KAN/2026/37/1536" not in identifiers
    assert "WB/COO/2026/37/1537" not in identifiers
    assert audit["late_report_annex_excluded"] is True


def test_explicit_cross_year_week_is_not_derived_from_iso_calendar():
    year, week, start, end = source._report_period("1st Week (29th December 2025 to 4th January 2026)")
    assert (year, week, start.isoformat(), end.isoformat()) == (2026, 1, "2025-12-29", "2026-01-04")
    with pytest.raises(source.IndiaIDSPContractError, match="explicit"):
        source._report_period("Epidemiological week 1 of 2026")


def test_pdf_cover_must_match_listing(document, report):
    with pytest.raises(source.IndiaIDSPContractError, match="disagree"):
        source.parse_report_pdf(document, report | {"week": 36})
    with pytest.raises(source.IndiaIDSPContractError, match="Incomplete"):
        source.parse_report_pdf(document[:-65536], report)
    with pytest.raises(source.IndiaIDSPContractError, match="Unable to read"):
        source.parse_report_pdf(b"%PDF broken\n%%EOF", report)


def test_conflicting_same_event_editions_are_quarantined(monkeypatch, parsed, report):
    base = parsed[0][0]
    monkeypatch.setattr(connector, "parse_report_pdf", lambda payload, info: ([base | {"Cases": payload.decode()}], {"year": 2026, "week": 37}))
    report2 = report | {"pdf_url": report["pdf_url"].replace("week37", "week37_edition")}
    rows, audit = connector.organize_documents([(report, b"21"), (report2, b"22")], expected_reports=[report, report2])
    assert rows == []
    assert len(audit["conflicting_observations"]) == 1


def test_parse_cache_reuses_verified_result_without_reparsing(tmp_path, monkeypatch, document, report):
    import hashlib
    raw_sha = hashlib.sha256(document).hexdigest()
    first_rows, first_audit = connector.organize_documents(
        [(report, document, raw_sha)], expected_reports=[report],
        parse_cache_directory=tmp_path,
    )
    assert first_rows and first_audit["parse_cache_misses"] == 1
    monkeypatch.setattr(connector, "parse_report_pdf", lambda *_: pytest.fail("cache miss"))
    rows, audit = connector.organize_documents(
        [(report, document, raw_sha)], expected_reports=[report],
        parse_cache_directory=tmp_path,
    )
    assert rows == first_rows
    assert audit["parse_cache_hits"] == 1


def test_replay_rejects_duplicate_pdf_artifacts(document, report):
    with pytest.raises(source.IndiaIDSPContractError, match="Duplicate IDSP PDF"):
        connector.organize_documents(
            [(report, document), (report, document)], expected_reports=[report],
        )


def test_routine_forms_rejected_by_outbreak_updater(parsed):
    with pytest.raises(ValueError, match="S/P/L"):
        IndiaIDSPUpdater().prepare_rows([parsed[0][0] | {"ReportingBasis": "syndromic_suspected"}])


def test_derived_parse_cache_is_source_and_parser_version_bound(tmp_path, monkeypatch, parsed, report):
    payload = b"verified-pdf-bytes"
    calls = {"count": 0}

    def parse_once(raw, info):
        calls["count"] += 1
        return parsed

    monkeypatch.setattr(connector, "parse_report_pdf", parse_once)
    first = connector.organize_documents([(report, payload, "" )], expected_reports=[report],
        parse_cache_directory=tmp_path)
    second = connector.organize_documents([(report, payload, "" )], expected_reports=[report],
        parse_cache_directory=tmp_path)
    assert calls["count"] == 1
    assert first[0] == second[0]
    assert second[1]["parse_cache_hits"] == 1
    assert second[1]["parsed_reports"] == 1


@pytest.mark.asyncio
async def test_bounded_capture_and_network_free_artifact_replay(monkeypatch, archive, document, report, parsed):
    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, payload, *, source_url, kind, metadata):
            self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, metadata_=metadata))

    class Crawler:
        def __init__(self):
            self.session = SimpleNamespace(close=lambda: None)

        def get(self, url):
            assert url == source.DIRECTORY_URL
            return SimpleNamespace(content=archive, url=url)

    class Cache:
        def __init__(self, **kwargs):
            pass

        def get(self, crawler, url, **kwargs):
            assert url == report["pdf_url"]
            return document, {"retrieved_at": "2026-11-17T00:00:00Z", "revalidated_at": "2026-11-17T00:00:00Z", "cache_status": "downloaded"}

    monkeypatch.setattr(connector, "NICDReportCache", Cache)
    writer = Writer()
    artifacts = await connector.collect(writer, start_year=2026, max_reports=1, crawler_type=Crawler)
    assert [a.metadata_["endpoint"] for a in artifacts] == ["directory", "pdf", "plan"]
    plan = json.loads(artifacts[-1].payload)
    assert plan["omitted_by_limit"] == 36
    monkeypatch.setattr(connector, "read_artifact", lambda a: a.payload)
    monkeypatch.setattr(connector, "parse_report_pdf", lambda payload, info: parsed)
    monkeypatch.setattr(connector.requests, "get", lambda *a, **k: pytest.fail("Organization must not access source network"))
    result = connector.organize(artifacts, updater=IndiaIDSPUpdater())
    assert len(result.rows) == 88
    assert result.source_audit["omitted_by_limit"] == 36
    assert result.source_audit["status"] == "partial"
    assert result.source_latest_date.isoformat() == "2026-09-13"
