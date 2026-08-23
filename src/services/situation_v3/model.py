"""Frequency-aware robust quasi-Poisson analysis for Situation Room v3.

The implementation is intentionally dependency-light and deterministic.  It
uses robust IRLS with a quasi-Poisson variance estimate, seasonal harmonics,
optional trend, and a one-sided predictive exceedance probability.  Correlated
views of the same baseline are not counted as independent detector votes.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from src.core.logging import get_logger

from .contracts import (
    AnomalyAssessment,
    EvidenceLink,
    ObservationComparison,
    PublicHealthRisk,
    RecentPoint,
    SignalAssessment,
    SignalIdentity,
    SituationSignalV3,
)


CADENCE_DEFAULTS: dict[str, dict[str, Any]] = {
    "daily": {
        "window_periods": 28,
        "periods_per_year": 365.25,
        "freshness_days": 14,
        "label": "Last 28 days",
        "minimum_observations": 730,
    },
    "weekly": {
        "window_periods": 4,
        "periods_per_year": 52.18,
        "freshness_days": 35,
        "label": "Last 4 weeks",
        "minimum_observations": 156,
    },
    "monthly": {
        "window_periods": 1,
        "periods_per_year": 12.0,
        "freshness_days": 75,
        "label": "Latest complete month",
        "minimum_observations": 36,
    },
}

RESPIRATORY_DISEASE_IDS = {"D038", "D142", "D004"}
COUNT_ACTIVITY_METRICS = {
    "case_notifications",
    "laboratory_diagnoses",
    "reported_diagnoses",
    "clinical_diagnoses",
    "sentinel_case_notifications",
    "organism_detections",
    "survey_positive_cases",
}
SEVERITY_METRICS = {"hospitalized_case_notifications"}
logger = get_logger(__name__)


@dataclass(frozen=True)
class SeriesV3Evaluation:
    signal: SituationSignalV3 | None
    rejection_reason: str | None
    ledger: dict[str, Any]


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_http_url(value: Any) -> str | None:
    """Return a publishable source URL without leaking pandas null sentinels."""

    if value is None or pd.isna(value):
        return None
    candidate = str(value).strip()
    if not candidate or candidate.lower() in {"nan", "none", "null", "nat"}:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _json_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or isinstance(item, (str, int, bool)):
            output[str(key)] = item
        elif (number := _finite_number(item)) is not None:
            output[str(key)] = number
        else:
            output[str(key)] = str(item)
    return output


def cadence_from_frame(frame: pd.DataFrame) -> str:
    raw = str(frame.iloc[-1].get("temporal_granularity") or "").strip().lower()
    if raw in CADENCE_DEFAULTS:
        return raw
    ordered = pd.to_datetime(frame["time"], errors="coerce", utc=True).dropna().sort_values()
    if len(ordered) < 2:
        return "unknown"
    days = ordered.diff().dropna().dt.total_seconds().median() / 86400
    if 5 <= days <= 10:
        return "weekly"
    if 25 <= days <= 35:
        return "monthly"
    if 0.5 <= days <= 2:
        return "daily"
    return "unknown"


def canonical_geography_key(row: pd.Series | dict[str, Any]) -> str:
    raw = str(row.get("geography_key") or "").strip()
    country = str(row.get("country_code") or "").strip().upper()
    source = str(row.get("source_system") or "").strip()
    national_aliases = {
        "national",
        f"country:{country}:national" if country else "",
        f"source:{source}:reporting-area:total" if source else "",
    }
    if country and raw in national_aliases:
        return f"country:{country}:national"
    return raw or (f"country:{country}:unknown" if country else "unknown")


def prepare_frame_v3(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], errors="coerce", utc=True)
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work[work["time"].notna() & work["value"].notna()].copy()
    work["canonical_geography_key"] = work.apply(canonical_geography_key, axis=1)
    sort_columns = [column for column in ("time", "updated_at", "id") if column in work.columns]
    return work.sort_values(sort_columns, kind="stable")


def _period_key(stamp: pd.Timestamp, cadence: str) -> Any:
    plain = stamp.tz_convert(None) if stamp.tzinfo else stamp
    if cadence == "monthly":
        return plain.to_period("M")
    if cadence == "weekly":
        iso = plain.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return plain.normalize()


def _period_end(stamp: pd.Timestamp | date, cadence: str) -> date:
    plain = pd.Timestamp(stamp).tz_localize(None)
    if cadence == "monthly":
        return plain.to_period("M").end_time.date()
    return plain.date()


def _period_age_days(stamp: pd.Timestamp | date, cadence: str, as_of: date) -> int:
    return max(0, (as_of - _period_end(stamp, cadence)).days)


def _regular_series(
    work: pd.DataFrame,
    cadence: str,
    window_periods: int,
    value_column: str = "value",
) -> tuple[pd.Series, pd.DatetimeIndex, float, list[str]]:
    data = work[["time", value_column, "geography_key"]].copy()
    data["period_key"] = data["time"].map(lambda value: _period_key(pd.Timestamp(value), cadence))
    source_keys = sorted({str(value) for value in data["geography_key"].dropna()})
    # Canonical aliases can contain the same source observation. Stable sorting
    # above makes the winning row reproducible without summing aliases.
    data = data.drop_duplicates("period_key", keep="last")
    latest = pd.Timestamp(data["time"].max()).tz_convert(None)
    earliest = pd.Timestamp(data["time"].min()).tz_convert(None)
    lookup = data.set_index("period_key")[value_column]
    if cadence == "monthly":
        keys = pd.period_range(start=earliest.to_period("M"), end=latest.to_period("M"), freq="M")
        dates = keys.to_timestamp(how="start")
    elif cadence == "weekly":
        dates = pd.date_range(start=earliest.normalize(), end=latest.normalize(), freq="7D")
        keys = pd.Index([_period_key(stamp, cadence) for stamp in dates])
    else:
        dates = pd.date_range(start=earliest.normalize(), end=latest.normalize(), freq="D")
        keys = pd.Index([stamp.normalize() for stamp in dates])
    values = pd.to_numeric(lookup.reindex(keys), errors="coerce").reset_index(drop=True)
    comparison = values.iloc[-window_periods * 2 :]
    completeness = float(comparison.notna().mean()) if len(comparison) else 0.0
    return values, pd.DatetimeIndex(dates), completeness, source_keys


def _design_matrix(
    dates: pd.DatetimeIndex,
    cadence: str,
    *,
    origin: pd.Timestamp,
    include_trend: bool,
) -> np.ndarray:
    columns: list[np.ndarray] = [np.ones(len(dates), dtype=float)]
    elapsed_days = np.asarray((dates - origin).days, dtype=float)
    if include_trend:
        columns.append(elapsed_days / 365.25)
    if cadence == "monthly":
        position = (np.asarray(dates.month, dtype=float) - 1.0) / 12.0
    elif cadence == "weekly":
        position = np.asarray([stamp.isocalendar().week for stamp in dates], dtype=float) / 52.18
    else:
        position = (np.asarray(dates.dayofyear, dtype=float) - 1.0) / 365.25
    for harmonic in (1.0, 2.0):
        columns.append(np.sin(2.0 * math.pi * harmonic * position))
        columns.append(np.cos(2.0 * math.pi * harmonic * position))
    if cadence == "daily":
        weekdays = np.asarray(dates.dayofweek)
        columns.extend((weekdays == weekday).astype(float) for weekday in range(1, 7))
    return np.column_stack(columns)


def _fit_robust_quasi_poisson(
    values: np.ndarray,
    dates: pd.DatetimeIndex,
    prediction_dates: pd.DatetimeIndex,
    cadence: str,
    periods_per_year: float,
    exposures: np.ndarray | None = None,
    prediction_exposures: np.ndarray | None = None,
) -> dict[str, Any] | None:
    valid = np.isfinite(values) & (values >= 0)
    if exposures is not None:
        valid &= np.isfinite(exposures) & (exposures > 0)
    y = values[valid].astype(float)
    fit_dates = dates[valid]
    if len(y) < max(24, int(periods_per_year * 2)):
        return None
    origin = pd.Timestamp(fit_dates[0])
    include_trend = len(y) >= int(periods_per_year * 2)
    x = _design_matrix(fit_dates, cadence, origin=origin, include_trend=include_trend)
    x_pred = _design_matrix(prediction_dates, cadence, origin=origin, include_trend=include_trend)
    fit_offset = (
        np.log(np.asarray(exposures, dtype=float)[valid])
        if exposures is not None
        else np.zeros(len(y), dtype=float)
    )
    if prediction_exposures is not None:
        prediction_exposures = np.asarray(prediction_exposures, dtype=float)
        if (
            len(prediction_exposures) != len(prediction_dates)
            or not np.isfinite(prediction_exposures).all()
            or (prediction_exposures <= 0).any()
        ):
            return None
        prediction_offset = np.log(prediction_exposures)
    else:
        prediction_offset = np.zeros(len(prediction_dates), dtype=float)
    if len(y) <= x.shape[1] + 3:
        return None
    beta = np.zeros(x.shape[1], dtype=float)
    beta[0] = math.log(max(float(np.sum(y) / np.sum(np.exp(fit_offset))), 1e-8))
    robust = np.ones(len(y), dtype=float)
    converged = False
    ridge = np.eye(x.shape[1], dtype=float) * 1e-8
    for _ in range(40):
        eta = np.clip(x @ beta + fit_offset, -20.0, 20.0)
        mu = np.maximum(np.exp(eta), 1e-8)
        working = eta + (y - mu) / mu - fit_offset
        weights = np.maximum(mu * robust, 1e-8)
        xtwx = x.T @ (weights[:, None] * x) + ridge
        xtwz = x.T @ (weights * working)
        try:
            next_beta = np.linalg.solve(xtwx, xtwz)
        except np.linalg.LinAlgError:
            next_beta = np.linalg.lstsq(xtwx, xtwz, rcond=None)[0]
        pearson = (y - mu) / np.sqrt(mu)
        absolute = np.abs(pearson)
        robust = np.where(absolute <= 2.58, 1.0, 2.58 / np.maximum(absolute, 1e-9))
        if float(np.max(np.abs(next_beta - beta))) < 1e-7:
            beta = next_beta
            converged = True
            break
        beta = next_beta
    eta = np.clip(x @ beta + fit_offset, -20.0, 20.0)
    mu = np.maximum(np.exp(eta), 1e-8)
    dof = max(1, len(y) - x.shape[1])
    dispersion = max(1.0, float(np.sum(robust * ((y - mu) ** 2 / mu)) / dof))
    # Keep the robustly weighted estimate for the established common-count
    # Gaussian path, but do not use it to define a rare-count tail.  Huber
    # weights deliberately suppress large Pearson residuals; reusing those
    # weights as a predictive dispersion estimate therefore trims the very
    # tail whose probability we are trying to measure.  The unweighted
    # Pearson estimate, with the regression residual degrees of freedom, is
    # the conventional quasi-likelihood estimate for predictive dispersion.
    rare_tail_dispersion = max(
        1.0,
        float(np.sum((y - mu) ** 2 / mu) / dof),
    )
    weights = np.maximum(mu * robust, 1e-8)
    try:
        covariance = np.linalg.inv(x.T @ (weights[:, None] * x) + ridge) * dispersion
    except np.linalg.LinAlgError:
        covariance = np.linalg.pinv(x.T @ (weights[:, None] * x) + ridge) * dispersion
    pred_mu = np.maximum(
        np.exp(np.clip(x_pred @ beta + prediction_offset, -20.0, 20.0)),
        1e-8,
    )
    parameter_variance = np.asarray(
        [mean**2 * float(row @ covariance @ row) for mean, row in zip(pred_mu, x_pred, strict=True)]
    )
    predictive_variance = np.maximum(dispersion * pred_mu + parameter_variance, 1e-8)
    # Predictions in one comparison window share the same fitted parameters.
    # Summing only pointwise variances drops their positive parameter
    # covariance and makes an aggregated count threshold anti-conservative.
    aggregate_gradient = np.sum(pred_mu[:, None] * x_pred, axis=0)
    aggregate_parameter_variance = float(
        aggregate_gradient @ covariance @ aggregate_gradient
    )
    aggregate_predictive_variance = max(
        float(dispersion * np.sum(pred_mu) + aggregate_parameter_variance),
        1e-8,
    )
    # A first-order unconditional predictive variance for a future aggregate:
    # E[Var(Y* | beta)] + Var(E[Y* | beta]).  The second term carries fitted
    # coefficient uncertainty and is essential when the expected count is low.
    rare_tail_aggregate_parameter_variance = float(
        aggregate_parameter_variance * rare_tail_dispersion / dispersion
    )
    rare_tail_aggregate_predictive_variance = max(
        float(
            rare_tail_dispersion * np.sum(pred_mu)
            + rare_tail_aggregate_parameter_variance
        ),
        1e-8,
    )
    return {
        "expected_points": pred_mu,
        "predictive_variance": predictive_variance,
        "aggregate_predictive_variance": aggregate_predictive_variance,
        "aggregate_parameter_variance": aggregate_parameter_variance,
        "rare_tail_aggregate_predictive_variance": (
            rare_tail_aggregate_predictive_variance
        ),
        "rare_tail_aggregate_parameter_variance": (
            rare_tail_aggregate_parameter_variance
        ),
        "dispersion": dispersion,
        "rare_tail_dispersion": rare_tail_dispersion,
        "converged": converged,
        "parameter_count": int(x.shape[1]),
        "history_count": int(len(y)),
        "include_trend": include_trend,
        "uses_exposure_offset": exposures is not None,
        # Private fit state is retained only long enough to build the
        # deterministic multi-horizon predictive distribution. It is stripped
        # before diagnostics are serialized into the public contract.
        "_beta": beta,
        "_covariance": covariance,
        "_prediction_design": x_pred,
        "_prediction_offset": prediction_offset,
    }


def _fit_seasonal_empirical_baseline(
    values: np.ndarray,
    dates: pd.DatetimeIndex,
    prediction_dates: pd.DatetimeIndex,
    cadence: str,
) -> dict[str, Any] | None:
    """Fallback to a transparent seasonal neighborhood when IRLS is unstable."""

    valid = np.isfinite(values) & (values >= 0)
    history_values = np.asarray(values, dtype=float)[valid]
    history_dates = dates[valid]
    if len(history_values) < 24:
        return None

    def seasonal_distance(left: pd.Timestamp, right: pd.Timestamp) -> int:
        if cadence == "monthly":
            raw = abs(int(left.month) - int(right.month))
            return min(raw, 12 - raw)
        if cadence == "weekly":
            raw = abs(int(left.isocalendar().week) - int(right.isocalendar().week))
            # ISO years can contain week 53. Treat the seasonal coordinate as a
            # 53-position ring so week 53 remains adjacent to week 1 without
            # collapsing week 1 and week 53 into the same season.
            return min(raw, 53 - min(raw, 53))
        raw = abs(int(left.dayofyear) - int(right.dayofyear))
        return min(raw, 366 - min(raw, 366))

    expected_points: list[float] = []
    predictive_variance: list[float] = []
    sample_counts: list[int] = []
    for prediction_date in prediction_dates:
        candidates = np.asarray(
            [
                value
                for value, stamp in zip(history_values, history_dates, strict=True)
                if (
                    seasonal_distance(pd.Timestamp(stamp), pd.Timestamp(prediction_date))
                    <= {"monthly": 1, "weekly": 2, "daily": 28}[cadence]
                    and (
                        cadence != "daily"
                        or pd.Timestamp(stamp).dayofweek
                        == pd.Timestamp(prediction_date).dayofweek
                    )
                )
            ],
            dtype=float,
        )
        if len(candidates) < 8:
            return None
        median = float(np.median(candidates))
        mad = float(np.median(np.abs(candidates - median)))
        if mad > 0:
            robust_sigma = 1.4826 * mad
            candidates = np.clip(
                candidates,
                max(0.0, median - 4.0 * robust_sigma),
                median + 4.0 * robust_sigma,
            )
        mean = max(float(np.mean(candidates)), 1e-8)
        variance = (
            float(np.var(candidates, ddof=1)) if len(candidates) > 1 else mean
        )
        expected_points.append(mean)
        predictive_variance.append(max(mean, variance) + variance / len(candidates))
        sample_counts.append(len(candidates))
    expected_array = np.asarray(expected_points, dtype=float)
    variance_array = np.asarray(predictive_variance, dtype=float)
    aggregate_mean = max(float(np.sum(expected_array)), 1e-8)
    aggregate_variance = max(float(np.sum(variance_array)), 1e-8)
    return {
        "expected_points": expected_array,
        "predictive_variance": variance_array,
        "aggregate_predictive_variance": aggregate_variance,
        "dispersion": max(1.0, aggregate_variance / aggregate_mean),
        "converged": True,
        "parameter_count": 0,
        "history_count": int(len(history_values)),
        "include_trend": False,
        "uses_exposure_offset": False,
        "fallback": "seasonal_empirical_v1",
        "seasonal_sample_counts": sample_counts,
        "primary_fit_status": "non_converged",
    }


def _unit_scale(unit: str) -> float:
    normalized = unit.strip().lower().replace(" ", "_")
    if normalized in {"percent", "percentage", "%"}:
        return 100.0
    if normalized in {"per_100,000", "per_100000", "per100000"}:
        return 100_000.0
    return 1.0


def _configured_effect_passed(
    *,
    current: float,
    absolute_change: float,
    relative_change: float | None,
    rules: dict[str, Any] | None,
) -> bool:
    if not rules:
        return False
    return bool(
        current
        >= float(rules.get("minimum_current", rules.get("minimum_current_cases", -math.inf)))
        and absolute_change >= float(rules.get("minimum_absolute_increase", 0.0))
        and relative_change is not None
        and relative_change >= float(rules.get("minimum_relative_increase_pct", 0.0))
    )


def _count_upper_tail_probability(
    observed: float,
    mean: float,
    predictive_variance: float,
) -> float:
    """Moment-matched Poisson/NB predictive upper tail without SciPy.

    ``predictive_variance`` is unconditional: it contains both count-process
    variance and fitted-mean parameter uncertainty.  Matching the first two
    moments to a negative binomial is the standard Gamma-Poisson predictive
    approximation; the Poisson is recovered when variance does not exceed the
    mean.
    """

    threshold = max(0, int(math.ceil(observed - 1e-12)))
    mean = max(float(mean), 1e-12)
    if threshold <= 0:
        return 1.0
    predictive_variance = max(float(predictive_variance), mean)
    if predictive_variance <= mean * 1.000001:
        term = math.exp(-mean + threshold * math.log(mean) - math.lgamma(threshold + 1))
        total = term
        index = threshold
        while index < 10_000:
            index += 1
            term *= mean / index
            total += term
            if term <= max(total, 1e-300) * 1e-14:
                break
        return min(1.0, max(0.0, total))
    size = mean * mean / max(predictive_variance - mean, 1e-12)
    probability = size / (size + mean)
    log_term = (
        math.lgamma(threshold + size)
        - math.lgamma(size)
        - math.lgamma(threshold + 1)
        + size * math.log(probability)
        + threshold * math.log1p(-probability)
    )
    term = math.exp(log_term)
    total = term
    index = threshold
    while index < 10_000:
        term *= ((index + size) / (index + 1.0)) * (1.0 - probability)
        index += 1
        total += term
        if term <= max(total, 1e-300) * 1e-14:
            break
    return min(1.0, max(0.0, total))


def _positive_semidefinite(matrix: np.ndarray) -> np.ndarray:
    """Return a symmetric positive-semidefinite covariance approximation."""

    symmetric = (np.asarray(matrix, dtype=float) + np.asarray(matrix, dtype=float).T) / 2.0
    values, vectors = np.linalg.eigh(symmetric)
    return vectors @ np.diag(np.maximum(values, 1e-12)) @ vectors.T


def _multi_horizon_predictive_assessment(
    *,
    observed_points: np.ndarray,
    model_result: dict[str, Any],
    horizons: list[int],
    seed_text: str,
    draws: int,
) -> dict[str, Any] | None:
    """Calibrate correlated count horizons with one deterministic omnibus p value.

    Coefficient draws are shared by all future points and therefore retain the
    positive parameter correlation between overlapping windows. Conditional
    count draws use a Gamma-Poisson representation matched to the untrimmed
    Pearson dispersion. The maximum standardized exceedance supplies one test
    per series rather than several uncorrected detector votes.
    """

    required = {"_beta", "_covariance", "_prediction_design", "_prediction_offset"}
    if not required.issubset(model_result):
        return None
    observed = np.asarray(observed_points, dtype=float)
    design = np.asarray(model_result["_prediction_design"], dtype=float)
    offset = np.asarray(model_result["_prediction_offset"], dtype=float)
    beta = np.asarray(model_result["_beta"], dtype=float)
    covariance = np.asarray(model_result["_covariance"], dtype=float)
    if len(observed) != len(design) or len(offset) != len(design):
        return None
    valid_horizons = sorted(
        {int(value) for value in horizons if 1 <= int(value) <= len(observed)}
    )
    if not valid_horizons:
        return None
    sample_count = max(512, min(65_536, int(draws)))
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big", signed=False))
    robust_dispersion = max(float(model_result.get("dispersion", 1.0)), 1.0)
    predictive_dispersion = max(
        float(model_result.get("rare_tail_dispersion", robust_dispersion)),
        1.0,
    )
    scaled_covariance = _positive_semidefinite(
        covariance * predictive_dispersion / robust_dispersion
    )
    coefficient_draws = rng.multivariate_normal(
        beta,
        scaled_covariance,
        size=sample_count,
        check_valid="ignore",
    )
    means = np.exp(
        np.clip(coefficient_draws @ design.T + offset[None, :], -20.0, 20.0)
    )
    if predictive_dispersion <= 1.000001:
        simulated = rng.poisson(means).astype(float)
        tail_model = "poisson_parameter_mixture"
    else:
        sizes = np.maximum(means / (predictive_dispersion - 1.0), 1e-8)
        probability = np.clip(sizes / (sizes + means), 1e-10, 1.0 - 1e-10)
        simulated = rng.negative_binomial(sizes, probability).astype(float)
        tail_model = "gamma_poisson_parameter_mixture"

    horizon_rows: list[dict[str, Any]] = []
    simulated_scores: list[np.ndarray] = []
    for horizon in valid_horizons:
        simulated_aggregate = simulated[:, -horizon:].sum(axis=1)
        observed_aggregate = float(observed[-horizon:].sum())
        mean = float(np.mean(simulated_aggregate))
        standard_deviation = max(float(np.std(simulated_aggregate, ddof=1)), 1e-9)
        observed_score = (observed_aggregate - mean) / standard_deviation
        score_draws = (simulated_aggregate - mean) / standard_deviation
        simulated_scores.append(score_draws)
        horizon_rows.append(
            {
                "horizon_periods": horizon,
                "observed": round(observed_aggregate, 6),
                "expected": round(mean, 6),
                "predictive_upper_95": round(
                    float(np.quantile(simulated_aggregate, 0.95)), 6
                ),
                "standardized_exceedance": round(observed_score, 6),
                "marginal_p_value": round(
                    (float(np.count_nonzero(simulated_aggregate >= observed_aggregate)) + 1.0)
                    / (sample_count + 1.0),
                    8,
                ),
            }
        )
    observed_scores = np.asarray(
        [float(row["standardized_exceedance"]) for row in horizon_rows],
        dtype=float,
    )
    selected_index = int(np.argmax(observed_scores))
    omnibus_draws = np.max(np.column_stack(simulated_scores), axis=1)
    omnibus_observed = float(observed_scores[selected_index])
    omnibus_p = (
        float(np.count_nonzero(omnibus_draws >= omnibus_observed)) + 1.0
    ) / (sample_count + 1.0)
    selected = horizon_rows[selected_index]
    return {
        "selected_horizon_periods": int(selected["horizon_periods"]),
        "observed": float(selected["observed"]),
        "expected": float(selected["expected"]),
        "predictive_upper_95": float(selected["predictive_upper_95"]),
        "standardized_exceedance": omnibus_observed,
        "raw_p_value": float(omnibus_p),
        "draws": sample_count,
        "tail_model": tail_model,
        "dispersion": predictive_dispersion,
        "horizons": horizon_rows,
        "decision_role": "single_correlated_omnibus_test",
    }


def _hurdle_negative_binomial_tail(
    *,
    history_values: np.ndarray,
    observed: float,
    horizon: int,
    seed_text: str,
    draws: int,
) -> dict[str, Any] | None:
    """Return a deterministic shadow p value for a hurdle count model."""

    history = np.asarray(history_values, dtype=float)
    history = history[np.isfinite(history) & (history >= 0)]
    positive = history[history > 0]
    if len(history) < 24 or len(positive) < 8 or horizon < 1:
        return None
    positive_probability = float(len(positive) / len(history))
    positive_mean = float(np.mean(positive))
    positive_variance = (
        float(np.var(positive, ddof=1)) if len(positive) > 1 else positive_mean
    )
    sample_count = max(512, min(65_536, int(draws)))
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big", signed=False))
    active = rng.random((sample_count, horizon)) < positive_probability
    if positive_variance <= positive_mean * 1.000001:
        positive_draws = rng.poisson(max(positive_mean, 1e-8), size=active.shape)
        distribution = "zero_truncated_poisson"
    else:
        size = positive_mean**2 / max(positive_variance - positive_mean, 1e-8)
        probability = size / (size + positive_mean)
        positive_draws = rng.negative_binomial(size, probability, size=active.shape)
        distribution = "zero_truncated_negative_binomial"
    zero_positive = active & (positive_draws == 0)
    while bool(np.any(zero_positive)):
        if distribution == "zero_truncated_poisson":
            replacements = rng.poisson(
                max(positive_mean, 1e-8), size=int(np.count_nonzero(zero_positive))
            )
        else:
            replacements = rng.negative_binomial(
                size, probability, size=int(np.count_nonzero(zero_positive))
            )
        positive_draws[zero_positive] = replacements
        zero_positive = active & (positive_draws == 0)
    simulated = np.where(active, positive_draws, 0).sum(axis=1)
    p_value = (
        float(np.count_nonzero(simulated >= observed)) + 1.0
    ) / (sample_count + 1.0)
    return {
        "raw_p_value": round(float(p_value), 8),
        "expected": round(float(np.mean(simulated)), 6),
        "predictive_upper_95": round(float(np.quantile(simulated, 0.95)), 6),
        "historical_zero_probability": round(1.0 - positive_probability, 6),
        "positive_mean": round(positive_mean, 6),
        "positive_variance": round(positive_variance, 6),
        "positive_distribution": distribution,
        "draws": sample_count,
        "decision_role": "shadow_only",
    }


def _effect_rules(
    config: dict[str, Any],
    identity: pd.Series,
    metric_type: str,
    *,
    is_count: bool,
    detector_tier: str,
    cadence: str,
    expected: float | None,
) -> dict[str, Any] | None:
    thresholds = config.get("thresholds", {})
    rules: dict[str, Any] | None = (
        {
            "minimum_current_cases": thresholds.get("minimum_current_cases", 20),
            "minimum_absolute_increase": thresholds.get("minimum_absolute_increase", 10),
            "minimum_relative_increase_pct": thresholds.get(
                "minimum_relative_increase_pct", 25
            ),
        }
        if is_count
        else dict((thresholds.get("metric_effects") or {}).get(metric_type) or {}) or None
    )
    if detector_tier == "rare_count":
        rare_effect = config.get("v3", {}).get("detector_tiers", {}).get(
            "rare_count_effect", {}
        )
        rules = {
            "minimum_current_cases": rare_effect.get("minimum_current_cases", 5),
            "minimum_absolute_increase": rare_effect.get(
                "minimum_absolute_increase", 3
            ),
            "minimum_relative_increase_pct": rare_effect.get(
                "minimum_relative_increase_pct", 100
            ),
        }
    cadence_gates = (
        config.get("v3", {})
        .get("effect_gates", {})
        .get(cadence, {})
        .get(detector_tier, [])
    )
    if isinstance(cadence_gates, list) and expected is not None:
        for gate in cadence_gates:
            if not isinstance(gate, dict):
                continue
            minimum_expected = float(gate.get("minimum_expected", -math.inf))
            maximum_expected = float(gate.get("maximum_expected", math.inf))
            if minimum_expected <= expected < maximum_expected:
                effect = gate.get("effect") or {}
                if isinstance(effect, dict):
                    rules = {**(rules or {}), **effect}
                break
    for special in thresholds.get("special_thresholds") or []:
        if not isinstance(special, dict):
            continue
        selectors = {
            "disease_id": str(identity.get("disease_id") or ""),
            "source_system": str(identity.get("source_system") or ""),
            "metric_type": metric_type,
        }
        if any(
            special.get(key) is not None and str(special[key]) != value
            for key, value in selectors.items()
        ):
            continue
        override = special.get("effect") or {}
        if isinstance(override, dict):
            rules = {**(rules or {}), **override}
    return rules


def _metric_label(metric_type: str) -> str:
    return {
        "case_notifications": "cases",
        "laboratory_diagnoses": "laboratory diagnoses",
        "reported_diagnoses": "reported diagnoses",
        "clinical_diagnoses": "clinical diagnoses",
        "sentinel_case_notifications": "sentinel activity",
        "organism_detections": "detections",
        "survey_positive_cases": "positive cases",
        "hospitalized_case_notifications": "hospital admissions",
        "test_positivity": "test positivity",
    }.get(metric_type, metric_type.replace("_", " "))


def _identity_ledger(first: pd.Series, cadence: str, canonical_key: str, source_keys: list[str]) -> dict[str, Any]:
    return {
        "series_code": str(first.get("series_code") or ""),
        "canonical_geography_key": canonical_key,
        "source_geography_keys": source_keys,
        "dimension_key": str(first.get("dimension_key") or "all"),
        "disease_id": str(first.get("disease_id") or ""),
        "disease_name": str(first.get("disease_name") or first.get("disease_id") or "Unknown"),
        "country_code": first.get("country_code"),
        "country_name": first.get("country_name"),
        "source_system": str(first.get("source_system") or "unknown"),
        "metric_type": str(first.get("metric_type") or "case_notifications"),
        "unit": str(first.get("unit") or "count"),
        "cadence": cadence,
    }


def evaluate_series_v3(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    as_of: date,
) -> SeriesV3Evaluation:
    if frame.empty:
        return SeriesV3Evaluation(None, "empty", {"status": "rejected", "rejection_reason": "empty"})
    work = frame.sort_values("time", kind="stable")
    first = work.iloc[-1]
    cadence = cadence_from_frame(work)
    canonical_key = str(first.get("canonical_geography_key") or canonical_geography_key(first))
    source_keys = sorted({str(value) for value in work.get("geography_key", pd.Series(dtype=str)).dropna()})
    ledger = _identity_ledger(first, cadence, canonical_key, source_keys)
    if cadence not in CADENCE_DEFAULTS:
        return SeriesV3Evaluation(None, "unsupported_frequency", {**ledger, "status": "rejected", "rejection_reason": "unsupported_frequency"})
    settings = {**CADENCE_DEFAULTS[cadence], **(config.get("cadences", {}).get(cadence, {}))}
    raw_latest_value = first.get("latest_available_time")
    raw_latest = (
        pd.Timestamp(raw_latest_value)
        if raw_latest_value is not None and not pd.isna(raw_latest_value)
        else pd.Timestamp(work["time"].max())
    )
    cutoff_value = first.get("analysis_cutoff")
    analysis_cutoff = (
        pd.Timestamp(cutoff_value)
        if cutoff_value is not None and not pd.isna(cutoff_value)
        else None
    )
    if analysis_cutoff is not None:
        if analysis_cutoff.tzinfo is None:
            analysis_cutoff = analysis_cutoff.tz_localize("UTC")
        work = work[work["time"] <= analysis_cutoff]
        if work.empty:
            return SeriesV3Evaluation(
                None,
                "awaiting_mature_period",
                {
                    **ledger,
                    "status": "rejected",
                    "rejection_reason": "awaiting_mature_period",
                    "latest_available_period": raw_latest.date().isoformat(),
                    "analysis_cutoff": analysis_cutoff.date().isoformat(),
                },
            )
        first = work.iloc[-1]
    if cadence in {"daily", "weekly"}:
        as_of_cutoff = pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(days=1)
        work = work[work["time"] < as_of_cutoff]
        if work.empty:
            return SeriesV3Evaluation(
                None,
                "no_observations_on_or_before_as_of",
                {
                    **ledger,
                    "status": "rejected",
                    "rejection_reason": "no_observations_on_or_before_as_of",
                },
            )
        first = work.iloc[-1]
    if cadence == "monthly":
        current_month = pd.Period(as_of, freq="M")
        work = work[work["time"].map(lambda stamp: pd.Timestamp(stamp).tz_convert(None).to_period("M") < current_month)]
        if work.empty:
            return SeriesV3Evaluation(None, "no_complete_month", {**ledger, "status": "rejected", "rejection_reason": "no_complete_month"})
        first = work.iloc[-1]
    configured_window_periods = int(settings["window_periods"])
    unit_hint = str(first.get("unit") or "count").strip().lower()
    multi_horizon_config = (
        config.get("v3", {}).get("detectors", {}).get("multi_horizon", {})
    )
    multi_horizon_enabled = bool(
        isinstance(multi_horizon_config, dict)
        and multi_horizon_config.get("enabled", False)
        and unit_hint == "count"
        and cadence in {"weekly", "monthly"}
    )
    configured_horizons = (
        multi_horizon_config.get("horizons", {}).get(cadence, [])
        if multi_horizon_enabled
        else []
    )
    model_horizons = sorted(
        {
            configured_window_periods,
            *(
                int(value)
                for value in configured_horizons
                if isinstance(value, (int, float)) and int(value) >= 1
            ),
        }
    )
    window_periods = max(model_horizons)
    values, periods, completeness, source_keys = _regular_series(work, cadence, window_periods)
    ledger["source_geography_keys"] = source_keys
    minimum = int(config.get("thresholds", {}).get("minimum_observations", {}).get(cadence, settings["minimum_observations"]))
    valid_count = int(values.notna().sum())
    ledger["observation_count"] = valid_count
    if valid_count < minimum:
        return SeriesV3Evaluation(None, "insufficient_observations", {**ledger, "status": "rejected", "rejection_reason": "insufficient_observations"})
    latest_date = pd.Timestamp(periods[-1]).date()
    age_days = _period_age_days(pd.Timestamp(periods[-1]), cadence, as_of)
    if age_days > int(settings["freshness_days"]):
        return SeriesV3Evaluation(None, "stale", {**ledger, "status": "rejected", "rejection_reason": "stale", "age_days": age_days})
    minimum_completeness = float(config.get("quality", {}).get("minimum_window_completeness", 0.8))
    comparison = values.iloc[-window_periods * 2 :]
    if len(comparison) < window_periods * 2 or comparison.isna().any() or completeness < minimum_completeness:
        return SeriesV3Evaluation(None, "incomplete_comparison_window", {**ledger, "status": "rejected", "rejection_reason": "incomplete_comparison_window"})
    metric_type = str(first.get("metric_type") or "case_notifications")
    unit = str(first.get("unit") or "count")
    is_count = unit.strip().lower() == "count"
    numerator_values = denominator_values = None
    has_rate_components = False
    scale = _unit_scale(unit)
    if not is_count and {"numerator", "denominator"}.issubset(work.columns):
        numerator_series, _, _, _ = _regular_series(work, cadence, window_periods, "numerator")
        denominator_series, _, _, _ = _regular_series(work, cadence, window_periods, "denominator")
        numerator_values = numerator_series.to_numpy(dtype=float)
        denominator_values = denominator_series.to_numpy(dtype=float)
        has_rate_components = bool(
            np.isfinite(numerator_values).all()
            and np.isfinite(denominator_values).all()
            and (numerator_values >= 0).all()
            and (denominator_values > 0).all()
        )
    reducer = "sum" if is_count else "mean"
    current_values = comparison.iloc[-window_periods:].to_numpy(dtype=float)
    previous_values = comparison.iloc[:window_periods].to_numpy(dtype=float)
    if has_rate_components and numerator_values is not None and denominator_values is not None:
        current = float(
            scale
            * np.sum(numerator_values[-window_periods:])
            / np.sum(denominator_values[-window_periods:])
        )
        previous = float(
            scale
            * np.sum(numerator_values[-window_periods * 2 : -window_periods])
            / np.sum(denominator_values[-window_periods * 2 : -window_periods])
        )
    else:
        current = float(np.sum(current_values) if reducer == "sum" else np.mean(current_values))
        previous = float(np.sum(previous_values) if reducer == "sum" else np.mean(previous_values))
    absolute_change = current - previous
    relative_change = ((current - previous) / previous * 100.0) if previous > 0 else None
    fit_status = "completed"
    model_result: dict[str, Any] | None = None
    if is_count or has_rate_components:
        history_values = (
            values.iloc[:-window_periods].to_numpy(dtype=float)
            if is_count
            else numerator_values[:-window_periods]
        )
        history_dates = periods[:-window_periods]
        prediction_dates = periods[-window_periods:]
        model_result = _fit_robust_quasi_poisson(
            history_values,
            history_dates,
            prediction_dates,
            cadence,
            float(settings["periods_per_year"]),
            exposures=(denominator_values[:-window_periods] if has_rate_components else None),
            prediction_exposures=(denominator_values[-window_periods:] if has_rate_components else None),
        )
        if model_result is None:
            fit_status = "insufficient_model_history"
    else:
        fit_status = "context_only_missing_denominator"
    expected = None
    upper = None
    raw_p = None
    z_value = None
    dispersion = None
    diagnostics: dict[str, Any] = {}
    expected_points: np.ndarray | None = None
    if model_result is not None and not bool(model_result.get("converged")):
        primary_diagnostics = {
            key: value
            for key, value in model_result.items()
            if not key.startswith("_")
            and key not in {
                "expected_points",
                "predictive_variance",
                "aggregate_predictive_variance",
                "dispersion",
            }
        }
        fallback_enabled = bool(
            config.get("v3", {})
            .get("detector_tiers", {})
            .get("enable_empirical_fallback", True)
        )
        fallback_result = (
            _fit_seasonal_empirical_baseline(
                history_values,
                history_dates,
                prediction_dates,
                cadence,
            )
            if fallback_enabled and is_count
            else None
        )
        if fallback_result is not None:
            model_result = fallback_result
            fit_status = "fallback_completed"
        else:
            fit_status = "non_converged"
            diagnostics = primary_diagnostics
            diagnostics["excluded_from_inference"] = True
            model_result = None
    if model_result is not None:
        expected_points = np.asarray(model_result["expected_points"], dtype=float)
        expected = (
            float(np.sum(expected_points))
            if is_count
            else float(
                scale
                * np.sum(expected_points)
                / np.sum(denominator_values[-window_periods:])
            )
        )
        variance_inflation = max(
            1.0,
            float(config.get("v3", {}).get("predictive_variance_inflation", 1.0)),
        )
        variance = float(model_result["aggregate_predictive_variance"]) * variance_inflation
        if has_rate_components:
            variance *= (
                scale / float(np.sum(denominator_values[-window_periods:]))
            ) ** 2
        standard_error = math.sqrt(max(variance, 1e-9))
        z_value = (current - expected) / standard_error
        raw_p = 0.5 * math.erfc(z_value / math.sqrt(2.0))
        upper = max(0.0, expected + 1.6448536269514722 * standard_error)
        dispersion = float(model_result["dispersion"])
        diagnostics = {
            key: value
            for key, value in model_result.items()
            if not key.startswith("_")
            and key not in {
                "expected_points",
                "predictive_variance",
                "aggregate_predictive_variance",
                "dispersion",
            }
        }
        diagnostics["predictive_variance_inflation"] = variance_inflation
    detector_tier = (
        "rate"
        if has_rate_components
        else "context_only"
        if not is_count
        else "rare_count"
        if expected is not None
        and expected
        <= float(
            config.get("v3", {})
            .get("detector_tiers", {})
            .get("rare_expected_max", 20)
        )
        else "common_count"
    )
    window_label = str(settings["label"])
    if (
        detector_tier == "common_count"
        and multi_horizon_enabled
        and model_result is not None
        and fit_status == "completed"
    ):
        multi_horizon = _multi_horizon_predictive_assessment(
            observed_points=values.iloc[-max(model_horizons) :].to_numpy(dtype=float),
            model_result=model_result,
            horizons=model_horizons,
            seed_text="|".join(
                [
                    str(first.get("series_code") or ""),
                    canonical_key,
                    str(first.get("dimension_key") or "all"),
                    str(first.get("disease_id") or ""),
                    str(as_of),
                    str(multi_horizon_config.get("version") or "v1"),
                ]
            ),
            draws=int(multi_horizon_config.get("production_draws", 2048)),
        )
        if multi_horizon is not None:
            window_periods = int(multi_horizon["selected_horizon_periods"])
            current_values = values.iloc[-window_periods:].to_numpy(dtype=float)
            previous_values = values.iloc[
                -window_periods * 2 : -window_periods
            ].to_numpy(dtype=float)
            current = float(multi_horizon["observed"])
            previous = float(np.sum(previous_values))
            expected = float(multi_horizon["expected"])
            upper = float(multi_horizon["predictive_upper_95"])
            z_value = float(multi_horizon["standardized_exceedance"])
            raw_p = float(multi_horizon["raw_p_value"])
            dispersion = float(multi_horizon["dispersion"])
            absolute_change = current - previous
            relative_change = (
                (current - previous) / previous * 100.0 if previous > 0 else None
            )
            diagnostics["multi_horizon"] = multi_horizon
            diagnostics["predictive_variance_inflation"] = 1.0
            diagnostics["detector_version"] = "multi_horizon_gamma_poisson_v1"
            variance_inflation = 1.0
            window_label = (
                f"Last {window_periods} weeks"
                if cadence == "weekly"
                else f"Last {window_periods} complete months"
            )
    if (
        detector_tier == "rare_count"
        and raw_p is not None
        and expected is not None
        and dispersion is not None
    ):
        uses_primary_rare_tail = (
            "rare_tail_aggregate_predictive_variance" in model_result
        )
        rare_tail_variance = float(
            model_result.get(
                "rare_tail_aggregate_predictive_variance",
                model_result["aggregate_predictive_variance"],
            )
        )
        raw_p = _count_upper_tail_probability(
            current,
            expected,
            rare_tail_variance,
        )
        diagnostics["rare_count_tail"] = (
            "negative_binomial_predictive"
            if rare_tail_variance > expected * 1.000001
            else "poisson_predictive"
        )
        diagnostics["rare_tail_predictive_variance"] = round(
            rare_tail_variance,
            8,
        )
        diagnostics["rare_tail_parameter_uncertainty"] = (
            "delta_method_moment_matched"
            if uses_primary_rare_tail
            else "seasonal_empirical_sampling_variance"
        )
        diagnostics["rare_tail_variance_inflation"] = 1.0
        hurdle_result = _hurdle_negative_binomial_tail(
            history_values=history_values,
            observed=current,
            horizon=window_periods,
            seed_text="|".join(
                [
                    str(first.get("series_code") or ""),
                    canonical_key,
                    str(first.get("dimension_key") or "all"),
                    str(as_of),
                    "hurdle-nb-v1",
                ]
            ),
            draws=int(multi_horizon_config.get("production_draws", 2048)),
        )
        empirical_result = _fit_seasonal_empirical_baseline(
            history_values,
            history_dates,
            prediction_dates,
            cadence,
        )
        empirical_shadow = None
        if empirical_result is not None:
            empirical_expected = float(
                np.sum(empirical_result["expected_points"])
            )
            empirical_variance = float(
                empirical_result["aggregate_predictive_variance"]
            )
            empirical_shadow = {
                "raw_p_value": round(
                    _count_upper_tail_probability(
                        current,
                        empirical_expected,
                        empirical_variance,
                    ),
                    8,
                ),
                "expected": round(empirical_expected, 6),
                "predictive_upper_95": round(
                    max(
                        0.0,
                        empirical_expected
                        + 1.6448536269514722 * math.sqrt(empirical_variance),
                    ),
                    6,
                ),
                "seasonal_sample_counts": empirical_result.get(
                    "seasonal_sample_counts", []
                ),
                "decision_role": "shadow_only",
            }
        diagnostics["rare_model_comparison"] = {
            "primary": {
                "model": diagnostics["rare_count_tail"],
                "raw_p_value": round(raw_p, 8),
                "decision_role": "review_signal",
            },
            "hurdle_negative_binomial_v1": hurdle_result,
            "seasonal_empirical_v1": empirical_shadow,
            "automation_eligible": False,
        }
    if model_result is not None and expected_points is not None:
        point_variance = np.asarray(model_result["predictive_variance"], dtype=float)
        observed_points = (
            values.iloc[-len(expected_points) :].to_numpy(dtype=float)
            if is_count
            else numerator_values[-len(expected_points) :]
        )
        standardized = (observed_points - expected_points) / np.sqrt(
            np.maximum(point_variance, 1e-9)
        )
        reference = float(
            config.get("v3", {})
            .get("detector_tiers", {})
            .get("cusum_reference", 0.5)
        )
        cumulative = 0.0
        maximum = 0.0
        for residual in standardized:
            cumulative = max(0.0, cumulative + float(residual) - reference)
            maximum = max(maximum, cumulative)
        diagnostics["supporting_cusum"] = {
            "score": round(maximum, 4),
            "reference": reference,
            "decision_role": "supporting_only",
        }
    diagnostics["detector_tier"] = detector_tier
    effect_passed = bool(
        (is_count and metric_type in COUNT_ACTIVITY_METRICS | SEVERITY_METRICS)
        or has_rate_components
    ) and _configured_effect_passed(
        current=current,
        absolute_change=absolute_change,
        relative_change=relative_change,
        rules=_effect_rules(
            config,
            first,
            metric_type,
            is_count=is_count,
            detector_tier=detector_tier,
            cadence=cadence,
            expected=expected,
        ),
    )
    identity_text = "|".join(
        [
            str(first.get("series_code") or ""),
            canonical_key,
            str(first.get("dimension_key") or "all"),
            str(first.get("disease_id") or ""),
        ]
    )
    signal_id = "signal-v3:" + hashlib.sha256(identity_text.encode()).hexdigest()[:20]
    source_url = _valid_http_url(first.get("source_url"))
    latest_available_date = raw_latest.date()
    reporting_lag_days = _period_age_days(raw_latest, cadence, as_of)
    analysis_lag_days = _period_age_days(pd.Timestamp(periods[-1]), cadence, as_of)
    delay_warning_days = int(
        config.get("data_latency", {})
        .get("delay_warning_days", {})
        .get(cadence, {"daily": 5, "weekly": 14, "monthly": 45}[cadence])
    )
    held_back = latest_available_date > latest_date
    reporting_delayed = reporting_lag_days > delay_warning_days
    analysis_delayed = analysis_lag_days > delay_warning_days
    data_status = (
        "delayed"
        if reporting_delayed or analysis_delayed
        else "held_back"
        if held_back
        else "current"
    )
    diagnostics["data_latency"] = {
        "status": data_status,
        "latest_available_period": latest_available_date.isoformat(),
        "analysis_through": latest_date.isoformat(),
        "reporting_lag_days": reporting_lag_days,
        "analysis_lag_days": analysis_lag_days,
        "delay_warning_days": delay_warning_days,
        "reporting_delayed": reporting_delayed,
        "analysis_delayed": analysis_delayed,
        "source_period_coverage": _finite_number(first.get("source_period_coverage")),
    }
    recent_values = values.iloc[-12:]
    recent_dates = periods[-len(recent_values) :]
    expected_by_period: dict[date, tuple[float, float]] = {}
    if model_result is not None and expected_points is not None:
        for point_index, (stamp, mean, variance) in enumerate(zip(
            periods[-len(expected_points):],
            expected_points,
            np.asarray(model_result["predictive_variance"], dtype=float),
            strict=True,
        )):
            if has_rate_components:
                point_scale = scale / float(
                    denominator_values[-len(expected_points) + point_index]
                )
                mean = float(mean) * point_scale
                variance = float(variance) * point_scale**2
            expected_by_period[stamp.date()] = (
                float(mean),
                max(
                    0.0,
                    float(mean)
                    + 1.6448536269514722
                    * math.sqrt(max(float(variance) * variance_inflation, 1e-9)),
                ),
            )
    recent_points = []
    for stamp, value in zip(recent_dates, recent_values, strict=True):
        if not math.isfinite(float(value)):
            continue
        expected_point = expected_by_period.get(stamp.date())
        recent_points.append(
            RecentPoint(
                period=stamp.date(),
                value=round(float(value), 3),
                expected=round(expected_point[0], 3) if expected_point else None,
                predictive_upper_95=round(expected_point[1], 3) if expected_point else None,
            )
        )
    signal = SituationSignalV3(
        identity=SignalIdentity(
            signal_id=signal_id,
            disease_id=str(first.get("disease_id") or ""),
            disease_name=str(first.get("disease_name") or first.get("disease_id") or "Unknown"),
            disease_slug=first.get("disease_slug"),
            country_code=first.get("country_code"),
            country_name=first.get("country_name"),
            canonical_geography_key=canonical_key,
            source_geography_keys=source_keys,
            dimension_key=str(first.get("dimension_key") or "all"),
            dimensions=_json_mapping(first.get("dimensions")),
            series_code=str(first.get("series_code") or ""),
            source_system=str(first.get("source_system") or "unknown"),
            source_label=first.get("source_label"),
            metric_type=metric_type,
            metric_label=_metric_label(metric_type),
            unit=unit,
            cadence=cadence,
        ),
        observation=ObservationComparison(
            window_label=window_label,
            window_periods=window_periods,
            data_through=latest_date,
            latest_available_period=latest_available_date,
            data_status=data_status,
            reporting_lag_days=reporting_lag_days,
            analysis_lag_days=analysis_lag_days,
            current=round(current, 3),
            previous=round(previous, 3),
            expected=round(expected, 3) if expected is not None else None,
            predictive_upper_95=round(upper, 3) if upper is not None else None,
            absolute_change=round(absolute_change, 3),
            relative_change_pct=round(relative_change, 2) if relative_change is not None else None,
            completeness=round(completeness, 3),
        ),
        anomaly=AnomalyAssessment(
            model=(
                "seasonal_empirical_fallback_v1"
                if model_result is not None and model_result.get("fallback")
                else "multi_horizon_gamma_poisson_v1"
                if diagnostics.get("detector_version")
                == "multi_horizon_gamma_poisson_v1"
                else "robust_quasi_poisson_v1"
                if is_count
                else "robust_quasi_poisson_offset_v1"
                if has_rate_components
                else "not_modeled"
            ),
            detector_tier=detector_tier,
            state="routine" if model_result is not None else "not_modeled",
            raw_p_value=round(raw_p, 8) if raw_p is not None else None,
            standardized_exceedance=round(z_value, 4) if z_value is not None else None,
            dispersion=round(dispersion, 4) if dispersion is not None else None,
            fit_status=fit_status,
            effect_threshold_passed=effect_passed,
            diagnostics=diagnostics,
        ),
        assessment=SignalAssessment(
            review_priority="routine",
            temporal_relevance=(
                "historical"
                if data_status == "delayed"
                else "lagged"
                if data_status == "held_back"
                else "current"
            ),
            evidence_gaps=(
                ["public_health_risk_not_assessed"]
                + (["latest_source_period_held_back"] if data_status == "held_back" else [])
                + (["source_reporting_delayed"] if reporting_delayed else [])
                + (["analysis_period_delayed"] if analysis_delayed else [])
                + (["model_fit_non_converged"] if fit_status == "non_converged" else [])
                + (["source_evidence_url_missing"] if source_url is None else [])
            ),
            public_health_risk=PublicHealthRisk(),
        ),
        tags=(
            (["respiratory"] if str(first.get("disease_id")) in RESPIRATORY_DISEASE_IDS else [])
            + (["severity"] if metric_type in SEVERITY_METRICS else [])
        ),
        recent_points=recent_points,
        evidence_links=(
            [EvidenceLink(title=str(first.get("source_label") or "Source data"), url=str(source_url))]
            if source_url is not None
            else []
        ),
    )
    ledger.update(
        {
            "status": "modeled" if model_result is not None else "context_only",
            "signal_id": signal_id,
            "data_through": latest_date.isoformat(),
            "latest_available_period": latest_available_date.isoformat(),
            "analysis_cutoff": analysis_cutoff.date().isoformat() if analysis_cutoff is not None else None,
            "data_status": data_status,
            "reporting_lag_days": reporting_lag_days,
            "analysis_lag_days": analysis_lag_days,
            "source_period_coverage": _finite_number(first.get("source_period_coverage")),
            "source_active_identities": _finite_number(first.get("source_active_identities")),
            "source_total_identities": _finite_number(first.get("source_total_identities")),
            "raw_p_value": signal.anomaly.raw_p_value,
            "fit_status": fit_status,
        }
    )
    return SeriesV3Evaluation(signal, None, ledger)


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    if not p_values:
        return []
    indexed = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    total = len(indexed)
    adjusted = [1.0] * total
    running = 1.0
    for rank_from_end, (original_index, value) in enumerate(reversed(indexed), start=1):
        rank = total - rank_from_end + 1
        running = min(running, float(value) * total / rank)
        adjusted[original_index] = min(1.0, max(0.0, running))
    return adjusted


def _evaluate_chunk_v3(
    chunk: list[pd.DataFrame],
    config: dict[str, Any],
    as_of: date,
) -> list[SeriesV3Evaluation]:
    return [evaluate_series_v3(group, config, as_of=as_of) for group in chunk]


def evaluate_frame_v3(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    as_of: date,
) -> tuple[list[SituationSignalV3], dict[str, int], list[dict[str, Any]]]:
    work = prepare_frame_v3(frame)
    if work.empty:
        return [], {"empty": 1}, []
    group_columns = ["series_code", "canonical_geography_key", "dimension_key"]
    groups = [group for _, group in work.groupby(group_columns, dropna=False, sort=True)]
    requested_workers = max(
        1,
        min(4, int(config.get("v3", {}).get("maximum_analysis_workers", 1))),
    )
    evaluations: list[SeriesV3Evaluation]
    if requested_workers > 1 and len(groups) >= 16:
        worker_count = min(requested_workers, len(groups))
        chunk_size = math.ceil(len(groups) / worker_count)
        chunks = [groups[offset : offset + chunk_size] for offset in range(0, len(groups), chunk_size)]
        try:
            with ProcessPoolExecutor(
                max_workers=worker_count,
                mp_context=multiprocessing.get_context("spawn"),
            ) as executor:
                chunk_results = list(
                    executor.map(
                        _evaluate_chunk_v3,
                        chunks,
                        [config] * len(chunks),
                        [as_of] * len(chunks),
                    )
                )
            evaluations = [evaluation for chunk in chunk_results for evaluation in chunk]
        except (OSError, RuntimeError) as exc:
            logger.warning("Situation v3 process pool unavailable; using deterministic serial model: {}", exc)
            evaluations = [evaluate_series_v3(group, config, as_of=as_of) for group in groups]
    else:
        evaluations = [evaluate_series_v3(group, config, as_of=as_of) for group in groups]
    rejected: dict[str, int] = {}
    for evaluation in evaluations:
        if evaluation.rejection_reason:
            rejected[evaluation.rejection_reason] = rejected.get(evaluation.rejection_reason, 0) + 1
    signals = [item.signal for item in evaluations if item.signal is not None]
    families: dict[tuple[str, str, str], list[SituationSignalV3]] = {}
    for signal in signals:
        if signal.anomaly.raw_p_value is None:
            continue
        if signal.observation.data_status == "delayed":
            # A historical exceedance from a delayed feed must not dilute the
            # contemporary FDR family or surface as a current alert.
            signal.anomaly.q_value = 1.0
            signal.anomaly.state = "watch"
            signal.assessment.review_priority = "routine"
            continue
        key = (
            signal.anomaly.detector_tier,
            signal.identity.metric_type,
            signal.identity.cadence,
        )
        families.setdefault(key, []).append(signal)
    v3_config = config.get("v3", {})
    alert_q = float(v3_config.get("alert_q", 0.05))
    strong_q = float(v3_config.get("strong_q", 0.01))
    for family in families.values():
        q_values = benjamini_hochberg([float(signal.anomaly.raw_p_value) for signal in family])
        for signal, q_value in zip(family, q_values, strict=True):
            signal.anomaly.q_value = round(q_value, 8)
            if not signal.anomaly.effect_threshold_passed:
                signal.anomaly.state = "routine"
                continue
            if q_value <= strong_q:
                signal.anomaly.state = "strong"
                signal.assessment.review_priority = "high"
                if "increasing" not in signal.tags:
                    signal.tags.append("increasing")
                if "unusual" not in signal.tags:
                    signal.tags.append("unusual")
            elif q_value <= alert_q:
                signal.anomaly.state = "alert"
                signal.assessment.review_priority = "standard"
                if "increasing" not in signal.tags:
                    signal.tags.append("increasing")
            else:
                signal.anomaly.state = "routine"
    by_signal_id = {signal.identity.signal_id: signal for signal in signals}
    ledger = []
    for evaluation in evaluations:
        row = dict(evaluation.ledger)
        signal = by_signal_id.get(str(row.get("signal_id") or ""))
        if signal is not None:
            row.update(
                {
                    "q_value": signal.anomaly.q_value,
                    "anomaly_state": signal.anomaly.state,
                    "review_priority": signal.assessment.review_priority,
                }
            )
        ledger.append(row)
    signals.sort(
        key=lambda signal: (
            {"strong": 3, "alert": 2, "watch": 1}.get(signal.anomaly.state, 0),
            -(signal.anomaly.q_value if signal.anomaly.q_value is not None else 1.0),
            signal.observation.absolute_change or 0.0,
        ),
        reverse=True,
    )
    return signals, rejected, ledger


__all__ = [
    "SeriesV3Evaluation",
    "benjamini_hochberg",
    "canonical_geography_key",
    "evaluate_frame_v3",
    "evaluate_series_v3",
    "prepare_frame_v3",
]
