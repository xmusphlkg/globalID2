from __future__ import annotations

import warnings

import pandas as pd

from src.generation.data_cleaner import infer_frequency, long_to_wide


def test_monthly_aggregation_normalizes_aware_timestamps_to_utc_without_warning():
    frame = pd.DataFrame(
        {
            "time": [
                "2026-01-31T23:30:00-02:00",
                "2026-02-15T12:00:00+00:00",
            ],
            "cases": [2, 3],
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        wide = long_to_wide(frame, "monthly")

    assert wide["_period_str"].tolist() == ["2026-02"]
    assert wide["cases"].tolist() == [5]


def test_calendar_quarters_and_years_keep_source_period_starts():
    frame = pd.DataFrame(
        {"time": ["2025-02-01", "2025-03-01", "2025-08-01"], "cases": [2, 3, 4]}
    )
    quarters = long_to_wide(frame, "quarterly")
    assert quarters["_period_str"].tolist() == ["2025-Q1", "2025-Q3"]
    assert quarters["cases"].tolist() == [5, 4]
    years = long_to_wide(frame, "annual")
    assert years["_period_str"].tolist() == ["2025"]
    assert years["cases"].tolist() == [9]


def test_frequency_inference_distinguishes_quarterly_and_annual_reports():
    quarters = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=6, freq="QS")})
    years = pd.DataFrame({"time": pd.date_range("2021-01-01", periods=5, freq="YS")})
    assert infer_frequency(quarters) == "quarterly"
    assert infer_frequency(years) == "annual"


def test_week_number_uses_iso_week_year_across_new_year():
    wide = long_to_wide(
        pd.DataFrame({"time": ["2019-12-31", "2020-01-01"], "cases": [2, 3]}),
        "weekly",
    )
    assert wide["_period_str"].tolist() == ["2020-W01"]
    assert wide["cases"].tolist() == [5]
