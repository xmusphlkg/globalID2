from __future__ import annotations

import json
from pathlib import Path

from src.services.situation_events import (
    load_disease_catalogue,
    map_event,
    normalize_event,
    normalize_cdc_respiratory_series,
    parse_africa_cdc_ebs,
    parse_cdc_respiratory,
    parse_ecdc_cdtr,
    parse_paho_alerts,
    parse_who_don,
    source_adapter_registry,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "situation"


def test_who_official_api_contract_keeps_metadata_not_body() -> None:
    records = parse_who_don(json.loads((FIXTURES / "who_don.json").read_text()))

    assert len(records) == 1
    assert records[0]["external_id"] == "DON-999"
    assert records[0]["published_at"] == "2026-08-10"
    assert records[0]["updated_at_source"] == "2026-08-11"
    assert records[0]["source_url"].startswith("https://www.who.int/")
    assert "Summary" not in records[0]


def test_official_html_adapters_are_separate_and_stable() -> None:
    ecdc = parse_ecdc_cdtr((FIXTURES / "ecdc.html").read_text())
    africa = parse_africa_cdc_ebs((FIXTURES / "africa_cdc.html").read_text())
    paho = parse_paho_alerts((FIXTURES / "paho.html").read_text())

    assert ecdc[0]["source"] == "ecdc_cdtr"
    assert africa[0]["source"] == "africa_cdc_ebs"
    assert paho[0]["source"] == "paho_alerts"


def test_exact_disease_and_geography_mapping_auto_publishes() -> None:
    catalogue = load_disease_catalogue()
    disease, places = map_event("Ebola disease - Democratic Republic of the Congo", catalogue)
    normalized = normalize_event(
        {
            "source": "who_don",
            "external_id": "DON-999",
            "source_url": "https://www.who.int/example",
            "title": "Ebola disease - Democratic Republic of the Congo",
            "published_at": "2026-08-10",
        },
        catalogue,
    )

    assert disease and disease["disease_id"] == "D050"
    assert [place["code"] for place in places] == ["CD"]
    assert normalized["status"] == "published"
    assert normalized["confidence"] == "high"


def test_ambiguous_event_stays_internal_candidate() -> None:
    normalized = normalize_event(
        {
            "source": "paho_alerts",
            "external_id": "alert-1",
            "source_url": "https://example.test/alert-1",
            "title": "Regional epidemiological alert",
            "published_at": "2026-08-10",
        },
        load_disease_catalogue(),
    )

    assert normalized["status"] == "candidate"
    assert normalized["event_key"] is None


def test_avian_influenza_subtype_does_not_map_to_seasonal_influenza() -> None:
    catalogue = load_disease_catalogue()
    known, _ = map_event("Avian Influenza A(H9N2) - Italy", catalogue)
    unknown, _ = map_event("Avian influenza A(H3N8) - Country", catalogue)

    assert known and known["disease_id"] == "D016"
    assert unknown is None


def test_cdc_metrics_remain_separate() -> None:
    cards = parse_cdc_respiratory(
        [
            {"week_end": "2026-08-01", "pathogen": "Influenza", "percent_test_positivity": "0.4"},
            {"week_end": "2026-08-01", "pathogen": "RSV", "percent_test_positivity": "0.5"},
            {"week_end": "2026-08-01", "pathogen": "COVID-19", "percent_test_positivity": "3.1"},
        ],
        [{"weekendingdate": "2026-08-01", "jurisdiction": "CA", "totalconfflunewadm": "10", "totalconfrsvnewadm": "5", "totalconfc19newadm": "20"}],
        [{"week_end": "2026-08-01", "geography": "California", "label": "Low"}],
    )

    assert [card["disease_name"] for card in cards] == ["Influenza", "Respiratory syncytial virus infection (RSV)", "COVID-19"]
    assert [metric["metric_type"] for metric in cards[0]["metrics"]] == ["test_positivity", "hospitalized_case_notifications", "acute_respiratory_illness_activity"]
    assert cards[0]["metrics"][2]["analysis_role"] == "context_only_not_pathogen_specific"


def test_cdc_history_normalizes_to_separate_exact_series() -> None:
    frame = normalize_cdc_respiratory_series(
        [
            {"week_end": "2026-08-01", "pathogen": "Influenza", "percent_test_positivity": "0.4"},
            {"week_end": "2026-08-01", "pathogen": "RSV", "percent_test_positivity": "0.5"},
            {"week_end": "2026-08-01", "pathogen": "COVID-19", "percent_test_positivity": "3.1"},
        ],
        [{"weekendingdate": "2026-08-01", "jurisdiction": "USA", "totalconfflunewadm": "10", "totalconfrsvnewadm": "5", "totalconfc19newadm": "20"}],
    )

    assert len(frame) == 6
    assert frame["series_code"].nunique() == 6
    assert set(frame["metric_type"]) == {"test_positivity", "hospitalized_case_notifications"}
    assert set(frame["source_system"]) == {"SRC_US_CDC_RESP_NREVSS", "SRC_US_CDC_RESP_NHSN"}


def test_control_plane_adapter_registry_exposes_all_contracts() -> None:
    adapters = source_adapter_registry()

    assert [row["source_id"] for row in adapters] == [
        "who_don",
        "ecdc_cdtr",
        "africa_cdc_ebs",
        "paho_alerts",
        "cdc_positivity",
        "cdc_hospital_admissions",
        "cdc_ari_activity",
    ]
    assert all(row["url"].startswith("https://") for row in adapters)
    assert all(row["contract"] for row in adapters)
    assert all(row["stale_policy_hours"] == 72 for row in adapters)
