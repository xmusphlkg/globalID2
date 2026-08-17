from __future__ import annotations

import pytest

from src.generation.site_series_projection import (
    apply_disease_cutover_projection as extracted_cutover_projection,
    apply_series_first_projection as extracted_series_first_projection,
    validate_series_first_projection as extracted_projection_validator,
)
from scripts.generate_site_data import (
    LEGACY_GAP_FILL_DATA_LAYER,
    LEGACY_DATA_LAYER,
    MIXED_DATA_LAYER,
    SERIES_DATA_LAYER,
    apply_disease_cutover_projection,
    apply_series_first_projection,
    build_country_data,
    build_country_site_data,
    build_disease_data,
    build_disease_site_data,
    validate_series_first_projection,
)


def test_generate_script_reexports_extracted_projection_api() -> None:
    """Keep historical script imports stable after extracting pure logic."""

    assert apply_series_first_projection is extracted_series_first_projection
    assert apply_disease_cutover_projection is extracted_cutover_projection
    assert validate_series_first_projection is extracted_projection_validator


def _legacy(
    disease_id: str,
    date: str,
    cases: int,
    *,
    deaths: int = 0,
    temporal_granularity: str | None = None,
    metric_type: str | None = None,
    reporting_basis: str | None = None,
) -> dict:
    record = {
        "disease_id": disease_id,
        "date": date,
        "year_month": date[:7],
        "cases": cases,
        "deaths": deaths,
        "recoveries": 0,
        "incidence_rate": None,
        "incidence_rate_source": "missing_population",
        "mortality_rate": None,
        "data_quality": "validated",
    }
    if temporal_granularity is not None:
        record["temporal_granularity"] = temporal_granularity
    if metric_type is not None:
        record["metric_type"] = metric_type
    if reporting_basis is not None:
        record["reporting_basis"] = reporting_basis
    return record


def _series(
    disease_id: str,
    series_code: str,
    date: str,
    cases: int,
    *,
    aggregation_policy: str = "non_additive",
    metric_type: str = "case_notifications",
    incidence_rate: float | None = None,
    temporal_granularity: str = "monthly",
    reporting_basis: str = "notification",
    time_basis: str = "report_date",
    series_is_active: bool | None = None,
    mapping_relation: str = "exact",
) -> dict:
    record = {
        "disease_id": disease_id,
        "date": date,
        "year_month": date[:7],
        "cases": cases,
        "deaths": 0,
        "recoveries": 0,
        "incidence_rate": incidence_rate,
        "incidence_rate_source": (
            "wpp_computed" if incidence_rate is not None else "missing_population"
        ),
        "mortality_rate": None,
        "data_quality": "validated",
        "quality_status": "validated",
        "series_code": series_code,
        "source_system": "SRC_TEST",
        "source_series_code": series_code.lower(),
        "source_label": f"Label {series_code}",
        "definition_version": "1",
        "case_definition": f"Definition {series_code}",
        "case_definition_uri": None,
        "metric_type": metric_type,
        "reporting_basis": reporting_basis,
        "time_basis": time_basis,
        "temporal_granularity": temporal_granularity,
        "series_unit": "count",
        "mapping_relation": mapping_relation,
        "comparability": "direct",
        "aggregation_policy": aggregation_policy,
        "availability_status": "active",
        "missing_value_policy": "missing_is_unknown",
        "valid_from": None,
        "valid_to": None,
        "geography_key": "country:XX:national",
        "dimension_key": "all",
    }
    if series_is_active is not None:
        record["series_is_active"] = series_is_active
    return record


def _by_disease(records: list[dict], disease_id: str) -> list[dict]:
    return [record for record in records if record["disease_id"] == disease_id]


