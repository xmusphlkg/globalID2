"""RCDC source evidence, observation semantics, and staged artifact replay."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.bt import (
    DEFAULT_SCOPE, LANDING_URL, BhutanRCDCContractError, discover_publications,
    discover_pdfs, discover_events, extract_pdf_pages, parse_report_pdf, parse_respiratory_text,
)
from src.data.processors.bt import BhutanRCDCPublicationsUpdater
from src.services.ingestion.countries import bt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/BT"
PDF_URL = "https://www.rcdc.gov.bt/web/wp-content/uploads/2026/11/Wk-4344.pdf"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def report():
    publication = discover_publications(fixture("publications_excerpt.html"))[0]
    return {**publication, "pdf_url": PDF_URL}


def test_real_publication_excerpt_classifies_all_document_kinds():
    publications = discover_publications(fixture("publications_excerpt.html"))
    assert {r["publication_kind"] for r in publications} == {
        "respiratory_surveillance", "monthly_epidemiology", "quarterly_bulletin",
        "annual_scientific", "event_report",
    }
    assert publications[0]["published_at"] == "2026-11-07T15:24:50+06:00"
    assert len(discover_publications(fixture("publications_excerpt.html"), start_year=2026)) == 4
    assert discover_pdfs(fixture("publication_8511_content.html"), publications[0]) == [PDF_URL]


def test_real_pdf_assigns_only_explicit_single_week_laboratory_detections():
    rows, detail = parse_report_pdf(fixture("rcdc_w43_w44_2026.pdf"), report())
    assert [(r["Date"], r["Cases"], r["SourceSamplesTested"]) for r in rows] == [
        ("2026-10-25", "44", "102"), ("2026-11-01", "13", "41")]
    assert {r["Measure"] for r in rows} == {"laboratory_detections"}
    assert {r["Frequency"] for r in rows} == {"weekly"}
    assert {r["PopulationScope"] for r in rows} == {"reporting_sentinel_network_not_population_cases"}
    assert detail["publication_frequency"] == "biweekly"
    assert all(r["RawSHA256"] == hashlib.sha256(fixture("rcdc_w43_w44_2026.pdf")).hexdigest() for r in rows)
    assert not any("RSV" in r["RawDiseaseLabel"] or "SARS" in r["RawDiseaseLabel"] for r in rows)


@pytest.mark.parametrize("name,expected", [
    ("rcdc_w39_w40_2026.pdf", [("2026-09-27", "64", "106"), ("2026-10-04", "18", "39")]),
    ("rcdc_w49_w50_2025.pdf", [("2025-12-07", "18", ""), ("2025-12-14", "25", "")]),
])
def test_real_older_cover_order_and_printed_counts_preserve_unknown_denominators(name, expected):
    rows, _ = parse_report_pdf(fixture(name), report())
    assert [(r["Date"], r["Cases"], r["SourceSamplesTested"]) for r in rows] == expected
    assert {r["Measure"] for r in rows} == {"laboratory_detections"}


@pytest.mark.parametrize("name,expected", [
    ("rcdc_w25_w26_2026.txt", [("2026-06-21", "53"), ("2026-06-28", "59")]),
    ("rcdc_w51_w52_2025.txt", [("2025-12-21", "22"), ("2025-12-28", "16")]),
    ("rcdc_w33_w34_2025.txt", [("2025-08-16", "10"), ("2025-08-23", "19")]),
])
def test_real_older_narrative_sentence_assigns_only_explicit_influenza_counts(name, expected):
    text = fixture(name).decode()
    report_data = report()
    rows, detail = parse_respiratory_text([text], report_data, raw_sha256="fixture")
    assert [(r["Date"], r["Cases"]) for r in rows] == expected
    assert all(r["SourceSamplesTested"] == "" for r in rows)
    assert detail["observation_frequency"] == "weekly"


@pytest.mark.parametrize("before,after", [
    ("43.1% (44/102)", "43.1% (144/102)"),
    ("43.1% (44/102)", "13.1% (44/102)"),
    ("in week 43", "in week 42"),
    ("October 19 - November 1, 2026", "October 18 - November 1, 2026"),
])
def test_ambiguous_or_conflicting_source_numbers_never_become_observations(before, after):
    pages = extract_pdf_pages(fixture("rcdc_w43_w44_2026.pdf"))
    assert before in pages[0]
    pages[0] = pages[0].replace(before, after)
    with pytest.raises(BhutanRCDCContractError):
        parse_respiratory_text(pages, report(), raw_sha256="test")


def test_no_cross_host_attachment_or_side_bar_document_is_trusted():
    payload = fixture("publication_8511_content.html").replace(PDF_URL.encode(), b"https://example.com/forged.pdf")
    with pytest.raises(BhutanRCDCContractError, match="outside official"):
        discover_pdfs(payload, report())
    assert discover_pdfs(b'<html><aside><a href="' + PDF_URL.encode() + b'">Related</a></aside></html>', report()) == []


def test_real_latest_events_preserve_event_count_and_district_without_incidence_or_addresses():
    events = discover_events(fixture("latest_events_excerpt.html"))
    assert len(events) == 5
    assert [(e["dzongkhag"], e["reported_date"], e["reported_event_cases"]) for e in events[:2]] == [
        ("Trongsa", "2026-11-07", 1), ("Dagana", "2026-11-04", 5)]
    assert events[-2]["raw_event_label"] == "Suspected Flu outbreak in Dungkar Primary School"
    assert all(e["count_semantics"] == "event_snapshot_not_daily_or_weekly_incidence" for e in events)
    assert not any("location" in e or "Cases" in e or "Date" in e for e in events)


class MemoryWriter:
    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, kind=kind,
                                              metadata_=metadata, sha256=hashlib.sha256(payload).hexdigest()))


class FixtureCrawler:
    def __init__(self):
        self.session = SimpleNamespace(close=lambda: None)

    def get(self, url):
        mapping = {LANDING_URL: "publications_excerpt.html", report()["publication_url"]: "publication_8511_content.html",
                   PDF_URL: "rcdc_w43_w44_2026.pdf"}
        return SimpleNamespace(content=fixture(mapping[url]), url=url)


async def test_captured_originals_replay_offline_and_expose_bounded_archive(monkeypatch):
    writer = MemoryWriter()
    await bt.collect(writer, max_reports=1, start_year=2026, crawler_type=FixtureCrawler)
    monkeypatch.setattr(bt, "read_artifact", lambda artifact: artifact.payload)
    result = bt.organize(writer.artifacts, updater=BhutanRCDCPublicationsUpdater(), source=DEFAULT_SCOPE)
    assert len(result.rows) == 2
    assert result.source_latest_date.isoformat() == "2026-11-01"
    assert result.source_audit["discovered_publications"] == 4
    assert result.source_audit["deferred_publications"] == 3
    assert result.source_audit["coverage_complete"] is False
    plan = next(a for a in writer.artifacts if a.metadata_["endpoint"] == "plan")
    document = json.loads(plan.payload)
    document["listing_sha256"] = "forged"
    plan.payload = json.dumps(document).encode()
    with pytest.raises(BhutanRCDCContractError, match="differs from captured"):
        bt.organize(writer.artifacts, updater=BhutanRCDCPublicationsUpdater())


def test_document_only_collection_is_valid_without_invented_numerical_rows():
    result = BhutanRCDCPublicationsUpdater().prepare_rows([], source_audit={"events": [{"title": "Outbreak"}]})
    assert result.rows == [] and result.source_latest_date is None
    assert result.source_audit["events"] == [{"title": "Outbreak"}]
    assert result.source_audit["coverage_complete"] is False


async def test_native_store_hook_has_no_legacy_projection():
    result = await BhutanRCDCPublicationsUpdater().import_rows(None, [])
    assert result.inserted_or_updated == 0
