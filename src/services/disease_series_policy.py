"""Shared semantic rules for projecting surveillance series.

Dashboard APIs and the static-site exporter use different public DTOs, but
they must make exactly the same decisions about which source series may fill a
flat ``cases`` curve.  Keeping those decisions here prevents the two adapters
from drifting while the legacy table is retired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SERIES_CASE_COUNT_METRICS = frozenset(
    {
        "aids_classifications",
        "case_notifications",
        "exposure_notifications",
        "hiv_diagnoses",
        "hospitalized_case_notifications",
        "pregnancy_notifications",
        "sentinel_case_notifications",
        "survey_positive_cases",
    }
)
SAFE_MULTI_SERIES_AGGREGATION_POLICIES = frozenset({"sum_disjoint"})


@dataclass(frozen=True)
class SeriesProjectionSelection:
    """One deterministic, provenance-preserving projection decision."""

    selected_codes: frozenset[str]
    projection_policy: str
    loss_risk: str | None


def is_case_count_series(record: Mapping[str, Any]) -> bool:
    """Return whether a typed source fact can truthfully populate ``cases``."""

    series_unit = str(record.get("series_unit") or record.get("unit") or "").lower()
    observation_unit = str(
        record.get("observation_unit")
        or record.get("series_unit")
        or record.get("unit")
        or ""
    ).lower()
    return (
        series_unit == "count"
        and observation_unit == "count"
        and str(record.get("metric_type") or "").lower() in SERIES_CASE_COUNT_METRICS
    )


def select_series_projection(
    source_series: Sequence[Mapping[str, Any]],
) -> SeriesProjectionSelection:
    """Select a safe flat view without silently summing non-additive series.

    ``source_series`` contains one definition summary per series.  Adapters may
    expose recency as ``coverage_end`` or as a ``dates`` array; both forms are
    accepted so API and static export selection remain identical.
    """

    if not source_series:
        raise ValueError("source_series must contain at least one definition")

    codes = {str(item.get("series_code") or "").strip() for item in source_series}
    codes.discard("")
    if len(codes) != len(source_series):
        raise ValueError("source_series must contain one unique non-empty series_code")

    if len(source_series) == 1:
        return SeriesProjectionSelection(
            selected_codes=frozenset(codes),
            projection_policy="single_series",
            loss_risk=None,
        )

    policies = {str(item.get("aggregation_policy") or "none") for item in source_series}
    if policies and policies <= SAFE_MULTI_SERIES_AGGREGATION_POLICIES:
        return SeriesProjectionSelection(
            selected_codes=frozenset(codes),
            projection_policy="sum_disjoint",
            loss_risk=None,
        )

    reported = [
        item
        for item in source_series
        if item.get("aggregation_policy") == "reported_aggregate"
    ]
    candidates = sorted(
        reported or source_series,
        key=lambda item: str(item.get("series_code") or ""),
    )
    selected = max(
        candidates,
        key=lambda item: (
            int(item.get("observation_count") or 0),
            _coverage_end(item),
        ),
    )
    policy = (
        "reported_aggregate_preferred"
        if selected.get("aggregation_policy") == "reported_aggregate"
        else "representative_series"
    )
    return SeriesProjectionSelection(
        selected_codes=frozenset({str(selected["series_code"])}),
        projection_policy=policy,
        loss_risk="non_additive_series_not_rolled_up",
    )


def _coverage_end(item: Mapping[str, Any]) -> str:
    direct = item.get("coverage_end")
    if direct:
        return str(direct)
    dates = item.get("dates")
    if isinstance(dates, Sequence) and not isinstance(dates, (str, bytes)) and dates:
        return str(dates[-1])
    return ""


__all__ = [
    "SAFE_MULTI_SERIES_AGGREGATION_POLICIES",
    "SERIES_CASE_COUNT_METRICS",
    "SeriesProjectionSelection",
    "is_case_count_series",
    "select_series_projection",
]
