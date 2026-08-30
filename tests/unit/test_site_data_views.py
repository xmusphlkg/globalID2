from __future__ import annotations

from scripts import generate_site_data as legacy_api
from src.generation import site_data_views as views
from src.generation.site_series_projection import SERIES_DATA_LAYER


def _record(
    date: str,
    cases: int,
    deaths: int = 0,
    *,
    disease_id: str = "D_TEST",
    granularity: str = "monthly",
) -> dict:
    return {
        "disease_id": disease_id,
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
            "period_granularity": granularity,
            "source_series": [
                {
                    "series_code": "SER_TEST",
                    "dates": ["omitted"],
                    "values": [999],
                    "quality_statuses": ["validated"],
                    "metric_type": "case_notifications",
                    "temporal_granularity": granularity,
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


def test_subdivision_display_name_prefers_chinese_database_name_over_code_fallback() -> None:
    assert views.resolve_country_display_names(
        "CN-SH",
        {
            "name_en": "Shanghai, China",
            "name_local": "上海市",
        },
    ) == ("Shanghai, China", "上海市")


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
        "SOURCE_REPORTED_PERIODS"
    )
    assert country["comparison_basis"]["metric"] == "cases"
    assert series["weekly_equiv_cases"] == []
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
            "temporal_granularity": "monthly",
        }
    ]


def test_country_curve_preserves_missing_counts_and_provisional_boundary() -> None:
    records = [
        {
            **_record("2024-01-01", 10),
            "data_quality": "validated",
            "time_basis": "report date",
            "comparability": "conditional",
        },
        {
            **_record("2024-02-01", 0),
            "cases": None,
            "deaths": None,
            "data_quality": "provisional",
            "time_basis": "report date",
            "comparability": "conditional",
        },
        {
            **_record("2024-03-01", 4),
            "data_quality": "provisional",
            "time_basis": "report date",
            "comparability": "conditional",
        },
    ]
    country = views.build_country_data(
        "XX",
        "Testland",
        records,
        {"D_TEST": {"name_en": "Test", "name_zh": "测试"}},
    )

    series = country["disease_series"]["D_TEST"]
    assert series["cases"] == [10, None, 4]
    assert series["deaths"] == [0, None, 0]
    assert series["total_cases"] == 14
    assert series["latest_cases"] == 4
    assert series["provisional_from"] == "2024-02-01"
    assert series["time_basis"] == "report date"
    compact_series = views.build_country_site_data(country)["series"][0]
    assert compact_series["pf"] == "2024-02-01"
    assert compact_series["tb"] == "report date"
    assert compact_series["cmp"] == "conditional"


def test_country_curve_does_not_treat_raw_as_provisional() -> None:
    records = [
        {**_record("2024-01-01", 10), "data_quality": "raw"},
        {**_record("2024-02-01", 11), "data_quality": "raw"},
        {**_record("2024-03-01", 12), "data_quality": "provisional"},
    ]

    country = views.build_country_data(
        "XX",
        "Testland",
        records,
        {"D_TEST": {"name_en": "Test", "name_zh": "测试"}},
    )

    assert country["disease_series"]["D_TEST"]["provisional_from"] == "2024-03-01"


def test_country_builder_preserves_annual_and_weekly_period_semantics() -> None:
    records = [
        _record("2023-01-01", 120, disease_id="D_ANNUAL", granularity="annual"),
        _record("2024-01-01", 140, disease_id="D_ANNUAL", granularity="annual"),
        _record("2024-01-07", 4, disease_id="D_WEEKLY", granularity="weekly"),
        _record("2024-01-14", 6, disease_id="D_WEEKLY", granularity="weekly"),
    ]
    diseases = {
        disease_id: {
            "name_en": disease_id,
            "name_zh": disease_id,
            "category": "Test",
            "slug": disease_id.lower(),
        }
        for disease_id in ("D_ANNUAL", "D_WEEKLY")
    }

    country = views.build_country_data("IS", "Iceland", records, diseases)

    assert country["disease_series"]["D_ANNUAL"]["weekly_equiv_cases"] == []
    assert country["disease_series"]["D_WEEKLY"]["weekly_equiv_cases"] == [
        4.0,
        6.0,
    ]
    assert country["heatmap"]["diseases"] == ["D_WEEKLY"]
    assert "2023-01" not in country["heatmap"]["months"]


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


def test_disease_global_summary_excludes_subdivision_double_counting() -> None:
    disease = views.build_disease_data(
        "D_TEST",
        {
            "disease_id": "D_TEST",
            "name_en": "Test disease",
            "name_zh": "测试疾病",
            "category": "Test",
        },
        {
            "CN": [_record("2024-01-01", 100, 10)],
            "CN-SH": [_record("2024-01-01", 40, 4)],
            "AA": [_record("2024-01-01", 5, 1)],
        },
    )

    assert list(disease["country_series"]) == ["CN", "CN-SH", "AA"]
    assert disease["total_cases"] == 105
    assert disease["total_deaths"] == 11
    assert disease["global_monthly"] == {
        "months": ["2024-01"],
        "cases": [105],
        "deaths": [11],
    }
    assert disease["aggregation_scope"] == "national_jurisdictions_only"
    assert disease["subdivision_country_codes"] == ["CN-SH"]
