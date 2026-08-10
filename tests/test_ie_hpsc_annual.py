from __future__ import annotations

import csv
import json
from pathlib import Path

from src.data.crawlers.ie_annual import IEAnnualReportSpec, parse_annual_pdf
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
