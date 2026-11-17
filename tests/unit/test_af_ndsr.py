"""NDSR candidate contract tests; fixtures are synthetic, not live originals."""
import json
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers import af as source
from src.data.processors.af import AfghanistanNDSRUpdater
from src.services.ingestion.countries import af as connector

REPORT = {"pdf_url": source.SEED_PDF_URL, "post_url": source.INDEX_URL,
          "listed_year": 2026, "discovery": "official_link", "title": "NDSR WER 33-2026"}
COVER = "Afghanistan Ministry of Public Health\nNDSR Weekly Epidemiological Report\nWeek 33 2026 (16-22 August 2026)"


def pages(*, headers=None, context=None, value="123", deaths="0"):
    return [{"text": COVER, "tables": []}, {
        "text": context or "Afghanistan national current week cases and deaths",
        "tables": [[headers or ["Disease", "Current week cases", "Current week deaths"],
                    ["Suspected measles", value, deaths]]]}]


def test_discovery_preserves_official_link_and_filters_upload_vs_report_year():
    payload = f'''<html><a href="{source.SEED_PDF_URL}">NDSR WER 33-2026</a>
        <a href="https://not-moph.example/NDSR-WER-2026.pdf">NDSR</a>
        <a href="/sites/default/files/2026-09/NDSR%20WER%2052-2024.pdf">NDSR 2024</a>
        <a href="/en/ndsr-weekly-epidemiological-reports">NDSR reports</a></html>'''.encode()
    directories, reports = source.discover_reports(payload, start_year=2025, end_year=2026)
    assert reports == [REPORT]
    assert directories == ["https://moph.gov.af/en/ndsr-weekly-epidemiological-reports"]


@pytest.mark.parametrize("url", ["http://moph.gov.af/sites/default/files/report.pdf",
    "https://moph.gov.af.evil.example/sites/default/files/report.pdf",
    "https://moph.gov.af:444/sites/default/files/report.pdf",
    "https://moph.gov.af/other/report.pdf"])
def test_explicit_urls_must_be_directly_published_ministry_pdfs(url):
    with pytest.raises(source.AfghanistanNDSRContractError):
        source.official_pdf_url(url)


def test_explicit_seed_does_not_fabricate_missing_history():
    assert connector._seed_reports([source.SEED_PDF_URL], start_year=2025, end_year=2025) == []
    assert connector._seed_reports([source.SEED_PDF_URL], start_year=2026, end_year=2026)[0]["listed_year"] == 2026


def test_explicit_period_cases_and_deaths_retain_separate_source_identities():
    rows, audit = source.parse_report_pages(pages(), REPORT, raw_sha256="digest")
    assert len(rows) == 2
    assert rows[0]["Cases"] == "123" and rows[0]["Measure"] == "cases"
    assert rows[1]["Cases"] == "0" and rows[1]["Measure"] == "deaths"
    assert rows[0]["SourceDiseaseCode"] != rows[1]["SourceDiseaseCode"]
    assert rows[0]["RawDiseaseLabel"] == "Suspected measles"
    assert rows[0]["PeriodStart"] == "2026-08-16"
    assert rows[0]["PeriodEnd"] == rows[0]["Date"] == "2026-08-22"
    assert rows[0]["GeographyKey"] == "source:AF:ndsr:reporting_sites"
    assert rows[0]["PublicReleaseEnabled"] == "false"
    assert audit["numerical_contract_live_verified"] is False


def test_missing_is_unknown_and_never_zero():
    rows, _ = source.parse_report_pages(pages(value="NR", deaths="-"), REPORT)
    assert all(row["Cases"] == "" and row["MissingValueStatus"] for row in rows)


