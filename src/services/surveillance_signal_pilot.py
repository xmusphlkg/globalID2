"""Offline, monthly surveillance diagnostics; no publication or delivery effects.

Only individual registered source series are eligible. A current snapshot cannot
reconstruct what a source had actually published at an earlier date, so replay
results must never be presented as measured early-warning performance.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from statistics import mean, stdev
from typing import Any


@dataclass(frozen=True)
class SignalPolicy:
    baseline_years: int = 5
    minimum_baseline_years: int = 3
    sigma: float = 2.0
    minimum_increase: float = 10.0
    minimum_ratio: float = 1.5
    minimum_recent_months: int = 10
    maximum_age_days: int = 62

    def __post_init__(self) -> None:
        if not 3 <= self.minimum_baseline_years <= self.baseline_years <= 10:
            raise ValueError("Baseline requires 3–10 prior years")
        if not 1 <= self.minimum_recent_months <= 12 or self.maximum_age_days < 0:
            raise ValueError("Invalid completeness or freshness policy")
        if not all(isfinite(v) for v in (self.sigma, self.minimum_increase, self.minimum_ratio)):
            raise ValueError("Thresholds must be finite")
        if self.sigma <= 0 or self.minimum_increase <= 0 or self.minimum_ratio < 1:
            raise ValueError("Thresholds must require an increase")


_ACCEPTED_QUALITY = frozenset({"raw", "final", "validated"})


def _month_index(value: date) -> int:
    return value.year * 12 + value.month - 1


def _month_start(index: int) -> date:
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) and value >= 0 and value.is_integer() else None


def _points(series: dict[str, Any]) -> dict[int, tuple[float | None, str]]:
    dates = series.get("dates")
    values = series.get("values")
    qualities = series.get("point_quality_statuses")
    if not all(isinstance(items, list) for items in (dates, values, qualities)):
        raise ValueError("Missing per-observation dates, values or quality")
    if not len(dates) == len(values) == len(qualities):
        raise ValueError("Per-observation arrays differ in length")
    points = {}
    for text, value, quality in zip(dates, values, qualities):
        observed = date.fromisoformat(text)
        index = _month_index(observed)
        if index in points:
            raise ValueError("Multiple observations in one source month")
        points[index] = (_number(value), str(quality or "").lower())
    return points


def _usable(point: tuple[float | None, str] | None) -> bool:
    return point is not None and point[0] is not None and point[1] in _ACCEPTED_QUALITY


def _diagnostic(
    points: dict[int, tuple[float | None, str]],
    current: int,
    as_of: date,
    policy: SignalPolicy,
) -> dict[str, Any]:
    start = _month_start(current)
    end_exclusive = _month_start(current + 1)
    result: dict[str, Any] = {
        "period": start.isoformat(),
        "period_end_exclusive": end_exclusive.isoformat(),
        "status": "skipped",
        "is_signal": None,
    }
    if end_exclusive > as_of:
        return {**result, "reason": "incomplete_period"}
    if (as_of - end_exclusive).days > policy.maximum_age_days:
        return {**result, "reason": "stale_observation"}
    point = points[current]
    if not _usable(point):
        return {**result, "reason": "missing_or_unaccepted_quality", "quality_status": point[1]}
    recent = sum(_usable(points.get(current - offset)) for offset in range(12))
    if recent < policy.minimum_recent_months:
        return {**result, "reason": "incomplete_recent_history", "usable_recent_months": recent}
    baseline = [
        (current - 12 * year, points.get(current - 12 * year))
        for year in range(1, policy.baseline_years + 1)
    ]
    baseline = [(index, item) for index, item in baseline if _usable(item)]
    if len(baseline) < policy.minimum_baseline_years:
        return {**result, "reason": "insufficient_same_month_history", "baseline_count": len(baseline)}
    counts = [item[0] for _, item in baseline]
    average = mean(counts)
    deviation = stdev(counts)
    observed = point[0]
    threshold = max(
        average + policy.sigma * deviation,
        average + policy.minimum_increase,
        average * policy.minimum_ratio,
    )
    return {
        **result,
        "status": "candidate" if observed > threshold else "within_threshold",
        "is_signal": observed > threshold,
        "observed_value": observed,
        "quality_status": point[1],
        "baseline_mean": round(average, 4),
        "baseline_standard_deviation": round(deviation, 4),
        "threshold": round(threshold, 4),
        "baseline_count": len(baseline),
        "baseline_periods": [_month_start(index).isoformat() for index, _ in baseline],
        "usable_recent_months": recent,
        "reason": "above_all_thresholds" if observed > threshold else "not_above_all_thresholds",
    }


def evaluate_snapshot(
    snapshot: dict[str, Any], *, as_of: date, replay: bool = False,
    policy: SignalPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate each source independently; never merge diseases or source versions."""
    policy = policy or SignalPolicy()
    results: list[dict[str, Any]] = []
    if not isinstance(snapshot, dict):
        raise ValueError("Expected an exported country snapshot object")
    diseases = snapshot.get("disease_series")
    if not isinstance(diseases, dict) or not snapshot.get("country_code"):
        raise ValueError("Expected an exported country snapshot with disease_series")
    for disease_id, disease in sorted(diseases.items()):
        if not isinstance(disease, dict):
            raise ValueError("Each disease_series entry must be an object")
        sources = disease.get("source_series", [])
        if not isinstance(sources, list):
            raise ValueError("source_series must be a list")
        if not sources:
            results.append({"disease_id": disease_id, "status": "skipped", "is_signal": None,
                            "reason": "no_registered_source_series"})
        for series in sources:
            if not isinstance(series, dict):
                raise ValueError("Each source_series entry must be an object")
            identity = {"disease_id": disease_id, "series_code": series.get("series_code"),
                        "selection_key": series.get("selection_key"),
                        "dimension_key": series.get("dimension_key") or "all",
                        "dimensions": series.get("dimensions") or {},
                        "source_system": series.get("source_system"),
                        "definition_version": series.get("definition_version"),
                        "geography_key": series.get("geography_key")}
            reason = None
            if series.get("temporal_granularity") != "monthly":
                reason = "unsupported_frequency"
            elif series.get("metric_type") != "case_notifications" or series.get("unit") != "count":
                reason = "unsupported_metric"
            elif not all(identity.get(key) for key in ("series_code", "source_system", "definition_version", "geography_key")):
                reason = "incomplete_source_identity"
            elif identity["definition_version"] in {"source-current", "unknown"}:
                reason = "unversioned_definition"
            elif series.get("comparability_break"):
                reason = "comparability_break"
            if reason:
                results.append({**identity, "status": "skipped", "is_signal": None, "reason": reason})
                continue
            try:
                points = _points(series)
            except (ValueError, TypeError, OverflowError) as exc:
                results.append({**identity, "status": "skipped", "is_signal": None,
                                "reason": "invalid_observations", "detail": str(exc)})
                continue
            cutoff = _month_index(as_of)
            candidates = sorted(index for index in points if index <= cutoff)
            if not candidates:
                results.append({**identity, "status": "skipped", "is_signal": None,
                                "reason": "no_observations_as_of_date"})
                continue
            # Ignore future placeholders. An observed, incomplete current month is
            # reported as such rather than silently replaced by an older value.
            for index in candidates if replay else candidates[-1:]:
                evaluation_date = _month_start(index + 1) if replay and index < cutoff else as_of
                results.append({**identity, **_diagnostic(points, index, evaluation_date, policy)})
    reasons = Counter(row["reason"] for row in results if row["status"] == "skipped")
    evaluated = sum(row["is_signal"] is not None for row in results)
    signals = sum(row["is_signal"] is True for row in results)
    return {
        "schema_version": "surveillance_signal_pilot.v1",
        "method": "same_calendar_month_mean_sd_with_absolute_and_ratio_gates",
        "country_code": snapshot["country_code"],
        "as_of": as_of.isoformat(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "mode": "retrospective_current_snapshot" if replay else "snapshot_diagnostic",
        "official_confirmation": False,
        "labels": {"zh": "算法信号，非官方确认", "en": "Algorithmic signal, not official confirmation",
                   "fr": "Signal algorithmique, sans confirmation officielle"},
        "limitations": [
            "Internal monthly pilot; thresholds have not been calibrated against confirmed outbreaks.",
            "Source publication dates and historical data vintages are unavailable; no measured lead time, sensitivity or false-positive rate.",
            "Raw observations are eligible; provisional, missing and unknown-quality observations are skipped.",
            "Zero-variance baselines use absolute and ratio gates; no sigma score is inferred.",
        ],
        "policy": asdict(policy),
        "summary": {"evaluated": evaluated, "candidates": signals,
                    "candidate_rate": round(signals / evaluated, 4) if evaluated else None,
                    "skipped": sum(reasons.values()), "skip_reasons": dict(sorted(reasons.items()))},
        "results": results,
    }
