"""Safe series-first compatibility projection for dashboard disease APIs.

The source-series tables retain definitions that the legacy ``disease_records``
identity cannot represent.  Dashboard charts still expect one value per disease
and report period, so this module builds that compatibility view conservatively:

* one registered case-count series is read directly;
* multiple series are summed only when every series explicitly declares
  ``sum_disjoint`` and every selected component exists in the period;
* otherwise one deterministic representative series is used and every source
  definition remains visible in provenance;
* legacy records fill only period keys that have no safe registry projection.

Keeping this logic outside the router makes the projection independently
testable and prevents comparison endpoints from reintroducing unsafe sums.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.disease_cutover import (
    READ_MODES,
    DiseaseReadPolicy,
    get_disease_cutover_config,
)
from src.domain.country import Country
from src.domain.disease import Disease
from src.domain.disease_ontology import (
    DiseaseSeriesObservation,
    DiseaseSurveillanceSeries,
)
from src.domain.disease_record import DiseaseRecord
from src.services.disease_series_policy import (
    SERIES_CASE_COUNT_METRICS as _SERIES_CASE_COUNT_METRICS,
    is_case_count_series,
    select_series_projection,
)

SERIES_DATA_LAYER = "series_registry"
LEGACY_DATA_LAYER = "legacy_fallback"
MIXED_DATA_LAYER = "mixed"
LEGACY_GAP_FILL_DATA_LAYER = "legacy_gap_fill"
SERIES_CASE_COUNT_METRICS = _SERIES_CASE_COUNT_METRICS


@dataclass(frozen=True)
class SeriesFirstResult:
    disease_code: str
    disease_name: str
    disease_numeric_id: int | None
    country_id: int
    records: list[dict[str, Any]]
    metadata: dict[str, Any]


def _get(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _period_key(value: object) -> str:
    """Return the timezone-stable public report-date identity for overlays.

    Legacy rows historically use noon UTC while series backfills use midnight
    UTC for the same published report date.  Comparing full timestamps would
    duplicate the curve, so the compatibility grain intentionally matches the
    static export's UTC calendar date.
    """

    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        else:
            normalized = normalized.astimezone(timezone.utc)
        return normalized.date().isoformat()
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


def _count(value: object) -> int | float:
    numeric = float(value or 0)
    return int(numeric) if numeric.is_integer() else numeric


def _series_is_case_count(record: Mapping[str, Any]) -> bool:
    return is_case_count_series(record)


def _source_series_details(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        code = str(record.get("series_code") or "").strip()
        if code:
            grouped[code].append(record)

    fields = (
        "source_system",
        "source_series_code",
        "source_label",
        "definition_version",
        "case_definition",
        "case_definition_uri",
        "metric_type",
        "reporting_basis",
        "temporal_granularity",
        "mapping_relation",
        "comparability",
        "aggregation_policy",
        "availability_status",
        "missing_value_policy",
        "valid_from",
        "valid_to",
    )
    details: list[dict[str, Any]] = []
    for code in sorted(grouped):
        definitions = grouped[code]
        observations = sorted(
            (row for row in definitions if row.get("time") is not None),
            key=lambda row: _period_key(row.get("time")),
        )
        first = definitions[0]
        details.append(
            {
                "series_code": code,
                **{field: first.get(field) for field in fields},
                "unit": first.get("series_unit"),
                "observation_count": len(observations),
                "coverage_start": (
                    _period_key(observations[0].get("time")) if observations else None
                ),
                "coverage_end": (
                    _period_key(observations[-1].get("time")) if observations else None
                ),
                "total_value": _count(
                    sum(float(row.get("value") or 0) for row in observations)
                ),
                "quality_statuses": sorted(
                    {
                        str(row.get("quality_status"))
                        for row in observations
                        if row.get("quality_status")
                    }
                ),
            }
        )
    return details


def _select_series(
    source_series: Sequence[Mapping[str, Any]],
) -> tuple[set[str], str, str | None]:
    selection = select_series_projection(source_series)
    return (
        set(selection.selected_codes),
        selection.projection_policy,
        selection.loss_risk,
    )


def _legacy_record(item: object) -> dict[str, Any]:
    fields = (
        "time",
        "disease_id",
        "country_id",
        "cases",
        "deaths",
        "recoveries",
        "active_cases",
        "new_cases",
        "new_deaths",
        "new_recoveries",
        "incidence_rate",
        "mortality_rate",
        "recovery_rate",
        "region",
        "city",
        "data_source",
        "data_quality",
        "confidence_score",
    )
    return {field: _get(item, field) for field in fields}


def _decorate(
    record: dict[str, Any],
    *,
    data_layer: str,
    context: Mapping[str, Any],
    gap_fill_reason: str | None = None,
) -> dict[str, Any]:
    result = dict(record)
    result["data_layer"] = data_layer
    result["projection_policy"] = context["projection_policy"]
    result["series_codes"] = list(context["selected_series_codes"])
    result["loss_risk"] = context.get("loss_risk")
    result["coverage"] = dict(context["coverage"])
    result["provenance"] = {
        "selected_series_codes": list(context["selected_series_codes"]),
        "source_series": list(context["source_series"]),
        "available_series_count": context["available_series_count"],
        "metric_layers": dict(context["metric_layers"]),
        "fallback_reason": context.get("fallback_reason"),
    }
    result["gap_fill_reason"] = gap_fill_reason
    return result


def project_series_first_records(
    legacy_records: Sequence[object],
    series_records: Sequence[Mapping[str, Any]],
    *,
    disease_numeric_id: int,
    country_id: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project one disease/country without hiding legacy coverage gaps."""

    legacy = [_legacy_record(item) for item in legacy_records]
    eligible = [record for record in series_records if _series_is_case_count(record)]
    all_source_series = _source_series_details(eligible)

    if not eligible:
        fallback_reason = (
            "registered_facts_not_case_count_compatible"
            if series_records
            else "no_eligible_registered_series_facts"
        )
        context = {
            "data_layer": LEGACY_DATA_LAYER,
            "projection_policy": "legacy_fallback",
            "loss_risk": "legacy_identity_may_be_lossy",
            "selected_series_codes": [],
            "available_series_count": len(
                {str(row.get("series_code")) for row in series_records}
            ),
            "source_series": _source_series_details(series_records),
            "fallback_reason": fallback_reason,
            "metric_layers": {
                "cases": LEGACY_DATA_LAYER,
                "deaths": LEGACY_DATA_LAYER,
                "recoveries": LEGACY_DATA_LAYER,
            },
            "coverage": {
                "status": "legacy_only",
                "legacy_period_count": len(legacy),
                "registry_period_count": 0,
                "overlap_period_count": 0,
                "legacy_gap_fill_count": 0,
                "coverage_ratio_against_legacy": 0.0 if legacy else 1.0,
            },
        }
        projected = [
            _decorate(row, data_layer=LEGACY_DATA_LAYER, context=context)
            for row in legacy
        ]
        return sorted(projected, key=lambda row: _period_key(row.get("time"))), context

    selected_codes, projection_policy, loss_risk = _select_series(all_source_series)
    selected = [
        row
        for row in eligible
        if str(row.get("series_code")) in selected_codes and row.get("time") is not None
    ]
    selected_by_period: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen_source_periods: set[tuple[str, str]] = set()
    for row in selected:
        period = _period_key(row.get("time"))
        identity = (str(row.get("series_code")), period)
        if identity in seen_source_periods:
            raise RuntimeError(
                "Source series has multiple observations at the compatibility grain: "
                f"{identity[0]} {identity[1]}"
            )
        seen_source_periods.add(identity)
        selected_by_period[period].append(row)

    registry: dict[str, dict[str, Any]] = {}
    for period, rows in selected_by_period.items():
        present_codes = {str(row.get("series_code")) for row in rows}
        if len(selected_codes) > 1 and present_codes != selected_codes:
            # A missing disjoint component is unknown, not zero.
            continue
        first = rows[0]
        source_names = sorted(
            {str(row.get("source_system")) for row in rows if row.get("source_system")}
        )
        registry[period] = {
            "time": first.get("time"),
            "disease_id": disease_numeric_id,
            "country_id": country_id,
            "cases": _count(sum(float(row.get("value") or 0) for row in rows)),
            "deaths": None,
            "recoveries": None,
            "active_cases": None,
            "new_cases": None,
            "new_deaths": None,
            "new_recoveries": None,
            "incidence_rate": None,
            "mortality_rate": None,
            "recovery_rate": None,
            "region": None,
            "city": None,
            "data_source": ", ".join(source_names) or None,
            "data_quality": next(
                (
                    str(row.get("quality_status"))
                    for row in rows
                    if row.get("quality_status")
                ),
                None,
            ),
            "confidence_score": None,
        }

    legacy_by_period = {_period_key(row.get("time")): row for row in legacy}
    legacy_periods = set(legacy_by_period)
    registry_periods = set(registry)
    overlap = legacy_periods & registry_periods
    gaps = legacy_periods - registry_periods
    coverage_ratio = len(overlap) / len(legacy_periods) if legacy_periods else 1.0

    if not registry_periods:
        data_layer = LEGACY_DATA_LAYER
        effective_policy = "legacy_fallback"
        fallback_reason = "incomplete_registered_rollup_periods"
        coverage_status = "registry_no_complete_periods"
    elif gaps:
        data_layer = MIXED_DATA_LAYER
        effective_policy = projection_policy
        fallback_reason = None
        coverage_status = "legacy_gap_fill"
    else:
        data_layer = SERIES_DATA_LAYER
        effective_policy = projection_policy
        fallback_reason = None
        coverage_status = "parity"

    safely_aligned_metrics = projection_policy in {"single_series", "sum_disjoint"}
    context = {
        "data_layer": data_layer,
        "projection_policy": effective_policy,
        "registry_projection_policy": projection_policy,
        "loss_risk": loss_risk,
        "selected_series_codes": sorted(selected_codes),
        "available_series_count": len(all_source_series),
        "source_series": all_source_series,
        "fallback_reason": fallback_reason,
        "metric_layers": {
            "cases": data_layer,
            "deaths": (
                LEGACY_DATA_LAYER
                if safely_aligned_metrics and legacy
                else "supplemental_legacy_only" if legacy else "not_available"
            ),
            "recoveries": (
                LEGACY_DATA_LAYER
                if safely_aligned_metrics and legacy
                else "supplemental_legacy_only" if legacy else "not_available"
            ),
        },
        "coverage": {
            "status": coverage_status,
            "legacy_period_count": len(legacy_periods),
            "registry_period_count": len(registry_periods),
            "overlap_period_count": len(overlap),
            "legacy_gap_fill_count": len(gaps),
            "registry_only_period_count": len(registry_periods - legacy_periods),
            "coverage_ratio_against_legacy": round(coverage_ratio, 6),
        },
    }

    if not registry_periods:
        projected = [
            _decorate(row, data_layer=LEGACY_DATA_LAYER, context=context)
            for row in legacy
        ]
        return sorted(projected, key=lambda row: _period_key(row.get("time"))), context

    projected: list[dict[str, Any]] = []
    for period, row in registry.items():
        legacy_row = legacy_by_period.get(period)
        if legacy_row and safely_aligned_metrics:
            row["deaths"] = legacy_row.get("deaths")
            row["recoveries"] = legacy_row.get("recoveries")
            row["mortality_rate"] = legacy_row.get("mortality_rate")
        projected.append(_decorate(row, data_layer=SERIES_DATA_LAYER, context=context))
    for period in gaps:
        projected.append(
            _decorate(
                legacy_by_period[period],
                data_layer=LEGACY_GAP_FILL_DATA_LAYER,
                context=context,
                gap_fill_reason="registry_period_missing",
            )
        )
    return sorted(projected, key=lambda row: _period_key(row.get("time"))), context


