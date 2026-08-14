from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.services.situation_room import load_config
from src.services.situation_statistics import (
    bayesian_change_probability,
    compute_risk,
    evaluate_frame_with_ledger,
    evaluate_series,
    ewma_residual_alert,
    z_scores,
    summarize_analysis_ledger,
)


def _frame(values: list[int], frequency: str, start: str, disease: str = "D001") -> pd.DataFrame:
    pandas_frequency = {"daily": "D", "weekly": "W-MON", "monthly": "MS"}[frequency]
    return pd.DataFrame(
        {
            "time": pd.date_range(start, periods=len(values), freq=pandas_frequency, tz="UTC"),
            "value": values,
            "quality_status": "validated",
            "geography_key": "national",
            "dimension_key": "all",
            "series_code": f"test-{frequency}",
            "disease_id": disease,
            "disease_name": "Test disease",
            "disease_slug": "test-disease",
            "country_code": "NZ",
            "country_name": "New Zealand",
            "source_system": "fixture",
            "source_label": "Fixture source",
            "metric_type": "case_notifications",
            "temporal_granularity": frequency,
            "unit": "count",
        }
    )


def test_week_53_and_zero_mad_are_handled() -> None:
    frame = _frame([10] * 209 + [35] * 4, "weekly", "2016-12-26")
    result = evaluate_series(frame, load_config())

    assert result.rejection_reason is None
    assert result.assessment is not None
    assert result.assessment["baseline"]["sample_size"] >= 3
    assert result.assessment["statistics"]["detectors"]["seasonal_band"] is True


def test_leap_day_daily_series_is_analyzable() -> None:
    values = [10] * 2200
    values[-28:] = [20] * 28
    frame = _frame(values, "daily", "2020-01-01")
    result = evaluate_series(frame, load_config())

    assert result.assessment is not None
    assert result.assessment["cadence"] == "daily"
    assert result.assessment["window"]["periods"] == 28


def test_missing_periods_are_not_imputed_as_zero() -> None:
    frame = _frame([10] * 180, "weekly", "2020-01-06")
    frame = frame.drop(frame.index[-3])
    result = evaluate_series(frame, load_config())

    assert result.assessment is None
    assert result.rejection_reason == "incomplete_comparison_window"


def test_reappearance_survives_low_base_suppression() -> None:
    values = [10] * 180 + [0] * 11 + [25]
    result = evaluate_series(_frame(values, "weekly", "2019-01-07"), load_config())

    assert result.assessment is not None
    assert result.assessment["reappearing"] is True
    assert result.assessment["candidate"] is True
    assert result.assessment["window"]["change_pct"] is None


def test_latest_partial_month_is_excluded() -> None:
    values = [20] * 80
    values[-2] = 40
    values[-1] = 999
    frame = _frame(values, "monthly", "2020-01-01")
    result = evaluate_series(frame, load_config(), as_of=date(2026, 8, 13))

    assert result.assessment is not None
    assert result.assessment["data_through"] == "2026-07-01"
    assert result.assessment["window"]["current"] == 40


def test_ewma_runs_on_residuals_and_detects_shift() -> None:
    residuals = pd.Series([0.0] * 40 + [2.0] * 4)
    value, upper, alert = ewma_residual_alert(residuals, smoothing=0.3, limit_sigma=3, holdout_periods=4)

    assert value is not None and upper == 0.0
    assert alert is True


def test_bayesian_change_probability_known_and_quiet_sequences() -> None:
    rng = np.random.default_rng(42)
    quiet = rng.normal(0, 0.2, 100)
    changed = np.concatenate([rng.normal(0, 0.2, 96), rng.normal(3, 0.2, 4)])

    quiet_probability = bayesian_change_probability(quiet, periods_per_year=52, recent_run_length=4)
    changed_probability = bayesian_change_probability(changed, periods_per_year=52, recent_run_length=4)

    assert quiet_probability is not None and quiet_probability < 0.2
    assert changed_probability is not None and changed_probability > 0.8


def test_zero_mad_uses_an_explicit_exceedance_without_crashing() -> None:
    robust, standard, median, lower, upper = z_scores(30, pd.Series([10.0, 10.0, 10.0]))

    assert robust == float("inf")
    assert standard == float("inf")
    assert median == 10
    assert lower == upper == 10


def test_risk_reweights_missing_dimensions_and_marks_confidence() -> None:
    trend_only = compute_risk({"trend": 80, "severity": None, "geographic_spread": None, "official_concern": None})
    three_dimensions = compute_risk({"trend": 80, "severity": 60, "geographic_spread": 50, "official_concern": None})

    assert trend_only["score"] == 80
    assert trend_only["confidence"] == "low"
    assert trend_only["missing_dimensions"] == ["severity", "geographic_spread", "official_concern"]
    assert three_dimensions["available_weight"] == 0.85
    assert three_dimensions["confidence"] == "high"


def test_percentage_windows_use_mean_and_cannot_be_case_candidates() -> None:
    frame = _frame([5] * 176 + [20] * 4, "weekly", "2022-01-03", disease="D038")
    frame["metric_type"] = "test_positivity"
    frame["unit"] = "percent"
    result = evaluate_series(frame, load_config())

    assert result.assessment is not None
    assert result.assessment["window"]["aggregation"] == "mean"
    assert result.assessment["window"]["current"] == 20
    assert result.assessment["window"]["previous"] == 5
    assert result.assessment["candidate"] is False
    assert all(
        method["status"] in {"completed", "degenerate_baseline"}
        for method in result.assessment["statistics"]["methods"].values()
    )


def test_full_ledger_records_execution_and_rejection_reasons() -> None:
    eligible = _frame([10] * 180, "weekly", "2022-01-03")
    short = _frame([10] * 20, "weekly", "2026-01-05", disease="D004")
    short["series_code"] = "short-series"
    assessments, rejected, ledger = evaluate_frame_with_ledger(
        pd.concat([eligible, short], ignore_index=True), load_config()
    )
    summary = summarize_analysis_ledger(ledger)

    assert len(assessments) == 1
    assert rejected == {"insufficient_observations": 1}
    assert {row["status"] for row in ledger} == {"analyzed", "rejected"}
    assert summary["methods"]["bayesian_change_point"]["executed_count"] == 1
    assert summary["source_usage"]["fixture"]["rejection_reasons"] == {"insufficient_observations": 1}
