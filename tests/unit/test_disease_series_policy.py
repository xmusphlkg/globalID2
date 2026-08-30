from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dashboard.api.services.disease_series_projection import (
    project_series_first_records,
)
from scripts.generate_site_data import apply_series_first_projection
from src.services.disease_series_policy import (
    SOURCE_OBSERVATIONS_ONLY_POLICY,
    is_case_count_series,
    select_series_projection,
)


def test_api_and_site_adapters_share_series_selection_policy() -> None:
    api_series = [
        {
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "series_code": "SER_A",
            "value": 5,
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": "case_notifications",
            "mapping_relation": "exact",
            "aggregation_policy": "non_additive",
            "quality_status": "validated",
        },
        {
            "time": datetime(2024, 2, 1, tzinfo=timezone.utc),
            "series_code": "SER_A",
            "value": 6,
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": "case_notifications",
            "mapping_relation": "exact",
            "aggregation_policy": "non_additive",
            "quality_status": "validated",
        },
        {
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "series_code": "SER_B",
            "value": 100,
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": "case_notifications",
            "mapping_relation": "exact",
            "aggregation_policy": "non_additive",
            "quality_status": "validated",
        },
    ]
    site_series = [
        {
            **record,
            "date": record["time"].date().isoformat(),
            "disease_id": "D001",
            "cases": record["value"],
        }
        for record in api_series
    ]

    api_records, api_metadata = project_series_first_records(
        [], api_series, disease_numeric_id=7, country_id=11
    )
    site_records = apply_series_first_projection([], site_series)
    site_metadata = site_records[0]["_series_context"]

    assert [record["cases"] for record in api_records] == [5, 6]
    assert [record["cases"] for record in site_records] == [5, 6]
    assert api_metadata["selected_series_codes"] == ["SER_A"]
    assert site_metadata["selected_series_codes"] == ["SER_A"]
    assert api_metadata["projection_policy"] == "representative_series"
    assert site_metadata["projection_policy"] == "representative_series"


@pytest.mark.parametrize(
    "mapping_relation",
    ["broader", "related", "aggregate", "ambiguous", "unmapped", None],
)
def test_unsafe_mapping_relations_cannot_fill_generic_cases(
    mapping_relation: str | None,
) -> None:
    assert not is_case_count_series(
        {
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": "case_notifications",
            "mapping_relation": mapping_relation,
        }
    )


def test_single_narrower_series_remains_projectable() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_NARROW",
                "mapping_relation": "narrower",
                "aggregation_policy": "non_additive",
            }
        ]
    )

    assert selection.selected_codes == frozenset({"SER_NARROW"})
    assert selection.projection_policy == "single_series"


@pytest.mark.parametrize(
    "metric_type",
    ["laboratory_diagnoses", "reported_diagnoses", "clinical_diagnoses"],
)
def test_typed_diagnosis_counts_can_fill_cases(metric_type: str) -> None:
    assert is_case_count_series(
        {
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": metric_type,
            "mapping_relation": "exact",
        }
    )


def test_multiple_narrower_non_additive_series_have_no_generic_rollup() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_NARROW_A",
                "mapping_relation": "narrower",
                "aggregation_policy": "non_additive",
            },
            {
                "series_code": "SER_NARROW_B",
                "mapping_relation": "narrower",
                "aggregation_policy": "non_additive",
            },
        ]
    )

    assert selection.selected_codes == frozenset()
    assert selection.projection_policy == SOURCE_OBSERVATIONS_ONLY_POLICY
    assert selection.loss_risk == (
        "narrower_non_additive_series_have_no_safe_rollup"
    )


def test_exact_series_is_preferred_to_narrower_representative() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_EXACT",
                "mapping_relation": "exact",
                "aggregation_policy": "non_additive",
                "coverage_end": "2020-01-01",
            },
            {
                "series_code": "SER_NARROW_CURRENT",
                "mapping_relation": "narrower",
                "aggregation_policy": "non_additive",
                "coverage_end": "2026-01-01",
            },
        ]
    )

    assert selection.selected_codes == frozenset({"SER_EXACT"})
    assert selection.projection_policy == "representative_series"


def test_equivalent_non_overlapping_series_form_temporal_handoff() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_HISTORY",
                "mapping_relation": "exact",
                "aggregation_policy": "non_additive",
                "metric_type": "case_notifications",
                "temporal_granularity": "weekly",
                "unit": "count",
                "valid_from": "2012-01-01",
                "valid_to": "2022-12-31",
            },
            {
                "series_code": "SER_CURRENT",
                "mapping_relation": "exact",
                "aggregation_policy": "non_additive",
                "metric_type": "case_notifications",
                "temporal_granularity": "weekly",
                "unit": "count",
                "valid_from": "2023-01-01",
                "coverage_end": "2026-08-16",
            },
        ]
    )

    assert selection.selected_codes == frozenset({"SER_HISTORY", "SER_CURRENT"})
    assert selection.projection_policy == "temporal_handoff"
    assert selection.loss_risk is None


def test_overlapping_series_do_not_form_temporal_handoff() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_A",
                "mapping_relation": "exact",
                "aggregation_policy": "non_additive",
                "valid_from": "2020-01-01",
                "valid_to": "2024-12-31",
            },
            {
                "series_code": "SER_B",
                "mapping_relation": "exact",
                "aggregation_policy": "non_additive",
                "valid_from": "2024-01-01",
                "valid_to": "2026-12-31",
            },
        ]
    )

    assert selection.projection_policy == "representative_series"


def test_reported_aggregate_allows_multiple_narrower_source_series() -> None:
    selection = select_series_projection(
        [
            {
                "series_code": "SER_REPORTED",
                "mapping_relation": "narrower",
                "aggregation_policy": "reported_aggregate",
            },
            {
                "series_code": "SER_COMPONENT",
                "mapping_relation": "narrower",
                "aggregation_policy": "non_additive",
            },
        ]
    )

    assert selection.selected_codes == frozenset({"SER_REPORTED"})
    assert selection.projection_policy == "reported_aggregate_preferred"