async def load_series_first_records(
    db: AsyncSession,
    *,
    disease_code: str,
    country_id: int,
    limit: int | None = None,
    read_mode: str | None = None,
    shadow_compare: bool | None = None,
) -> SeriesFirstResult:
    """Load a cutover-aware compatibility curve.

    ``series_only`` is deliberately strict: its SQL path never reads
    ``disease_records`` and a missing/unsafe Registry projection remains
    unknown instead of being silently filled from the legacy table.

    The optional mode arguments are intended for audits and tests.  Production
    callers normally resolve the versioned country/concept policy.
    """

    disease = (
        await db.execute(select(Disease).where(Disease.name == disease_code))
    ).scalar_one_or_none()
    country = (
        await db.execute(select(Country).where(Country.id == country_id))
    ).scalar_one_or_none()
    if disease is None or country is None:
        return SeriesFirstResult(
            disease_code=disease_code,
            disease_name=disease_code,
            disease_numeric_id=getattr(disease, "id", None),
            country_id=country_id,
            records=[],
            metadata={},
        )

    policy = get_disease_cutover_config().resolve_read_policy(
        country.code, disease_code
    )
    if read_mode is not None:
        normalized_mode = str(read_mode).strip().lower()
        if normalized_mode not in READ_MODES:
            raise ValueError(f"Unsupported disease read mode: {read_mode!r}")
        policy = replace(policy, read_mode=normalized_mode)
    if shadow_compare is not None:
        policy = replace(policy, shadow_compare=bool(shadow_compare))

    legacy: Sequence[object] = []
    if policy.may_query_legacy:
        legacy_query = (
            select(DiseaseRecord)
            .where(
                DiseaseRecord.disease_id == disease.id,
                DiseaseRecord.country_id == country_id,
            )
            .order_by(DiseaseRecord.time)
        )
        legacy = (await db.execute(legacy_query)).scalars().all()

    observation_join = and_(
        DiseaseSeriesObservation.series_code == DiseaseSurveillanceSeries.series_code,
        DiseaseSeriesObservation.geography_key == f"country:{country.code}:national",
        DiseaseSeriesObservation.dimension_key == "all",
        DiseaseSeriesObservation.suppressed.is_(False),
        DiseaseSeriesObservation.value.is_not(None),
        DiseaseSeriesObservation.unit == DiseaseSurveillanceSeries.unit,
        DiseaseSeriesObservation.quality_status != "rejected",
    )
    series: list[dict[str, Any]] = []
    if policy.read_mode != "legacy" or policy.shadow_compare:
        series_query = (
            select(
                DiseaseSeriesObservation.time,
                DiseaseSurveillanceSeries.series_code.label("series_code"),
                DiseaseSeriesObservation.value,
                DiseaseSeriesObservation.unit.label("observation_unit"),
                DiseaseSeriesObservation.quality_status,
                DiseaseSurveillanceSeries.source_system,
                DiseaseSurveillanceSeries.source_series_code,
                DiseaseSurveillanceSeries.source_label,
                DiseaseSurveillanceSeries.definition_version,
                DiseaseSurveillanceSeries.case_definition,
                DiseaseSurveillanceSeries.case_definition_uri,
                DiseaseSurveillanceSeries.metric_type,
                DiseaseSurveillanceSeries.reporting_basis,
                DiseaseSurveillanceSeries.temporal_granularity,
                DiseaseSurveillanceSeries.unit.label("series_unit"),
                DiseaseSurveillanceSeries.mapping_relation,
                DiseaseSurveillanceSeries.comparability,
                DiseaseSurveillanceSeries.aggregation_policy,
                DiseaseSurveillanceSeries.availability_status,
                DiseaseSurveillanceSeries.missing_value_policy,
                DiseaseSurveillanceSeries.valid_from,
                DiseaseSurveillanceSeries.valid_to,
            )
            .select_from(DiseaseSurveillanceSeries)
            .outerjoin(DiseaseSeriesObservation, observation_join)
            .where(
                DiseaseSurveillanceSeries.disease_id == disease_code,
                DiseaseSurveillanceSeries.country_code == country.code,
                DiseaseSurveillanceSeries.is_active.is_(True),
            )
            .order_by(
                DiseaseSeriesObservation.series_code,
                DiseaseSeriesObservation.time,
            )
        )
        rows = (await db.execute(series_query)).all()
        series = [dict(row._mapping) for row in rows]

    if policy.read_mode == "legacy":
        records, metadata = project_series_first_records(
            legacy,
            [],
            disease_numeric_id=disease.id,
            country_id=country_id,
        )
    elif policy.read_mode == "series_only":
        records, metadata = project_series_first_records(
            [],
            series,
            disease_numeric_id=disease.id,
            country_id=country_id,
        )
        metadata = _strict_series_metadata(metadata, records)
    else:
        records, metadata = project_series_first_records(
            legacy,
            series,
            disease_numeric_id=disease.id,
            country_id=country_id,
        )

    strict_records: list[dict[str, Any]] = (
        list(records) if policy.read_mode == "series_only" else []
    )
    strict_metadata: dict[str, Any] = (
        dict(metadata) if policy.read_mode == "series_only" else {}
    )
    if policy.shadow_compare and series and policy.read_mode != "series_only":
        strict_records, strict_metadata = project_series_first_records(
            [],
            series,
            disease_numeric_id=disease.id,
            country_id=country_id,
        )
        strict_metadata = _strict_series_metadata(strict_metadata, strict_records)

    cutover = _cutover_metadata(
        policy,
        metadata=metadata,
        legacy_records=legacy,
        strict_records=strict_records,
        strict_metadata=strict_metadata,
    )
    metadata["cutover"] = cutover
    if policy.read_mode == "series_only" and cutover["blocked_reasons"]:
        records = []
        metadata["coverage"] = {
            **(metadata.get("coverage") or {}),
            "status": "series_only_blocked",
        }
    for record in records:
        provenance = record.setdefault("provenance", {})
        provenance["cutover"] = {
            "release_version": cutover["release_version"],
            "read_mode": cutover["read_mode"],
            "shadow_compare": cutover["shadow_compare"],
            "target_override": cutover["target_override"],
            "blocked_reasons": list(cutover["blocked_reasons"]),
        }
    if limit is not None:
        records = records[:limit]
    return SeriesFirstResult(
        disease_code=disease_code,
        disease_name=disease.name_en or disease.name,
        disease_numeric_id=disease.id,
        country_id=country_id,
        records=records,
        metadata=metadata,
    )


