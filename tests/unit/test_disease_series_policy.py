from __future__ import annotations

from datetime import datetime, timezone

from dashboard.api.services.disease_series_projection import (
    project_series_first_records,
)
from scripts.generate_site_data import apply_series_first_projection


def test_api_and_site_adapters_share_series_selection_policy() -> None:
    api_series = [
        {
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "series_code": "SER_A",
            "value": 5,
            "series_unit": "count",
            "observation_unit": "count",
            "metric_type": "case_notifications",
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
