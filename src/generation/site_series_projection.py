"""Pure Series-first projection logic for static site exports.

This module deliberately has no database or filesystem dependencies.  The
legacy ``scripts.generate_site_data`` module re-exports its symbols so callers
can migrate without a flag day.
"""

from __future__ import annotations

import math
from collections import defaultdict

from src.core.disease_cutover import get_disease_cutover_config
from src.core.reporting_period import report_period_key, selected_series_granularity
from src.services.disease_series_policy import (
    PRIORITY_OVERLAY_POLICY,
    SOURCE_OBSERVATIONS_ONLY_POLICY,
    TEMPORAL_HANDOFF_POLICY,
    is_case_count_metric,
    is_case_count_series,
    select_series_projection,
)

SERIES_DATA_LAYER = "series_registry"
LEGACY_DATA_LAYER = "legacy_fallback"
MIXED_DATA_LAYER = "mixed"
LEGACY_GAP_FILL_DATA_LAYER = "legacy_gap_fill"


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    except (TypeError, ValueError):
        return None


def _avg_or_none(values: list[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _dominant_value(values: list[str | None]) -> str | None:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[value] += 1
    return max(counts.items(), key=lambda item: item[1])[0] if counts else None


def _normalise_count(value) -> int | float | None:
    """Keep integer counts compact without discarding valid fractional values."""

    numeric = _safe_float(value)
    if numeric is None:
        return None
    return int(numeric) if numeric.is_integer() else numeric


def _sum_counts(values: list[object]) -> int | float | None:
    """Sum observed counts while preserving an entirely missing period."""

    numbers = [_safe_float(value) for value in values]
    observed = [value for value in numbers if value is not None]
    return _normalise_count(sum(observed)) if observed else None


def _series_is_case_count(record: dict) -> bool:
    return is_case_count_series(record)


def _source_series_details(records: list[dict]) -> list[dict]:
    """Build lossless, JSON-ready source-series payloads for one concept."""

    by_series: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        series_code = str(record.get("series_code") or "").strip()
        if series_code:
            by_series[series_code].append(record)

    metadata_fields = (
        "source_system", "source_series_code", "source_label",
        "definition_version", "case_definition", "case_definition_uri",
        "metric_type", "reporting_basis", "time_basis", "temporal_granularity",
        "mapping_relation", "comparability", "aggregation_policy",
        "availability_status", "missing_value_policy", "valid_from", "valid_to",
        "definition_effective_from", "definition_effective_to",
        "comparability_break",
        "comparability_set", "projection_policy", "projection_priority",
        "series_is_active",
    )
    details: list[dict] = []
    for series_code in sorted(by_series):
        series_records = sorted(
            by_series[series_code], key=lambda item: str(item.get("date") or "")
        )
        first = series_records[0]
        dates = [item.get("date") for item in series_records if item.get("date")]
        dated_records = [item for item in series_records if item.get("date")]
        values = [_normalise_count(item.get("cases")) for item in dated_records]
        point_quality_statuses = [
            str(item.get("quality_status") or item.get("data_quality") or "unknown")
            for item in dated_records
        ]
        quality_statuses = sorted(
            {
                str(item.get("quality_status") or item.get("data_quality") or "")
                for item in series_records
                if item.get("quality_status") or item.get("data_quality")
            }
        )
        details.append(
            {
                "series_code": series_code,
                **{field: first.get(field) for field in metadata_fields},
                "unit": first.get("series_unit") or first.get("unit"),
                "geography_key": first.get("geography_key"),
                "dimension_key": first.get("dimension_key"),
                "dates": dates,
                "values": values,
                "total_value": sum(value for value in values if value is not None),
                "observation_count": len(values),
                "quality_statuses": quality_statuses,
                "point_quality_statuses": point_quality_statuses,
                "provisional_from": _trailing_provisional_from(
                    dates, point_quality_statuses
                ),
                "latest_quality_status": (
                    point_quality_statuses[-1] if point_quality_statuses else None
                ),
            }
        )
    return details


def _trailing_provisional_from(
    dates: list[str], quality_statuses: list[str]
) -> str | None:
    """Return the start of a trailing source-declared provisional run.

    ``raw`` is an internal validation state, not evidence that the publisher
    considers an observation preliminary.  Treating it as provisional made a
    single open period pull the boundary back across an otherwise unclassified
    historical series.
    """

    provisional = {"provisional", "preliminary"}
    start: str | None = None
    for date, status in reversed(list(zip(dates, quality_statuses))):
        if str(status or "").strip().lower() not in provisional:
            break
        start = date
    return start


def _representative_series_code(source_series: list[dict]) -> str:
    """Choose one deterministic conservative view when rollup is unsafe."""

    selection = select_series_projection(source_series)
    if len(selection.selected_codes) != 1:
        raise ValueError("representative series selection must choose one series")
    return next(iter(selection.selected_codes))


def _projection_context(series_records: list[dict]) -> tuple[set[str], dict]:
    source_series = _source_series_details(series_records)
    selection = select_series_projection(source_series)
    selected_codes = set(selection.selected_codes)
    period_granularity = selected_series_granularity(source_series, selected_codes)
    projection_policy = selection.projection_policy
    if projection_policy == "single_series":
        note_en = "The public curve is read directly from one registered source series."
        note_zh = "公开曲线直接读取自一个已注册的来源序列。"
    elif projection_policy == "sum_disjoint":
        note_en = "Registered source series are summed under an explicit disjoint-series policy."
        note_zh = "多个来源序列依据明确的互斥可加策略进行汇总。"
    elif projection_policy == TEMPORAL_HANDOFF_POLICY:
        note_en = "Equivalent registered source series are joined across strictly non-overlapping validity windows."
        note_zh = "口径一致的已注册来源序列按严格不重叠的有效期接力拼接。"
    elif projection_policy == PRIORITY_OVERLAY_POLICY:
        note_en = (
            "Retained source series are overlaid per period using their explicit "
            "source priority; lower-priority observations remain downloadable."
        )
        note_zh = "各来源原始序列均予保留；公开曲线按每期明确的来源优先级覆盖。"
    elif projection_policy == SOURCE_OBSERVATIONS_ONLY_POLICY:
        note_en = (
            "The registered definitions are narrower non-additive source series "
            "without a reported aggregate. They remain available as source "
            "observations, but no generic public cases curve is inferred."
        )
        note_zh = (
            "已注册定义均为不可相加的窄口径来源序列，且没有来源报告的汇总序列；"
            "这些事实仅作为来源观测保留，不推导通用病例公开曲线。"
        )
    else:
        note_en = (
            "Multiple registered series are not declared safely additive. "
            "The compatibility curve uses one representative series; inspect "
            "source_series for every retained definition."
        )
        note_zh = (
            "多个已注册序列未声明为可安全相加。兼容曲线仅采用一个代表序列；"
            "所有独立口径均保留在 source_series 中。"
        )
    return selected_codes, {
        "data_layer": SERIES_DATA_LAYER,
        "projection_policy": projection_policy,
        "loss_risk": selection.loss_risk,
        "selected_series_codes": sorted(selected_codes),
        "available_series_count": len(source_series),
        "source_series": source_series,
        "period_granularity": period_granularity,
        "note_en": note_en,
        "note_zh": note_zh,
    }


def _collapse_selected_series_records(records: list[dict], context: dict) -> list[dict]:
    """Collapse only the explicitly selected series to the legacy chart grain."""

    selected_codes = set(context.get("selected_series_codes") or [])
    period_granularity = context.get("period_granularity")
    selected = [r for r in records if r.get("series_code") in selected_codes]
    seen: set[tuple[str, str]] = set()
    for record in selected:
        identity = (
            str(record.get("series_code") or ""),
            report_period_key(record.get("date"), period_granularity),
        )
        if identity in seen:
            raise RuntimeError(
                "Source series has multiple observations at the public date grain: "
                f"{identity[0]} {identity[1]}"
            )
        seen.add(identity)

    by_period: dict[str, list[dict]] = defaultdict(list)
    for record in selected:
        if record.get("date"):
            by_period[
                report_period_key(record["date"], period_granularity)
            ].append(record)

    projected: list[dict] = []
    for period in sorted(by_period):
        date_records = by_period[period]
        present_codes = {str(r.get("series_code") or "") for r in date_records}
        if len(selected_codes) > 1:
            if context.get("projection_policy") == PRIORITY_OVERLAY_POLICY:
                date_records = [
                    max(
                        date_records,
                        key=lambda record: (
                            int(record.get("projection_priority") or 0),
                            str(record.get("series_code") or ""),
                        ),
                    )
                ]
            elif context.get("projection_policy") == TEMPORAL_HANDOFF_POLICY:
                if len(present_codes) != 1:
                    raise RuntimeError(
                        "Temporal handoff series overlap at the public date grain: "
                        f"{period}"
                    )
            elif present_codes != selected_codes:
                continue
        first = date_records[0]
        report_date = max(str(record.get("date")) for record in date_records)
        incidence_values = [_safe_float(r.get("incidence_rate")) for r in date_records]
        incidence_values = [value for value in incidence_values if value is not None]
        projected.append(
            {
                "date": report_date,
                "year_month": first.get("year_month") or report_date[:7],
                "disease_id": first.get("disease_id"),
                "cases": _sum_counts([r.get("cases") for r in date_records]),
                "deaths": None,
                "recoveries": None,
                "incidence_rate": sum(incidence_values) if incidence_values else None,
                "incidence_rate_source": _dominant_value(
                    [r.get("incidence_rate_source") for r in date_records]
                ) or "missing_population",
                "mortality_rate": None,
                "data_quality": _dominant_value(
                    [r.get("quality_status") or r.get("data_quality") for r in date_records]
                ),
                "metric_type": first.get("metric_type"),
                "reporting_basis": first.get("reporting_basis"),
                "time_basis": first.get("time_basis"),
                "temporal_granularity": (
                    first.get("temporal_granularity")
                    or context.get("period_granularity")
                ),
                "comparability": first.get("comparability"),
                "definition_version": first.get("definition_version"),
                "data_layer": SERIES_DATA_LAYER,
                "series_code": (
                    first.get("series_code")
                    if len(selected_codes) == 1
                    or context.get("projection_policy") in {
                        TEMPORAL_HANDOFF_POLICY, PRIORITY_OVERLAY_POLICY,
                    }
                    else None
                ),
                "_series_context": context,
            }
        )
    return projected


def _attach_legacy_supplemental_metrics(
    projected: list[dict], context: dict, legacy_records: list[dict]
) -> dict:
    """Retain non-case legacy metrics without reintroducing case duplication."""

    period_granularity = context.get("period_granularity")
    legacy_by_period: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_period[
                report_period_key(record["date"], period_granularity)
            ].append(record)
    dates = sorted(str(record.get("date")) for record in legacy_records if record.get("date"))
    context["supplemental_legacy_metrics"] = {
        "dates": dates,
        "deaths": [
            record.get("deaths") or 0
            for record in sorted(legacy_records, key=lambda item: str(item.get("date") or ""))
            if record.get("date")
        ],
        "recoveries": [
            record.get("recoveries") or 0
            for record in sorted(legacy_records, key=lambda item: str(item.get("date") or ""))
            if record.get("date")
        ],
        "mortality_rates": [
            record.get("mortality_rate")
            for record in sorted(legacy_records, key=lambda item: str(item.get("date") or ""))
            if record.get("date")
        ],
    }
    safe_alignment = context.get("projection_policy") in {
        "single_series", "sum_disjoint", TEMPORAL_HANDOFF_POLICY,
        PRIORITY_OVERLAY_POLICY,
    }
    context["metric_layers"] = {
        "cases": SERIES_DATA_LAYER,
        "deaths": LEGACY_DATA_LAYER if safe_alignment and legacy_records else (
            "supplemental_legacy_only" if legacy_records else "not_available"
        ),
        "recoveries": LEGACY_DATA_LAYER if safe_alignment and legacy_records else (
            "supplemental_legacy_only" if legacy_records else "not_available"
        ),
    }
    if safe_alignment:
        for record in projected:
            period = report_period_key(record.get("date"), period_granularity)
            candidates = legacy_by_period.get(period) or []
            exact = str(record.get("date") or "")
            legacy = [
                next(
                    (item for item in candidates if str(item.get("date") or "") == exact),
                    max(candidates, key=lambda item: str(item.get("date") or "")),
                )
            ] if candidates else []
            record["deaths"] = _sum_counts([item.get("deaths") for item in legacy])
            record["recoveries"] = _sum_counts(
                [item.get("recoveries") for item in legacy]
            )
            record["mortality_rate"] = _avg_or_none([item.get("mortality_rate") for item in legacy])
    return context


def _semantic_value(value: object, *, granularity: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if granularity and normalized == "yearly":
        return "annual"
    return normalized


def _partition_compatible_legacy_records(
    context: dict,
    legacy_records: list[dict],
) -> tuple[list[dict], list[dict], list[str]]:
    """Keep only legacy rows whose declared grain and measure are compatible.

    Older countries often have no structured legacy semantics at all.  Missing
    declarations therefore remain backward compatible, while an explicit
    mismatch is never allowed to extend a registered curve.  This is especially
    important for Iceland, where notification counts and primary-care diagnoses
    coexist under the same ontology concept.
    """

    selected_codes = set(context.get("selected_series_codes") or [])
    selected_series = [
        item
        for item in context.get("source_series") or []
        if str(item.get("series_code") or "") in selected_codes
    ]
    field_specs = (
        ("temporal_granularity", True),
        ("metric_type", False),
        ("reporting_basis", False),
    )
    expected: dict[str, str] = {}
    reasons: set[str] = set()
    for field, is_granularity in field_specs:
        values = {
            _semantic_value(item.get(field), granularity=is_granularity)
            for item in selected_series
            if _semantic_value(item.get(field), granularity=is_granularity)
        }
        if len(values) == 1:
            expected[field] = next(iter(values))
        elif len(values) > 1:
            reasons.add(f"selected_series_mixed_{field}")

    if reasons:
        return [], list(legacy_records), sorted(reasons)

    compatible: list[dict] = []
    blocked: list[dict] = []
    for record in legacy_records:
        record_reasons: list[str] = []
        for field, is_granularity in field_specs:
            actual = _semantic_value(
                record.get(field), granularity=is_granularity
            )
            if actual and expected.get(field) and actual != expected[field]:
                record_reasons.append(f"{field}_mismatch")
        if _semantic_value(record.get("comparability")) == "not_comparable":
            record_reasons.append("legacy_not_comparable")
        if record_reasons:
            blocked.append(record)
            reasons.update(record_reasons)
        else:
            compatible.append(record)
    return compatible, blocked, sorted(reasons)


def _overlay_legacy_coverage_gaps(
    projected: list[dict], context: dict, legacy_records: list[dict]
) -> list[dict]:
    """Overlay registry facts by period without sacrificing legacy coverage."""

    period_granularity = context.get("period_granularity")
    registry_periods = {
        report_period_key(r.get("date"), period_granularity)
        for r in projected
        if r.get("date")
    }
    legacy_by_period: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_period[
                report_period_key(record["date"], period_granularity)
            ].append(record)
    legacy_periods = set(legacy_by_period)
    gap_periods = sorted(legacy_periods - registry_periods)
    overlap_periods = legacy_periods & registry_periods
    context.update(
        {
            "coverage_policy": "source_period_overlay",
            "coverage_status": "legacy_gap_fill" if gap_periods else "parity",
            "legacy_period_count": len(legacy_periods),
            "registry_period_count": len(registry_periods),
            "overlap_period_count": len(overlap_periods),
            "legacy_gap_fill_count": len(gap_periods),
            "registry_only_period_count": len(registry_periods - legacy_periods),
            "coverage_ratio_against_legacy": round(
                len(overlap_periods) / len(legacy_periods) if legacy_periods else 1.0, 6
            ),
        }
    )
    if not gap_periods:
        return projected
    if not registry_periods:
        context["registry_projection_policy"] = context.get("projection_policy")
        context["data_layer"] = LEGACY_DATA_LAYER
        context["projection_policy"] = "legacy_fallback"
        context["coverage_status"] = "registry_no_complete_periods"
        context["fallback_reason"] = "incomplete_registered_rollup_periods"
        context["coverage_risk"] = "registry_history_incomplete"
        context["metric_layers"]["cases"] = LEGACY_DATA_LAYER
        result: list[dict] = []
        for period in gap_periods:
            for source_record in legacy_by_period[period]:
                record = dict(source_record)
                record["data_layer"] = LEGACY_DATA_LAYER
                record["_series_context"] = context
                result.append(record)
        return result

    context["data_layer"] = MIXED_DATA_LAYER
    context["coverage_risk"] = "registry_history_incomplete"
    context["metric_layers"]["cases"] = MIXED_DATA_LAYER
    context["note_en"] = (
        f"{context.get('note_en') or ''} Registry facts replace matching periods; "
        "legacy-only periods are retained as explicit coverage gap fills."
    ).strip()
    context["note_zh"] = (
        f"{context.get('note_zh') or ''} 注册序列替换同一期旧事实；仅旧表存在的期间"
        "作为明确的覆盖缺口补全予以保留。"
    ).strip()
    result = list(projected)
    for period in gap_periods:
        for source_record in legacy_by_period[period]:
            record = dict(source_record)
            record["data_layer"] = LEGACY_GAP_FILL_DATA_LAYER
            record["_series_context"] = context
            record["gap_fill_reason"] = "registry_period_missing"
            result.append(record)
    return result


def _legacy_projection_context(
    *, registry_series_fact_count: int = 0,
    reason: str = "no_eligible_registered_series_facts",
) -> dict:
    return {
        "data_layer": LEGACY_DATA_LAYER,
        "projection_policy": "legacy_fallback",
        "loss_risk": "legacy_identity_may_be_lossy",
        "selected_series_codes": [],
        "available_series_count": registry_series_fact_count,
        "source_series": [],
        "fallback_reason": reason,
        "metric_layers": {
            "cases": LEGACY_DATA_LAYER,
            "deaths": LEGACY_DATA_LAYER,
            "recoveries": LEGACY_DATA_LAYER,
        },
        "note_en": (
            "No eligible registered case-count series facts are available for "
            "this disease and country; the public curve uses the legacy flat table."
        ),
        "note_zh": "该疾病与国家尚无可用的注册病例计数序列事实；公开曲线受控回退到旧扁平事实表。",
    }


def apply_series_first_projection(
    legacy_records: list[dict], series_records: list[dict]
) -> list[dict]:
    """Overlay eligible registry facts without mixing or double counting layers."""

    legacy_by_disease: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        disease_id = str(record.get("disease_id") or "").strip()
        if disease_id:
            legacy_by_disease[disease_id].append(record)
    all_series: dict[str, list[dict]] = defaultdict(list)
    case_count_series: dict[str, list[dict]] = defaultdict(list)
    eligible_series: dict[str, list[dict]] = defaultdict(list)
    for record in series_records:
        disease_id = str(record.get("disease_id") or "").strip()
        if disease_id:
            all_series[disease_id].append(record)
            if is_case_count_metric(record):
                case_count_series[disease_id].append(record)
            if _series_is_case_count(record):
                eligible_series[disease_id].append(record)

    result: list[dict] = []
    for disease_id in sorted(
        set(legacy_by_disease) | set(case_count_series) | set(eligible_series)
    ):
        eligible = eligible_series.get(disease_id) or []
        if eligible:
            _selected, context = _projection_context(eligible)
            retained_source_series = _source_series_details(
                all_series.get(disease_id) or eligible
            )
            context["source_series"] = retained_source_series
            context["available_series_count"] = len(retained_source_series)
            context["non_projection_series_codes"] = sorted(
                {
                    str(item.get("series_code") or "")
                    for item in retained_source_series
                    if str(item.get("series_code") or "")
                    not in context["selected_series_codes"]
                }
            )
            if context["projection_policy"] == SOURCE_OBSERVATIONS_ONLY_POLICY:
                # Typed source observations are exported separately.  A legacy
                # flat row must not disguise the absence of a safe rollup.
                continue
            projected = _collapse_selected_series_records(eligible, context)
            legacy_candidates = legacy_by_disease.get(disease_id) or []
            compatible_legacy, blocked_legacy, incompatibility_reasons = (
                _partition_compatible_legacy_records(context, legacy_candidates)
            )
            _attach_legacy_supplemental_metrics(
                projected, context, compatible_legacy
            )
            overlaid = _overlay_legacy_coverage_gaps(
                projected, context, compatible_legacy
            )
            context["coverage_policy"] = "compatible_source_period_overlay"
            context["legacy_gap_fill_blocked_count"] = len(blocked_legacy)
            context["legacy_incompatibility_reasons"] = incompatibility_reasons
            if blocked_legacy:
                context["note_en"] = (
                    f"{context.get('note_en') or ''} Legacy rows with a different "
                    "reporting grain or comparison basis were not joined to this curve."
                ).strip()
                context["note_zh"] = (
                    f"{context.get('note_zh') or ''} 与所选序列报告粒度或比较口径不同的"
                    "旧表记录未拼接到该曲线。"
                ).strip()
                if not compatible_legacy:
                    context["coverage_status"] = (
                        "legacy_gap_fill_blocked_incompatible"
                    )
            result.extend(overlaid)
            continue
        if case_count_series.get(disease_id):
            # A typed case-count fact with an unsafe ontology relation remains
            # downloadable as a source observation.  It must not be replaced
            # by an opaque legacy cases row.
            continue
        raw_count = len(all_series.get(disease_id) or [])
        context = _legacy_projection_context(
            registry_series_fact_count=raw_count,
            reason=(
                "registered_facts_not_case_count_compatible"
                if raw_count else "no_eligible_registered_series_facts"
            ),
        )
        retained_source_series = _source_series_details(
            all_series.get(disease_id) or []
        )
        context["source_series"] = retained_source_series
        context["available_series_count"] = len(retained_source_series)
        context["non_projection_series_codes"] = sorted(
            str(item.get("series_code") or "")
            for item in retained_source_series
            if item.get("series_code")
        )
        for legacy_record in legacy_by_disease.get(disease_id) or []:
            record = dict(legacy_record)
            record["data_layer"] = LEGACY_DATA_LAYER
            record["_series_context"] = context
            result.append(record)
    projected_records = sorted(
        result,
        key=lambda item: (str(item.get("date") or ""), str(item.get("disease_id") or "")),
    )
    validate_series_first_projection(projected_records)
    return projected_records


def apply_disease_cutover_projection(
    legacy_records: list[dict], series_records: list[dict], *, country_code: str
) -> list[dict]:
    """Apply the versioned per-concept cutover policy to a country export."""

    config = get_disease_cutover_config()
    country = str(country_code or "").strip().upper()
    disease_ids = {
        str(r.get("disease_id") or "").strip().upper()
        for r in [*legacy_records, *series_records]
        if r.get("disease_id")
    }
    policies = {
        disease_id: config.resolve_read_policy(country, disease_id)
        for disease_id in disease_ids
    }
    filtered_legacy = [
        r for r in legacy_records
        if policies[str(r.get("disease_id") or "").strip().upper()].read_mode != "series_only"
    ]
    filtered_series = [
        r for r in series_records
        if policies[str(r.get("disease_id") or "").strip().upper()].read_mode != "legacy"
    ]
    accepted: list[dict] = []
    for record in apply_series_first_projection(filtered_legacy, filtered_series):
        disease_id = str(record.get("disease_id") or "").strip().upper()
        policy = policies[disease_id]
        context = record.get("_series_context") or {}
        selected_codes = set(context.get("selected_series_codes") or [])
        blocked: list[str] = []
        missing = sorted(set(policy.required_series) - selected_codes)
        if missing:
            blocked.append("missing_required_series:" + ",".join(missing))
        if (
            policy.allowed_projection_policy
            and context.get("projection_policy")
            != policy.allowed_projection_policy
        ):
            blocked.append(
                "projection_policy_mismatch:"
                f"expected={policy.allowed_projection_policy},"
                f"actual={context.get('projection_policy')}"
            )
        context["cutover"] = {
            "release_version": config.release_version,
            "read_mode": policy.read_mode,
            "shadow_compare": policy.shadow_compare,
            "target_override": policy.target_override,
            "required_series": list(policy.required_series),
            "allowed_projection_policy": policy.allowed_projection_policy,
            "blocked_reasons": blocked,
        }
        if policy.read_mode != "series_only" or not blocked:
            accepted.append(record)
    validate_series_first_projection(accepted)
    return accepted


def validate_series_first_projection(records: list[dict]) -> None:
    """Enforce public-export invariants before charts can silently sum rows."""

    seen: set[tuple[str, str]] = set()
    layers: dict[str, set[str]] = defaultdict(set)
    record_layers: dict[str, set[str]] = defaultdict(set)
    for record in records:
        disease_id = str(record.get("disease_id") or "").strip()
        report_date = str(record.get("date") or "").strip()
        if not disease_id or not report_date:
            continue
        identity = (disease_id, report_date)
        if identity in seen:
            raise RuntimeError(
                "Series-first site projection produced duplicate disease/date "
                f"identity: {disease_id} {report_date}"
            )
        seen.add(identity)
        context = record.get("_series_context")
        if not isinstance(context, dict):
            raise TypeError(f"Site projection lacks data-layer provenance for {disease_id}")
        layer = str(context.get("data_layer") or "").strip()
        layers[disease_id].add(layer)
        record_layers[disease_id].add(str(record.get("data_layer") or layer).strip())
        policy = str(context.get("projection_policy") or "")
        selected = context.get("selected_series_codes") or []
        if layer in {SERIES_DATA_LAYER, MIXED_DATA_LAYER}:
            if not selected:
                raise RuntimeError(f"Registry projection has no selected series for {disease_id}")
            if len(selected) > 1 and policy not in {
                "sum_disjoint", TEMPORAL_HANDOFF_POLICY, PRIORITY_OVERLAY_POLICY,
            }:
                raise RuntimeError(
                    "Multiple source series reached the flat public curve without "
                    f"an explicit multi-series policy for {disease_id}"
                )
            if policy in {
                "representative_series",
                "reported_aggregate_preferred",
            } and not context.get("loss_risk"):
                raise RuntimeError(
                    "Conservative series selection is not risk-labelled for "
                    f"{disease_id}"
                )
        if layer == MIXED_DATA_LAYER:
            if context.get("coverage_status") != "legacy_gap_fill":
                raise RuntimeError(
                    "Mixed projection lacks a gap-fill coverage status for "
                    f"{disease_id}"
                )
            if int(context.get("legacy_gap_fill_count") or 0) < 1:
                raise RuntimeError(
                    f"Mixed projection lacks an explicit legacy gap count for {disease_id}"
                )

    invalid = sorted(
        disease_id
        for disease_id, values in layers.items()
        if len(values) > 1 and MIXED_DATA_LAYER not in values
    )
    if invalid:
        raise RuntimeError(
            "Site projection mixed independent provenance contexts: "
            + ", ".join(invalid)
        )
    malformed = sorted(
        d for d, values in layers.items()
        if MIXED_DATA_LAYER in values
        and record_layers[d] != {SERIES_DATA_LAYER, LEGACY_GAP_FILL_DATA_LAYER}
    )
    if malformed:
        raise RuntimeError(
            "Mixed projection does not contain both registry and explicit gap-fill rows: "
            + ", ".join(malformed)
        )
