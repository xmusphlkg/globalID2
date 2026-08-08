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
        "clinical_diagnoses",
        "exposure_notifications",
        "hiv_diagnoses",
        "hospitalized_case_notifications",
        "laboratory_diagnoses",
        "pregnancy_notifications",
        "reported_diagnoses",
        "sentinel_case_notifications",
        "survey_positive_cases",
    }
)
GENERIC_CASE_MAPPING_RELATIONS = frozenset({"exact", "narrower"})
UNSAFE_GENERIC_CASE_MAPPING_RELATIONS = frozenset(
    {"broader", "related", "aggregate", "ambiguous", "unmapped"}
)
SAFE_MULTI_SERIES_AGGREGATION_POLICIES = frozenset({"sum_disjoint"})
SOURCE_OBSERVATIONS_ONLY_POLICY = "source_observations_only"


@dataclass(frozen=True)
class SeriesProjectionSelection:
    """One deterministic, provenance-preserving projection decision."""

    selected_codes: frozenset[str]
    projection_policy: str
    loss_risk: str | None


def is_case_count_metric(record: Mapping[str, Any]) -> bool:
    """Return whether a typed source fact is expressed as a case count."""

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


def is_case_count_series(record: Mapping[str, Any]) -> bool:
    """Return whether a source fact may populate a generic ``cases`` curve.

    Count-compatible metrics are necessary but not sufficient.  A broader,
    related, aggregate, ambiguous, or unmapped source definition is not a
    faithful observation of the target ontology concept and therefore remains
    available only as a typed source observation.  A single narrower series is
    intentionally allowed, with its narrower relation retained in provenance.
    """

    relation = str(record.get("mapping_relation") or "unmapped").strip().lower()
    return is_case_count_metric(record) and relation in GENERIC_CASE_MAPPING_RELATIONS


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

    projectable = [
        item
        for item in source_series
        if str(item.get("mapping_relation") or "unmapped").strip().lower()
        in GENERIC_CASE_MAPPING_RELATIONS
    ]
    if not projectable:
        return SeriesProjectionSelection(
            selected_codes=frozenset(),
            projection_policy=SOURCE_OBSERVATIONS_ONLY_POLICY,
            loss_risk="mapping_relation_not_safe_for_generic_cases",
        )

    if len(projectable) == 1:
        return SeriesProjectionSelection(
            selected_codes=frozenset({str(projectable[0]["series_code"])}),
            projection_policy="single_series",
            loss_risk=None,
        )

    policies = {
        str(item.get("aggregation_policy") or "none").strip().lower()
        for item in projectable
    }
    if policies and policies <= SAFE_MULTI_SERIES_AGGREGATION_POLICIES:
        return SeriesProjectionSelection(
            selected_codes=frozenset(
                str(item["series_code"]) for item in projectable
            ),
            projection_policy="sum_disjoint",
            loss_risk=None,
        )

    reported = [
        item
        for item in projectable
        if str(item.get("aggregation_policy") or "").strip().lower()
        == "reported_aggregate"
    ]
    relations = {
        str(item.get("mapping_relation") or "unmapped").strip().lower()
        for item in projectable
    }
    if (
        relations == {"narrower"}
        and policies == {"non_additive"}
        and not reported
    ):
        return SeriesProjectionSelection(
            selected_codes=frozenset(),
            projection_policy=SOURCE_OBSERVATIONS_ONLY_POLICY,
            loss_risk="narrower_non_additive_series_have_no_safe_rollup",
        )

    exact = [
        item
        for item in projectable
        if str(item.get("mapping_relation") or "").strip().lower() == "exact"
    ]
    candidate_pool = exact or projectable
    reported = [item for item in reported if item in candidate_pool]
    candidates = sorted(
        reported or candidate_pool,
        key=lambda item: str(item.get("series_code") or ""),
    )
    selected = max(
        candidates,
        key=lambda item: (
            _current_series_rank(item),
            _coverage_end(item),
            int(item.get("observation_count") or 0),
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


def _current_series_rank(item: Mapping[str, Any]) -> int:
    """Prefer an explicitly current definition before comparing coverage.

    Static exports include inactive historical definitions so their observations
    remain downloadable.  Those long histories must not beat a current feed
    merely because they contain more rows.  Callers that do not expose an
    active flag remain neutral and are ordered by coverage recency next, which
    preserves compatibility with the dashboard adapter.
    """

    active = item.get("series_is_active")
    if active is None:
        active = item.get("is_active")
    if active is None:
        return 0
    if isinstance(active, str):
        return int(active.strip().lower() in {"1", "true", "yes", "active"})
    return int(bool(active))


__all__ = [
    "GENERIC_CASE_MAPPING_RELATIONS",
    "SAFE_MULTI_SERIES_AGGREGATION_POLICIES",
    "SERIES_CASE_COUNT_METRICS",
    "SOURCE_OBSERVATIONS_ONLY_POLICY",
    "UNSAFE_GENERIC_CASE_MAPPING_RELATIONS",
    "SeriesProjectionSelection",
    "is_case_count_metric",
    "is_case_count_series",
    "select_series_projection",
]