def test_source_series_metadata_retains_time_basis_and_provisional_tail() -> None:
    rows = [
        _series(
            "D001",
            "SER_ONE",
            "2024-01-01",
            4,
            temporal_granularity="weekly",
            time_basis="symptom_onset_date",
        ),
        _series(
            "D001",
            "SER_ONE",
            "2024-01-08",
            5,
            temporal_granularity="weekly",
            time_basis="symptom_onset_date",
        ),
    ]
    rows[1]["quality_status"] = "provisional"
    rows[1]["data_quality"] = "provisional"

    projected = apply_series_first_projection([], rows)
    source = projected[0]["_series_context"]["source_series"][0]

    assert source["time_basis"] == "symptom_onset_date"
    assert source["point_quality_statuses"] == ["validated", "provisional"]
    assert source["provisional_from"] == "2024-01-08"
    assert source["latest_quality_status"] == "provisional"


def test_raw_history_does_not_expand_a_provisional_tail() -> None:
    rows = [
        _series("D001", "SER_ONE", "2024-01-01", 4),
        _series("D001", "SER_ONE", "2024-02-01", 5),
        _series("D001", "SER_ONE", "2024-03-01", 6),
    ]
    for row in rows[:2]:
        row["quality_status"] = "raw"
        row["data_quality"] = "raw"
    rows[2]["quality_status"] = "provisional"
    rows[2]["data_quality"] = "provisional"

    projected = apply_series_first_projection([], rows)
    source = projected[0]["_series_context"]["source_series"][0]

    assert source["point_quality_statuses"] == ["raw", "raw", "provisional"]
    assert source["provisional_from"] == "2024-03-01"


def test_all_raw_series_has_no_provisional_boundary() -> None:
    rows = [
        _series("D001", "SER_ONE", "2024-01-01", 4),
        _series("D001", "SER_ONE", "2024-02-01", 5),
    ]
    for row in rows:
        row["quality_status"] = "raw"
        row["data_quality"] = "raw"

    projected = apply_series_first_projection([], rows)
    source = projected[0]["_series_context"]["source_series"][0]

    assert source["provisional_from"] is None


def test_partial_registry_history_replaces_overlap_and_gap_fills_legacy() -> None:
    legacy = [
        _legacy("D001", "2024-01-01", 900, deaths=2),
        _legacy("D001", "2024-02-01", 901, deaths=3),
        _legacy("D002", "2024-01-01", 8, deaths=1),
    ]
    series = [_series("D001", "SER_ONE", "2024-02-01", 4)]

    projected = apply_series_first_projection(legacy, series)

    d001 = _by_disease(projected, "D001")
    assert [(row["date"], row["cases"]) for row in d001] == [
        ("2024-01-01", 900),
        ("2024-02-01", 4),
    ]
    assert d001[0]["deaths"] == 2
    assert d001[1]["deaths"] == 3
    assert d001[0]["data_layer"] == LEGACY_GAP_FILL_DATA_LAYER
    assert d001[1]["data_layer"] == SERIES_DATA_LAYER
    context = d001[0]["_series_context"]
    assert context["data_layer"] == MIXED_DATA_LAYER
    assert context["coverage_status"] == "legacy_gap_fill"
    assert context["coverage_ratio_against_legacy"] == 0.5
    assert context["legacy_gap_fill_count"] == 1
    assert context["metric_layers"] == {
        "cases": MIXED_DATA_LAYER,
        "deaths": LEGACY_DATA_LAYER,
        "recoveries": LEGACY_DATA_LAYER,
    }

    d002 = _by_disease(projected, "D002")
    assert d002[0]["cases"] == 8
    assert d002[0]["data_layer"] == LEGACY_DATA_LAYER
    assert d002[0]["_series_context"]["loss_risk"] == (
        "legacy_identity_may_be_lossy"
    )


def test_registry_tail_lag_preserves_newer_legacy_period() -> None:
    """A registry backfill may cover history but lag the live legacy feed."""

    legacy = [
        _legacy("D068", "2023-12-31", 10),
        _legacy("D068", "2024-01-07", 11),
        _legacy("D068", "2024-01-14", 12),
    ]
    series = [
        _series("D068", "SER_US_D068", "2023-12-31", 100, temporal_granularity="weekly"),
        _series("D068", "SER_US_D068", "2024-01-07", 101, temporal_granularity="weekly"),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"], row["data_layer"]) for row in projected] == [
        ("2023-12-31", 100, SERIES_DATA_LAYER),
        ("2024-01-07", 101, SERIES_DATA_LAYER),
        ("2024-01-14", 12, LEGACY_GAP_FILL_DATA_LAYER),
    ]
    assert projected[0]["_series_context"]["registry_only_period_count"] == 0


