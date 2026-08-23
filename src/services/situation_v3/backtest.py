"""Deterministic offline calibration harness for the Situation v3 detector."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Iterable
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from src.services.situation_statistics import evaluate_frame as evaluate_frame_v2

from .configuration import calibration_definition_hash
from .model import evaluate_frame_v3


@dataclass(frozen=True)
class SimulationScenario:
    """A named calibration stratum with an explicit outbreak profile."""

    key: str
    description: str
    cadence: str
    baseline_range: tuple[float, float]
    dispersion_size_range: tuple[float, float]
    historical_zero_probability: float
    anomaly_factor: float
    anomaly_duration_source_periods: int
    minimum_sensitivity: float | None = None


DEFAULT_SCENARIOS: tuple[SimulationScenario, ...] = (
    SimulationScenario(
        key="weekly_common_sustained_2x",
        description="Common weekly counts with a two-period 2x increase.",
        cadence="weekly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=2.0,
        anomaly_duration_source_periods=2,
        minimum_sensitivity=0.80,
    ),
    SimulationScenario(
        key="weekly_common_subtle_1_5x",
        description="Common weekly counts with a subtler two-period 1.5x increase.",
        cadence="weekly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=1.5,
        anomaly_duration_source_periods=2,
    ),
    SimulationScenario(
        key="weekly_common_single_cycle_2x",
        description="Common weekly counts with a transient one-period 2x increase.",
        cadence="weekly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=2.0,
        anomaly_duration_source_periods=1,
    ),
    SimulationScenario(
        key="weekly_rare_sustained_4x",
        description="Low-count weekly series with a two-period 4x cluster.",
        cadence="weekly",
        baseline_range=(1.0, 8.0),
        dispersion_size_range=(10.0, 25.0),
        historical_zero_probability=0.08,
        anomaly_factor=4.0,
        anomaly_duration_source_periods=2,
    ),
    SimulationScenario(
        key="monthly_common_sustained_2x",
        description="Common monthly counts with a two-period 2x increase.",
        cadence="monthly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=2.0,
        anomaly_duration_source_periods=2,
        minimum_sensitivity=0.80,
    ),
    SimulationScenario(
        key="monthly_common_subtle_1_5x",
        description="Common monthly counts with a two-period 1.5x increase.",
        cadence="monthly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=1.5,
        anomaly_duration_source_periods=2,
    ),
    SimulationScenario(
        key="monthly_common_single_cycle_2x",
        description="Common monthly counts with a one-period 2x increase.",
        cadence="monthly",
        baseline_range=(24.0, 90.0),
        dispersion_size_range=(22.0, 45.0),
        historical_zero_probability=0.015,
        anomaly_factor=2.0,
        anomaly_duration_source_periods=1,
    ),
)

NULL_STRESS_STRATA: tuple[str, ...] = (
    "zero_inflation",
    "correlated_series",
    "missing_periods",
    "revisions",
    "structural_break",
    "delayed_data",
)


@dataclass(frozen=True)
class SimulationBatch:
    null: pd.DataFrame
    first_cycle: pd.DataFrame
    second_cycle: pd.DataFrame
    true_series: frozenset[str]
    null_series: frozenset[str]

    @property
    def complete_null_series(self) -> frozenset[str]:
        """All series are null in ``null``, including the future anomaly arm."""

        return self.true_series | self.null_series


def _series_rows(
    *,
    series_code: str,
    values: np.ndarray,
    dates: pd.DatetimeIndex,
    cadence: str,
) -> list[dict[str, Any]]:
    return [
        {
            "time": stamp,
            "value": float(value),
            "geography_key": "national",
            "dimension_key": "all",
            "dimensions": {},
            "series_code": series_code,
            "disease_id": f"SIM_{series_code}",
            "disease_name": f"Simulated disease {series_code}",
            "disease_slug": f"simulated-{series_code.lower()}",
            "country_code": "ZZ",
            "country_name": "Simulation",
            "source_system": "SRC_SIMULATION",
            "source_label": "Offline simulation",
            "source_url": "https://example.invalid/situation-v3-backtest",
            "metric_type": "case_notifications",
            "temporal_granularity": cadence,
            "unit": "count",
        }
        for stamp, value in zip(dates, values, strict=True)
    ]


def simulate_batch(
    *,
    seed: int,
    series_per_class: int = 32,
    periods: int = 312,
    anomaly_factor: float = 2.0,
    anomaly_duration_source_periods: int = 2,
    baseline_range: tuple[float, float] = (24.0, 90.0),
    dispersion_size_range: tuple[float, float] = (22.0, 45.0),
    historical_zero_probability: float = 0.015,
    cadence: str = "weekly",
) -> SimulationBatch:
    """Create a reproducible cadence-aware null and anomaly arm."""

    minimum_periods = {"weekly": 208, "monthly": 48}
    if cadence not in minimum_periods:
        raise ValueError("simulation cadence must be weekly or monthly")
    if series_per_class < 1 or periods < minimum_periods[cadence]:
        raise ValueError(
            f"simulation requires at least one series and {minimum_periods[cadence]} "
            f"periods of {cadence} history"
        )
    if not 1 <= anomaly_duration_source_periods <= 2:
        raise ValueError("calibration currently supports one- or two-period anomalies")
    if anomaly_factor <= 1.0:
        raise ValueError("anomaly_factor must be greater than one")
    if baseline_range[0] <= 0.0 or baseline_range[1] < baseline_range[0]:
        raise ValueError("baseline_range must be positive and ordered")
    if dispersion_size_range[0] <= 0.0 or dispersion_size_range[1] < dispersion_size_range[0]:
        raise ValueError("dispersion_size_range must be positive and ordered")
    if not 0.0 <= historical_zero_probability < 1.0:
        raise ValueError("historical_zero_probability must be in [0, 1)")
    rng = np.random.default_rng(seed)
    if cadence == "weekly":
        dates = pd.date_range("2020-08-23", periods=periods, freq="7D", tz="UTC")
        periods_per_year = 52.18
    else:
        dates = pd.date_range("2000-01-01", periods=periods, freq="MS", tz="UTC")
        periods_per_year = 12.0
    time_index = np.arange(periods, dtype=float)
    null_rows: list[dict[str, Any]] = []
    first_rows: list[dict[str, Any]] = []
    second_rows: list[dict[str, Any]] = []
    null_series = frozenset(f"N{index:03d}" for index in range(series_per_class))
    true_series = frozenset(f"A{index:03d}" for index in range(series_per_class))
    for series_code in sorted(null_series | true_series):
        baseline = rng.uniform(*baseline_range)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        seasonal_amplitude = rng.uniform(0.12, 0.32)
        trend = rng.uniform(-0.0015, 0.0035)
        mean = np.maximum(
            0.05,
            baseline
            * (
                1.0
                + seasonal_amplitude
                * np.sin(time_index * 2.0 * np.pi / periods_per_year + phase)
            )
            * np.exp(trend * time_index),
        )
        # Negative binomial variance is mu + mu^2/size. The scenario-specific
        # structural-zero component stresses both common and rare count paths.
        size = rng.uniform(*dispersion_size_range)
        probability = size / (size + mean)
        observed = rng.negative_binomial(size, probability).astype(float)
        historical_zero = rng.random(periods) < historical_zero_probability
        # The complete-null endpoint must follow the same zero-inflated process
        # as its history. Suppressing structural zeros in the final window
        # creates an artificial upward regime shift, especially for rare counts.
        observed[historical_zero] = 0.0
        null_rows.extend(
            _series_rows(
                series_code=series_code,
                values=observed,
                dates=dates,
                cadence=cadence,
            )
        )
        first = observed.copy()
        second = observed.copy()
        if series_code in true_series:
            first[-1] = float(rng.poisson(mean[-1] * anomaly_factor))
            for offset in range(1, anomaly_duration_source_periods + 1):
                second[-offset] = float(rng.poisson(mean[-offset] * anomaly_factor))
        first_rows.extend(
            _series_rows(
                series_code=series_code,
                values=first,
                dates=dates,
                cadence=cadence,
            )
        )
        second_rows.extend(
            _series_rows(
                series_code=series_code,
                values=second,
                dates=dates,
                cadence=cadence,
            )
        )
    return SimulationBatch(
        null=pd.DataFrame(null_rows),
        first_cycle=pd.DataFrame(first_rows),
        second_cycle=pd.DataFrame(second_rows),
        true_series=true_series,
        null_series=null_series,
    )


def simulate_weekly_batch(
    *,
    seed: int,
    series_per_class: int = 32,
    periods: int = 312,
    anomaly_factor: float = 2.0,
    anomaly_duration_source_periods: int = 2,
    baseline_range: tuple[float, float] = (24.0, 90.0),
    dispersion_size_range: tuple[float, float] = (22.0, 45.0),
    historical_zero_probability: float = 0.015,
) -> SimulationBatch:
    """Backward-compatible weekly simulation wrapper."""

    return simulate_batch(
        seed=seed,
        series_per_class=series_per_class,
        periods=periods,
        anomaly_factor=anomaly_factor,
        anomaly_duration_source_periods=anomaly_duration_source_periods,
        baseline_range=baseline_range,
        dispersion_size_range=dispersion_size_range,
        historical_zero_probability=historical_zero_probability,
        cadence="weekly",
    )


def _apply_null_stress(
    frame: pd.DataFrame,
    *,
    cadence: str,
    stress: str,
    seed: int,
    as_of: date,
) -> tuple[pd.DataFrame, date]:
    """Apply one deterministic operational null stress without an outbreak."""

    if stress not in NULL_STRESS_STRATA:
        raise ValueError(f"unsupported null stress: {stress}")
    work = frame.copy()
    rng = np.random.default_rng(seed)
    if stress == "zero_inflation":
        mask = rng.random(len(work)) < 0.05
        work.loc[mask, "value"] = 0.0
    elif stress == "correlated_series":
        periods = sorted(work["time"].unique())
        factors = rng.lognormal(mean=-0.5 * 0.18**2, sigma=0.18, size=len(periods))
        factor_by_period = dict(zip(periods, factors, strict=True))
        work["value"] = np.round(
            work["value"]
            * work["time"].map(factor_by_period).to_numpy(dtype=float)
        )
    elif stress == "missing_periods":
        recent_periods = set(sorted(work["time"].unique())[-8:])
        eligible = ~work["time"].isin(recent_periods)
        mask = eligible & (rng.random(len(work)) < 0.04)
        work.loc[mask, "value"] = np.nan
    elif stress == "revisions":
        work["updated_at"] = "2026-01-02T00:00:00Z"
        candidates = work.loc[~work["time"].isin(sorted(work["time"].unique())[-8:])]
        revised = candidates.groupby("series_code", sort=True).tail(2).copy()
        if not revised.empty:
            work.loc[revised.index, "value"] = np.maximum(
                0.0,
                np.round(work.loc[revised.index, "value"].to_numpy(dtype=float) * 1.3),
            )
            work.loc[revised.index, "updated_at"] = "2026-01-01T00:00:00Z"
            revised["updated_at"] = "2026-01-03T00:00:00Z"
            work = pd.concat([work, revised], ignore_index=True)
    elif stress == "structural_break":
        break_periods = 26 if cadence == "weekly" else 6
        affected = set(sorted(work["time"].unique())[-break_periods:])
        mask = work["time"].isin(affected)
        work.loc[mask, "value"] = np.round(
            work.loc[mask, "value"].to_numpy(dtype=float) * 1.25
        )
    elif stress == "delayed_data":
        delay_days = 60 if cadence == "weekly" else 120
        as_of = as_of + timedelta(days=delay_days)
    return work, as_of


def _v3_analysis(
    frame: pd.DataFrame,
    config: dict[str, Any],
    as_of: date,
) -> tuple[set[str], list[Any]]:
    signals, _, _ = evaluate_frame_v3(frame, config, as_of=as_of)
    alerts = {
        signal.identity.series_code
        for signal in signals
        if signal.anomaly.state in {"alert", "strong"}
    }
    return alerts, signals


def _simulation_as_of(frame: pd.DataFrame, cadence: str) -> date:
    latest = pd.Timestamp(frame["time"].max())
    if cadence == "monthly":
        return (latest + pd.offsets.MonthBegin(1)).date()
    return latest.date()


def _v3_alerts(frame: pd.DataFrame, config: dict[str, Any], as_of: date) -> set[str]:
    return _v3_analysis(frame, config, as_of)[0]


def _guarded_auto_candidates(
    signals: list[Any],
    *,
    maximum_q: float = 0.01,
) -> set[str]:
    """Apply the pre-registered strict gate for automation diagnostics only."""

    candidates: set[str] = set()
    for signal in signals:
        evidence_is_valid = any(
            (parsed := urlparse(link.url)).scheme in {"http", "https"}
            and bool(parsed.hostname)
            for link in signal.evidence_links
        )
        if (
            signal.observation.data_status == "current"
            and signal.anomaly.fit_status == "completed"
            and signal.anomaly.model != "seasonal_empirical_fallback_v1"
            and signal.anomaly.detector_tier == "common_count"
            and signal.anomaly.effect_threshold_passed
            and evidence_is_valid
            and signal.anomaly.q_value is not None
            and signal.anomaly.q_value <= maximum_q
        ):
            candidates.add(signal.identity.series_code)
    return candidates


def _v2_alerts(frame: pd.DataFrame, config: dict[str, Any], as_of: date) -> set[str]:
    assessments, _ = evaluate_frame_v2(frame, config, as_of=as_of)
    return {
        str(assessment.get("series_code"))
        for assessment in assessments
        if assessment.get("candidate")
    }


def wilson_interval(
    successes: int,
    trials: int,
    *,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Return a dependency-free Wilson score interval for a binary rate."""

    if successes < 0 or trials < 1 or successes > trials:
        raise ValueError("successes and trials must satisfy 0 <= successes <= trials")
    if not math.isclose(confidence_level, 0.95):
        raise ValueError("only the pre-registered 95% confidence level is supported")
    z = 1.959963984540054
    estimate = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    centre = (estimate + z_squared / (2.0 * trials)) / denominator
    half_width = (
        z
        * math.sqrt(
            estimate * (1.0 - estimate) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    return {
        "method": "wilson_score",
        "confidence_level": confidence_level,
        "successes": successes,
        "trials": trials,
        "estimate": round(estimate, 6),
        "lower": round(max(0.0, centre - half_width), 6),
        "upper": round(min(1.0, centre + half_width), 6),
    }


def _mean_or_zero(values: Iterable[float]) -> float:
    materialized = list(values)
    return float(np.mean(materialized)) if materialized else 0.0


def _scenario_payload(scenario: SimulationScenario) -> dict[str, Any]:
    payload = asdict(scenario)
    payload["baseline_range"] = list(scenario.baseline_range)
    payload["dispersion_size_range"] = list(scenario.dispersion_size_range)
    return payload


def projected_trials_for_wilson_upper(
    observed_rate: float,
    *,
    target_upper: float = 0.05,
    minimum_trials: int = 1,
    trial_multiple: int = 1,
    maximum_trials: int = 1_000_000,
) -> int | None:
    """Project a conservative trial count while holding the observed rate fixed.

    This is a planning calculation, not a guarantee. ``ceil(rate * n)`` avoids
    understating the projected discovery count at a candidate sample size.
    """

    if not 0.0 <= observed_rate < 1.0:
        raise ValueError("observed_rate must be in [0, 1)")
    if not 0.0 < target_upper < 1.0:
        raise ValueError("target_upper must be in (0, 1)")
    if minimum_trials < 1 or trial_multiple < 1:
        raise ValueError("trial counts and multiples must be positive")
    if observed_rate >= target_upper:
        return None
    first = max(minimum_trials, trial_multiple)
    first += (-first) % trial_multiple
    for trials in range(first, maximum_trials + 1, trial_multiple):
        projected_discoveries = math.ceil(observed_rate * trials)
        if wilson_interval(projected_discoveries, trials)["upper"] <= target_upper:
            return trials
    return None


def projected_trials_for_wilson_half_width(
    planning_rate: float,
    *,
    maximum_half_width: float,
    minimum_trials: int = 1,
    trial_multiple: int = 1,
    maximum_trials: int = 1_000_000,
) -> int | None:
    """Plan trials for estimation precision without claiming threshold control."""

    if not 0.0 <= planning_rate <= 1.0:
        raise ValueError("planning_rate must be in [0, 1]")
    if not 0.0 < maximum_half_width < 0.5:
        raise ValueError("maximum_half_width must be in (0, 0.5)")
    first = max(minimum_trials, trial_multiple)
    first += (-first) % trial_multiple
    for trials in range(first, maximum_trials + 1, trial_multiple):
        projected_discoveries = round(planning_rate * trials)
        interval = wilson_interval(projected_discoveries, trials)
        half_width = (interval["upper"] - interval["lower"]) / 2.0
        if half_width <= maximum_half_width:
            return trials
    return None


def _binomial_probability_exact(successes: int, trials: int, rate: float) -> float:
    return float(
        math.comb(trials, successes)
        * rate**successes
        * (1.0 - rate) ** (trials - successes)
    )


def _null_discovery_details(
    *,
    signals: list[Any],
    frame: pd.DataFrame,
    scenario: SimulationScenario,
    batch_index: int,
    seed: int,
    stress: str | None = None,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for signal in signals:
        if signal.anomaly.state not in {"alert", "strong"}:
            continue
        values = frame.loc[
            frame["series_code"] == signal.identity.series_code, "value"
        ].to_numpy(dtype=float)
        history_values = values[:-4]
        finite_history = history_values[np.isfinite(history_values)]
        diagnostics = signal.anomaly.diagnostics
        details.append(
            {
                "scenario": scenario.key,
                "null_stress": stress,
                "batch_index": batch_index,
                "seed": seed,
                "series_code": signal.identity.series_code,
                "detector_tier": signal.anomaly.detector_tier,
                "model": signal.anomaly.model,
                "fit_status": signal.anomaly.fit_status,
                "fallback_used": signal.anomaly.fit_status == "fallback_completed",
                "guarded_auto_candidate": bool(
                    _guarded_auto_candidates([signal])
                ),
                "state": signal.anomaly.state,
                "raw_p_value": signal.anomaly.raw_p_value,
                "q_value": signal.anomaly.q_value,
                "current": signal.observation.current,
                "previous": signal.observation.previous,
                "expected": signal.observation.expected,
                "predictive_upper_95": signal.observation.predictive_upper_95,
                "absolute_change": signal.observation.absolute_change,
                "relative_change_pct": signal.observation.relative_change_pct,
                "dispersion": signal.anomaly.dispersion,
                "standardized_exceedance": signal.anomaly.standardized_exceedance,
                "effect_threshold_passed": signal.anomaly.effect_threshold_passed,
                "history_mean": (
                    round(float(np.mean(finite_history)), 6)
                    if len(finite_history)
                    else None
                ),
                "history_variance": (
                    round(float(np.var(finite_history, ddof=1)), 6)
                    if len(finite_history) > 1
                    else None
                ),
                "history_zero_fraction": (
                    round(float(np.mean(finite_history == 0)), 6)
                    if len(finite_history)
                    else None
                ),
                "recent_12_values": [
                    float(value) if np.isfinite(value) else None
                    for value in values[-12:]
                ],
                "supporting_cusum": diagnostics.get("supporting_cusum"),
            }
        )
    return details


def _pvalue_calibration_summary(
    tallies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for stratum, tally in sorted(tallies.items()):
        trials = int(tally["trials"])
        thresholds: dict[str, Any] = {}
        for threshold_text, successes in tally["threshold_counts"].items():
            threshold = float(threshold_text)
            interval = wilson_interval(int(successes), trials)
            thresholds[threshold_text] = {
                "nominal_rate": threshold,
                "observed_rate": interval["estimate"],
                "observed_rate_ci_95": interval,
                "compatible_with_super_uniform_null": interval["lower"] <= threshold,
            }
        summary[stratum] = {
            "series_trials": trials,
            "fit_status_counts": dict(sorted(tally["fit_status_counts"].items())),
            "thresholds": thresholds,
        }
    return summary


def _record_pvalue_tallies(
    tallies: dict[str, dict[str, Any]],
    *,
    stratum_prefix: str,
    signals: list[Any],
) -> None:
    for signal in signals:
        if signal.anomaly.raw_p_value is None:
            continue
        stratum = (
            f"{stratum_prefix}|{signal.anomaly.detector_tier}:"
            f"{signal.anomaly.model}"
        )
        tally = tallies.setdefault(
            stratum,
            {
                "trials": 0,
                "fit_status_counts": Counter(),
                "threshold_counts": {
                    "0.001": 0,
                    "0.005": 0,
                    "0.01": 0,
                    "0.05": 0,
                },
            },
        )
        tally["trials"] += 1
        tally["fit_status_counts"][signal.anomaly.fit_status] += 1
        for threshold_text in tally["threshold_counts"]:
            if signal.anomaly.raw_p_value <= float(threshold_text):
                tally["threshold_counts"][threshold_text] += 1


def run_backtest(
    config: dict[str, Any],
    *,
    batches: int = 20,
    series_per_class: int = 16,
    seed: int = 20260817,
    scenarios: tuple[SimulationScenario, ...] = DEFAULT_SCENARIOS,
    minimum_complete_null_families: int = 384,
    minimum_complete_null_families_per_cadence: int = 384,
    automatic_q_grid: tuple[float, ...] = (0.0025, 0.005, 0.01, 0.015, 0.025),
    locked_event_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run weekly/monthly calibration against v3.1 and the v2 comparator."""

    if batches < 1:
        raise ValueError("batches must be positive")
    if series_per_class < 1:
        raise ValueError("series_per_class must be positive")
    if not scenarios or len({scenario.key for scenario in scenarios}) != len(scenarios):
        raise ValueError("scenarios must be non-empty and have unique keys")
    if minimum_complete_null_families < 1:
        raise ValueError("minimum_complete_null_families must be positive")
    if minimum_complete_null_families_per_cadence < 384:
        raise ValueError(
            "minimum_complete_null_families_per_cadence cannot be below 384"
        )
    if (
        not automatic_q_grid
        or tuple(sorted(set(automatic_q_grid))) != automatic_q_grid
        or any(not 0.0 < threshold < 0.05 for threshold in automatic_q_grid)
    ):
        raise ValueError("automatic_q_grid must be unique, sorted, and inside (0, 0.05)")
    evaluation_config = deepcopy(config)
    evaluation_config.setdefault("v3", {})["maximum_analysis_workers"] = 1
    evaluation_multi_horizon = (
        evaluation_config.setdefault("v3", {})
        .setdefault("detectors", {})
        .setdefault("multi_horizon", {})
    )
    if "calibration_draws" in evaluation_multi_horizon:
        evaluation_multi_horizon["production_draws"] = int(
            evaluation_multi_horizon["calibration_draws"]
        )
    champion_config = deepcopy(evaluation_config)
    champion_v3 = champion_config.setdefault("v3", {})
    champion_v3["method_version"] = "situation_room_v3.1"
    champion_v3["model"] = "robust_quasi_poisson_v1"
    champion_v3.setdefault("detectors", {}).setdefault("multi_horizon", {})[
        "enabled"
    ] = False
    weekly_comparator = next(
        (
            scenario
            for scenario in scenarios
            if scenario.key == "weekly_common_sustained_2x"
        ),
        None,
    )
    primary = weekly_comparator or scenarios[0]
    all_null_family_discoveries: list[int] = []
    guarded_auto_null_family_discoveries: list[int] = []
    all_mixed_false_discovery_ratios: list[float] = []
    null_discovery_details: list[dict[str, Any]] = []
    pvalue_tallies: dict[str, dict[str, Any]] = {}
    aggregate_v3_false = aggregate_null_trials = 0
    scenario_results: dict[str, Any] = {}
    champion_detected_by_scenario: Counter[str] = Counter()
    group_tallies: dict[str, dict[str, Any]] = {}
    q_scenario_tallies: dict[str, dict[float, dict[str, Any]]] = {}
    null_stress_tallies: dict[str, dict[str, dict[str, Any]]] = {}
    primary_v2_true = primary_v2_false = 0
    primary_v2_null_trials = 0
    for scenario_index, scenario in enumerate(scenarios):
        if scenario.cadence not in {"weekly", "monthly"}:
            raise ValueError(f"unsupported scenario cadence: {scenario.cadence}")
        null_family_discoveries: list[int] = []
        mixed_false_discovery_ratios: list[float] = []
        delays: list[int] = []
        first_cycle_true = detected_true = false_complete_null = 0
        guarded_first_cycle_true = guarded_detected_true = 0
        guarded_false_complete_null = 0
        guarded_scenario_family_discoveries: list[int] = []
        complete_null_trials = total_true = mixed_null_trials = 0
        for batch_index in range(batches):
            batch = simulate_batch(
                seed=seed + scenario_index * 100_000 + batch_index,
                series_per_class=series_per_class,
                periods=312 if scenario.cadence == "weekly" else 72,
                anomaly_factor=scenario.anomaly_factor,
                anomaly_duration_source_periods=scenario.anomaly_duration_source_periods,
                baseline_range=scenario.baseline_range,
                dispersion_size_range=scenario.dispersion_size_range,
                historical_zero_probability=scenario.historical_zero_probability,
                cadence=scenario.cadence,
            )
            as_of = _simulation_as_of(batch.second_cycle, scenario.cadence)
            null_frame = batch.null
            null_as_of = as_of
            null_stress = None
            if "common_" in scenario.key:
                null_stress = NULL_STRESS_STRATA[
                    (scenario_index + batch_index) % len(NULL_STRESS_STRATA)
                ]
                null_frame, null_as_of = _apply_null_stress(
                    batch.null,
                    cadence=scenario.cadence,
                    stress=null_stress,
                    seed=seed + scenario_index * 100_000 + batch_index + 50_000,
                    as_of=as_of,
                )
            v3_null, null_signals = _v3_analysis(
                null_frame, evaluation_config, null_as_of
            )
            _record_pvalue_tallies(
                pvalue_tallies,
                stratum_prefix=(
                    f"{scenario.key}|{null_stress}"
                    if null_stress
                    else scenario.key
                ),
                signals=null_signals,
            )
            if v3_null:
                null_discovery_details.extend(
                    _null_discovery_details(
                        signals=null_signals,
                        frame=null_frame,
                        scenario=scenario,
                        batch_index=batch_index,
                        seed=seed + scenario_index * 100_000 + batch_index,
                        stress=null_stress,
                    )
                )
            guarded_null = _guarded_auto_candidates(null_signals)
            guarded_family_discovery = int(bool(guarded_null))
            guarded_auto_null_family_discoveries.append(guarded_family_discovery)
            guarded_scenario_family_discoveries.append(guarded_family_discovery)
            guarded_false_complete_null += len(
                guarded_null & batch.complete_null_series
            )
            v3_first, first_signals = _v3_analysis(
                batch.first_cycle, evaluation_config, as_of
            )
            guarded_first = _guarded_auto_candidates(first_signals)
            if scenario.anomaly_duration_source_periods == 1:
                v3_endpoint = v3_first
                endpoint_signals = first_signals
                guarded_endpoint = guarded_first
                endpoint_frame = batch.first_cycle
            else:
                v3_endpoint, endpoint_signals = _v3_analysis(
                    batch.second_cycle, evaluation_config, as_of
                )
                guarded_endpoint = _guarded_auto_candidates(endpoint_signals)
                endpoint_frame = batch.second_cycle
            if "common_" in scenario.key:
                group_key = f"{scenario.cadence}.common_count"
                group_tally = group_tallies.setdefault(
                    group_key,
                    {
                        "family_trials": 0,
                        "thresholds": {
                            threshold: {"families_with_candidate": 0}
                            for threshold in automatic_q_grid
                        },
                    },
                )
                group_tally["family_trials"] += 1
                stress_tally = null_stress_tallies.setdefault(
                    group_key, {}
                ).setdefault(
                    str(null_stress),
                    {
                        "family_trials": 0,
                        "review_families_with_discovery": 0,
                        "thresholds": {
                            threshold: 0 for threshold in automatic_q_grid
                        },
                    },
                )
                stress_tally["family_trials"] += 1
                stress_tally["review_families_with_discovery"] += int(
                    bool(v3_null)
                )
                scenario_q = q_scenario_tallies.setdefault(
                    scenario.key,
                    {
                        threshold: {
                            "true_trials": 0,
                            "detected_true": 0,
                            "first_cycle_true": 0,
                            "delays": [],
                        }
                        for threshold in automatic_q_grid
                    },
                )
                for threshold in automatic_q_grid:
                    q_null = _guarded_auto_candidates(
                        null_signals, maximum_q=threshold
                    )
                    q_first = _guarded_auto_candidates(
                        first_signals, maximum_q=threshold
                    )
                    q_endpoint = _guarded_auto_candidates(
                        endpoint_signals, maximum_q=threshold
                    )
                    group_tally["thresholds"][threshold][
                        "families_with_candidate"
                    ] += int(bool(q_null))
                    stress_tally["thresholds"][threshold] += int(bool(q_null))
                    scenario_q[threshold]["true_trials"] += len(batch.true_series)
                    scenario_q[threshold]["detected_true"] += len(
                        q_endpoint & batch.true_series
                    )
                    scenario_q[threshold]["first_cycle_true"] += len(
                        q_first & batch.true_series
                    )
                    for series_code in batch.true_series:
                        if series_code in q_first:
                            scenario_q[threshold]["delays"].append(0)
                        elif series_code in q_endpoint:
                            scenario_q[threshold]["delays"].append(1)
            family_discovery = int(bool(v3_null))
            null_family_discoveries.append(family_discovery)
            all_null_family_discoveries.append(family_discovery)
            false_endpoint = len(v3_endpoint & batch.null_series)
            mixed_fdr = false_endpoint / len(v3_endpoint) if v3_endpoint else 0.0
            mixed_false_discovery_ratios.append(mixed_fdr)
            all_mixed_false_discovery_ratios.append(mixed_fdr)
            first_cycle_true += len(v3_first & batch.true_series)
            detected_true += len(v3_endpoint & batch.true_series)
            guarded_first_cycle_true += len(guarded_first & batch.true_series)
            guarded_detected_true += len(guarded_endpoint & batch.true_series)
            false_complete_null += len(v3_null & batch.complete_null_series)
            complete_null_trials += len(batch.complete_null_series)
            mixed_null_trials += len(batch.null_series)
            total_true += len(batch.true_series)
            for series_code in batch.true_series:
                if series_code in v3_first:
                    delays.append(0)
                elif series_code in v3_endpoint:
                    delays.append(1)
            if "common_" in scenario.key:
                champion_endpoint, _ = _v3_analysis(
                    endpoint_frame,
                    champion_config,
                    as_of,
                )
                champion_detected_by_scenario[scenario.key] += len(
                    champion_endpoint & batch.true_series
                )
            if weekly_comparator is not None and scenario.key == weekly_comparator.key:
                v2_null = _v2_alerts(null_frame, config, null_as_of)
                v2_endpoint = _v2_alerts(endpoint_frame, config, as_of)
                primary_v2_true += len(v2_endpoint & batch.true_series)
                primary_v2_false += len(v2_null & batch.complete_null_series)
                primary_v2_null_trials += len(batch.complete_null_series)
        sensitivity_interval = wilson_interval(detected_true, total_true)
        first_cycle_interval = wilson_interval(first_cycle_true, total_true)
        false_positive_interval = wilson_interval(false_complete_null, complete_null_trials)
        family_interval = wilson_interval(
            sum(null_family_discoveries), len(null_family_discoveries)
        )
        guarded_family_interval = wilson_interval(
            sum(guarded_scenario_family_discoveries),
            len(guarded_scenario_family_discoveries),
        )
        guarded_fpr_interval = wilson_interval(
            guarded_false_complete_null, complete_null_trials
        )
        guarded_sensitivity_interval = wilson_interval(
            guarded_detected_true, total_true
        )
        guarded_first_interval = wilson_interval(
            guarded_first_cycle_true, total_true
        )
        median_delay = float(np.median(delays)) if delays else None
        champion_sensitivity = (
            champion_detected_by_scenario[scenario.key] / total_true
            if "common_" in scenario.key and total_true
            else None
        )
        scenario_results[scenario.key] = {
            "profile": _scenario_payload(scenario),
            "complete_null": {
                "family_empirical_fdr": family_interval["estimate"],
                "family_empirical_fdr_ci_95": family_interval,
                "families_with_any_discovery": sum(null_family_discoveries),
                "family_trials": len(null_family_discoveries),
                "false_positive_rate": false_positive_interval["estimate"],
                "false_positive_rate_ci_95": false_positive_interval,
                "false_alerts": false_complete_null,
                "series_trials": complete_null_trials,
            },
            "mixed_anomaly": {
                "mean_empirical_fdr": round(_mean_or_zero(mixed_false_discovery_ratios), 6),
                "sensitivity": sensitivity_interval["estimate"],
                "sensitivity_ci_95": sensitivity_interval,
                "first_cycle_sensitivity": first_cycle_interval["estimate"],
                "first_cycle_sensitivity_ci_95": first_cycle_interval,
                "detected_true_anomalies": detected_true,
                "true_anomaly_trials": total_true,
                "null_series_trials": mixed_null_trials,
                "median_detection_delay_source_periods": median_delay,
                "minimum_sensitivity": scenario.minimum_sensitivity,
                "meets_sensitivity_target": (
                    None
                    if scenario.minimum_sensitivity is None
                    else sensitivity_interval["estimate"] >= scenario.minimum_sensitivity
                ),
                "champion_v3_1_sensitivity": (
                    None
                    if champion_sensitivity is None
                    else round(champion_sensitivity, 6)
                ),
                "sensitivity_change_vs_champion_pp": (
                    None
                    if champion_sensitivity is None
                    else round(
                        (sensitivity_interval["estimate"] - champion_sensitivity)
                        * 100.0,
                        3,
                    )
                ),
            },
            "guarded_auto": {
                "complete_null_family_discovery_rate": guarded_family_interval[
                    "estimate"
                ],
                "complete_null_family_discovery_rate_ci_95": guarded_family_interval,
                "complete_null_false_candidates": guarded_false_complete_null,
                "complete_null_false_candidate_rate": guarded_fpr_interval["estimate"],
                "complete_null_false_candidate_rate_ci_95": guarded_fpr_interval,
                "sensitivity": guarded_sensitivity_interval["estimate"],
                "sensitivity_ci_95": guarded_sensitivity_interval,
                "first_cycle_sensitivity": guarded_first_interval["estimate"],
                "first_cycle_sensitivity_ci_95": guarded_first_interval,
                "detected_true_anomalies": guarded_detected_true,
            },
        }
        aggregate_v3_false += false_complete_null
        aggregate_null_trials += complete_null_trials

    rare_zero_control: dict[str, Any] | None = None
    diagnostic_model_evaluation_calls = 0
    rare_scenario_entry = next(
        (
            scenario
            for scenario in scenarios
            if scenario.key == "weekly_rare_sustained_4x"
        ),
        None,
    )
    if rare_scenario_entry is not None:
        rare_scenario_index = scenarios.index(rare_scenario_entry)
        control_family_discoveries: list[int] = []
        control_tallies: dict[str, dict[str, Any]] = {}
        for batch_index in range(batches):
            control_seed = seed + rare_scenario_index * 100_000 + batch_index
            control_batch = simulate_batch(
                seed=control_seed,
                series_per_class=series_per_class,
                periods=312,
                anomaly_factor=rare_scenario_entry.anomaly_factor,
                anomaly_duration_source_periods=(
                    rare_scenario_entry.anomaly_duration_source_periods
                ),
                baseline_range=rare_scenario_entry.baseline_range,
                dispersion_size_range=rare_scenario_entry.dispersion_size_range,
                historical_zero_probability=0.0,
                cadence="weekly",
            )
            control_alerts, control_signals = _v3_analysis(
                control_batch.null,
                evaluation_config,
                _simulation_as_of(control_batch.null, "weekly"),
            )
            diagnostic_model_evaluation_calls += 1
            control_family_discoveries.append(int(bool(control_alerts)))
            _record_pvalue_tallies(
                control_tallies,
                stratum_prefix="rare_no_structural_zero_control",
                signals=control_signals,
            )
        control_family_interval = wilson_interval(
            sum(control_family_discoveries), len(control_family_discoveries)
        )
        control_pvalue_calibration = _pvalue_calibration_summary(control_tallies)
        control_anti_conservative_strata = sorted(
            stratum
            for stratum, summary in control_pvalue_calibration.items()
            if any(
                not threshold["compatible_with_super_uniform_null"]
                for threshold in summary["thresholds"].values()
            )
        )
        rare_zero_control = {
            "purpose": (
                "Paired null-only diagnostic using the same rare-count seeds and "
                "parameters but no structural-zero replacement. It is excluded "
                "from acceptance and sensitivity estimates."
            ),
            "family_discovery_rate": control_family_interval["estimate"],
            "family_discovery_rate_ci_95": control_family_interval,
            "families_with_discovery": sum(control_family_discoveries),
            "family_trials": len(control_family_discoveries),
            "p_value_calibration": control_pvalue_calibration,
            "anti_conservative_pvalue_strata": control_anti_conservative_strata,
        }

    family_interval = wilson_interval(
        sum(all_null_family_discoveries), len(all_null_family_discoveries)
    )
    nominal_fdr = 0.05
    projected_family_trials = projected_trials_for_wilson_upper(
        float(family_interval["estimate"]),
        target_upper=nominal_fdr,
        minimum_trials=len(all_null_family_discoveries),
        trial_multiple=len(scenarios),
    )
    estimation_precision_trials = projected_trials_for_wilson_half_width(
        nominal_fdr,
        maximum_half_width=0.015,
        minimum_trials=len(all_null_family_discoveries),
        trial_multiple=len(scenarios),
    )
    pvalue_calibration = _pvalue_calibration_summary(pvalue_tallies)
    anti_conservative_strata = sorted(
        stratum
        for stratum, summary in pvalue_calibration.items()
        if any(
            not threshold["compatible_with_super_uniform_null"]
            for threshold in summary["thresholds"].values()
        )
    )
    rare_main_anti_conservative = any(
        stratum.startswith("weekly_rare_sustained_4x|rare_count:")
        for stratum in anti_conservative_strata
    )
    rare_control_anti_conservative = bool(
        rare_zero_control
        and rare_zero_control["anti_conservative_pvalue_strata"]
    )
    nominal_within_interval = (
        family_interval["lower"] <= nominal_fdr <= family_interval["upper"]
    )
    point_estimate_passed = family_interval["estimate"] <= nominal_fdr
    confidence_bound_passed = family_interval["upper"] <= nominal_fdr
    if point_estimate_passed and confidence_bound_passed:
        fdr_diagnosis = "point_and_confidence_bound_passed"
    elif point_estimate_passed and nominal_within_interval and not anti_conservative_strata:
        fdr_diagnosis = "sampling_variation_consistent_with_nominal_precision_inconclusive"
    elif anti_conservative_strata:
        fdr_diagnosis = "possible_pvalue_anti_conservatism_requires_investigation"
    else:
        fdr_diagnosis = "nominal_fdr_not_demonstrated"
    guarded_auto_nominal_q = 0.01
    guarded_auto_family_interval = wilson_interval(
        sum(guarded_auto_null_family_discoveries),
        len(guarded_auto_null_family_discoveries),
    )
    guarded_auto_projected_trials = projected_trials_for_wilson_upper(
        float(guarded_auto_family_interval["estimate"]),
        target_upper=guarded_auto_nominal_q,
        minimum_trials=len(guarded_auto_null_family_discoveries),
        trial_multiple=len(scenarios),
    )
    for scenario_result in scenario_results.values():
        review_sensitivity = float(scenario_result["mixed_anomaly"]["sensitivity"])
        guarded_sensitivity = float(scenario_result["guarded_auto"]["sensitivity"])
        scenario_result["guarded_auto"]["sensitivity_change_vs_review_pp"] = round(
            (guarded_sensitivity - review_sensitivity) * 100.0, 3
        )
    sensitivity_preservation: dict[str, bool] = {}
    for scenario_key in (primary.key, "weekly_rare_sustained_4x"):
        if scenario_key not in scenario_results:
            sensitivity_preservation[scenario_key] = False
            continue
        sensitivity_preservation[scenario_key] = (
            scenario_results[scenario_key]["guarded_auto"][
                "sensitivity_change_vs_review_pp"
            ]
            >= -5.0
        )
    guarded_auto_false_control_supported = (
        guarded_auto_family_interval["upper"] <= guarded_auto_nominal_q
    )
    guarded_auto_supported = guarded_auto_false_control_supported and all(
        sensitivity_preservation.values()
    )
    calibration_groups: dict[str, Any] = {}
    for cadence in ("weekly", "monthly"):
        group_key = f"{cadence}.common_count"
        group_tally = group_tallies.get(group_key) or {
            "family_trials": 0,
            "thresholds": {},
        }
        sustained_key = f"{cadence}_common_sustained_2x"
        subtle_key = f"{cadence}_common_subtle_1_5x"
        transient_key = f"{cadence}_common_single_cycle_2x"
        weak_improvements = {
            scenario_key: (
                scenario_results.get(scenario_key, {})
                .get("mixed_anomaly", {})
                .get("sensitivity_change_vs_champion_pp")
            )
            for scenario_key in (subtle_key, transient_key)
        }
        weak_signal_gate = all(
            value is not None and float(value) >= 15.0
            for value in weak_improvements.values()
        )
        real_metrics = (locked_event_metrics or {}).get(group_key)
        real_event_gate = bool(
            real_metrics
            and float(real_metrics.get("event_detection_rate", -1.0)) >= 0.80
            and float(real_metrics.get("event_detection_rate", -1.0))
            >= float(real_metrics.get("champion_event_detection_rate", 1.0)) - 0.05
        )
        threshold_results: dict[str, Any] = {}
        simulation_eligible_thresholds: list[float] = []
        fully_eligible_thresholds: list[float] = []
        for threshold in automatic_q_grid:
            false_families = int(
                (group_tally.get("thresholds", {}).get(threshold) or {}).get(
                    "families_with_candidate", 0
                )
            )
            family_trials = int(group_tally.get("family_trials", 0))
            family_ci = (
                wilson_interval(false_families, family_trials)
                if family_trials
                else None
            )
            sensitivity_tally = (
                q_scenario_tallies.get(sustained_key, {}).get(threshold) or {}
            )
            true_trials = int(sensitivity_tally.get("true_trials", 0))
            detected_true = int(sensitivity_tally.get("detected_true", 0))
            sensitivity_ci = (
                wilson_interval(detected_true, true_trials) if true_trials else None
            )
            sensitivity = (
                float(sensitivity_ci["estimate"]) if sensitivity_ci else None
            )
            threshold_delays = list(sensitivity_tally.get("delays", []))
            median_threshold_delay = (
                float(np.median(threshold_delays)) if threshold_delays else None
            )
            simulation_gates = {
                "complete_null_families_gte_384": (
                    family_trials >= minimum_complete_null_families_per_cadence
                ),
                "false_publication_upper_95_lte_2_5pct": bool(
                    family_ci and float(family_ci["upper"]) <= 0.025
                ),
                "sustained_2x_sensitivity_gte_80pct": bool(
                    sensitivity is not None and sensitivity >= 0.80
                ),
                "median_detection_delay_lte_1_period": bool(
                    median_threshold_delay is not None
                    and median_threshold_delay <= 1.0
                ),
                "weak_signal_improvement_gte_15pp": weak_signal_gate,
            }
            simulation_supported = all(simulation_gates.values())
            if simulation_supported:
                simulation_eligible_thresholds.append(threshold)
            if simulation_supported and real_event_gate:
                fully_eligible_thresholds.append(threshold)
            threshold_results[str(threshold)] = {
                "complete_null_family_trials": family_trials,
                "families_with_false_publication": false_families,
                "false_publication_rate_ci_95": family_ci,
                "sustained_2x_sensitivity": sensitivity,
                "sustained_2x_sensitivity_ci_95": sensitivity_ci,
                "median_detection_delay_periods": median_threshold_delay,
                "simulation_gates": simulation_gates,
                "simulation_supported": simulation_supported,
                "locked_real_event_gate": real_event_gate,
            }
        stress_results: dict[str, Any] = {}
        for stress in NULL_STRESS_STRATA:
            stress_tally = (
                null_stress_tallies.get(group_key, {}).get(stress) or {}
            )
            stress_trials = int(stress_tally.get("family_trials", 0))
            review_discoveries = int(
                stress_tally.get("review_families_with_discovery", 0)
            )
            stress_results[stress] = {
                "family_trials": stress_trials,
                "review_family_discovery_rate_ci_95": (
                    wilson_interval(review_discoveries, stress_trials)
                    if stress_trials
                    else None
                ),
                "automatic_thresholds": {
                    str(threshold): (
                        wilson_interval(
                            int(
                                (stress_tally.get("thresholds", {}) or {}).get(
                                    threshold, 0
                                )
                            ),
                            stress_trials,
                        )
                        if stress_trials
                        else None
                    )
                    for threshold in automatic_q_grid
                },
            }
        calibration_groups[group_key] = {
            "status": "supported" if fully_eligible_thresholds else "not_supported",
            "maximum_q": (
                max(fully_eligible_thresholds) if fully_eligible_thresholds else None
            ),
            "simulation_maximum_q": (
                max(simulation_eligible_thresholds)
                if simulation_eligible_thresholds
                else None
            ),
            "thresholds": threshold_results,
            "null_stress_strata": stress_results,
            "weak_signal_improvement_vs_champion_pp": weak_improvements,
            "locked_real_event_metrics": real_metrics,
            "failure_reasons": [
                reason
                for reason, failed in (
                    ("insufficient_complete_null_families", int(group_tally.get("family_trials", 0)) < minimum_complete_null_families_per_cadence),
                    ("weak_signal_improvement_below_15pp", not weak_signal_gate),
                    ("locked_real_event_evaluation_missing_or_failed", not real_event_gate),
                    ("no_q_threshold_passed_simulation_gates", not simulation_eligible_thresholds),
                )
                if failed
            ],
        }
    aggregate_fpr_interval = wilson_interval(aggregate_v3_false, aggregate_null_trials)
    primary_result = scenario_results[primary.key]["mixed_anomaly"]
    primary_sensitivity = float(primary_result["sensitivity"])
    primary_median_delay = primary_result["median_detection_delay_source_periods"]
    primary_v2_sensitivity_interval = (
        wilson_interval(primary_v2_true, batches * series_per_class)
        if weekly_comparator is not None
        else None
    )
    primary_v2_fpr_interval = (
        wilson_interval(primary_v2_false, primary_v2_null_trials)
        if primary_v2_null_trials
        else None
    )
    v2_sensitivity = (
        float(primary_v2_sensitivity_interval["estimate"])
        if primary_v2_sensitivity_interval
        else None
    )
    v2_false_positive_rate = (
        float(primary_v2_fpr_interval["estimate"])
        if primary_v2_fpr_interval
        else None
    )
    primary_v3_false_positive_rate = float(
        scenario_results[primary.key]["complete_null"]["false_positive_rate"]
    )
    false_positive_reduction = (
        None
        if v2_false_positive_rate is None
        else (
            (v2_false_positive_rate - primary_v3_false_positive_rate)
            / v2_false_positive_rate
            if v2_false_positive_rate
            else (1.0 if primary_v3_false_positive_rate == 0 else 0.0)
        )
    )

    latency_guards: dict[str, Any] = {}
    provisional_true_alert_count = 0
    delayed_alert_count = 0
    for cadence_index, cadence in enumerate(("weekly", "monthly")):
        latency_scenario = next(
            (
                scenario
                for scenario in scenarios
                if scenario.key == f"{cadence}_common_sustained_2x"
            ),
            None,
        )
        if latency_scenario is None:
            continue
        latency_batch = simulate_batch(
            seed=seed + len(scenarios) * 100_000 + batches + cadence_index,
            series_per_class=series_per_class,
            periods=312 if cadence == "weekly" else 72,
            anomaly_factor=latency_scenario.anomaly_factor,
            anomaly_duration_source_periods=(
                latency_scenario.anomaly_duration_source_periods
            ),
            baseline_range=latency_scenario.baseline_range,
            dispersion_size_range=latency_scenario.dispersion_size_range,
            historical_zero_probability=(
                latency_scenario.historical_zero_probability
            ),
            cadence=cadence,
        )
        latency_latest = pd.Timestamp(latency_batch.first_cycle["time"].max())
        provisional = latency_batch.first_cycle.copy()
        provisional["latest_available_time"] = latency_latest
        cutoff_days = 7 if cadence == "weekly" else 31
        delayed_days = 21 if cadence == "weekly" else 90
        provisional["analysis_cutoff"] = latency_latest - pd.Timedelta(
            days=cutoff_days
        )
        provisional["source_period_coverage"] = 0.8
        analysis_as_of = _simulation_as_of(
            latency_batch.first_cycle,
            cadence,
        )
        provisional_alerts = _v3_alerts(
            provisional,
            evaluation_config,
            analysis_as_of,
        )
        delayed_alerts = _v3_alerts(
            latency_batch.second_cycle,
            evaluation_config,
            (latency_latest + pd.Timedelta(days=delayed_days)).date(),
        )
        provisional_true = len(provisional_alerts & latency_batch.true_series)
        provisional_true_alert_count += provisional_true
        delayed_alert_count += len(delayed_alerts)
        latency_guards[cadence] = {
            "provisional_true_alerts_after_cutoff": provisional_true,
            "delayed_feed_current_alerts": len(delayed_alerts),
        }
    scenario_manifest = [_scenario_payload(scenario) for scenario in scenarios]
    protocol_payload = {
        "protocol_version": "v3.2_weekly_monthly_multi_horizon",
        "batches_per_scenario": batches,
        "series_per_class": series_per_class,
        "seed": seed,
        "scenarios": scenario_manifest,
        "minimum_complete_null_families": minimum_complete_null_families,
        "minimum_complete_null_families_per_cadence": (
            minimum_complete_null_families_per_cadence
        ),
        "automatic_q_grid": list(automatic_q_grid),
        "null_stress_strata": list(NULL_STRESS_STRATA),
        "paired_rare_no_structural_zero_control": rare_scenario_entry is not None,
    }
    result = {
        "method": "stratified_seasonal_overdispersed_weekly_monthly_simulation_v3_2",
        "config_hash": hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "calibration_definition_hash": calibration_definition_hash(config),
        "simulation_protocol_hash": hashlib.sha256(
            json.dumps(protocol_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "evaluation_workers": 1,
        "seed": seed,
        "batches_per_scenario": batches,
        "scenario_count": len(scenarios),
        "series_per_class_per_batch": series_per_class,
        "total_complete_null_series": aggregate_null_trials,
        "total_true_anomaly_series": batches * series_per_class * len(scenarios),
        "primary_scenario": primary.key,
        "scenarios": scenario_results,
        "calibration_groups": calibration_groups,
        "precision": {
            "binary_interval_method": "wilson_score",
            "confidence_level": 0.95,
            "complete_null_family_trials": len(all_null_family_discoveries),
            "minimum_complete_null_families": minimum_complete_null_families,
            "minimum_nonzero_empirical_fdr_step": round(
                1.0 / len(all_null_family_discoveries), 6
            ),
            "sufficient_family_trials": (
                len(all_null_family_discoveries) >= minimum_complete_null_families
            ),
            "complete_null_family_trials_by_automation_group": {
                key: int(value.get("family_trials", 0))
                for key, value in group_tallies.items()
            },
            "minimum_complete_null_families_per_cadence": (
                minimum_complete_null_families_per_cadence
            ),
            "note": (
                "Complete-null family FDR is binary per independent scenario batch; "
                "mixed-family FDR is descriptive because discovery counts vary by batch."
            ),
        },
        "fdr_assessment": {
            "nominal_fdr": nominal_fdr,
            "point_estimate_decision": "passed" if point_estimate_passed else "failed",
            "confidence_bound_decision": (
                "passed" if confidence_bound_passed else "inconclusive"
            ),
            "overall_calibration_decision": (
                "passed" if point_estimate_passed and confidence_bound_passed else "failed"
            ),
            "diagnosis": fdr_diagnosis,
            "nominal_rate_within_observed_ci_95": nominal_within_interval,
            "expected_families_with_discovery_at_nominal": round(
                nominal_fdr * len(all_null_family_discoveries), 3
            ),
            "observed_families_with_discovery": sum(all_null_family_discoveries),
            "probability_of_exact_observed_count_under_independent_nominal_model": round(
                _binomial_probability_exact(
                    sum(all_null_family_discoveries),
                    len(all_null_family_discoveries),
                    nominal_fdr,
                ),
                6,
            ),
            "binomial_reference_note": (
                "Diagnostic reference only: simulated series and batches are independent; "
                "the four scenario strata intentionally have different count distributions."
            ),
            "projected_family_trials_for_upper_95_lte_nominal": projected_family_trials,
            "upper_bound_projection_status": (
                "finite_projection_available"
                if projected_family_trials is not None
                else "no_finite_projection_at_observed_rate"
            ),
            "upper_bound_projection_reason": (
                None
                if projected_family_trials is not None
                else "The observed rate is at or above the target; increasing sample "
                "size alone cannot move a confidence upper bound below that target "
                "while holding the observed rate fixed."
            ),
            "projected_batches_per_scenario": (
                None
                if projected_family_trials is None
                else math.ceil(projected_family_trials / len(scenarios))
            ),
            "projection_assumption": (
                "Holds the observed family-discovery rate fixed, rounds projected "
                "discoveries upward, and is a planning estimate rather than a guarantee."
            ),
            "estimation_precision_plan": {
                "planning_rate": nominal_fdr,
                "maximum_wilson_95_half_width": 0.015,
                "required_family_trials": estimation_precision_trials,
                "purpose": (
                    "Estimates the family-discovery rate to about +/-1.5 percentage "
                    "points; it does not make an upper-bound acceptance claim."
                ),
            },
            "null_discoveries": null_discovery_details,
            "discoveries_using_empirical_fallback": sum(
                int(detail["fallback_used"]) for detail in null_discovery_details
            ),
            "p_value_calibration": pvalue_calibration,
            "anti_conservative_pvalue_strata": anti_conservative_strata,
            "rare_count_zero_inflation_control": rare_zero_control,
            "model_misspecification_assessment": (
                "rare_tail_anti_conservatism_persists_without_structural_zeros"
                if rare_main_anti_conservative and rare_control_anti_conservative
                else "rare_tail_anti_conservatism_specific_to_zero_inflation_stress"
                if rare_main_anti_conservative
                else "no_rare_tail_anti_conservatism_identified"
            ),
            "fallback_bias_assessment": (
                "not_identified_in_null_discoveries"
                if not any(
                    detail["fallback_used"] for detail in null_discovery_details
                )
                else "fallback_present_in_null_discoveries"
            ),
        },
        "guarded_auto": {
            "purpose": (
                "Candidate gate for minimizing manual intervention; it does not change "
                "the q<=0.05 review-signal calibration or publication policy."
            ),
            "criteria": [
                "current data",
                "stable completed primary fit (empirical fallback excluded)",
                "effect threshold passed",
                "syntactically valid HTTP(S) evidence link",
                "q_value <= 0.01",
            ],
            "evidence_fixture_note": (
                "Simulation uses a deterministic syntactically valid evidence URL; "
                "the backtest does not test source authority or network availability."
            ),
            "nominal_q": guarded_auto_nominal_q,
            "complete_null_family_discovery_rate": guarded_auto_family_interval[
                "estimate"
            ],
            "complete_null_family_discovery_rate_ci_95": guarded_auto_family_interval,
            "families_with_candidate": sum(guarded_auto_null_family_discoveries),
            "family_trials": len(guarded_auto_null_family_discoveries),
            "projected_family_trials_for_upper_95_lte_nominal": (
                guarded_auto_projected_trials
            ),
            "upper_bound_projection_status": (
                "finite_projection_available"
                if guarded_auto_projected_trials is not None
                else "no_finite_projection_at_observed_rate"
            ),
            "sensitivity_preserved_within_5pp": sensitivity_preservation,
            "false_candidate_control_supported": guarded_auto_false_control_supported,
            "automation_evidence_decision": (
                "supported" if guarded_auto_supported else "not_yet_supported"
            ),
            "reason": (
                "Both the 95% false-candidate bound and primary/rare sensitivity "
                "retention must pass; a zero point estimate alone is insufficient."
            ),
        },
        "v3": {
            "empirical_fdr_complete_null": family_interval["estimate"],
            "empirical_fdr_complete_null_ci_95": family_interval,
            "mean_empirical_fdr_mixed": round(
                _mean_or_zero(all_mixed_false_discovery_ratios), 6
            ),
            "false_positive_rate": aggregate_fpr_interval["estimate"],
            "false_positive_rate_ci_95": aggregate_fpr_interval,
            "sensitivity": primary_sensitivity,
            "sensitivity_ci_95": primary_result["sensitivity_ci_95"],
            "median_detection_delay_source_periods": primary_median_delay,
            "detected_true_anomalies": primary_result["detected_true_anomalies"],
            "false_alerts_complete_null": aggregate_v3_false,
        },
        "v2_comparator": {
            "scope": weekly_comparator.key if weekly_comparator else None,
            "false_positive_rate": v2_false_positive_rate,
            "false_positive_rate_ci_95": primary_v2_fpr_interval,
            "sensitivity": v2_sensitivity,
            "sensitivity_ci_95": primary_v2_sensitivity_interval,
            "detected_true_anomalies": primary_v2_true,
            "false_alerts_complete_null": primary_v2_false,
        },
        "comparison": {
            "scope": weekly_comparator.key if weekly_comparator else None,
            "false_positive_reduction": (
                None
                if false_positive_reduction is None
                else round(false_positive_reduction, 6)
            ),
            "sensitivity_change_percentage_points": (
                None
                if v2_sensitivity is None
                else round((primary_sensitivity - v2_sensitivity) * 100.0, 3)
            ),
        },
        "latency_guard": {
            "provisional_true_alerts_after_cutoff": provisional_true_alert_count,
            "delayed_feed_current_alerts": delayed_alert_count,
            "by_cadence": latency_guards,
            "source_period_coverage_required": float(
                config.get("data_latency", {}).get("minimum_source_period_coverage", 0.8)
            ),
        },
        "diagnostic_model_evaluation_calls": diagnostic_model_evaluation_calls,
    }
    result["acceptance"] = {
        "empirical_fdr_point_lte_5pct": point_estimate_passed,
        "empirical_fdr_upper_95_lte_5pct": confidence_bound_passed,
        "complete_null_precision_sufficient": result["precision"][
            "sufficient_family_trials"
        ],
        "primary_scenario_detection_gte_80pct": primary_sensitivity >= 0.80,
        "median_delay_lte_1_cycle": (
            primary_median_delay is not None and primary_median_delay <= 1.0
        ),
        "false_positive_reduction_gte_30pct": bool(
            false_positive_reduction is not None
            and false_positive_reduction >= 0.30
        ),
        "sensitivity_drop_lte_5pp": bool(
            v2_sensitivity is not None
            and primary_sensitivity >= v2_sensitivity - 0.05
        ),
        "provisional_period_does_not_leak": not (
            provisional_true_alert_count
        ),
        "delayed_feed_does_not_create_current_alert": not delayed_alert_count,
        "weekly_automation_group_supported": (
            calibration_groups["weekly.common_count"]["status"] == "supported"
        ),
        "monthly_automation_group_supported": (
            calibration_groups["monthly.common_count"]["status"] == "supported"
        ),
    }
    result["passed"] = all(result["acceptance"].values())
    return result


__all__ = [
    "DEFAULT_SCENARIOS",
    "SimulationBatch",
    "SimulationScenario",
    "projected_trials_for_wilson_half_width",
    "projected_trials_for_wilson_upper",
    "run_backtest",
    "simulate_weekly_batch",
    "wilson_interval",
]
