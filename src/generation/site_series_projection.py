"""Pure Series-first projection logic for static site exports.

This module deliberately has no database or filesystem dependencies.  The
legacy ``scripts.generate_site_data`` module re-exports its symbols so callers
can migrate without a flag day.
"""

from __future__ import annotations

import math
from collections import defaultdict

from src.core.disease_cutover import get_disease_cutover_config
from src.services.disease_series_policy import (
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


def _normalise_count(value) -> int | float:
    """Keep integer counts compact without discarding valid fractional values."""

    numeric = _safe_float(value)
    if numeric is None:
        return 0
    return int(numeric) if numeric.is_integer() else numeric


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
        "metric_type", "reporting_basis", "temporal_granularity",
        "mapping_relation", "comparability", "aggregation_policy",
        "availability_status", "missing_value_policy", "valid_from", "valid_to",
    )
    details: list[dict] = []
    for series_code in sorted(by_series):
        series_records = sorted(
            by_series[series_code], key=lambda item: str(item.get("date") or "")
        )
        first = series_records[0]
        dates = [item.get("date") for item in series_records if item.get("date")]
        values = [item.get("cases") or 0 for item in series_records if item.get("date")]
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
                "total_value": sum(values),
                "observation_count": len(values),
                "quality_statuses": quality_statuses,
            }
        )
    return details


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
    projection_policy = selection.projection_policy
    if projection_policy == "single_series":
        note_en = "The public curve is read directly from one registered source series."
        note_zh = "公开曲线直接读取自一个已注册的来源序列。"
    elif projection_policy == "sum_disjoint":
        note_en = "Registered source series are summed under an explicit disjoint-series policy."
        note_zh = "多个来源序列依据明确的互斥可加策略进行汇总。"
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
        "note_en": note_en,
        "note_zh": note_zh,
    }


def _collapse_selected_series_records(records: list[dict], context: dict) -> list[dict]:
    """Collapse only the explicitly selected series to the legacy chart grain."""

    selected_codes = set(context.get("selected_series_codes") or [])
    selected = [r for r in records if r.get("series_code") in selected_codes]
    seen: set[tuple[str, str]] = set()
    for record in selected:
        identity = (str(record.get("series_code") or ""), str(record.get("date") or ""))
        if identity in seen:
            raise RuntimeError(
                "Source series has multiple observations at the public date grain: "
                f"{identity[0]} {identity[1]}"
            )
        seen.add(identity)

    by_date: dict[str, list[dict]] = defaultdict(list)
    for record in selected:
        if record.get("date"):
            by_date[str(record["date"])].append(record)

    projected: list[dict] = []
    for report_date in sorted(by_date):
        date_records = by_date[report_date]
        present_codes = {str(r.get("series_code") or "") for r in date_records}
        if len(selected_codes) > 1 and present_codes != selected_codes:
            continue
        first = date_records[0]
        incidence_values = [_safe_float(r.get("incidence_rate")) for r in date_records]
        incidence_values = [value for value in incidence_values if value is not None]
        projected.append(
            {
                "date": report_date,
                "year_month": first.get("year_month") or report_date[:7],
                "disease_id": first.get("disease_id"),
                "cases": _normalise_count(
                    sum(_safe_float(r.get("cases")) or 0 for r in date_records)
                ),
                "deaths": 0,
                "recoveries": 0,
                "incidence_rate": sum(incidence_values) if incidence_values else None,
                "incidence_rate_source": _dominant_value(
                    [r.get("incidence_rate_source") for r in date_records]
                ) or "missing_population",
                "mortality_rate": None,
                "data_quality": _dominant_value(
                    [r.get("quality_status") or r.get("data_quality") for r in date_records]
                ),
                "data_layer": SERIES_DATA_LAYER,
                "series_code": first.get("series_code") if len(selected_codes) == 1 else None,
                "_series_context": context,
            }
        )
    return projected


def _attach_legacy_supplemental_metrics(
    projected: list[dict], context: dict, legacy_records: list[dict]
) -> dict:
    """Retain non-case legacy metrics without reintroducing case duplication."""

    legacy_by_date: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_date[str(record["date"])].append(record)
    dates = sorted(legacy_by_date)
    context["supplemental_legacy_metrics"] = {
        "dates": dates,
        "deaths": [sum(r.get("deaths") or 0 for r in legacy_by_date[d]) for d in dates],
        "recoveries": [sum(r.get("recoveries") or 0 for r in legacy_by_date[d]) for d in dates],
        "mortality_rates": [
            _avg_or_none([r.get("mortality_rate") for r in legacy_by_date[d]]) for d in dates
        ],
    }
    safe_alignment = context.get("projection_policy") in {"single_series", "sum_disjoint"}
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
            legacy = legacy_by_date.get(str(record.get("date"))) or []
            record["deaths"] = sum(item.get("deaths") or 0 for item in legacy)
            record["recoveries"] = sum(item.get("recoveries") or 0 for item in legacy)
            record["mortality_rate"] = _avg_or_none([item.get("mortality_rate") for item in legacy])
    return context


