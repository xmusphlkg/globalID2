"""Historical HPA originals with moving panels and printed numeric labels."""
import hashlib
import json
from pathlib import Path

import pytest

from src.data.crawlers.mv import parse_report_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/MV"
MANIFEST = json.loads((FIXTURES / "historical_pdf_manifest.json").read_text())


def capture(period):
    item = next(item for item in MANIFEST if (item["report"]["year"], item["report"]["month"]) == period)
    return item, (FIXTURES / item["fixture"]).read_bytes()


@pytest.mark.parametrize("item", MANIFEST, ids=lambda item: item["fixture"])
def test_historical_fixtures_are_unmodified_captured_official_pdfs(item):
    payload = (FIXTURES / item["fixture"]).read_bytes()
    assert len(payload) == item["byte_size"]
    assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    assert item["source_url"] == item["report"]["pdf_url"]


def test_2022_horizontal_labels_belong_to_current_bars_not_historical_lines():
    item, payload = capture((2022, 12))
    rows, audit = parse_report_pdf(payload, item["report"])
    assert len(rows) == 48
    dengue = sorted((r for r in rows if r["RawDiseaseLabel"] == "Dengue Fever"), key=lambda r: int(r["Month"]))
    # Read directly from the labels above the green 2022 bars; the historical
    # black/blue/orange lines in this chart represent different years.
    assert [int(r["Cases"]) for r in dengue] == [22, 21, 26, 95, 212, 302, 361, 250, 269, 660, 282, 205]
    assert dengue[-1]["Date"] == "2022-12-31"
    assert audit["skipped_panels"] == []


def test_2023_early_layout_preserves_four_diseases_and_excludes_future_months():
    item, payload = capture((2023, 2))
    rows, audit = parse_report_pdf(payload, item["report"])
    assert len(rows) == 8
    expected = {"ARI": [29307, 30628], "Dengue Fever": [198, 145],
                "Viral Fever": [8219, 7738], "AGE": [3502, 4357]}
    for label, values in expected.items():
        selected = sorted((r for r in rows if r["RawDiseaseLabel"] == label), key=lambda r: r["Date"])
        assert [int(r["Cases"]) for r in selected] == values
    assert {r["Date"] for r in rows} == {"2023-01-31", "2023-02-28"}
    assert audit["skipped_panels"] == []


def test_2023_december_charts_move_without_changing_disease_identity():
    item, payload = capture((2023, 12))
    rows, audit = parse_report_pdf(payload, item["report"])
    assert len(rows) == 48
    december = {r["RawDiseaseLabel"]: int(r["Cases"]) for r in rows if r["Date"] == "2023-12-31"}
    assert december == {"ARI": 31225, "Dengue Fever": 194, "Viral Fever": 10452, "AGE": 3486}
    assert audit["skipped_panels"] == []


def test_2024_march_panel_reordering_does_not_swap_ari_and_dengue():
    item, payload = capture((2024, 3))
    rows, audit = parse_report_pdf(payload, item["report"])
    assert len(rows) == 12
    march = {r["RawDiseaseLabel"]: int(r["Cases"]) for r in rows if r["Date"] == "2024-03-31"}
    assert march == {"ARI": 33637, "Dengue Fever": 119, "AGE": 5428, "Viral Fever": 11760}
    assert audit["skipped_panels"] == []
    assert {r["DefinitionVersion"] for r in rows} == {"mv_hpa_monthly_2024_printed_bar_labels_v1"}


def test_image_only_disease_headings_are_not_inferred_from_panel_position():
    item, payload = capture((2024, 2))
    rows, audit = parse_report_pdf(payload, item["report"])
    assert len(rows) == 4
    assert {r["RawDiseaseLabel"] for r in rows} == {"AGE", "Viral Fever"}
    assert len(audit["skipped_panels"]) == 2
    assert all("heading is absent" in missing["error"] for missing in audit["skipped_panels"])


def test_overlapped_count_is_unknown_without_discarding_other_printed_months():
    item, payload = capture((2024, 4))
    rows, audit = parse_report_pdf(payload, item["report"])
    dengue = [(r["Month"], r["Cases"]) for r in rows if r["RawDiseaseLabel"] == "Dengue Fever"]
    assert dengue == [("1", "145"), ("2", "116"), ("4", "244")]
    assert audit["skipped_panels"] == [{"disease": "Dengue Fever", "month": 3,
                                         "error": "Month lacks one unambiguous directly attached printed count"}]
    assert len(rows) == 15
