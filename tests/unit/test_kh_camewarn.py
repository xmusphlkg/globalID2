"""Real CamEWARN artifacts: exact matrices, native calendar and chart gaps."""
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from src.data.crawlers.kh import (
    CambodiaContractError, INDEX_URL, archive_members, discover_reports,
    merge_report_rows, parse_report_pdf, report_period,
)
from src.data.processors.kh import CambodiaCamEWARNWeeklyUpdater
from src.services.ingestion.countries import kh

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/KH"


def parse(name, year):
    return parse_report_pdf((FIXTURES / name).read_bytes(), source_url=INDEX_URL, expected_year=year)


@pytest.mark.parametrize("year,expected,diarrhea", [(2015, 10, "5084"), (2016, 10, "5023"),
                                                   (2017, 7, "4577"), (2018, 7, "5804")])
def test_real_historical_matrices_keep_every_published_category(year, expected, diarrhea):
    rows, audit = parse(f"week01_{year}.pdf", year)
    assert len(rows) == expected
    values = {row["SourceDiseaseCode"]: row["Cases"] for row in rows}
    assert values["acute_diarrhea"] == diarrhea
    assert values["acute_flaccid_paralysis"] == "0"
    assert audit["observed_values"] == expected
    assert all(row["GeographyKey"] == "country:KH:national" for row in rows)
    # Future-week cells and stale totals in the 2016 template are not facts.
    assert {row["SourceWeek"] for row in rows} == {"1"}


def test_source_calendar_is_not_silently_replaced_with_iso_weeks():
    rows, _ = parse("week01_2015.pdf", 2015)
    assert rows[0]["Date"] == "2014-12-31"
    assert rows[0]["PeriodEnd"] == "2015-01-06"
    assert rows[0]["SourceReportYear"] == "2015"
    assert rows[0]["Date"] != date.fromisocalendar(2015, 1, 1).isoformat()


def test_raster_chart_numbers_and_reporting_completeness_are_not_cases():
    rows, audit = parse("week43_2026.pdf", 2026)
    assert len(rows) == 7
    assert audit["observed_values"] == 0
    assert all(row["Cases"] == "" and row["MissingValueStatus"] == "source_missing" for row in rows)
    assert all(json.loads(row["Dimensions"])["chart_only"] for row in rows)
    assert all(row["Cases"] not in {"1339", "1438", "1278"} for row in rows)


def test_real_header_year_conflict_is_rejected_without_silent_repair():
    with pytest.raises(CambodiaContractError, match="conflicts"):
        parse("invalid_header_week10_2021.pdf", 2021)


def test_full_directory_discovers_only_linked_history_and_retains_wrong_aliases():
    targets = discover_reports((FIXTURES / "index.html").read_bytes())
    archives = [target for target in targets if target["format"] != "pdf"]
    assert len(archives) == 9
    assert {entry["year"] for entry in targets} == set(range(2015, 2023)) | {2025, 2026}
    week25 = next(target for target in targets if target["url"].endswith("W25_2026.pdf"))
    assert set(week25["labels"]) == {"W25", "W31"}
    assert not any("W31_2026.pdf" in target["url"] for target in targets)


def test_zip_members_do_not_extract_untrusted_paths(tmp_path):
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../../report.pdf", b"%PDF sample")
    assert archive_members(source.getvalue(), kind="zip") == [("../../report.pdf", b"%PDF sample")]
    assert not (tmp_path / "report.pdf").exists()


def test_later_snapshot_revises_same_source_cell_and_same_edition_conflicts_fail():
    rows, _ = parse("week01_2015.pdf", 2015)
    revised = dict(rows[0], Cases="5000", SourceReportWeek="2")
    assert merge_report_rows([rows, [revised]])[0]["Cases"] == "5000"
    conflicting = dict(rows[0], Cases="5000")
    with pytest.raises(CambodiaContractError, match="Conflicting same-edition"):
        merge_report_rows([rows, [conflicting]])


@pytest.mark.parametrize("value", ["Week 43-2026 (19 - 24 Oct 2026)", "Week 55-2026 (19 - 25 Oct 2026)"])
def test_invalid_week_or_interval_fails_closed(value):
    with pytest.raises(CambodiaContractError):
        report_period(value, expected_year=2026)


def _artifact(payload, tmp_path, *, endpoint, url=INDEX_URL, kind="archive_json", **metadata):
    path = tmp_path / f"{hashlib.sha256(payload).hexdigest()}.bin"; path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload), source_url=url, kind=kind, metadata_={"endpoint": endpoint, **metadata})


def test_organize_replays_only_captured_source_and_detects_missing_pdf_manifest(tmp_path, monkeypatch):
    payload = (FIXTURES / "index.html").read_bytes()
    targets = discover_reports(payload, start_year=2026, end_year=2026)
    target = next(t for t in targets if t["url"].endswith("W43_2026.pdf"))
    pdf = (FIXTURES / "week43_2026.pdf").read_bytes()
    index = _artifact(payload, tmp_path, endpoint="index", kind="html_report")
    report = _artifact(pdf, tmp_path, endpoint="published_file", url=target["url"], kind="cda_pdf", year=2026)
    collection = {"targets": targets, "captured": [target["url"]], "errors": [], "member_manifest": [
        {"url": target["url"], "member": "", "year": 2026, "sha256": report.sha256}]}
    audit = _artifact(json.dumps(collection).encode(), tmp_path, endpoint="collection_audit")
    # read_artifact's real hash check is covered by shared artifact tests; allow
    # this isolated fixture directory and explicitly prohibit source requests.
    monkeypatch.setattr(kh, "read_artifact", lambda a: Path(a.relative_path).read_bytes())
    monkeypatch.setattr(kh.CambodiaCamEWARNCrawler, "get", lambda *a, **kw: pytest.fail("offline organize made a request"))
    result = kh.organize([index, report, audit], updater=CambodiaCamEWARNWeeklyUpdater(), start_year=2026, end_year=2026)
    assert len(result.rows) == 7
    with pytest.raises(CambodiaContractError, match="manifest"):
        kh.organize([index, audit], updater=CambodiaCamEWARNWeeklyUpdater(), start_year=2026, end_year=2026)
