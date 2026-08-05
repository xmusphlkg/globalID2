from __future__ import annotations

from scripts.generate_site_data import build_country_canonical_facts


def test_generator_builds_canonical_v2_facts_without_v1_display_fields() -> None:
    facts = build_country_canonical_facts(
        {
            "country_code": "us",
            "country_name": "United States",
            "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
            "disease_series": {
                "D162": {
                    "disease_id": "D162",
                    "name_en": "HIV infection",
                    "dates": ["2025-01-01"],
                    "cases": [7],
                    "weekly_equiv_cases": [7.0],
                    "deaths": [0],
                    "incidence_rates": [0.1],
                    "incidence_sources": ["wpp_computed"],
                    "mortality_rates": [None],
                    "selected_series_codes": ["SER_B", "SER_A", "SER_A"],
                    "data_layer": "series_registry",
                    "projection_policy": "representative_series",
                    "coverage_status": "parity",
                    "legacy_gap_fill_count": 0,
                    "coverage_ratio_against_legacy": 1.0,
                }
            },
        },
        {
            "primary_scope": "nhss",
            "sources": [
                {"scope": "nhss"},
                {"scope": "nndss_api"},
            ],
        },
    )

    assert facts == [
        {
            "country_code": "US",
            "disease_id": "D162",
            "date": "2025-01-01",
            "cases": 7,
            "weekly_equiv_cases": 7.0,
            "deaths": 0,
            "incidence_rate_per_100k": 0.1,
            "incidence_rate_source": "wpp_computed",
            "mortality_rate": None,
            "data_layer": "series_registry",
            "projection_policy": "representative_series",
            "series_codes": ["SER_A", "SER_B"],
            "loss_risk": None,
            "coverage_status": "parity",
            "legacy_gap_fill_count": 0,
            "coverage_ratio_against_legacy": 1.0,
            "primary_source_ref": "US:nhss",
            "source_refs": ["US:nhss", "US:nndss_api"],
        }
    ]
    forbidden = {
        "dataset_kind",
        "dataset_name",
        "country_name",
        "disease_name_en",
        "year_month",
        "source_urls",
        "generated_at",
    }
    assert forbidden.isdisjoint(facts[0])
