"""Exact numerical regressions from genuine WHO Afghanistan PDF originals."""
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers import af_who_outbreaks as source
from src.data.crawlers.af import AfghanistanNDSRContractError
from src.data.processors.af_who_outbreaks import AfghanistanWHOOutbreaksUpdater
from src.services.ingestion.countries import af_who_outbreaks as connector

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/AF"


def reports():
    return source.discover_reports((FIXTURE / "who_outbreaks_index_20261117.html").read_bytes(), max_reports=None)


def test_real_official_archive_contains_50_reports_in_2025_without_2026_claim():
    found = reports()
    assert len(found) == 50
    assert {r["listed_week"] for r in found} == set(range(1, 51))
    assert {r["listed_year"] for r in found} == {2025}
    assert source.discover_reports((FIXTURE / "who_outbreaks_index_20261117.html").read_bytes(), start_year=2026) == []
    assert [r["listed_week"] for r in source.discover_reports(
        (FIXTURE / "who_outbreaks_index_20261117.html").read_bytes(), weeks=[1, 49, 50])] == [50, 49, 1]
    with pytest.raises(AfghanistanNDSRContractError, match="source weeks"):
        source.discover_reports(b"<html/>", weeks=[0, 54])


def test_historical_archive_counts_and_conflicting_2023_link_are_audited():
    index = (FIXTURE / "who_outbreaks_index_20261117.html").read_bytes()
    reports_2022, conflicts_2022 = source.discover_reports(index, start_year=2022, end_year=2022,
                                                             max_reports=None, return_conflicts=True)
    reports_2023, conflicts_2023 = source.discover_reports(index, start_year=2023, end_year=2023,
                                                             max_reports=None, return_conflicts=True)
    reports_2024, conflicts_2024 = source.discover_reports(index, start_year=2024, end_year=2024,
                                                             max_reports=None, return_conflicts=True)
    assert (len(reports_2022), len(conflicts_2022)) == (48, 0)
    assert (len(reports_2023), len(conflicts_2023)) == (49, 1)
    assert (len(reports_2024), len(conflicts_2024)) == (51, 0)
    conflict = conflicts_2023[0]
    assert (conflict["first"]["listed_week"], conflict["conflicting"]["listed_week"]) == (19, 18)
    assert conflict["status"] == "conflicting_official_link_periods"


def test_legacy_report_headers_keep_explicit_period_without_iso_inference():
    report = {"listed_year": 2023, "listed_week": 1}
    assert source.report_period(
        "AFGHANISTAN\nINFECTIOUS DISEASE OUTBREAKS\nSITUATION REPORT | Epidemiological week #01-2023\nNo. 01/ (01 to 07 Jan)",
        report,
    )[2:] == (date(2023, 1, 1), date(2023, 1, 7))
    legacy = source.report_period(
        "Afghanistan: Infectious Disease Outbreaks\nEpidemiological week # 1, 2022 (2-8 January)\nSituation Report #22",
        {"listed_year": 2022, "listed_week": 1},
    )
    assert legacy[2].isoformat() == "2022-01-02" and legacy[3].isoformat() == "2022-01-08"


def test_attached_revision_footnote_is_not_an_ambiguous_count():
    assert source._matrix_count("2,955*")[0] == "2955"


@pytest.mark.parametrize("week,filename,expected", [
    (1, "who_outbreaks_2025_w01.pdf", {
        ("ari_pneumonia", "cases"): "38012", ("ari_pneumonia", "deaths"): "99",
        ("measles", "cases"): "1136", ("measles", "deaths"): "8",
        ("covid_19", "cases"): "55", ("covid_19", "deaths"): "0",
        ("awd_with_dehydration", "cases"): "1663", ("awd_with_dehydration", "deaths"): "0",
        ("dengue_fever", "cases"): "9", ("dengue_fever", "deaths"): "0",
        ("cchf", "cases"): "7", ("cchf", "deaths"): "1",
        ("malaria", "cases"): "238", ("malaria", "deaths"): "0",
    }),
    (49, "who_outbreaks_2025_w49.pdf", {
        ("ari_pneumonia", "cases"): "38819", ("ari_pneumonia", "deaths"): "94",
        ("measles", "cases"): "409", ("measles", "deaths"): "3",
        ("covid_19", "cases"): "43", ("covid_19", "deaths"): "0",
    }),
    (18, "who_outbreaks_2025_w18.pdf", {
        ("measles", "cases"): "3905", ("measles", "deaths"): "27",
        ("awd_with_dehydration", "cases"): "3160", ("awd_with_dehydration", "deaths"): "0",
        ("ari_pneumonia", "cases"): "25747", ("ari_pneumonia", "deaths"): "52",
        ("covid_19", "cases"): "52", ("covid_19", "deaths"): "0",
        ("dengue_fever", "cases"): "33", ("dengue_fever", "deaths"): "0",
        ("cchf", "cases"): "45", ("cchf", "deaths"): "1",
        ("malaria", "cases"): "838", ("malaria", "deaths"): "0",
    }),
    (50, "who_outbreaks_2025_w50.pdf", {
        ("ari_pneumonia", "cases"): "37388", ("ari_pneumonia", "deaths"): "65",
        ("measles", "cases"): "405", ("measles", "deaths"): "3",
    }),
])
def test_live_pdf_regression_reads_current_week_cells_and_no_cumulative_counts(week, filename, expected):
    report = next(r for r in reports() if r["listed_week"] == week)
    payload = (FIXTURE / filename).read_bytes()
    rows, audit = source.parse_report_pdf(payload, report)
    actual = {(r["SourceDiseaseCode"].split(":")[1], r["Measure"]): r["Cases"] for r in rows}
    assert actual == expected
    assert all(r["RawSHA256"] == hashlib.sha256(payload).hexdigest() for r in rows)
    assert all(r["SourceReportWeek"] == str(week) and r["GeographyKey"] == source.GEOGRAPHY_KEY for r in rows)
    assert all(r["PublicReleaseEnabled"] == "false" for r in rows)
    assert audit["numerical_contract_live_verified"] is True
    assert audit["coverage_complete"] is False
    if week == 1:
        assert all(r["PeriodStart"] == "2024-12-29" and r["PeriodEnd"] == "2025-01-04" for r in rows)
    if week == 50:
        assert sum(t["status"] == "unreadable_matrix" for t in audit["tables"]) == 5