def test_us_d068_registry_late_start_preserves_earlier_history() -> None:
    legacy = [
        _legacy("D068", "2018-01-07", 8),
        _legacy("D068", "2023-01-01", 9),
        _legacy("D068", "2023-01-08", 10),
    ]
    series = [
        _series("D068", "SER_US_D068", "2023-01-01", 90, temporal_granularity="weekly"),
        _series("D068", "SER_US_D068", "2023-01-08", 100, temporal_granularity="weekly"),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2018-01-07", 8),
        ("2023-01-01", 90),
        ("2023-01-08", 100),
    ]
    context = projected[0]["_series_context"]
    assert context["coverage_status"] == "legacy_gap_fill"
    assert context["legacy_gap_fill_count"] == 1


def test_weekly_registry_hole_keeps_same_jp_legacy_week() -> None:
    legacy = [
        _legacy("D105", "2026-01-05", 1),
        _legacy("D105", "2026-01-12", 2),
        _legacy("D105", "2026-01-19", 3),
    ]
    series = [
        _series("D105", "SER_JP_D105", "2026-01-05", 10, temporal_granularity="weekly"),
        _series("D105", "SER_JP_D105", "2026-01-19", 30, temporal_granularity="weekly"),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [row["cases"] for row in projected] == [10, 2, 30]
    assert projected[1]["gap_fill_reason"] == "registry_period_missing"
    assert projected[1]["data_layer"] == LEGACY_GAP_FILL_DATA_LAYER


def test_weekly_overlay_aligns_shifted_jp_report_dates_without_double_counting() -> None:
    legacy = [_legacy("D227", "2026-01-10", 9, deaths=2)]
    series = [
        _series(
            "D227",
            "SER_JP_CRE_WEEKLY",
            "2026-01-11",
            9,
            temporal_granularity="weekly",
        )
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2026-01-11", 9)
    ]
    assert projected[0]["deaths"] == 2
    context = projected[0]["_series_context"]
    assert context["period_granularity"] == "weekly"
    assert context["coverage_status"] == "parity"
    assert context["legacy_gap_fill_count"] == 0


def test_sum_disjoint_does_not_treat_missing_component_as_zero() -> None:
    legacy = [
        _legacy("D001", "2024-01-01", 90),
        _legacy("D001", "2024-02-01", 91),
    ]
    series = [
        _series(
            "D001", "SER_CHILD", "2024-01-01", 5,
            aggregation_policy="sum_disjoint",
        ),
        _series(
            "D001", "SER_ADULT", "2024-01-01", 7,
            aggregation_policy="sum_disjoint",
        ),
        _series(
            "D001", "SER_CHILD", "2024-02-01", 6,
            aggregation_policy="sum_disjoint",
        ),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2024-01-01", 12),
        ("2024-02-01", 91),
    ]
    assert projected[1]["data_layer"] == LEGACY_GAP_FILL_DATA_LAYER


def test_non_additive_series_are_exposed_but_never_silently_summed() -> None:
    legacy = [_legacy("D001", "2024-01-01", 999, deaths=7)]
    series = [
        _series("D001", "SER_A", "2024-01-01", 5),
        _series("D001", "SER_A", "2024-02-01", 6),
        _series("D001", "SER_B", "2024-01-01", 100),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [row["cases"] for row in projected] == [5, 6]
    assert [row["deaths"] for row in projected] == [None, None]
    context = projected[0]["_series_context"]
    assert context["projection_policy"] == "representative_series"
    assert context["selected_series_codes"] == ["SER_A"]
    assert context["loss_risk"] == "non_additive_series_not_rolled_up"
    assert {item["series_code"] for item in context["source_series"]} == {
        "SER_A",
        "SER_B",
    }
    assert {item["total_value"] for item in context["source_series"]} == {11, 100}
    assert context["supplemental_legacy_metrics"]["deaths"] == [7]
    assert context["metric_layers"]["deaths"] == "supplemental_legacy_only"


def test_multi_series_sum_requires_explicit_disjoint_policy() -> None:
    series = [
        _series(
            "D001",
            "SER_CHILD",
            "2024-01-01",
            5,
            aggregation_policy="sum_disjoint",
            incidence_rate=0.5,
        ),
        _series(
            "D001",
            "SER_ADULT",
            "2024-01-01",
            7,
            aggregation_policy="sum_disjoint",
            incidence_rate=0.7,
        ),
    ]

    projected = apply_series_first_projection([], series)

    assert len(projected) == 1
    assert projected[0]["cases"] == 12
    assert projected[0]["incidence_rate"] == 1.2
    context = projected[0]["_series_context"]
    assert context["projection_policy"] == "sum_disjoint"
    assert context["selected_series_codes"] == ["SER_ADULT", "SER_CHILD"]
    assert context["loss_risk"] is None


def test_reported_aggregate_is_preferred_over_non_additive_components() -> None:
    series = [
        _series(
            "D001",
            "SER_REPORTED_TOTAL",
            "2024-01-01",
            20,
            aggregation_policy="reported_aggregate",
        ),
        _series("D001", "SER_COMPONENT", "2024-01-01", 9),
        _series("D001", "SER_COMPONENT", "2024-02-01", 10),
    ]

    projected = apply_series_first_projection([], series)

    assert [row["cases"] for row in projected] == [20]
    context = projected[0]["_series_context"]
    assert context["projection_policy"] == "reported_aggregate_preferred"
    assert context["selected_series_codes"] == ["SER_REPORTED_TOTAL"]


def test_multiple_narrower_non_additive_series_never_use_legacy_fallback() -> None:
    legacy = [_legacy("D001", "2024-01-01", 999)]
    series = [
        _series(
            "D001",
            "SER_NARROW_A",
            "2024-01-01",
            5,
            mapping_relation="narrower",
        ),
        _series(
            "D001",
            "SER_NARROW_B",
            "2024-01-01",
            100,
            mapping_relation="narrower",
        ),
    ]

    assert apply_series_first_projection(legacy, series) == []


def test_single_narrower_series_can_fill_cases() -> None:
    projected = apply_series_first_projection(
        [],
        [
            _series(
                "D001",
                "SER_NARROW",
                "2024-01-01",
                5,
                mapping_relation="narrower",
            )
        ],
    )

    assert [row["cases"] for row in projected] == [5]
    assert projected[0]["_series_context"]["projection_policy"] == "single_series"


def test_unsafe_case_mapping_never_uses_legacy_fallback() -> None:
    projected = apply_series_first_projection(
        [_legacy("D001", "2024-01-01", 999)],
        [
            _series(
                "D001",
                "SER_RELATED",
                "2024-01-01",
                5,
                mapping_relation="related",
            )
        ],
    )

    assert projected == []


def test_exact_series_beats_newer_narrower_series() -> None:
    projected = apply_series_first_projection(
        [],
        [
            _series("D001", "SER_EXACT", "2020-01-01", 5),
            _series(
                "D001",
                "SER_NARROW",
                "2026-01-01",
                100,
                mapping_relation="narrower",
            ),
        ],
    )

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2020-01-01", 5)
    ]
    assert projected[0]["_series_context"]["selected_series_codes"] == [
        "SER_EXACT"
    ]


def test_incompatible_registered_metric_does_not_replace_legacy_cases() -> None:
    legacy = [_legacy("D001", "2024-01-01", 12)]
    death_series = [
        _series(
            "D001",
            "SER_DEATHS",
            "2024-01-01",
            4,
            metric_type="deaths",
        )
    ]

    projected = apply_series_first_projection(legacy, death_series)

    assert projected[0]["cases"] == 12
    context = projected[0]["_series_context"]
    assert context["data_layer"] == LEGACY_DATA_LAYER
    assert context["fallback_reason"] == (
        "registered_facts_not_case_count_compatible"
    )
    assert context["available_series_count"] == 1
    assert context["source_series"][0]["metric_type"] == "deaths"


def test_non_case_indicators_are_retained_but_never_added_to_cases() -> None:
    series = [
        _series("D001", "SER_CASES", "2024-01-01", 12),
        _series(
            "D001",
            "SER_HOSPITAL", "2024-01-01", 3,
            metric_type="hospitalizations",
        ),
    ]

    projected = apply_series_first_projection([], series)

    assert [row["cases"] for row in projected] == [12]
    context = projected[0]["_series_context"]
    assert context["selected_series_codes"] == ["SER_CASES"]
    assert context["available_series_count"] == 2
    assert context["non_projection_series_codes"] == ["SER_HOSPITAL"]
    assert {item["metric_type"] for item in context["source_series"]} == {
        "case_notifications",
        "hospitalizations",
    }


def test_iceland_d033_prefers_current_monthly_series_and_blocks_basis_gap_fill() -> None:
    legacy = [
        _legacy(
            "D033",
            "2000-01-01",
            99,
            temporal_granularity="monthly",
            metric_type="case_notifications",
            reporting_basis="national_registry_notifications",
        )
    ]
    series = [
        _series(
            "D033",
            "SER_IS_HISTORY_GONORRHEA_MONTHLY",
            "2020-01-01",
            8,
            reporting_basis="national_registry_notifications",
            series_is_active=False,
        ),
        _series(
            "D033",
            "SER_IS_HISTORY_GONORRHEA_MONTHLY",
            "2021-01-01",
            9,
            reporting_basis="national_registry_notifications",
            series_is_active=False,
        ),
        _series(
            "D033",
            "SER_IS_DOH_ANNUAL_GONORRHOEA",
            "2025-01-01",
            25,
            temporal_granularity="annual",
            reporting_basis="registry_and_laboratory_surveillance",
            series_is_active=True,
        ),
        _series(
            "D033",
            "SER_IS_DOH_STI_GONORRHOEA_MONTHLY",
            "2026-06-01",
            6,
            reporting_basis="laboratory_and_registry_diagnoses",
            series_is_active=True,
        ),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2026-06-01", 6)
    ]
    context = projected[0]["_series_context"]
    assert context["selected_series_codes"] == [
        "SER_IS_DOH_STI_GONORRHOEA_MONTHLY"
    ]
    assert context["legacy_gap_fill_count"] == 0
    assert context["legacy_gap_fill_blocked_count"] == 1
    assert context["coverage_status"] == "legacy_gap_fill_blocked_incompatible"
    assert context["legacy_incompatibility_reasons"] == [
        "reporting_basis_mismatch"
    ]


def test_iceland_d028_keeps_weekly_curve_separate_from_monthly_legacy_basis() -> None:
    legacy = [
        _legacy(
            "D028",
            "2021-01-01",
            77,
            temporal_granularity="monthly",
            metric_type="case_notifications",
            reporting_basis="national_registry_notifications",
        )
    ]
    series = [
        _series(
            "D028",
            "SER_IS_HISTORY_PERTUSSIS_MONTHLY",
            "2020-01-01",
            4,
            reporting_basis="national_registry_notifications",
            series_is_active=False,
        ),
        _series(
            "D028",
            "SER_IS_HISTORY_PERTUSSIS_MONTHLY",
            "2021-01-01",
            5,
            reporting_basis="national_registry_notifications",
            series_is_active=False,
        ),
        _series(
            "D028",
            "SER_IS_DOH_RESPIRATORY_PERTUSSIS_WEEKLY",
            "2026-05-04",
            3,
            temporal_granularity="weekly",
            reporting_basis="registry_and_laboratory_diagnoses",
            series_is_active=True,
        ),
        _series(
            "D028",
            "SER_IS_LEGACY_ICD_A37_KIKHOSTI_MONTHLY",
            "2020-01-01",
            12,
            metric_type="registered_diagnoses",
            reporting_basis="primary_care_icd_registered_diagnoses",
            series_is_active=False,
        ),
    ]

    projected = apply_series_first_projection(legacy, series)

    assert [(row["date"], row["cases"]) for row in projected] == [
        ("2026-05-04", 3)
    ]
    context = projected[0]["_series_context"]
    assert context["selected_series_codes"] == [
        "SER_IS_DOH_RESPIRATORY_PERTUSSIS_WEEKLY"
    ]
    assert context["legacy_gap_fill_blocked_count"] == 1
    assert context["legacy_incompatibility_reasons"] == [
        "reporting_basis_mismatch",
        "temporal_granularity_mismatch",
    ]
    assert "SER_IS_LEGACY_ICD_A37_KIKHOSTI_MONTHLY" in context[
        "non_projection_series_codes"
    ]


def test_builders_publish_layer_and_series_definition_metadata() -> None:
    records = apply_series_first_projection(
        [_legacy("D001", "2024-01-01", 999)],
        [
            _series("D001", "SER_A", "2024-01-01", 5),
            _series("D001", "SER_B", "2024-01-01", 100),
        ],
    )
    disease_catalogue = {
        "D001": {
            "name_en": "Example",
            "name_zh": "示例",
            "category": "Test",
            "slug": "example",
        }
    }

    country = build_country_data("XX", "Exampleland", records, disease_catalogue)
    country_series = country["disease_series"]["D001"]
    assert country_series["data_layer"] == SERIES_DATA_LAYER
    assert country_series["projection_policy"] == "representative_series"
    assert len(country_series["source_series"]) == 2
    assert country["data_layer_summary"]["non_additive_series_disease_ids"] == [
        "D001"
    ]

    country_site = build_country_site_data(country)
    compact = country_site["series"][0]
    assert compact["loss_risk"] == "non_additive_series_not_rolled_up"
    assert {item["series_code"] for item in compact["source_series"]} == {
        "SER_A",
        "SER_B",
    }
    assert all("values" not in item for item in compact["source_series"])

    disease = build_disease_data(
        "D001",
        {"disease_id": "D001", **disease_catalogue["D001"]},
        {"XX": records},
    )
    assert disease["country_series"]["XX"]["data_layer"] == SERIES_DATA_LAYER
    disease_site = build_disease_site_data(disease)
    assert disease_site["series"][0]["selected_series_codes"] == ["SER_A"]


def test_projection_validator_rejects_duplicate_flat_identity() -> None:
    projected = apply_series_first_projection(
        [], [_series("D001", "SER_A", "2024-01-01", 5)]
    )

    with pytest.raises(RuntimeError, match="duplicate disease/date"):
        validate_series_first_projection([projected[0], dict(projected[0])])


def test_projection_rejects_duplicate_series_at_public_date_grain() -> None:
    duplicate = _series("D001", "SER_A", "2024-01-01", 5)

    with pytest.raises(RuntimeError, match="public date grain"):
        apply_series_first_projection([], [duplicate, {**duplicate, "cases": 6}])


def test_us_hiv_canary_never_falls_back_to_legacy() -> None:
    legacy = [_legacy("D162", "2024-12-31", 999)]
    series = [
        _series(
            "D162",
            "SER_US_NHSS_HIV_ANNUAL",
            "2024-12-31",
            37,
        )
    ]

    projected = apply_disease_cutover_projection(
        legacy,
        series,
        country_code="US",
    )

    assert [(record["cases"], record["data_layer"]) for record in projected] == [
        (37, SERIES_DATA_LAYER)
    ]
    assert projected[0]["_series_context"]["cutover"]["read_mode"] == "series_only"

    assert (
        apply_disease_cutover_projection(legacy, [], country_code="US") == []
    )
