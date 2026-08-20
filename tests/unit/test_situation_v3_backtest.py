from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.services.situation_room import load_config
from src.services.situation_v3 import backtest


def _values(frame: pd.DataFrame, series_code: str) -> np.ndarray:
    return frame.loc[frame["series_code"] == series_code, "value"].to_numpy()


def test_wilson_interval_discloses_small_sample_uncertainty() -> None:
    small = backtest.wilson_interval(0, 10)
    calibrated = backtest.wilson_interval(0, 80)

    assert small["estimate"] == 0.0
    assert small["upper"] > 0.05
    assert calibrated["upper"] < 0.05
    assert calibrated["method"] == "wilson_score"
    assert calibrated["trials"] == 80


@pytest.mark.parametrize(
    ("successes", "trials"),
    [(-1, 10), (11, 10), (0, 0)],
)
def test_wilson_interval_rejects_invalid_counts(successes: int, trials: int) -> None:
    with pytest.raises(ValueError):
        backtest.wilson_interval(successes, trials)


def test_simulation_supports_rare_counts_and_one_or_two_period_anomalies() -> None:
    one_cycle = backtest.simulate_weekly_batch(
        seed=41,
        series_per_class=2,
        periods=208,
        baseline_range=(1.0, 4.0),
        dispersion_size_range=(8.0, 12.0),
        historical_zero_probability=0.1,
        anomaly_factor=4.0,
        anomaly_duration_source_periods=1,
    )
    two_cycle = backtest.simulate_weekly_batch(
        seed=41,
        series_per_class=2,
        periods=208,
        baseline_range=(1.0, 4.0),
        dispersion_size_range=(8.0, 12.0),
        historical_zero_probability=0.1,
        anomaly_factor=4.0,
        anomaly_duration_source_periods=2,
    )
    true_code = sorted(one_cycle.true_series)[0]

    np.testing.assert_array_equal(
        _values(one_cycle.null, true_code)[:-1],
        _values(one_cycle.second_cycle, true_code)[:-1],
    )
    np.testing.assert_array_equal(
        _values(two_cycle.null, true_code)[:-2],
        _values(two_cycle.second_cycle, true_code)[:-2],
    )
    assert float(one_cycle.null["value"].mean()) < 10.0
    assert len(one_cycle.complete_null_series) == 4


def test_simulation_rejects_unsupported_anomaly_duration() -> None:
    with pytest.raises(ValueError, match="one- or two-period"):
        backtest.simulate_weekly_batch(
            seed=1,
            series_per_class=1,
            periods=208,
            anomaly_duration_source_periods=3,
        )


def test_complete_null_keeps_zero_inflation_in_the_endpoint_window() -> None:
    batch = backtest.simulate_weekly_batch(
        seed=12,
        series_per_class=1,
        periods=208,
        historical_zero_probability=0.999999,
    )

    assert (batch.null.groupby("series_code").tail(8)["value"] == 0.0).all()


@pytest.mark.parametrize("historical_zero_probability", [0.08, 0.0])
def test_corrected_rare_tail_null_smoke_is_not_grossly_anti_conservative(
    historical_zero_probability: float,
) -> None:
    """Regression smoke only; the checked multi-batch report carries inference."""

    batch = backtest.simulate_weekly_batch(
        seed=20260901,
        series_per_class=8,
        periods=312,
        baseline_range=(0.5, 3.0),
        dispersion_size_range=(10.0, 25.0),
        historical_zero_probability=historical_zero_probability,
        anomaly_factor=4.0,
        anomaly_duration_source_periods=2,
    )
    config = deepcopy(load_config())
    config.setdefault("v3", {})["maximum_analysis_workers"] = 1
    _, signals = backtest._v3_analysis(
        batch.null,
        config,
        as_of=batch.null["time"].max().date(),
    )
    rare = [
        signal
        for signal in signals
        if signal.anomaly.detector_tier == "rare_count"
        and signal.anomaly.fit_status == "completed"
    ]

    assert len(rare) >= 12
    assert sum(signal.anomaly.raw_p_value <= 0.01 for signal in rare) <= 1
    assert all(
        signal.anomaly.diagnostics["rare_tail_predictive_variance"]
        >= signal.observation.expected * signal.anomaly.dispersion
        for signal in rare
    )