def test_real_originals_match_recorded_provenance_digests():
    provenance = json.loads((FIXTURE / "provenance.json").read_text())
    for name, digest in provenance["files"].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest


def test_different_pdf_and_listing_week_cannot_share_one_source_period():
    report = next(r for r in reports() if r["listed_week"] == 50)
    cover = source.extract_pdf_pages((FIXTURE / "who_outbreaks_2025_w50.pdf").read_bytes())[0]
    with pytest.raises(AfghanistanNDSRContractError, match="differs"):
        source.report_period(cover, report | {"listed_week": 49})
    with pytest.raises(AfghanistanNDSRContractError, match="soft 404"):
        source.discover_reports(b"<title>WHO EMRO - 404 Error</title>")


def test_missing_counts_and_duplicate_week_columns_fail_without_invented_cases():
    report = next(r for r in reports() if r["listed_week"] == 50)
    cover = "AFGHANISTAN\nINFECTIOUS DISEASE OUTBREAKS\nSITUATION REPORT | Epidemiological week #50-2025 No. 50 (07-13 Dec 2025)"
    table = "\nTable 1: Summary of the measles outbreak in the last eight weeks in Afghanistan\nIndicators W49 W50 Trend line\nSuspected cases 409 NR\nSuspected deaths 3 0\n"
    rows, _ = source.parse_report_pages([cover + table], report)
    assert rows[0]["Cases"] == "" and rows[0]["MissingValueStatus"] == "source_not_reported"
    assert rows[1]["Cases"] == "0" and rows[1]["Measure"] == "deaths"
    with pytest.raises(AfghanistanNDSRContractError, match="unique final"):
        source.parse_report_pages([cover + table.replace("W49 W50", "W50 W50")], report)


def test_offline_organize_requires_captured_links_and_complete_report_plan(monkeypatch):
    report = reports()[0]
    directory = SimpleNamespace(source_url=source.INDEX_URL, metadata_={"endpoint": "directory"},
                                payload=(FIXTURE / "who_outbreaks_index_20261117.html").read_bytes())
    plan_data = {"reports": [report], "failures": [], "start_year": 2025, "end_year": 2026,
                 "max_reports": 1, "weeks": None, "captured_at": "2026-11-17T01:00:00+00:00"}
    plan = SimpleNamespace(metadata_={"endpoint": "plan"}, payload=json.dumps(plan_data).encode())
    original = SimpleNamespace(source_url=report["pdf_url"], metadata_={"endpoint": "report", "report": report},
                               payload=(FIXTURE / "who_outbreaks_2025_w50.pdf").read_bytes())
    monkeypatch.setattr(connector, "read_artifact", lambda artifact: artifact.payload)
    result = connector.organize([directory, original, plan], updater=AfghanistanWHOOutbreaksUpdater())
    assert len(result.rows) == 4 and result.source_latest_date.isoformat() == "2025-12-13"
    with pytest.raises(AfghanistanNDSRContractError, match="without explicit"):
        connector.organize([directory, plan], updater=AfghanistanWHOOutbreaksUpdater())
    plan_data["reports"][0] = report | {"listed_week": 51}
    plan.payload = json.dumps(plan_data).encode()
    with pytest.raises(AfghanistanNDSRContractError, match="differs"):
        connector.organize([directory, original, plan], updater=AfghanistanWHOOutbreaksUpdater())