@pytest.mark.parametrize("headers,context", [
    (["Disease", "Cumulative cases", "Cumulative deaths"], None),
    (["Disease", "Cases", "Deaths"], None),
    (None, "Afghanistan current week outbreak cases and deaths"),
    (None, "Afghanistan current week province cases and deaths"),
])
def test_ambiguous_cumulative_outbreak_and_province_tables_are_rejected(headers, context):
    with pytest.raises(source.AfghanistanNDSRContractError, match="No supported"):
        source.parse_report_pages(pages(headers=headers, context=context), REPORT)


def test_week_label_without_explicit_date_interval_is_not_iso_inferred():
    with pytest.raises(source.AfghanistanNDSRContractError, match="explicit"):
        source.report_period("Afghanistan NDSR Week 33 2026")
    with pytest.raises(source.AfghanistanNDSRContractError, match="seven-day"):
        source.report_period("Afghanistan NDSR Week 33 2026 (15-22 August 2026)")
    with pytest.raises(source.AfghanistanNDSRContractError, match="differs"):
        source.parse_report_pages(pages(), REPORT | {"listed_year": 2025})


def test_organize_requires_every_expected_original_or_explicit_failure():
    with pytest.raises(source.AfghanistanNDSRContractError, match="incomplete"):
        connector.organize_documents([], expected_reports=[REPORT], failed_downloads=[])
    rows, audit = connector.organize_documents([], expected_reports=[REPORT], failed_downloads=[
        {"url": REPORT["pdf_url"], "stage": "pdf", "error": "source inaccessible"}])
    assert rows == [] and audit["captured_reports"] == 0


async def test_blocked_collection_replays_offline_as_explicit_zero_observation_audit(monkeypatch):
    class Writer:
        def __init__(self):
            self.artifacts = []
        async def write(self, payload, *, source_url, kind, metadata):
            artifact = SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata)
            self.artifacts.append(artifact)
            return artifact
    class BlockedCrawler:
        session = SimpleNamespace(close=lambda: None)
        def get(self, url, **kwargs):
            raise requests.ConnectionError("test: source inaccessible")
    writer = Writer()
    artifacts = await connector.collect(writer, crawler_type=BlockedCrawler, start_year=2025, end_year=2026)
    plan = json.loads(artifacts[-1].payload)
    assert len(plan["failures"]) == 3
    assert plan["reports"][0]["discovery"] == "explicit_report_url"
    monkeypatch.setattr(connector, "read_artifact", lambda a: a.payload)
    monkeypatch.setattr(connector, "parse_report_pdf", lambda *_: pytest.fail("No invented report bytes"))
    result = connector.organize(artifacts, updater=AfghanistanNDSRUpdater())
    assert result.rows == []
    assert result.source_latest_date is None
    assert result.source_audit["status"] == "source_access_blocked"
    assert result.source_audit["coverage_complete"] is False
    # A changed plan cannot silently introduce an uncaptured source URL.
    plan["reports"][0]["pdf_url"] = "https://moph.gov.af/sites/default/files/unlinked.pdf"
    artifacts[-1].payload = json.dumps(plan).encode()
    with pytest.raises(source.AfghanistanNDSRContractError, match="differs"):
        connector.organize(artifacts, updater=AfghanistanNDSRUpdater())


def test_duplicate_conflicting_reports_are_quarantined(monkeypatch):
    second = REPORT | {"pdf_url": source.SEED_PDF_URL + "?revision=2"}
    def parse(payload, report):
        return source.parse_report_pages(pages(value=payload.decode()), report)
    monkeypatch.setattr(connector, "parse_report_pdf", parse)
    rows, audit = connector.organize_documents([(REPORT, b"123"), (second, b"124")],
        expected_reports=[REPORT, second], failed_downloads=[])
    assert len(rows) == 1 and rows[0]["Measure"] == "deaths"
    assert len(audit["conflicts"]) == 1
    with pytest.raises(source.AfghanistanNDSRContractError, match="duplicated"):
        connector.organize_documents([(REPORT, b"123"), (REPORT, b"123")],
            expected_reports=[REPORT], failed_downloads=[])