def _strict_series_metadata(
    metadata: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Normalize no-fallback metadata so absence is represented as unknown."""

    result = dict(metadata)
    result["read_mode"] = "series_only"
    result["missing_is_unknown"] = True
    if not records:
        result["data_layer"] = SERIES_DATA_LAYER
        result["projection_policy"] = (
            result.get("registry_projection_policy") or "series_only_unavailable"
        )
        result["fallback_reason"] = None
        result["series_unavailable_reason"] = (
            metadata.get("fallback_reason") or "no_complete_registered_series_periods"
        )
        result["metric_layers"] = {
            "cases": "not_available",
            "deaths": "not_available",
            "recoveries": "not_available",
        }
        result["coverage"] = {
            **(metadata.get("coverage") or {}),
            "status": "series_only_unavailable",
            "legacy_gap_fill_count": 0,
        }
    return result


def _cutover_metadata(
    policy: DiseaseReadPolicy,
    *,
    metadata: Mapping[str, Any],
    legacy_records: Sequence[object],
    strict_records: Sequence[Mapping[str, Any]],
    strict_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    config = get_disease_cutover_config()
    selected_codes = set(
        strict_metadata.get("selected_series_codes")
        or metadata.get("selected_series_codes")
        or []
    )
    blocked_reasons: list[str] = []
    missing_required = sorted(set(policy.required_series) - selected_codes)
    if missing_required:
        blocked_reasons.append("missing_required_series:" + ",".join(missing_required))
    effective_projection = strict_metadata.get("projection_policy") or metadata.get(
        "projection_policy"
    )
    if (
        policy.allowed_projection_policy
        and effective_projection != policy.allowed_projection_policy
    ):
        blocked_reasons.append(
            "projection_policy_mismatch:"
            f"expected={policy.allowed_projection_policy},actual={effective_projection}"
        )
    if policy.read_mode == "series_only" and not strict_records:
        blocked_reasons.append("no_complete_registered_series_periods")

    shadow = (
        _compare_legacy_and_registry(legacy_records, strict_records)
        if policy.shadow_compare and strict_records
        else None
    )
    return {
        "release_version": config.release_version,
        "read_mode": policy.read_mode,
        "shadow_compare": policy.shadow_compare,
        "target_override": policy.target_override,
        "required_series": list(policy.required_series),
        "allowed_projection_policy": policy.allowed_projection_policy,
        "blocked_reasons": blocked_reasons,
        "shadow": shadow,
    }


def _compare_legacy_and_registry(
    legacy_records: Sequence[object],
    registry_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    legacy_by_period = {
        _period_key(_get(record, "time")): _count(_get(record, "cases"))
        for record in legacy_records
        if _get(record, "time") is not None
    }
    registry_by_period = {
        _period_key(record.get("time")): _count(record.get("cases"))
        for record in registry_records
        if record.get("time") is not None
    }
    periods = sorted(set(legacy_by_period) | set(registry_by_period))
    aligned = 0
    differences: list[dict[str, Any]] = []
    legacy_only = 0
    registry_only = 0
    for period in periods:
        legacy_value = legacy_by_period.get(period)
        registry_value = registry_by_period.get(period)
        if period not in registry_by_period:
            classification = "coverage_gap"
            legacy_only += 1
        elif period not in legacy_by_period:
            classification = "registry_only"
            registry_only += 1
        elif legacy_value == registry_value:
            aligned += 1
            continue
        else:
            classification = "value_difference"
        if len(differences) < 20:
            differences.append(
                {
                    "period": period,
                    "classification": classification,
                    "legacy_cases": legacy_value,
                    "registry_cases": registry_value,
                    "delta": (
                        registry_value - legacy_value
                        if legacy_value is not None and registry_value is not None
                        else None
                    ),
                }
            )
    return {
        "legacy_period_count": len(legacy_by_period),
        "registry_period_count": len(registry_by_period),
        "aligned_period_count": aligned,
        "value_difference_count": sum(
            1
            for period in set(legacy_by_period) & set(registry_by_period)
            if legacy_by_period[period] != registry_by_period[period]
        ),
        "legacy_only_period_count": legacy_only,
        "registry_only_period_count": registry_only,
        "difference_samples": differences,
        "samples_truncated": (len(periods) - aligned > len(differences)),
    }


def monthly_comparison_points(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate one already-safe disease curve to calendar months."""

    monthly: dict[str, dict[str, Any]] = {}
    for record in records:
        time_value = record.get("time")
        if not isinstance(time_value, datetime):
            continue
        normalized = (
            time_value.replace(tzinfo=timezone.utc)
            if time_value.tzinfo is None
            else time_value.astimezone(timezone.utc)
        )
        month = normalized.strftime("%Y-%m-01")
        point = monthly.setdefault(
            month, {"time_period": month, "cases": 0, "deaths": 0}
        )
        point["cases"] = _count(float(point["cases"]) + float(record.get("cases") or 0))
        point["deaths"] = _count(
            float(point["deaths"]) + float(record.get("deaths") or 0)
        )
    return [monthly[key] for key in sorted(monthly)]
