"""Real HPA vector labels, provenance, and staged replay boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.mv import (
    INDEX_URL, DEFAULT_SCOPE, MaldivesContractError, discover_reports,
    discover_pdfs, parse_report_pdf,
)
from src.data.processors.mv import MaldivesHPAUpdater
from src.services.ingestion.countries import mv

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/MV"
DETAIL = "https://health.gov.mv/en/publications/monthly-cd-report-august-2024"
PDF = "https://health.gov.mv/storage/uploads/0wNNyBwA/hvdpwbbo.pdf"


def report():
    return {"url": DETAIL, "title": "Monthly CD report August 2024", "year": 2024, "month": 8, "pdf_url": PDF}


def test_real_ministry_discovery_selects_disease_attachment_and_not_recruitment():
    refs, pages = discover_reports((FIXTURES / "monthly_index.html").read_bytes())
    assert [(r["year"], r["month"]) for r in refs] == [(2024, 8), (2024, 7), (2024, 6), (2024, 5)]
    assert pages and all("search=monthly" in url for url in pages)
    found = discover_pdfs((FIXTURES / "monthly_202408.html").read_bytes(), report())
    assert found["pdf_url"] == PDF
    assert "ndgeuyyb" not in found["pdf_url"]


def test_real_pdf_reads_only_printed_current_year_counts_with_calendar_periods():
    payload = (FIXTURES / "hpa_monthly_202408.pdf").read_bytes()
    rows, audit = parse_report_pdf(payload, report())
    assert len(rows) == 32
    dengue = [r for r in rows if r["RawDiseaseLabel"] == "Dengue Fever"]
    assert [int(r["Cases"]) for r in dengue] == [144, 120, 126, 256, 675, 798, 577, 393]
    assert dengue[1]["SourcePeriodEnd"] == "2024-02-29"
    assert {r["Frequency"] for r in rows} == {"monthly"}
    assert {r["Measure"] for r in rows} == {"cases"}
    assert {r["RawSHA256"] for r in rows} == {hashlib.sha256(payload).hexdigest()}
    assert {r["GeographyKey"] for r in rows} == {"country:MV:national"}
    assert not any(r["Cases"] in {"458", "660", "194"} for r in dengue)  # historical line endpoints
    assert audit["coverage_complete"] is False
    assert audit["skipped_panels"] == []


@pytest.mark.parametrize("patch", [{"year": 2025}, {"month": 7}])
def test_report_title_period_must_match_pdf(patch):
    ref = {**report(), **patch}
    with pytest.raises(MaldivesContractError, match="contradicts"):
        parse_report_pdf((FIXTURES / "hpa_monthly_202408.pdf").read_bytes(), ref)


def test_incomplete_pdf_and_cross_host_catalogue_are_rejected():
    with pytest.raises(MaldivesContractError, match="Incomplete"):
        parse_report_pdf(b"%PDF fake truncated file", report())
    with pytest.raises(MaldivesContractError, match="host"):
        discover_reports(b"<html/>", "https://example.com/")
    with pytest.raises(MaldivesContractError, match="attachment"):
        discover_pdfs(b'<a href="https://example.com/report.pdf">Monthly CD report August</a>', report())


class MemoryWriter:
    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        a = SimpleNamespace(payload=payload, sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload),
                            source_url=source_url, kind=kind, metadata_=metadata)
        self.artifacts.append(a)
        return a


async def test_collect_then_replay_requires_original_publication_links(monkeypatch):
    payloads = {INDEX_URL: f'<a href="{DETAIL}">Monthly CD report August 2024</a>'.encode(),
                DETAIL: (FIXTURES / "monthly_202408.html").read_bytes(),
                PDF: (FIXTURES / "hpa_monthly_202408.pdf").read_bytes()}
    class FixtureCrawler:
        def __init__(self):
            self.session = SimpleNamespace(close=lambda: None)
        def get(self, url):
            return SimpleNamespace(content=payloads[url])
    writer = MemoryWriter()
    await mv.collect(writer, start_year=2024, max_reports=1, crawler_type=FixtureCrawler)
    monkeypatch.setattr(mv, "read_artifact", lambda a: a.payload)
    fetched = mv.organize(writer.artifacts, updater=MaldivesHPAUpdater())
    assert len(fetched.rows) == 32
    assert fetched.source_audit["latest_report_period"] == "2024-08"
    plan = next(a for a in writer.artifacts if a.metadata_["endpoint"] == "plan")
    document = json.loads(plan.payload)
    document["captured"][0]["report"]["pdf_url"] = "https://health.gov.mv/storage/forged.pdf"
    plan.payload = json.dumps(document).encode()
    with pytest.raises(MaldivesContractError, match="manifest"):
        mv.organize(writer.artifacts, updater=MaldivesHPAUpdater())


def test_documents_only_is_explicit_and_never_zero_cases():
    fetched = MaldivesHPAUpdater().prepare_rows([], source_audit={"status": "documents_only"})
    assert fetched.rows == [] and fetched.source_latest_date is None
    assert fetched.source_audit["status"] == "documents_only"
    assert MaldivesHPAUpdater.requires_verified_observations is True
