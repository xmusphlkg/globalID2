"""WER discovery and capture contracts; no synthetic numerical source claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests

from src.data.crawlers.lk_wer import (
    INDEX_URL, SriLankaWERContractError, discover_archive_links, discover_reports,
    parse_report_pdf, validate_index, discover_wayback_reports, inspect_report_pdf,
)
from src.data.processors.lk_wer import SriLankaWERUpdater
from src.services.ingestion.countries import lk_wer

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/LK"
PDF_URL = "https://www.epid.gov.lk/reports/2025/wer-english.pdf"
# Synthetic link topology exercises discovery only; it is not an official
# archive snapshot and contains no invented disease observations.
INDEX = f'''<html><h1>Weekly Epidemiological Report</h1>
<p><a href="{PDF_URL}">WER English 2025</a></p>
<p><a href="https://evil.example/2025/wer.pdf">WER English 2025</a></p>
<p><a href="/reports/2025/wer-sinhala.pdf">WER Sinhala 2025</a></p>
<p><a href="/reports/2024/wer.pdf">WER English 2024</a></p>
<p><a href="/reports/wer.pdf">WER English</a></p>
<a href="{INDEX_URL}?year=2025">Archive 2025</a></html>'''.encode()


class MemoryWriter:
    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        artifact = SimpleNamespace(payload=payload, source_url=source_url, kind=kind,
            metadata_=metadata, sha256=hashlib.sha256(payload).hexdigest())
        self.artifacts.append(artifact)
        return artifact


def response(url, payload, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = payload
    return result


def crawler_type(responses):
    class Crawler:
        closed = False

        def __init__(self):
            self.session = SimpleNamespace(close=self.close)

        def close(self):
            type(self).closed = True

        def get(self, url):
            result = responses[url]
            result.raise_for_status()
            return result
    return Crawler


def test_official_outage_is_not_an_empty_archive():
    payload = (FIXTURE / "official-archive-bandwidth-error.html").read_bytes()
    receipt = json.loads((FIXTURE / "retrieval.json").read_text())
    assert hashlib.sha256(payload).hexdigest() == receipt["sha256"]
    with pytest.raises(SriLankaWERContractError, match="unavailable"):
        validate_index(payload)


def test_discovery_excludes_external_language_unknown_year_and_old_reports():
    reports = discover_reports(INDEX, start_year=2025)
    assert [report["pdf_url"] for report in reports] == [PDF_URL]
    assert reports[0]["listing_year"] == 2025
    assert discover_archive_links(INDEX) == [INDEX_URL + "?year=2025"]
    with pytest.raises(SriLankaWERContractError, match="host"):
        discover_reports(INDEX, source_url="https://example.com/weekly-epidemiological-report")


@pytest.mark.asyncio
async def test_http_outage_bytes_and_plan_survive_failed_collection():
    payload = (FIXTURE / "official-archive-bandwidth-error.html").read_bytes()
    crawler = crawler_type({INDEX_URL: response(INDEX_URL, payload, 500)})
    writer = MemoryWriter()
    with pytest.raises(SriLankaWERContractError, match="No official WER originals"):
        await lk_wer.collect(writer, crawler_type=crawler)
    assert crawler.closed
    assert writer.artifacts[0].payload == payload
    assert writer.artifacts[0].metadata_["http_status"] == 500
    plan = json.loads(writer.artifacts[-1].payload)
    assert plan["failures"][0]["stage"] == "index_download"
    assert plan["manifest"] == []


@pytest.mark.asyncio
async def test_bounded_capture_and_offline_inspection_never_emit_case_numbers(monkeypatch):
    payload = b"%PDF-1.4\nnot a validated report\n%%EOF"
    crawler = crawler_type({INDEX_URL: response(INDEX_URL, INDEX), PDF_URL: response(PDF_URL, payload)})
    writer = MemoryWriter()
    artifacts = await lk_wer.collect(writer, crawler_type=crawler, max_index_pages=1)
    monkeypatch.setattr(lk_wer, "read_artifact", lambda artifact: artifact.payload)
    audit = lk_wer.inspect_artifacts(artifacts)
    assert audit["observations"] == 0
    assert audit["remaining_archive_pages"] == 1
    assert audit["reports"][0]["status"] == "unparsed"
    assert audit["authoritative_replacement"] is False
    with pytest.raises(SriLankaWERContractError, match="no observations"):
        lk_wer.organize(artifacts, updater=SriLankaWERUpdater())


@pytest.mark.asyncio
async def test_manifest_mismatch_fails_offline_integrity(monkeypatch):
    crawler = crawler_type({INDEX_URL: response(INDEX_URL, INDEX), PDF_URL: response(PDF_URL, b"%PDF\n%%EOF")})
    artifacts = await lk_wer.collect(MemoryWriter(), crawler_type=crawler, max_index_pages=1)
    monkeypatch.setattr(lk_wer, "read_artifact", lambda artifact: artifact.payload)
    pdf = next(artifact for artifact in artifacts if artifact.kind == "cda_pdf")
    pdf.sha256 = "0" * 64
    with pytest.raises(SriLankaWERContractError, match="manifest"):
        lk_wer.inspect_artifacts(artifacts)


@pytest.mark.asyncio
async def test_artifact_persistence_failure_is_not_hidden():
    class BrokenWriter(MemoryWriter):
        async def write(self, *args, **kwargs):
            raise OSError("disk failure")
    crawler = crawler_type({INDEX_URL: response(INDEX_URL, INDEX)})
    with pytest.raises(OSError, match="disk failure"):
        await lk_wer.collect(BrokenWriter(), crawler_type=crawler)
    assert crawler.closed


def test_pdf_format_and_numerical_processor_fail_closed():
    with pytest.raises(SriLankaWERContractError, match="Invalid or incomplete"):
        parse_report_pdf(b"<html>403 Forbidden</html>", {"pdf_url": PDF_URL})
    updater = SriLankaWERUpdater()
    assert updater.public_release_enabled is False
    assert updater.numerical_contract_verified is True
    updater.normalize_source("lk_epid_wer_weekly")
    with pytest.raises(ValueError, match="Unsupported"):
        updater.normalize_source("pk_nih_idsr_weekly")
    with pytest.raises(SriLankaWERContractError, match="No verified"):
        updater.prepare_rows([{"Cases": "0"}])


def test_wayback_cdx_and_legacy_table5_parser_preserve_week_and_measures():
    cdx = (FIXTURE / "wayback-cdx.json").read_bytes()
    reports = discover_wayback_reports(cdx, start_year=2007, end_year=2007)
    assert reports
    assert all(item["original_url"].endswith("_english.pdf") for item in reports)
    assert all("web.archive.org" in item["pdf_url"] for item in reports)
    payload = (FIXTURE / "wayback-2007-vol34-no05.pdf").read_bytes()
    report = {"pdf_url": reports[0]["pdf_url"], "original_url": "https://www.epid.gov.lk/web/images/pdf/wer/2007/vol_34_no_05_english.pdf"}
    # Use the captured known report even if CDX ordering changes.
    report["pdf_url"] = "https://www.epid.gov.lk/web/images/pdf/wer/2007/vol_34_no_05_english.pdf"
    inspection = inspect_report_pdf(payload, source_url=report["original_url"])
    assert inspection["numerical_contract"] == "legacy_table5_v1"
    rows, detail = parse_report_pdf(payload, report)
    assert len(rows) == 27 * 8 * 2
    assert detail["period_start"] == "2007-01-20"
    assert detail["period_end"] == "2007-01-26"
    assert detail["week"] == 4
    assert {row["Measure"] for row in rows} == {"cases", "cumulative_cases"}
    assert {row["ReportingBasis"] for row in rows} == {"notification"}
    assert {row["GeographyKey"] for row in rows if row["Geography"] == "Sri Lanka"} == {"country:LK:national"}
    colombo_dengue = next(row for row in rows if row["Geography"] == "Colombo" and row["Measure"] == "cases" and row["RawDiseaseLabel"].startswith("Dengue"))
    assert colombo_dengue["Cases"] == "52"
    assert colombo_dengue["SourcePeriodStart"] == "2007-01-20"


def test_wayback_cdx_rejects_malformed_rows():
    with pytest.raises(SriLankaWERContractError, match="schema"):
        discover_wayback_reports(b'[["urlkey"]]')


def test_wayback_full_cdx_fixture_keeps_archive_provenance_and_year_bounds():
    cdx = (FIXTURE / "wayback-cdx-all.json").read_bytes()
    reports = discover_wayback_reports(cdx, start_year=2007, end_year=2023)
    assert len(reports) >= 700
    years = {item["listing_year"] for item in reports}
    assert {2007, 2014, 2021, 2022, 2023}.issubset(years)
    assert all(item["archive_digest"] and item["archive_timestamp"] for item in reports)
    assert all(urlparse(item["original_url"]).hostname in {"epid.gov.lk", "www.epid.gov.lk"} for item in reports)
