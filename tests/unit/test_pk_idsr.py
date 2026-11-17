"""NIH source evidence, area totals, source weeks and offline artifact replay."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers import pk_idsr as source
from src.data.processors.pk_idsr import PakistanNIHIDSRUpdater
from src.services.ingestion.countries import pk_idsr as connector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/PK"


@pytest.fixture(scope="module")
def report():
    return source.discover_reports((FIXTURES / "nih_weekly_index.html").read_bytes())[0]


@pytest.fixture(scope="module")
def parsed(report):
    return source.parse_report_pdf((FIXTURES / "nih_2026_w42.pdf").read_bytes(), report)


def test_official_index_real_history_and_bounded_latest_selection(report):
    payload = (FIXTURES / "nih_weekly_index.html").read_bytes()
    reports = source.discover_reports(payload, max_reports=None)
    assert len(reports) == 94  # Official 2025 weeks 1–52 plus 2026 weeks 1–42.
    assert report["year"] == 2026 and report["week"] == 42
    assert report["pdf_url"].startswith("https://nih.org.pk/")
    assert len(source.discover_reports(payload)) == 12
    only = source.discover_reports(payload, start_year=2025, end_year=2025, weeks=[1], max_reports=None)
    assert [(item["year"], item["week"]) for item in only] == [(2025, 1)]
    assert all("\\n" not in item["pdf_url"] and "\n" not in item["pdf_url"] for item in reports)


def test_full_captured_archive_window_has_all_linked_years():
    reports = source.discover_reports((FIXTURES / "nih_weekly_index.html").read_bytes(),
                                      start_year=2021, max_reports=None)
    assert len(reports) == 204
    assert {item["year"] for item in reports} == {2021, 2022, 2023, 2024, 2025, 2026}


@pytest.mark.parametrize("href", ["https://evil.example/week.pdf", "javascript:alert(1)"])
def test_discovery_rejects_nonofficial_targets(href):
    with pytest.raises(source.PakistanIDSRContractError, match="host/path"):
        source.discover_reports(f'<a href="{href}">IDSRS Week 42 Bulletin (2026)</a>'.encode())


def test_actual_pdf_exact_counts_zero_nr_and_no_false_national_total(parsed):
    rows, audit = parsed
    assert audit["categories"] == 28 and len(rows) == 224
    indexed = {(row["RawDiseaseLabel"], json.loads(row["Dimensions"])["source_area"]): row for row in rows}
    assert indexed[("AD (Non-Cholera)", "Total")]["Cases"] == "98005"
    assert indexed[("Malaria", "Sindh")]["Cases"] == "68531"
    assert indexed[("Animal / Dog Bite", "ICT")]["Cases"] == "0"
    assert indexed[("Malaria", "Punjab")]["Cases"] == ""
    assert indexed[("Malaria", "Punjab")]["MissingValueStatus"] == "source_not_reported"
    assert indexed[("Malaria", "Total")]["GeographyKey"] == source.AGGREGATE_GEOGRAPHY_KEY
    assert all(row["GeographyKey"] != "country:PK:national" for row in rows)
    assert {row["Measure"] for row in rows} == {"cases"}
    assert all(row["PublicReleaseEnabled"] == "false" for row in rows)
    assert audit["death_counts_available_in_supported_table"] is False
    assert indexed[("Chickenpox/ Varicella", "Total")]["Cases"] == "196"
    assert indexed[("Diphtheria (Probable)", "Total")]["Cases"] == "4"
    assert PakistanNIHIDSRUpdater.series_geography_key is None
    assert PakistanNIHIDSRUpdater.series_geography_from_rows is True


def test_calendar_dates_never_guessed_and_source_week_is_explicit(parsed):
    rows, audit = parsed
    assert audit["dates_inferred"] is False
    for row in rows:
        assert row["Date"] == "2026-01-01"
        assert row["SourcePeriodYear"] == "2026" and row["SourcePeriodWeek"] == "42"
        assert row["DateRole"] == "source_year_storage_anchor_only"
        assert row["SourcePeriodStart"] == row["SourcePeriodEnd"] == ""
        assert json.loads(row["Dimensions"])["source_week"] == 42


@pytest.mark.parametrize("mutation", ["wrong_week", "wrong_header", "bad_total", "bad_row"])
def test_ambiguous_or_changed_table_contract_fails_closed(report, mutation):
    # Source text copied from the captured PDF, with one intentional corruption.
    pages = ["PUBLIC HEALTH BULLETIN-PAKISTAN",
             "Public Health Bulletin - Pakistan, Week 42, 2026",
             "Table 1: Province/Area wise distribution of most frequently reported suspected cases during Week 42, Pakistan.\n"
             "Diseases AJK Balochistan GB ICT KP Punjab Sindh Total\n" +
             "\n".join(f"Disease {chr(65+i)} 1 2 3 0 4 NR 5 15" for i in range(10)) + "\nPage | 5"]
    if mutation == "wrong_week": pages[1] = pages[1].replace("42", "41")
    if mutation == "wrong_header": pages[2] = pages[2].replace("Punjab Sindh", "Sindh Punjab")
    if mutation == "bad_total": pages[2] = pages[2].replace("5 15", "5 16", 1)
    if mutation == "bad_row": pages[2] = pages[2].replace("1 2 3 0 4 NR 5 15", "1 2 3 0 4 NR 5", 1)
    with pytest.raises(source.PakistanIDSRContractError):
        source.parse_report_pages(pages, report)


def test_truncated_or_html_pdf_rejected(report):
    with pytest.raises(source.PakistanIDSRContractError, match="Incomplete"):
        source.parse_report_pdf(b"<html>not a pdf</html>", report)


def test_conflicting_editions_quarantine_values_and_keep_source_weeks_distinct(monkeypatch, parsed, report):
    def fake(payload, target):
        rows = deepcopy(parsed[0])
        for row in rows:
            row["SourcePeriodWeek"] = str(target["week"])
        if payload == b"conflict": rows[0]["Cases"] = "9999"
        return rows, {"year": target["year"], "week": target["week"]}
    monkeypatch.setattr(connector, "parse_report_pdf", fake)
    rows, audit = connector.organize_documents([(report, b"original"), (report, b"conflict")])
    assert len(rows) == 223 and len(audit["conflicting_observations"]) == 1
    rows, audit = connector.organize_documents([(report, b"original"), ({**report, "week": 41}, b"original")])
    assert len(rows) == 448 and not audit["conflicting_observations"]


class MemoryWriter:
    def __init__(self): self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        artifact = SimpleNamespace(payload=payload, source_url=source_url, kind=kind,
            metadata_=metadata, sha256=hashlib.sha256(payload).hexdigest())
        self.artifacts.append(artifact)
        return artifact


@pytest.mark.asyncio
async def test_collect_then_offline_organize_and_manifest_integrity(monkeypatch, parsed):
    index = (FIXTURES / "nih_weekly_index.html").read_bytes()
    pdf = (FIXTURES / "nih_2026_w42.pdf").read_bytes()
    calls = []
    class FakeCrawler:
        session = SimpleNamespace(close=lambda: None)
        def get(self, url):
            calls.append(url)
            return SimpleNamespace(content=index if url == source.INDEX_URL else pdf)
    writer = MemoryWriter()
    await connector.collect(writer, max_reports=1, crawler_type=FakeCrawler)
    assert len(calls) == 2
    monkeypatch.setattr(connector, "read_artifact", lambda artifact: artifact.payload)
    monkeypatch.setattr(connector, "parse_report_pdf", lambda *args: deepcopy(parsed))
    result = connector.organize(writer.artifacts, updater=PakistanNIHIDSRUpdater())
    assert len(calls) == 2 and len(result.rows) == 224
    assert result.source_audit["latest_source_week"] == 42
    assert result.source_audit["omitted_by_limit"] == 93
    assert result.source_audit["authoritative_replacement"] is False
    artifact = next(a for a in writer.artifacts if a.metadata_["endpoint"] == "pdf")
    artifact.sha256 = "wrong"
    with pytest.raises(source.PakistanIDSRContractError, match="manifest"):
        connector.organize(writer.artifacts, updater=PakistanNIHIDSRUpdater())


@pytest.mark.asyncio
async def test_artifact_write_failure_propagates():
    class FakeCrawler:
        session = SimpleNamespace(close=lambda: None)
        def get(self, url): return SimpleNamespace(content=b"index")
    class BrokenWriter:
        async def write(self, *args, **kwargs): raise OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await connector.collect(BrokenWriter(), crawler_type=FakeCrawler)
