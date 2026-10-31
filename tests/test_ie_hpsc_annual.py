from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import requests

from src.data.crawlers.ie import IEContractError
from src.data.crawlers.ie_annual import (
    ANNUAL_REPORTS, DEFAULT_ANNUAL_INDEX_URL, IEAnnualReportSpec,
    IrelandHPSCAnnualCrawler, parse_annual_pdf,
)
from src.data.storage.series_observation_store import SeriesObservationStore


class _FakePage:
    def extract_tables(self):
        return [
            [
                ["Disease", "2004", "2005"],
                ["Measles", "0", "12*"],
                ["Carbapenem-resistant Enterobacteriaceae infection (invasive)", "NA", "3"],
            ]
        ]


class _FakeDocument:
    pages = [_FakePage()]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _catalogue(extra="", host="file.hpsc.ie"):
    return "".join(
        f'<a href="https://{host}/{report.report_id}.pdf">'
        'Annual Infectious Disease Notifications in Ireland '
        f'{report.report_id.rsplit("_", 2)[-2]} - {report.report_id.rsplit("_", 2)[-1]}</a>'
        for report in ANNUAL_REPORTS
    ) + extra


def _response(content):
    response = requests.Response()
    response.status_code = 200
    response.url = DEFAULT_ANNUAL_INDEX_URL
    response._content = content.encode() if isinstance(content, str) else content
    return response


def test_annual_catalogue_discovers_migrated_urls_without_expanding_years(monkeypatch):
    crawler = IrelandHPSCAnnualCrawler()
    monkeypatch.setattr(crawler, "get", lambda url: _response(_catalogue()))
    reports = crawler.discover_reports()
    assert reports[-1].url == "https://file.hpsc.ie/hpsc_annual_2020_2025.pdf"
    assert reports[-1].years == (2020,)
    assert {year for report in reports for year in report.years} == set(range(2004, 2021))


@pytest.mark.parametrize("html", [
    "<html>Temporarily unavailable</html>",
    _catalogue(host="evil.example"),
    _catalogue('<a href="https://file.hpsc.ie/duplicate.pdf">Annual Infectious Disease Notifications in Ireland 2020-2025</a>'),
])
def test_annual_catalogue_rejects_missing_ambiguous_or_foreign_reports(monkeypatch, html):
    crawler = IrelandHPSCAnnualCrawler()
    monkeypatch.setattr(crawler, "get", lambda url: _response(html))
    with pytest.raises(IEContractError):
        crawler.discover_reports()


def test_annual_report_rejects_html_success_response(monkeypatch):
    crawler = IrelandHPSCAnnualCrawler()
    monkeypatch.setattr(crawler, "get", lambda url: _response("<html>Not found</html>"))
    with pytest.raises(IEContractError, match="not a PDF"):
        crawler.fetch_report(ANNUAL_REPORTS[-1])


def test_annual_replay_keeps_original_pdf_only_capture():
    crawler = IrelandHPSCAnnualCrawler()
    crawler.replay_artifacts = ()
    assert crawler.discover_reports() == ANNUAL_REPORTS


def test_annual_pdf_parser_preserves_zero_and_na(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.data.crawlers.ie_annual.pdfplumber.open",
        lambda *_args, **_kwargs: _FakeDocument(),
    )
    rows = parse_annual_pdf(
        b"%PDF-fake",
        report=IEAnnualReportSpec("fixture", "https://example.test/report.pdf", (2004, 2005)),
        retrieved_at="2026-08-10T00:00:00+00:00",
    )

    values = {
        (row["RawDiseaseLabel"], row["Year"]): (row["Cases"], row["ValueStatus"])
        for row in rows
    }
    assert values[("Measles", "2004")] == ("0", "zero")
    assert values[("Measles", "2005")] == ("12", "reported")
    assert values[("Carbapenem-resistant Enterobacteriaceae infection (invasive)", "2004")] == (
        "",
        "not_applicable",
    )


def test_annual_registry_covers_live_observed_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    ontology = json.loads(
        (root / "configs/disease_ontology.json").read_text(encoding="utf-8")
    )
    annual_series = [
        item
        for item in ontology["source_series"]
        if item["source_id"] == "SRC_IE_HPSC_ANNUAL"
    ]
    annual_availability = [
        item
        for item in ontology["availability"]
        if item["source_id"] == "SRC_IE_HPSC_ANNUAL"
    ]

    assert len(annual_series) == 82
    assert len(annual_availability) == 82
    assert {item["frequency"] for item in annual_series} == {"annual"}
    assert {item["status"] for item in annual_series} == {"historical"}
    assert {item["valid_from"] for item in annual_series} == {"2004-01-01"}
    assert {item["valid_to"] for item in annual_series} == {"2020-12-31"}


def test_live_annual_extract_is_fully_resolved_when_available() -> None:
    """Exercise a prior live extract if the explicit network test produced it."""

    path = Path("/tmp/ie_hpsc_annual_full.csv")
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selection = SeriesObservationStore().select_registry_rows(
        rows,
        "IE",
        source_id="SRC_IE_HPSC_ANNUAL",
    )
    assert selection.skipped_unregistered == 0
    assert len(selection.rows) + selection.skipped_missing == len(rows)