def test_guarded_auto_gate_excludes_fallback_and_requires_strict_q_and_evidence() -> None:
    signal = SimpleNamespace(
        identity=SimpleNamespace(series_code="A001"),
        observation=SimpleNamespace(data_status="current"),
        anomaly=SimpleNamespace(
            fit_status="completed",
            model="robust_quasi_poisson_v1",
            detector_tier="common_count",
            effect_threshold_passed=True,
            q_value=0.01,
        ),
        evidence_links=[SimpleNamespace(url="https://example.invalid/evidence")],
    )

    assert backtest._guarded_auto_candidates([signal]) == {"A001"}
    signal.anomaly.model = "seasonal_empirical_fallback_v1"
    assert not backtest._guarded_auto_candidates([signal])
    signal.anomaly.model = "robust_quasi_poisson_v1"
    signal.anomaly.q_value = 0.010001
    assert not backtest._guarded_auto_candidates([signal])


def test_stratified_report_exposes_scenarios_precision_and_correct_null_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "_v3_alerts", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(
        backtest,
        "_v3_analysis",
        lambda *_args, **_kwargs: (set(), []),
    )
    monkeypatch.setattr(backtest, "_v2_alerts", lambda *_args, **_kwargs: set())

    result = backtest.run_backtest(
        {},
        batches=2,
        series_per_class=1,
        seed=99,
        minimum_complete_null_families=8,
    )

    assert result["scenario_count"] == 7
    assert set(result["scenarios"]) == {
        "weekly_common_sustained_2x",
        "weekly_common_subtle_1_5x",
        "weekly_common_single_cycle_2x",
        "weekly_rare_sustained_4x",
        "monthly_common_sustained_2x",
        "monthly_common_subtle_1_5x",
        "monthly_common_single_cycle_2x",
    }
    assert result["precision"]["complete_null_family_trials"] == 14
    assert result["precision"]["minimum_nonzero_empirical_fdr_step"] == round(1 / 14, 6)
    assert result["precision"]["complete_null_family_trials_by_automation_group"] == {
        "weekly.common_count": 6,
        "monthly.common_count": 6,
    }
    for group in result["calibration_groups"].values():
        assert set(group["null_stress_strata"]) == set(backtest.NULL_STRESS_STRATA)
        assert sum(
            row["family_trials"]
            for row in group["null_stress_strata"].values()
        ) == 6
    # Each complete-null frame contains both the N and future-A arms.
    assert result["total_complete_null_series"] == 28
    assert result["v3"]["false_positive_rate_ci_95"]["trials"] == 28
    assert result["precision"]["sufficient_family_trials"] is True
    assert result["acceptance"]["empirical_fdr_upper_95_lte_5pct"] is False
    assert result["fdr_assessment"]["point_estimate_decision"] == "passed"
    assert result["fdr_assessment"]["confidence_bound_decision"] == "inconclusive"
    assert result["fdr_assessment"]["overall_calibration_decision"] == "failed"
    assert result["passed"] is False


def test_default_protocol_has_enough_trials_to_resolve_zero_fdr_below_five_percent() -> None:
    batches = 128
    family_trials = batches * len(backtest.DEFAULT_SCENARIOS)
    interval = backtest.wilson_interval(0, family_trials)

    assert family_trials == 896
    assert batches * 3 == 384  # three common-count strata per cadence
    assert 1 / family_trials < 0.05
    assert interval["upper"] < 0.025


def test_automation_precision_requirement_cannot_be_lowered() -> None:
    with pytest.raises(ValueError, match="cannot be below 384"):
        backtest.run_backtest(
            {},
            batches=1,
            series_per_class=1,
            minimum_complete_null_families_per_cadence=383,
        )


def test_projected_trials_use_conservative_discovery_rounding() -> None:
    projected = backtest.projected_trials_for_wilson_upper(
        3 / 80,
        target_upper=0.05,
        minimum_trials=80,
        trial_multiple=4,
    )

    assert projected == 1196
    assert backtest.wilson_interval(45, projected)["upper"] <= 0.05


def test_precision_plan_does_not_pretend_to_prove_threshold_control() -> None:
    projected = backtest.projected_trials_for_wilson_half_width(
        0.05,
        maximum_half_width=0.015,
        minimum_trials=80,
        trial_multiple=4,
    )

    assert projected == 824
    interval = backtest.wilson_interval(round(0.05 * projected), projected)
    assert (interval["upper"] - interval["lower"]) / 2 <= 0.015
    assert interval["upper"] > 0.05
