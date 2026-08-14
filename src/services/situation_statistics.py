"""Frequency-aware statistical detection and epidemiological risk scoring.

All calculations operate on one source-native series at a time.  Missing
periods remain missing, incompatible metrics are never combined, and every
detector is deterministic so a published snapshot can be reproduced.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd


CADENCE_DEFAULTS: dict[str, dict[str, Any]] = {
    "daily": {"window_periods": 28, "periods_per_year": 365, "freshness_days": 14, "label": "Last 28 days"},
    "weekly": {"window_periods": 4, "periods_per_year": 52, "freshness_days": 35, "label": "Last 4 weeks"},
    "monthly": {"window_periods": 1, "periods_per_year": 12, "freshness_days": 75, "label": "Latest complete month"},
}

RISK_WEIGHTS = {"trend": 0.40, "severity": 0.25, "geographic_spread": 0.20, "official_concern": 0.15}


@dataclass(frozen=True)
class SeriesEvaluation:
    assessment: dict[str, Any] | None
    rejection_reason: str | None = None


def _number(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and math.isfinite(number) else None


def _round(value: Any, digits: int = 3) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def cadence_from_granularity(value: Any, periods: pd.Series) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"daily", "weekly", "monthly", "annual"}:
        return raw
    ordered = pd.to_datetime(periods, errors="coerce", utc=True).dropna().sort_values()
    if len(ordered) < 2:
        return "unknown"
    days = ordered.diff().dropna().dt.total_seconds().median() / 86400
    if 5 <= days <= 10:
        return "weekly"
    if 25 <= days <= 35:
        return "monthly"
    if days >= 300:
        return "annual"
    if 0.5 <= days <= 2:
        return "daily"
    return "unknown"


def _period_key(timestamp: pd.Timestamp, cadence: str) -> Any:
    stamp = timestamp.tz_convert(None) if timestamp.tzinfo else timestamp
    if cadence == "monthly":
        return stamp.to_period("M")
    if cadence == "weekly":
        iso = stamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return stamp.date()


def _regular_values(work: pd.DataFrame, cadence: str, window_periods: int) -> tuple[pd.Series, pd.Series, float]:
    data = work[["time", "value"]].copy()
    data["period_key"] = data["time"].map(lambda stamp: _period_key(pd.Timestamp(stamp), cadence))
    # A duplicate in one exact source-native series is a revision, not another
    # count to add.  The most recently ingested observation wins.
    data = data.drop_duplicates("period_key", keep="last")
    latest = pd.Timestamp(data["time"].max())
    if cadence == "monthly":
        end = latest.tz_convert(None).to_period("M") if latest.tzinfo else latest.to_period("M")
        expected = pd.period_range(end=end, periods=max(window_periods * 2, 2), freq="M")
        lookup = data.set_index("period_key")["value"]
        recent = lookup.reindex(expected)
        all_keys = pd.period_range(start=min(data["period_key"]), end=end, freq="M")
        all_values = lookup.reindex(all_keys)
        all_dates = pd.Series(all_keys.to_timestamp(how="end"), dtype="datetime64[ns]")
    elif cadence == "weekly":
        expected_dates = pd.date_range(end=latest.tz_convert(None).normalize(), periods=max(window_periods * 2, 2), freq="7D")
        expected = pd.Index([_period_key(stamp, cadence) for stamp in expected_dates])
        lookup = data.set_index("period_key")["value"]
        recent = lookup.reindex(expected)
        first = pd.Timestamp(data["time"].min()).tz_convert(None).normalize()
        all_date_index = pd.date_range(start=first, end=latest.tz_convert(None).normalize(), freq="7D")
        all_keys = pd.Index([_period_key(stamp, cadence) for stamp in all_date_index])
        all_values = lookup.reindex(all_keys)
        all_dates = pd.Series(all_date_index, dtype="datetime64[ns]")
    else:
        end = latest.tz_convert(None).normalize()
        expected = pd.date_range(end=end, periods=max(window_periods * 2, 2), freq="D")
        lookup = data.assign(period_key=data["period_key"].map(pd.Timestamp)).set_index("period_key")["value"]
        recent = lookup.reindex(expected)
        all_dates_index = pd.date_range(start=pd.Timestamp(data["time"].min()).tz_convert(None).normalize(), end=end, freq="D")
        all_values = lookup.reindex(all_dates_index)
        all_dates = pd.Series(all_dates_index, dtype="datetime64[ns]")
    completeness = float(recent.notna().mean()) if len(recent) else 0.0
    return all_values.reset_index(drop=True), all_dates.reset_index(drop=True), completeness


def _reduce_window(window: pd.Series, reducer: str) -> float:
    return float(window.mean()) if reducer == "mean" else float(window.sum())


def _window_reducer(metric_type: str, unit: str) -> str:
    normalized_unit = unit.strip().lower()
    if normalized_unit in {"percent", "percentage", "rate", "ratio", "index", "per 100,000", "per_100000"}:
        return "mean"
    if metric_type in {"test_positivity", "incidence_rate", "hospital_admission_rate", "activity_rate"}:
        return "mean"
    return "sum"


def _window_for_anchor(
    values: pd.Series,
    dates: pd.Series,
    anchor: pd.Timestamp,
    cadence: str,
    window_periods: int,
    reducer: str = "sum",
) -> float | None:
    if cadence == "monthly":
        target = anchor.to_period("M")
        keys = dates.map(lambda stamp: pd.Timestamp(stamp).to_period("M"))
        matches = np.flatnonzero((keys == target).to_numpy())
    elif cadence == "weekly":
        target_iso = anchor.isocalendar()
        matches = np.flatnonzero(
            dates.map(
                lambda stamp: (
                    pd.Timestamp(stamp).isocalendar().year == target_iso.year
                    and pd.Timestamp(stamp).isocalendar().week == target_iso.week
                )
            ).to_numpy()
        )
    else:
        matches = np.flatnonzero(
            dates.map(
                lambda stamp: (
                    pd.Timestamp(stamp).year == anchor.year
                    and abs(pd.Timestamp(stamp).dayofyear - anchor.dayofyear) <= 1
                )
            ).to_numpy()
        )
    if not len(matches):
        return None
    end = int(matches[-1])
    start = end - window_periods + 1
    if start < 0:
        return None
    window = pd.to_numeric(values.iloc[start : end + 1], errors="coerce")
    if len(window) != window_periods or window.isna().any():
        return None
    return _reduce_window(window, reducer)


def seasonal_window_baseline(
    values: pd.Series,
    dates: pd.Series,
    cadence: str,
    window_periods: int,
    max_seasons: int = 5,
    reducer: str = "sum",
) -> pd.Series:
    latest = pd.Timestamp(dates.iloc[-1])
    samples: list[float] = []
    for years_back in range(1, max_seasons + 1):
        try:
            anchor = latest - pd.DateOffset(years=years_back)
        except ValueError:  # February 29 uses the last valid day in pandas, but keep this explicit.
            anchor = latest.replace(month=2, day=28) - pd.DateOffset(years=years_back)
        sample = _window_for_anchor(values, dates, anchor, cadence, window_periods, reducer)
        if sample is not None:
            samples.append(sample)
    return pd.Series(samples, dtype=float)


def z_scores(current: float, baseline: pd.Series) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    if len(baseline) < 2:
        return None, None, None, None, None
    mean = float(baseline.mean())
    median = float(baseline.median())
    std = float(baseline.std(ddof=1))
    mad = float((baseline - median).abs().median())
    z_score = (current - mean) / std if std else (math.inf if current > mean else 0.0)
    robust_z = 0.6745 * (current - median) / mad if mad else (math.inf if current > median else 0.0)
    band_sigma = std if std else 0.0
    return robust_z, z_score, median, mean - 1.96 * band_sigma, mean + 1.96 * band_sigma


def seasonal_residuals(values: pd.Series, dates: pd.Series, cadence: str, max_seasons: int = 5) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    timestamps = [pd.Timestamp(stamp) for stamp in dates]
    residuals: list[float] = []
    history: dict[Any, list[tuple[int, float]]] = {}
    for value, stamp in zip(numeric, timestamps, strict=True):
        if not math.isfinite(value):
            continue
        if cadence == "monthly":
            keys = [stamp.month]
        elif cadence == "weekly":
            keys = [stamp.isocalendar().week]
        else:
            # A one-day tolerance is useful around leap years while preserving
            # strict past-only calculation.
            keys = [stamp.dayofyear - 1, stamp.dayofyear, stamp.dayofyear + 1]
        cutoff_year = stamp.year - max_seasons
        cutoff_ordinal = date(
            cutoff_year,
            stamp.month,
            min(stamp.day, 28 if stamp.month == 2 else stamp.day),
        ).toordinal()
        historical: list[float] = []
        for key in keys:
            retained = [
                (prior_ordinal, prior_value)
                for prior_ordinal, prior_value in history.get(key, [])
                if prior_ordinal >= cutoff_ordinal
            ]
            history[key] = retained
            historical.extend(prior_value for _, prior_value in retained)
        if historical:
            baseline = float(np.median(np.log1p(np.maximum(historical, 0.0))))
            residuals.append(math.log1p(max(float(value), 0.0)) - baseline)
        primary_key = keys[len(keys) // 2]
        history.setdefault(primary_key, []).append((stamp.date().toordinal(), float(value)))
    return pd.Series(residuals, dtype=float)


def ewma_residual_alert(residuals: pd.Series, smoothing: float = 0.3, limit_sigma: float = 3.0, holdout_periods: int = 4) -> tuple[float | None, float | None, bool]:
    clean = pd.to_numeric(residuals, errors="coerce").dropna().reset_index(drop=True)
    if len(clean) < max(8, holdout_periods + 3):
        return None, None, False
    baseline = clean.iloc[:-holdout_periods] if len(clean) > holdout_periods else clean.iloc[:-1]
    center = float(baseline.median())
    mad = float((baseline - center).abs().median())
    sigma = 1.4826 * mad
    if sigma == 0:
        sigma = float(baseline.std(ddof=1))
    ewma = center
    for value in clean:
        ewma = smoothing * float(value) + (1.0 - smoothing) * ewma
    steady_sigma = sigma * math.sqrt(smoothing / (2.0 - smoothing))
    upper = center + limit_sigma * steady_sigma
    alert = ewma > upper if sigma > 0 else ewma > center + 1e-9
    return _round(ewma), _round(upper), bool(alert)


@lru_cache(maxsize=16)
def _student_t_log_gamma_terms(length: int) -> np.ndarray:
    """Cache the only non-vectorized part of the Student-t normalization.

    In this conjugate model alpha depends only on run length, so these gamma
    terms are identical for every series and every refresh.
    """
    degrees = 2.0 + np.arange(length, dtype=float)
    return np.fromiter(
        (
            math.lgamma((degree + 1.0) / 2.0) - math.lgamma(degree / 2.0)
            for degree in degrees
        ),
        dtype=float,
        count=length,
    )


def _student_t_pdf(value: float, mu: np.ndarray, kappa: np.ndarray, alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
    degrees = 2.0 * alpha
    scale2 = beta * (kappa + 1.0) / (alpha * kappa)
    scale2 = np.maximum(scale2, 1e-12)
    log_norm = _student_t_log_gamma_terms(len(degrees)).copy()
    log_norm -= 0.5 * (np.log(degrees * math.pi) + np.log(scale2))
    log_body = -((degrees + 1.0) / 2.0) * np.log1p(((value - mu) ** 2) / (degrees * scale2))
    return np.exp(np.clip(log_norm + log_body, -745, 100))


def bayesian_change_probability(values: pd.Series | np.ndarray | list[float], *, periods_per_year: int, max_run_length: int | None = None, recent_run_length: int = 4) -> float | None:
    """Return posterior probability that the current run began recently.

    This is pure NumPy Bayesian Online Change Point Detection with a
    Normal-Inverse-Gamma prior and Student-t posterior predictive.  A constant
    hazard alone makes P(r=0) equal to the hazard, so the useful public measure
    is P(run length <= the comparison window).
    """
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if len(data) < max(12, recent_run_length * 3):
        return None
    max_run = max_run_length or periods_per_year * 2
    # A capped run-length model cannot distinguish histories older than its
    # maximum state. Processing only the bounded reproducible tail avoids an
    # O(full-history × max-run) cost for every daily series.
    data = data[-(max_run + recent_run_length + 1) :]
    hazard = 1.0 / max(periods_per_year, 1)
    prior_mu, prior_kappa, prior_alpha, prior_beta = 0.0, 1.0, 1.0, 1.0
    run_probs = np.array([1.0])
    mu = np.array([prior_mu])
    kappa = np.array([prior_kappa])
    alpha = np.array([prior_alpha])
    beta = np.array([prior_beta])
    for point in data:
        predictive = _student_t_pdf(float(point), mu, kappa, alpha, beta)
        growth = run_probs * predictive * (1.0 - hazard)
        changepoint = float(np.sum(run_probs * predictive * hazard))
        next_probs = np.concatenate(([changepoint], growth))[: max_run + 1]
        total = float(next_probs.sum())
        if total <= 0 or not math.isfinite(total):
            return None
        next_probs /= total

        updated_kappa = kappa + 1.0
        updated_mu = (kappa * mu + point) / updated_kappa
        updated_alpha = alpha + 0.5
        updated_beta = beta + (kappa * (point - mu) ** 2) / (2.0 * updated_kappa)
        mu = np.concatenate(([prior_mu], updated_mu))[: max_run + 1]
        kappa = np.concatenate(([prior_kappa], updated_kappa))[: max_run + 1]
        alpha = np.concatenate(([prior_alpha], updated_alpha))[: max_run + 1]
        beta = np.concatenate(([prior_beta], updated_beta))[: max_run + 1]
        run_probs = next_probs
    probability = float(run_probs[: min(recent_run_length + 1, len(run_probs))].sum())
    return round(_clamp(probability), 4)


def _risk_level(score: float) -> str:
    if score >= 75:
        return "very_high"
    if score >= 50:
        return "high"
    if score >= 25:
        return "moderate"
    return "low"


def compute_risk(dimensions: dict[str, float | None], weights: dict[str, float] | None = None) -> dict[str, Any]:
    configured = weights or RISK_WEIGHTS
    available = {name: _clamp(float(score), 0.0, 100.0) for name, score in dimensions.items() if score is not None and name in configured}
    available_weight = sum(configured[name] for name in available)
    score = sum(available[name] * configured[name] for name in available) / available_weight if available_weight else 0.0
    confidence = "high" if available_weight >= 0.85 else "medium" if available_weight >= 0.60 else "low"
    missing = [name for name in configured if name not in available]
    return {
        "score": round(score, 1),
        "level": _risk_level(score),
        "confidence": confidence,
        "available_weight": round(available_weight, 2),
        "dimensions": {name: (round(available[name], 1) if name in available else None) for name in configured},
        "missing_dimensions": missing,
    }


def trend_risk_score(*, z_score: float | None, robust_z: float | None, ewma_alert: bool, change_probability: float | None, change_pct: float | None) -> float:
    finite_z = [value for value in (_finite(z_score), _finite(robust_z)) if value is not None]
    z_strength = _clamp(max(finite_z, default=0.0) / 4.0)
    relative = _clamp(max(change_pct or 0.0, 0.0) / 200.0)
    probability = _clamp(change_probability or 0.0)
    return round(100.0 * (0.35 * z_strength + 0.20 * float(ewma_alert) + 0.25 * probability + 0.20 * relative), 1)


def evaluate_series(frame: pd.DataFrame, config: dict[str, Any], *, as_of: date | None = None) -> SeriesEvaluation:
    if frame.empty:
        return SeriesEvaluation(None, "empty")
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], errors="coerce", utc=True)
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work[work["time"].notna() & work["value"].notna()].sort_values("time")
    if work.empty:
        return SeriesEvaluation(None, "no_valid_observations")
    cadence = cadence_from_granularity(work.get("temporal_granularity", pd.Series(dtype=str)).iloc[0] if "temporal_granularity" in work else None, work["time"])
    if cadence not in CADENCE_DEFAULTS:
        return SeriesEvaluation(None, "unsupported_frequency")
    if cadence == "monthly" and as_of is not None:
        current_month = pd.Period(as_of, freq="M")
        work = work[work["time"].map(lambda stamp: pd.Timestamp(stamp).tz_convert(None).to_period("M") < current_month)]
        if work.empty:
            return SeriesEvaluation(None, "no_complete_month")
    first = work.iloc[-1]
    metric_type = str(first.get("metric_type") or "case_notifications")
    unit = str(first.get("unit") or "count")
    window_reducer = _window_reducer(metric_type, unit)
    cadence_config = {**CADENCE_DEFAULTS[cadence], **(config.get("cadences", {}).get(cadence, {}))}
    window_periods = int(cadence_config["window_periods"])
    minimum = int(config.get("thresholds", {}).get("minimum_observations", {}).get(cadence, window_periods * 3))
    if len(work) < minimum:
        return SeriesEvaluation(None, "insufficient_observations")
    latest_date = pd.Timestamp(work["time"].max()).date()
    reference_date = as_of or latest_date
    age_days = (reference_date - latest_date).days
    if age_days > int(cadence_config["freshness_days"]):
        return SeriesEvaluation(None, "stale")
    values, periods, completeness = _regular_values(work, cadence, window_periods)
    minimum_completeness = float(config.get("quality", {}).get("minimum_window_completeness", 0.8))
    if completeness < minimum_completeness:
        return SeriesEvaluation(None, "incomplete_comparison_window")
    recent = values.iloc[-window_periods * 2 :]
    if len(recent) < window_periods * 2 or recent.isna().any():
        return SeriesEvaluation(None, "incomplete_comparison_window")
    current = _reduce_window(recent.iloc[-window_periods:], window_reducer)
    previous = _reduce_window(recent.iloc[:window_periods], window_reducer)
    absolute_change = current - previous
    change_pct = ((current - previous) / previous * 100.0) if previous > 0 else None
    seasonal = seasonal_window_baseline(
        values,
        periods,
        cadence,
        window_periods,
        int(config.get("quality", {}).get("maximum_seasons", 5)),
        window_reducer,
    )
    minimum_seasons = int(config.get("quality", {}).get("minimum_seasonal_samples", 3))
    if len(seasonal) < minimum_seasons:
        return SeriesEvaluation(None, "insufficient_seasonal_samples")
    robust_z, standard_z, seasonal_median, band_lower, band_upper = z_scores(current, seasonal)
    residuals = seasonal_residuals(values, periods, cadence, int(config.get("quality", {}).get("maximum_seasons", 5)))
    thresholds = config.get("thresholds", {})
    ewma, ewma_upper, ewma_alert = ewma_residual_alert(
        residuals,
        float(thresholds.get("ewma_lambda", 0.3)),
        float(thresholds.get("ewma_limit_sigma", 3.0)),
        window_periods,
    )
    change_probability = bayesian_change_probability(
        residuals,
        periods_per_year=int(cadence_config["periods_per_year"]),
        max_run_length=int(cadence_config["periods_per_year"]) * 2,
        recent_run_length=window_periods,
    )
    seasonal_alert = band_upper is not None and current > band_upper
    z_alert = (robust_z is not None and robust_z >= float(thresholds.get("robust_z_elevated", 2.0))) or (standard_z is not None and standard_z >= float(thresholds.get("z_elevated", 2.0)))
    bayesian_alert = change_probability is not None and change_probability >= float(thresholds.get("bayesian_probability", 0.80))
    detectors = {"seasonal_band": bool(seasonal_alert), "z_score": bool(z_alert), "ewma": bool(ewma_alert), "bayesian_change": bool(bayesian_alert)}
    votes = sum(detectors.values())
    is_count_metric = unit.strip().lower() == "count"
    low_base = is_count_metric and previous < float(thresholds.get("low_base_previous_cases", 10))
    zero_periods = 0
    for value in reversed(values.iloc[:-1].dropna().tolist()):
        if value == 0:
            zero_periods += 1
        else:
            break
    reappearing = is_count_metric and zero_periods >= int(thresholds.get("reappearance_zero_periods", 8)) and current >= float(thresholds.get("minimum_current_cases", 20))
    candidate = (
        is_count_metric
        and metric_type != "hospitalized_case_notifications"
        and
        current >= float(thresholds.get("minimum_current_cases", 20))
        and absolute_change >= float(thresholds.get("minimum_absolute_increase", 10))
        and (change_pct or 0.0) >= float(thresholds.get("minimum_relative_increase_pct", 25))
        and votes >= int(thresholds.get("minimum_detector_votes", 2))
    )
    if low_base and not reappearing:
        candidate = False
    strong_z = max([value for value in (_finite(robust_z), _finite(standard_z)) if value is not None], default=0.0) >= float(thresholds.get("robust_z_strong", 3.5))
    unusual = candidate and (votes >= 3 or (strong_z and (ewma_alert or bayesian_alert)))
    signal_level = "strong" if unusual or reappearing else "elevated" if candidate else "baseline"
    metric_label = {
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
    quality_status = str(first.get("quality_status") or "validated")
    trend_score = trend_risk_score(z_score=standard_z, robust_z=robust_z, ewma_alert=ewma_alert, change_probability=change_probability, change_pct=change_pct)
    risk = compute_risk({"trend": trend_score, "severity": None, "geographic_spread": None, "official_concern": None})
    identity = f"{first.get('series_code')}|{first.get('geography_key')}|{first.get('dimension_key')}|{first.get('disease_id')}"
    assessment = {
        "id": "signal:" + hashlib.sha256(identity.encode()).hexdigest()[:16],
        "kind": "statistical_signal",
        "disease_id": first.get("disease_id"),
        "disease_name": first.get("disease_name") or first.get("disease_id"),
        "disease_slug": first.get("disease_slug"),
        "country_code": first.get("country_code"),
        "country_name": first.get("country_name") or first.get("country_code"),
        "series_code": first.get("series_code"),
        "source_system": first.get("source_system"),
        "source_label": first.get("source_label"),
        "source_url": first.get("source_url"),
        "geography_key": first.get("geography_key"),
        "dimension_key": first.get("dimension_key"),
        "metric_type": metric_type,
        "metric_label": metric_label,
        "unit": unit,
        "cadence": cadence,
        "data_through": latest_date.isoformat(),
        "freshness": {"age_days": max(age_days, 0), "sla_days": int(cadence_config["freshness_days"]), "status": "fresh"},
        "quality": {"status": quality_status, "comparison_completeness": round(completeness, 3)},
        "window": {
            "label": cadence_config["label"],
            "periods": window_periods,
            "aggregation": window_reducer,
            "current": round(current, 3),
            "previous": round(previous, 3),
            "current_cases": int(current) if unit == "count" else None,
            "previous_cases": int(previous) if unit == "count" else None,
            "absolute_change": round(absolute_change, 3),
            "change_pct": None if low_base else _round(change_pct, 2),
        },
        "baseline": {
            "same_season_median": _round(seasonal_median),
            "lower_95": _round(band_lower),
            "upper_95": _round(band_upper),
            "sample_size": int(len(seasonal)),
            "seasons": int(len(seasonal)),
        },
        "statistics": {
            "z_score": _round(standard_z),
            "robust_z": _round(robust_z),
            "ewma_residual": ewma,
            "ewma_upper_limit": ewma_upper,
            "bayesian_change_probability": change_probability,
            "detectors": detectors,
            "detector_votes": votes,
            "methods": {
                "seasonal_baseline": {
                    "status": "completed",
                    "sample_size": int(len(seasonal)),
                    "maximum_seasons": int(config.get("quality", {}).get("maximum_seasons", 5)),
                    "alert": bool(seasonal_alert),
                },
                "standard_z": {
                    "status": "completed" if _finite(standard_z) is not None else "degenerate_baseline",
                    "value": _round(standard_z),
                    "alert": bool(standard_z is not None and standard_z >= float(thresholds.get("z_elevated", 2.0))),
                },
                "robust_z": {
                    "status": "completed" if _finite(robust_z) is not None else "degenerate_baseline",
                    "value": _round(robust_z),
                    "alert": bool(robust_z is not None and robust_z >= float(thresholds.get("robust_z_elevated", 2.0))),
                },
                "ewma": {
                    "status": "completed" if ewma is not None else "insufficient_residual_history",
                    "lambda": float(thresholds.get("ewma_lambda", 0.3)),
                    "control_limit_sigma": float(thresholds.get("ewma_limit_sigma", 3.0)),
                    "alert": bool(ewma_alert),
                },
                "bayesian_change_point": {
                    "status": "completed" if change_probability is not None else "insufficient_residual_history",
                    "hazard_periods": int(cadence_config["periods_per_year"]),
                    "maximum_run_length": int(cadence_config["periods_per_year"]) * 2,
                    "alert": bool(bayesian_alert),
                },
            },
        },
        "risk": risk,
        "signal_level": signal_level,
        "confidence": risk["confidence"],
        "candidate": bool(candidate or reappearing),
        "reappearing": bool(reappearing),
        "unusual": bool(unusual),
        "evidence_links": ([{"title": str(first.get("source_label") or "Source data"), "url": str(first.get("source_url"))}] if first.get("source_url") else []),
    }
    return SeriesEvaluation(assessment)


def _ledger_identity(group: pd.DataFrame) -> dict[str, Any]:
    ordered = group.copy()
    ordered["time"] = pd.to_datetime(ordered.get("time"), errors="coerce", utc=True)
    ordered = ordered.sort_values("time")
    first = ordered.iloc[-1]
    cadence = cadence_from_granularity(first.get("temporal_granularity"), ordered["time"])
    valid_dates = ordered["time"].dropna()
    return {
        "series_code": first.get("series_code"),
        "geography_key": first.get("geography_key"),
        "dimension_key": first.get("dimension_key"),
        "disease_id": first.get("disease_id"),
        "disease_name": first.get("disease_name") or first.get("disease_id"),
        "country_code": first.get("country_code"),
        "country_name": first.get("country_name") or first.get("country_code"),
        "source_system": first.get("source_system"),
        "source_label": first.get("source_label"),
        "metric_type": first.get("metric_type"),
        "unit": first.get("unit"),
        "cadence": cadence,
        "observation_count": int(len(ordered)),
        "data_start": pd.Timestamp(valid_dates.min()).date().isoformat() if not valid_dates.empty else None,
        "data_through": pd.Timestamp(valid_dates.max()).date().isoformat() if not valid_dates.empty else None,
    }


def evaluate_frame_with_ledger(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    as_of: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    if frame.empty:
        return [], {"empty": 1}, []
    group_columns = [column for column in ("series_code", "geography_key", "dimension_key") if column in frame.columns]
    assessments: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    ledger: list[dict[str, Any]] = []
    for _, group in frame.groupby(group_columns, dropna=False):
        identity = _ledger_identity(group)
        result = evaluate_series(group, config, as_of=as_of)
        if result.assessment:
            assessments.append(result.assessment)
            ledger.append(
                {
                    **identity,
                    "status": "analyzed",
                    "assessment_id": result.assessment.get("id"),
                    "candidate": bool(result.assessment.get("candidate")),
                    "statistics": result.assessment.get("statistics") or {},
                    "risk": result.assessment.get("risk") or {},
                }
            )
        else:
            reason = result.rejection_reason or "unknown"
            rejected[reason] = rejected.get(reason, 0) + 1
            ledger.append({**identity, "status": "rejected", "rejection_reason": reason})
    assessments.sort(
        key=lambda row: (
            bool(row.get("candidate")),
            row.get("risk", {}).get("score", 0),
            row.get("statistics", {}).get("detector_votes", 0),
            row.get("window", {}).get("absolute_change", 0),
        ),
        reverse=True,
    )
    return assessments, rejected, ledger


def evaluate_frame(frame: pd.DataFrame, config: dict[str, Any], *, as_of: date | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    assessments, rejected, _ = evaluate_frame_with_ledger(frame, config, as_of=as_of)
    return assessments, rejected


def summarize_analysis_ledger(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    method_names = (
        "seasonal_baseline",
        "standard_z",
        "robust_z",
        "ewma",
        "bayesian_change_point",
    )
    analyzed = [row for row in ledger if row.get("status") == "analyzed"]
    methods: dict[str, dict[str, int]] = {}
    for method_name in method_names:
        executions = [
            ((row.get("statistics") or {}).get("methods") or {}).get(method_name) or {}
            for row in analyzed
        ]
        methods[method_name] = {
            "executed_count": sum(1 for item in executions if item.get("status") in {"completed", "degenerate_baseline"}),
            "completed_count": sum(1 for item in executions if item.get("status") == "completed"),
            "alert_count": sum(1 for item in executions if item.get("alert") is True),
            "unavailable_count": sum(1 for item in executions if item.get("status") not in {"completed", "degenerate_baseline"}),
        }
    source_usage: dict[str, dict[str, Any]] = {}
    for row in ledger:
        source = str(row.get("source_system") or "unknown")
        usage = source_usage.setdefault(source, {"series_count": 0, "analyzed_count": 0, "rejected_count": 0, "candidate_count": 0, "rejection_reasons": {}})
        usage["series_count"] += 1
        if row.get("status") == "analyzed":
            usage["analyzed_count"] += 1
            usage["candidate_count"] += int(bool(row.get("candidate")))
        else:
            usage["rejected_count"] += 1
            reason = str(row.get("rejection_reason") or "unknown")
            reasons = usage["rejection_reasons"]
            reasons[reason] = int(reasons.get(reason, 0)) + 1
    return {
        "series_count": len(ledger),
        "analyzed_count": len(analyzed),
        "rejected_count": len(ledger) - len(analyzed),
        "methods": methods,
        "source_usage": source_usage,
    }


def analyze_series(frame: pd.DataFrame, config: dict[str, Any], *, as_of: date | None = None) -> dict[str, Any] | None:
    result = evaluate_series(frame, config, as_of=as_of)
    if result.assessment and result.assessment.get("candidate"):
        return result.assessment
    return None


def analyze_frame(frame: pd.DataFrame, config: dict[str, Any], *, as_of: date | None = None) -> list[dict[str, Any]]:
    assessments, _ = evaluate_frame(frame, config, as_of=as_of)
    return [row for row in assessments if row.get("candidate")]
