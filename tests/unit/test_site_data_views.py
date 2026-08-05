from __future__ import annotations

from scripts import generate_site_data as legacy_api
from src.generation import site_data_views as views
from src.generation.site_series_projection import SERIES_DATA_LAYER


def _record(date: str, cases: int, deaths: int = 0) -> dict:
    return {
        "disease_id": "D_TEST",
        "date": date,
        "year_month": date[:7],
        "cases": cases,
        "deaths": deaths,
        "incidence_rate": float(cases),
        "incidence_rate_source": "test_population",
        "mortality_rate": None,
        "_series_context": {
            "data_layer": SERIES_DATA_LAYER,
            "projection_policy": "single_series",
            "selected_series_codes": ["SER_TEST"],
            "available_series_count": 1,
            "source_series": [
                {
                    "series_code": "SER_TEST",
                    "dates": ["omitted"],
                    "values": [999],
                    "quality_statuses": ["validated"],
                    "metric_type": "case_notifications",
                }
            ],
            "metric_layers": {"cases": SERIES_DATA_LAYER},
        },
    }


def test_generate_script_reexports_site_view_builders() -> None:
    """Historical imports remain stable after extracting the view builders."""

    exported_names = (
        "_compact_source_series_metadata",
        "_country_series_data_layer_summary",
        "_data_layer_summary",
        "_series_context_for_records",
        "_series_provenance_fields",
        "resolve_country_display_names",
        "avg_or_none",
        "dominant_value",
        "calculate_weekly_equivalent",
        "build_country_data",
        "build_country_site_data",
        "build_disease_data",
        "build_disease_site_data",
    )
    for name in exported_names:
        assert getattr(legacy_api, name) is getattr(views, name)


def test_country_builders_preserve_schema_sorting_and_compaction() -> None:
    records = [
        _record("2024-02-01", 20, 2),
        _record("2024-01-01", 10, 1),
    ]
    country = views.build_country_data(
        "XX",
        "Testland",
        records,
        {
            "D_TEST": {
                "name_en": "Test disease",
                "name_zh": "测试疾病",
                "category": "Test",
                "slug": "test-disease",
            }
        },
    )

    series = country["disease_series"]["D_TEST"]
    assert series["dates"] == ["2024-01-01", "2024-02-01"]
    assert series["cases"] == [10, 20]
    assert series["data_layer"] == SERIES_DATA_LAYER
    assert country["date_range"] == {
        "start": "2024-01-01",
        "end": "2024-02-01",
    }
    assert country["frequency_meta"]["canonical_frequency"] == (
        "WEEKLY_EQUIVALENT_7D"
    )
    assert country["data_layer_summary"] == {
        "series_registry_disease_count": 1,
        "mixed_disease_count": 0,
        "legacy_fallback_disease_count": 0,
        "loss_risk_disease_count": 0,
        "loss_risk_disease_ids": [],
        "non_additive_series_disease_ids": [],
    }

    compact = views.build_country_site_data(country)
    assert compact["v"] == 1
    assert compact["dates"] == ["2024-01-01", "2024-02-01"]
    assert compact["series"][0]["x"] == [0, 1]
    assert compact["series"][0]["c"] == [10, 20]
    assert compact["series"][0]["source_series"] == [
        {
            "series_code": "SER_TEST",
            "metric_type": "case_notifications",
        }
    ]


def test_disease_builders_preserve_country_order_and_sorted_summary_codes() -> None:
    disease = views.build_disease_data(
        "D_TEST",
        {
            "disease_id": "D_TEST",
            "name_en": "Test disease",
            "name_zh": "测试疾病",
            "category": "Test",
        },
        {
            "ZZ": [_record("2024-02-01", 2)],
            "AA": [_record("2024-01-01", 1)],
        },
    )

    assert list(disease["country_series"]) == ["ZZ", "AA"]
    assert disease["global_monthly"] == {
        "months": ["2024-01", "2024-02"],
        "cases": [1, 2],
        "deaths": [0, 0],
    }
    assert disease["data_layer_summary"]["series_registry_country_codes"] == [
        "AA",
        "ZZ",
    ]

    compact = views.build_disease_site_data(
        disease,
        {"ZZ": "Zed", "AA": "Alpha"},
        {"ZZ": "泽德", "AA": "阿尔法"},
    )
    assert [entry["cc"] for entry in compact["series"]] == ["ZZ", "AA"]
    assert compact["dates"] == ["2024-01-01", "2024-02-01"]
    assert compact["monthly"] == disease["global_monthly"]