def _overlay_legacy_coverage_gaps(
    projected: list[dict], context: dict, legacy_records: list[dict]
) -> list[dict]:
    """Overlay registry facts by period without sacrificing legacy coverage."""

    registry_dates = {str(r.get("date") or "") for r in projected if r.get("date")}
    legacy_by_date: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_date[str(record["date"])].append(record)
    legacy_dates = set(legacy_by_date)
    gap_dates = sorted(legacy_dates - registry_dates)
    overlap_dates = legacy_dates & registry_dates
    context.update(
        {
            "coverage_policy": "period_key_overlay",
            "coverage_status": "legacy_gap_fill" if gap_dates else "parity",
            "legacy_period_count": len(legacy_dates),
            "registry_period_count": len(registry_dates),
            "overlap_period_count": len(overlap_dates),
            "legacy_gap_fill_count": len(gap_dates),
            "registry_only_period_count": len(registry_dates - legacy_dates),
            "coverage_ratio_against_legacy": round(
                len(overlap_dates) / len(legacy_dates) if legacy_dates else 1.0, 6
            ),
        }
    )
    if not gap_dates:
        return projected
    if not registry_dates:
        context["registry_projection_policy"] = context.get("projection_policy")
        context["data_layer"] = LEGACY_DATA_LAYER
        context["projection_policy"] = "legacy_fallback"
        context["coverage_status"] = "registry_no_complete_periods"
        context["fallback_reason"] = "incomplete_registered_rollup_periods"
        context["coverage_risk"] = "registry_history_incomplete"
        context["metric_layers"]["cases"] = LEGACY_DATA_LAYER
        result: list[dict] = []
        for report_date in gap_dates:
            records = legacy_by_date[report_date]
            if len(records) != 1:
                raise RuntimeError(
                    "Legacy layer has multiple observations at the public date grain: "
                    f"{records[0].get('disease_id')} {report_date}"
                )
            record = dict(records[0])
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
    for report_date in gap_dates:
        records = legacy_by_date[report_date]
        if len(records) != 1:
            raise RuntimeError(
                "Legacy layer has multiple observations at the public date grain: "
                f"{records[0].get('disease_id')} {report_date}"
            )
        record = dict(records[0])
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
    eligible_series: dict[str, list[dict]] = defaultdict(list)
    for record in series_records:
        disease_id = str(record.get("disease_id") or "").strip()
        if disease_id:
            all_series[disease_id].append(record)
            if _series_is_case_count(record):
                eligible_series[disease_id].append(record)

    result: list[dict] = []
    for disease_id in sorted(set(legacy_by_disease) | set(eligible_series)):
        eligible = eligible_series.get(disease_id) or []
        if eligible:
            _selected, context = _projection_context(eligible)
            projected = _collapse_selected_series_records(eligible, context)
            _attach_legacy_supplemental_metrics(
                projected, context, legacy_by_disease.get(disease_id) or []
            )
            result.extend(
                _overlay_legacy_coverage_gaps(
                    projected, context, legacy_by_disease.get(disease_id) or []
                )
            )
            continue
        raw_count = len(all_series.get(disease_id) or [])
        context = _legacy_projection_context(
            registry_series_fact_count=raw_count,
            reason=(
                "registered_facts_not_case_count_compatible"
                if raw_count else "no_eligible_registered_series_facts"
            ),
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
            raise RuntimeError(f"Site projection lacks data-layer provenance for {disease_id}")
        layer = str(context.get("data_layer") or "").strip()
        layers[disease_id].add(layer)
        record_layers[disease_id].add(str(record.get("data_layer") or layer).strip())
        policy = str(context.get("projection_policy") or "")
        selected = context.get("selected_series_codes") or []
        if layer in {SERIES_DATA_LAYER, MIXED_DATA_LAYER}:
            if not selected:
                raise RuntimeError(f"Registry projection has no selected series for {disease_id}")
            if len(selected) > 1 and policy != "sum_disjoint":
                raise RuntimeError(
                    "Multiple source series reached the flat public curve without "
                    f"an explicit sum_disjoint policy for {disease_id}"
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
