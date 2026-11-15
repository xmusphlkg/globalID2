"""Assess observation batches without acquiring a session or committing facts."""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
import json
import math
from typing import Any, Iterable, Mapping

from .identity import (
    _as_utc, _json_safe, _observation_allows_authoritative_revision,
    _periods_are_consecutive, _previous_period, _period_range,
)

_QUALITY_MODES = {"off", "report", "quarantine", "fail_closed"}
_REGISTRY_COVERAGE_MODES = {"auto", "required", "legacy_only"}
_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class SeriesObservationQualityIssue:
    """One machine-readable anomaly found before an observation batch is committed."""

    code: str
    severity: str
    stage: str
    message: str
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "details": _json_safe(self.details),
        }


@dataclass(frozen=True)
class SeriesObservationQualityReport:
    """Structured pre-save and projected-post-save quality assessment."""

    country_code: str
    source_row_count: int
    analyzed_observations: int
    history_observations: int
    issues: tuple[SeriesObservationQualityIssue, ...] = ()

    @property
    def highest_severity(self) -> str | None:
        if not self.issues:
            return None
        return max(
            (issue.severity for issue in self.issues),
            key=lambda severity: _SEVERITY_RANK.get(severity, -1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "source_row_count": self.source_row_count,
            "analyzed_observations": self.analyzed_observations,
            "history_observations": self.history_observations,
            "highest_severity": self.highest_severity,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class SeriesObservationQualityPolicy:
    """Configurable guardrails for source-series imports.

    ``report`` never blocks a write. ``quarantine`` blocks only critical,
    high-confidence batch anomalies. ``fail_closed`` additionally blocks
    error-level anomalies. ``registry_coverage=required`` promotes any mapping
    or Registry synchronization loss to critical even for a one-row batch;
    ``legacy_only`` is the explicit exemption for sources outside Registry
    scope. Warnings such as an isolated time gap remain report-only in every
    mode so legitimate sparse and zero-valued series are not rejected.
    """

    mode: str = "quarantine"
    minimum_cross_series: int = 5
    critical_zero_tail_periods: int = 3
    coverage_drop_ratio: float = 0.5
    low_mapping_coverage_ratio: float = 0.5
    history_lookback_days: int = 740
    max_examples: int = 20
    registry_coverage: str = "auto"
    material_revision_drop_ratio: float = 0.8

    def __post_init__(self) -> None:
        normalized_mode = str(self.mode).strip().casefold().replace("-", "_")
        if normalized_mode not in _QUALITY_MODES:
            raise ValueError(
                "Series observation quality mode must be one of: "
                + ", ".join(sorted(_QUALITY_MODES))
            )
        object.__setattr__(self, "mode", normalized_mode)
        normalized_coverage = (
            str(self.registry_coverage).strip().casefold().replace("-", "_")
        )
        if normalized_coverage not in _REGISTRY_COVERAGE_MODES:
            raise ValueError(
                "Registry coverage must be one of: "
                + ", ".join(sorted(_REGISTRY_COVERAGE_MODES))
            )
        object.__setattr__(self, "registry_coverage", normalized_coverage)
        if self.minimum_cross_series < 2:
            raise ValueError("minimum_cross_series must be at least 2")
        if self.critical_zero_tail_periods < 2:
            raise ValueError("critical_zero_tail_periods must be at least 2")
        if not 0 < self.coverage_drop_ratio < 1:
            raise ValueError("coverage_drop_ratio must be between 0 and 1")
        if not 0 < self.low_mapping_coverage_ratio < 1:
            raise ValueError("low_mapping_coverage_ratio must be between 0 and 1")
        if self.history_lookback_days < 0:
            raise ValueError("history_lookback_days cannot be negative")
        if self.max_examples < 1:
            raise ValueError("max_examples must be positive")
        if not 0 < self.material_revision_drop_ratio < 1:
            raise ValueError("material_revision_drop_ratio must be between 0 and 1")

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | None
    ) -> "SeriesObservationQualityPolicy":
        raw = dict(value or {})
        allowed = {
            "mode",
            "minimum_cross_series",
            "critical_zero_tail_periods",
            "coverage_drop_ratio",
            "low_mapping_coverage_ratio",
            "history_lookback_days",
            "max_examples",
            "registry_coverage",
            "material_revision_drop_ratio",
        }
        selected = {key: raw[key] for key in allowed if key in raw}
        for key in {
            "minimum_cross_series",
            "critical_zero_tail_periods",
            "history_lookback_days",
            "max_examples",
        }:
            if key in selected:
                selected[key] = int(selected[key])
        for key in {
            "coverage_drop_ratio",
            "low_mapping_coverage_ratio",
            "material_revision_drop_ratio",
        }:
            if key in selected:
                selected[key] = float(selected[key])
        return cls(**selected)

    def blocks(self, report: SeriesObservationQualityReport) -> bool:
        if self.mode in {"off", "report"}:
            return False
        threshold = 3 if self.mode == "quarantine" else 2
        return any(
            _SEVERITY_RANK.get(issue.severity, -1) >= threshold
            for issue in report.issues
        )


class SeriesObservationQualityError(RuntimeError):
    """Raised when a fail-closed observation quality policy rejects a batch."""

    def __init__(self, report: SeriesObservationQualityReport) -> None:
        self.report = report
        payload = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
        super().__init__(f"Disease series observation quality gate failed: {payload}")


class SeriesObservationQuarantinedError(SeriesObservationQualityError):
    """Raised when a critical batch is quarantined before transaction commit."""


def _mapping_coverage_issues(
    *,
    observation_count: int,
    source_row_count: int,
    skipped_unmatched: int,
    skipped_ambiguous: int,
    skipped_invalid: int,
    skipped_registry_not_synced: int,
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    if policy.registry_coverage == "legacy_only":
        return []
    if policy.registry_coverage == "required":
        required_failures = {
            "skipped_unmatched": skipped_unmatched,
            "skipped_ambiguous": skipped_ambiguous,
            "skipped_invalid": skipped_invalid,
            "skipped_registry_not_synced": skipped_registry_not_synced,
        }
        if source_row_count > 0 and (
            observation_count == 0 or any(required_failures.values())
        ):
            return [
                SeriesObservationQualityIssue(
                    code="required_registry_coverage_incomplete",
                    severity="critical",
                    stage="pre_save",
                    message=(
                        "A source declared as Registry-covered did not resolve and "
                        "stage every source row; the dual write is fail-closed."
                    ),
                    details={
                        "source_rows": source_row_count,
                        "resolved_observations": observation_count,
                        **required_failures,
                    },
                )
            ]
    if source_row_count < policy.minimum_cross_series:
        return []
    usable_ratio = observation_count / source_row_count if source_row_count else 1.0
    if usable_ratio >= policy.low_mapping_coverage_ratio:
        return []
    return [
        SeriesObservationQualityIssue(
            code="batch_mapping_coverage_low",
            severity="warning",
            stage="pre_save",
            message=(
                "Fewer than the configured share of source rows resolved to a "
                "synced surveillance series."
            ),
            details={
                "source_rows": source_row_count,
                "resolved_observations": observation_count,
                "resolved_ratio": round(usable_ratio, 6),
                "minimum_ratio": policy.low_mapping_coverage_ratio,
                "skipped_unmatched": skipped_unmatched,
                "skipped_ambiguous": skipped_ambiguous,
                "skipped_invalid": skipped_invalid,
                "skipped_registry_not_synced": skipped_registry_not_synced,
            },
        )
    ]


def _zero_tail_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Find implausible all-zero tails without flagging isolated series zeros."""

    issues: list[SeriesObservationQualityIssue] = []
    incoming_cohorts = _cohort_period_rows(incoming)
    history_cohorts = _cohort_period_rows(
        history,
        frequency_by_series=_frequency_by_series(incoming),
    )

    for cohort_key, rows_by_time in incoming_cohorts.items():
        frequency = cohort_key[0]
        all_zero_times = {
            report_time
            for report_time, period_rows in rows_by_time.items()
            if _period_is_cross_series_zero(period_rows, policy.minimum_cross_series)
        }
        if not all_zero_times:
            continue

        ordered_times = sorted(rows_by_time)
        zero_tail: list[datetime] = []
        for report_time in reversed(ordered_times):
            if report_time not in all_zero_times:
                break
            if zero_tail and not _periods_are_consecutive(
                report_time, zero_tail[-1], frequency
            ):
                break
            zero_tail.append(report_time)
        zero_tail.reverse()
        if not zero_tail:
            continue

        latest = zero_tail[-1]
        latest_rows = rows_by_time[latest]
        series_codes = sorted({str(row["series_code"]) for row in latest_rows})
        details = {
            "frequency": frequency,
            "geography_key": cohort_key[1],
            "dimension_key": cohort_key[2],
            "unit": cohort_key[3],
            "tail_period_count": len(zero_tail),
            "tail_start": zero_tail[0].isoformat(),
            "tail_end": latest.isoformat(),
            "series_count": len(series_codes),
            "series_examples": series_codes[: policy.max_examples],
        }

        history_by_time = history_cohorts.get(cohort_key, {})
        overwritten_rows = history_by_time.get(latest, [])
        overwritten = {
            str(row["series_code"]): row
            for row in overwritten_rows
            if row.get("value") is not None and not row.get("suppressed")
        }
        overwritten_positive = sorted(
            code
            for code in series_codes
            if code in overwritten and float(overwritten[code]["value"]) > 0
        )
        if overwritten_positive:
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_zero_overwrites_positive",
                    severity="critical",
                    stage="pre_save",
                    message=(
                        "An all-zero multi-disease period would overwrite existing "
                        "positive observations."
                    ),
                    details={
                        **details,
                        "positive_overwrite_count": len(overwritten_positive),
                        "positive_overwrite_examples": overwritten_positive[
                            : policy.max_examples
                        ],
                    },
                )
            )
            continue

        # "Sudden" requires a baseline. Prefer the period immediately before
        # the zero tail from the same source batch, then fall back to stored
        # history. This keeps a long run of perfectly legitimate rare-disease
        # zeros reportable without quarantining it merely for being zero.
        prior_time = _previous_period(zero_tail[0], frequency)
        prior_rows = rows_by_time.get(prior_time, []) if prior_time else []
        prior_stage = "incoming_batch"
        if not prior_rows and prior_time:
            prior_rows = history_by_time.get(prior_time, [])
            prior_stage = "stored_history"
        prior_by_series = {
            str(row["series_code"]): row
            for row in prior_rows
            if row.get("value") is not None and not row.get("suppressed")
        }
        overlapping = [code for code in series_codes if code in prior_by_series]
        prior_positive = [
            code for code in overlapping if float(prior_by_series[code]["value"]) > 0
        ]
        has_positive_baseline = (
            len(overlapping) >= policy.minimum_cross_series and bool(prior_positive)
        )
        baseline_details = (
            {
                "prior_period": prior_time.isoformat(),
                "prior_stage": prior_stage,
                "prior_overlap_count": len(overlapping),
                "prior_positive_count": len(prior_positive),
                "prior_positive_examples": prior_positive[: policy.max_examples],
            }
            if prior_time and overlapping
            else {}
        )

        if len(zero_tail) >= policy.critical_zero_tail_periods:
            issues.append(
                SeriesObservationQualityIssue(
                    code=(
                        "cross_series_all_zero_tail"
                        if has_positive_baseline
                        else "cross_series_all_zero_tail_unverified"
                    ),
                    severity="critical" if has_positive_baseline else "error",
                    stage="pre_save",
                    message=(
                        "A multi-period, multi-disease zero tail follows a positive "
                        "baseline and is likely an upstream missing-to-zero failure."
                        if has_positive_baseline
                        else "A multi-period, multi-disease batch is all zero, but "
                        "no adjacent positive baseline is available; source "
                        "verification is required before fail-closed imports."
                    ),
                    details={**details, **baseline_details},
                )
            )
        elif has_positive_baseline:
            issues.append(
                SeriesObservationQualityIssue(
                    code="sudden_cross_series_all_zero_period",
                    severity="error",
                    stage="pre_save",
                    message=(
                        "Many disease series simultaneously changed from a prior "
                        "period containing positives to all zero."
                    ),
                    details={**details, **baseline_details},
                )
            )
        elif len(zero_tail) >= 2:
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_all_zero_tail_short",
                    severity="error",
                    stage="pre_save",
                    message=(
                        "Several consecutive periods are zero across many disease "
                        "series and require source verification."
                    ),
                    details=details,
                )
            )
        else:
            # A single period is report-only. In particular, a legitimate zero
            # for one disease can never reach the cross-series threshold here.
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_all_zero_period",
                    severity="warning",
                    stage="pre_save",
                    message=(
                        "One period is zero across many disease series; retained as "
                        "a warning because a single all-zero period can be valid."
                    ),
                    details=details,
                )
            )

    return issues


def _single_series_revision_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Reject destructive same-identity revisions unless explicitly authorized.

    Period-to-period disease counts can legitimately fall to zero. The high
    confidence failure mode is narrower: a refresh overwriting an already
    stored positive value at the same natural fact identity with zero or a
    material retreat. That catches parser/source regressions without inventing
    epidemiological constraints.
    """

    history_by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in history:
        try:
            identity = (
                _as_utc(row["time"]),
                str(row["series_code"]),
                str(row.get("geography_key") or ""),
                str(row.get("dimension_key") or "all"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        history_by_identity[identity] = row

    issues: list[SeriesObservationQualityIssue] = []
    for row in incoming:
        identity = (
            _as_utc(row["time"]),
            str(row["series_code"]),
            str(row.get("geography_key") or ""),
            str(row.get("dimension_key") or "all"),
        )
        previous = history_by_identity.get(identity)
        if previous is None or row.get("suppressed") or previous.get("suppressed"):
            continue
        if _observation_allows_authoritative_revision(row):
            continue
        try:
            old_value = float(previous["value"])
            new_value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(old_value) or not math.isfinite(new_value) or old_value <= 0:
            continue

        drop_ratio = (old_value - new_value) / old_value
        if new_value == 0:
            code = "single_series_positive_overwritten_by_zero"
            message = (
                "A source refresh would overwrite a stored positive observation "
                "with zero without explicit correction semantics."
            )
        elif drop_ratio >= policy.material_revision_drop_ratio:
            code = "single_series_material_revision_retreat"
            message = (
                "A source refresh would materially reduce a stored observation "
                "without explicit correction semantics."
            )
        else:
            continue
        issues.append(
            SeriesObservationQualityIssue(
                code=code,
                severity="critical",
                stage="pre_save",
                message=message,
                details={
                    "series_code": identity[1],
                    "time": identity[0].isoformat(),
                    "geography_key": identity[2],
                    "dimension_key": identity[3],
                    "stored_value": old_value,
                    "incoming_value": new_value,
                    "drop_ratio": round(drop_ratio, 6),
                    "required_override": "authoritative_revision",
                },
            )
        )
    return issues


def _time_completeness_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Report inferred batch gaps and gaps in the projected stored timeline."""

    if not incoming:
        return []
    issues: list[SeriesObservationQualityIssue] = []
    frequency_by_series = _frequency_by_series(incoming)
    cohorts = _cohort_period_rows(incoming)
    missing_batch_period_examples: list[dict[str, Any]] = []
    coverage_drop_examples: list[dict[str, Any]] = []
    missing_batch_period_count = 0
    coverage_drop_period_count = 0

    for cohort_key, rows_by_time in cohorts.items():
        frequency = cohort_key[0]
        if frequency not in {"daily", "weekly", "monthly", "quarterly", "annual"}:
            continue
        ordered = sorted(rows_by_time)
        if len(ordered) < 2:
            continue
        expected = _period_range(ordered[0], ordered[-1], frequency)
        missing = [item for item in expected if item not in rows_by_time]
        missing_batch_period_count += len(missing)
        remaining_examples = policy.max_examples - len(missing_batch_period_examples)
        for report_time in missing[: max(0, remaining_examples)]:
            missing_batch_period_examples.append(
                {
                    "frequency": frequency,
                    "geography_key": cohort_key[1],
                    "dimension_key": cohort_key[2],
                    "time": report_time.isoformat(),
                }
            )

        series_counts = {
            report_time: len({str(row["series_code"]) for row in period_rows})
            for report_time, period_rows in rows_by_time.items()
        }
        maximum = max(series_counts.values(), default=0)
        if maximum >= policy.minimum_cross_series and len(series_counts) >= 2:
            cutoff = maximum * (1 - policy.coverage_drop_ratio)
            for report_time, count in sorted(series_counts.items()):
                if count <= cutoff:
                    coverage_drop_period_count += 1
                    if len(coverage_drop_examples) < policy.max_examples:
                        coverage_drop_examples.append(
                            {
                                "frequency": frequency,
                                "geography_key": cohort_key[1],
                                "dimension_key": cohort_key[2],
                                "time": report_time.isoformat(),
                                "series_count": count,
                                "maximum_series_count": maximum,
                            }
                        )

    if missing_batch_period_examples:
        issues.append(
            SeriesObservationQualityIssue(
                code="incoming_batch_period_gap",
                severity="warning",
                stage="pre_save",
                message=(
                    "The incoming batch has inferred calendar periods with no "
                    "observations between its first and last period."
                ),
                details={
                    "missing_period_count": missing_batch_period_count,
                    "examples": missing_batch_period_examples,
                    "inference_note": (
                        "Expected periods are inferred from series frequency; pass "
                        "a source-specific review for intentionally sparse extracts."
                    ),
                },
            )
        )
    if coverage_drop_examples:
        issues.append(
            SeriesObservationQualityIssue(
                code="incoming_batch_series_coverage_drop",
                severity="warning",
                stage="pre_save",
                message=(
                    "One or more periods contain substantially fewer disease series "
                    "than another period in the same batch."
                ),
                details={
                    "period_count": coverage_drop_period_count,
                    "configured_drop_ratio": policy.coverage_drop_ratio,
                    "examples": coverage_drop_examples,
                },
            )
        )

    projected_by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in [*history, *incoming]:
        try:
            identity = (
                _as_utc(row["time"]),
                str(row["series_code"]),
                str(row.get("geography_key") or ""),
                str(row.get("dimension_key") or "all"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        projected_by_identity[identity] = row

    incoming_times = [_as_utc(row["time"]) for row in incoming]
    window_start, window_end = min(incoming_times), max(incoming_times)
    projected_series: dict[tuple[str, str, str], set[datetime]] = {}
    for identity in projected_by_identity:
        report_time, series_code, geography_key, dimension_key = identity
        if window_start <= report_time <= window_end:
            projected_series.setdefault(
                (series_code, geography_key, dimension_key), set()
            ).add(report_time)

    gap_examples: list[dict[str, Any]] = []
    total_gap_count = 0
    affected_series: set[str] = set()
    for (series_code, geography_key, dimension_key), times in projected_series.items():
        frequency = frequency_by_series.get(series_code, "unknown")
        if frequency not in {"daily", "weekly", "monthly", "quarterly", "annual"}:
            continue
        ordered = sorted(times)
        if len(ordered) < 2:
            continue
        missing = [
            item
            for item in _period_range(ordered[0], ordered[-1], frequency)
            if item not in times
        ]
        if not missing:
            continue
        affected_series.add(series_code)
        total_gap_count += len(missing)
        for report_time in missing:
            if len(gap_examples) >= policy.max_examples:
                break
            gap_examples.append(
                {
                    "series_code": series_code,
                    "frequency": frequency,
                    "geography_key": geography_key,
                    "dimension_key": dimension_key,
                    "time": report_time.isoformat(),
                }
            )

    if total_gap_count:
        issues.append(
            SeriesObservationQualityIssue(
                code="projected_series_time_gap",
                severity="warning",
                stage="projected_post_save",
                message=(
                    "The timeline that would exist after this upsert still contains "
                    "one or more inferred series gaps."
                ),
                details={
                    "affected_series_count": len(affected_series),
                    "missing_period_count": total_gap_count,
                    "series_examples": sorted(affected_series)[: policy.max_examples],
                    "gap_examples": gap_examples,
                },
            )
        )
    return issues


def _frequency_by_series(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in observations:
        series_code = str(row.get("series_code") or "")
        metadata = row.get("metadata") or row.get("metadata_") or {}
        frequency = (
            str(metadata.get("frequency") or "unknown").strip().casefold()
            if isinstance(metadata, Mapping)
            else "unknown"
        )
        if series_code:
            result[series_code] = frequency
    return result


def _cohort_period_rows(
    observations: Iterable[Mapping[str, Any]],
    *,
    frequency_by_series: Mapping[str, str] | None = None,
) -> dict[tuple[str, str, str, str], dict[datetime, list[dict[str, Any]]]]:
    rows = [dict(row) for row in observations]
    frequencies = dict(frequency_by_series or _frequency_by_series(rows))
    cohorts: dict[tuple[str, str, str, str], dict[datetime, list[dict[str, Any]]]] = {}
    for row in rows:
        series_code = str(row.get("series_code") or "")
        if not series_code or row.get("time") is None:
            continue
        metadata = row.get("metadata") or row.get("metadata_") or {}
        frequency = frequencies.get(series_code)
        if not frequency and isinstance(metadata, Mapping):
            frequency = str(metadata.get("frequency") or "unknown").casefold()
        cohort_key = (
            frequency or "unknown",
            str(row.get("geography_key") or ""),
            str(row.get("dimension_key") or "all"),
            str(row.get("unit") or "count"),
        )
        cohorts.setdefault(cohort_key, {}).setdefault(_as_utc(row["time"]), []).append(
            row
        )
    return cohorts


def _period_is_cross_series_zero(
    rows: list[dict[str, Any]], minimum_cross_series: int
) -> bool:
    series_codes: set[str] = set()
    for row in rows:
        if row.get("suppressed") or row.get("value") is None:
            return False
        try:
            if float(row["value"]) != 0:
                return False
        except (TypeError, ValueError):
            return False
        series_codes.add(str(row.get("series_code") or ""))
    series_codes.discard("")
    return len(series_codes) >= minimum_cross_series
