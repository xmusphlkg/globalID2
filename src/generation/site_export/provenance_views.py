"""Lossless source-series provenance and data-layer summaries for public projections."""
from __future__ import annotations

from collections import defaultdict
from src.generation.site_series_projection import (
    LEGACY_DATA_LAYER, MIXED_DATA_LAYER, SERIES_DATA_LAYER,
    _legacy_projection_context, _source_series_details,
)

SOURCE_OBSERVATIONS_ONLY_DATA_LAYER = "source_observations_only"


def _series_context_for_records(records: list[dict]) -> dict:
    for record in records:
        context = record.get("_series_context")
        if isinstance(context, dict):
            return context
    # Direct callers of the pure builders may still provide historical record
    # dictionaries.  Treat those explicitly as legacy rather than leaving the
    # output provenance ambiguous.
    return _legacy_projection_context(reason="builder_received_unmarked_legacy_rows")


def _series_provenance_fields(records: list[dict]) -> dict:
    context = _series_context_for_records(records)
    return {
        "data_layer": context.get("data_layer") or LEGACY_DATA_LAYER,
        "projection_policy": context.get("projection_policy") or "legacy_fallback",
        "loss_risk": context.get("loss_risk"),
        "selected_series_codes": context.get("selected_series_codes") or [],
        "available_series_count": context.get("available_series_count") or 0,
        "source_series": context.get("source_series") or [],
        "coverage_status": context.get("coverage_status"),
        "coverage_policy": context.get("coverage_policy"),
        "period_granularity": context.get("period_granularity"),
        "legacy_gap_fill_count": context.get("legacy_gap_fill_count") or 0,
        "coverage_ratio_against_legacy": context.get("coverage_ratio_against_legacy"),
        "data_provenance": {
            key: value for key, value in context.items() if key != "source_series"
        },
    }


def _source_observation_provenance(source_records: list[dict]) -> dict:
    """Describe retained source facts that cannot truthfully form a cases curve."""

    source_series = _source_series_details(source_records)
    granularities = {
        str(item.get("temporal_granularity") or "").strip().lower()
        for item in source_series
        if str(item.get("temporal_granularity") or "").strip()
    }
    context = {
        "data_layer": SOURCE_OBSERVATIONS_ONLY_DATA_LAYER,
        "projection_policy": "no_eligible_public_projection",
        "loss_risk": None,
        "selected_series_codes": [],
        "available_series_count": len(source_series),
        "non_projection_series_codes": [
            item["series_code"] for item in source_series
        ],
        "period_granularity": (
            next(iter(granularities)) if len(granularities) == 1 else None
        ),
        "metric_layers": {
            "cases": "not_available",
            "deaths": "not_available",
            "recoveries": "not_available",
        },
        "source_series_export_decoupled": True,
        "note_en": (
            "Registered source observations are retained for provenance and "
            "downloads, but their measure is not eligible for the public cases curve."
        ),
        "note_zh": "注册来源观测保留用于溯源和下载，但其度量不适合生成公开病例曲线。",
    }
    return {
        "data_layer": context["data_layer"],
        "projection_policy": context["projection_policy"],
        "loss_risk": None,
        "selected_series_codes": [],
        "available_series_count": len(source_series),
        "source_series": source_series,
        "coverage_status": "source_observations_only",
        "coverage_policy": "no_public_projection",
        "period_granularity": context["period_granularity"],
        "legacy_gap_fill_count": 0,
        "coverage_ratio_against_legacy": None,
        "data_provenance": context,
    }


def _provenance_with_source_records(
    projected_records: list[dict],
    source_records: list[dict] | None,
) -> dict:
    """Attach lossless source facts independently from the public projection."""

    if not projected_records:
        return _source_observation_provenance(source_records or [])
    provenance = _series_provenance_fields(projected_records)
    if source_records is None:
        return provenance
    source_series = _source_series_details(source_records)
    selected = set(provenance.get("selected_series_codes") or [])
    provenance["source_series"] = source_series
    provenance["available_series_count"] = len(source_series)
    data_provenance = dict(provenance.get("data_provenance") or {})
    data_provenance["available_series_count"] = len(source_series)
    data_provenance["non_projection_series_codes"] = sorted(
        item["series_code"]
        for item in source_series
        if item.get("series_code") not in selected
    )
    data_provenance["source_series_export_decoupled"] = True
    provenance["data_provenance"] = data_provenance
    return provenance


def _data_layer_summary(disease_series: dict[str, dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    risky_diseases: list[str] = []
    non_additive_diseases: list[str] = []
    for disease_id, series in disease_series.items():
        layer = str(series.get("data_layer") or LEGACY_DATA_LAYER)
        counts[layer] += 1
        if series.get("loss_risk") or series.get("data_provenance", {}).get(
            "coverage_risk"
        ):
            risky_diseases.append(disease_id)
        if series.get("loss_risk") == "non_additive_series_not_rolled_up":
            non_additive_diseases.append(disease_id)
    summary = {
        "series_registry_disease_count": counts.get(SERIES_DATA_LAYER, 0),
        "mixed_disease_count": counts.get(MIXED_DATA_LAYER, 0),
        "legacy_fallback_disease_count": counts.get(LEGACY_DATA_LAYER, 0),
        "loss_risk_disease_count": len(risky_diseases),
        "loss_risk_disease_ids": sorted(risky_diseases),
        "non_additive_series_disease_ids": sorted(non_additive_diseases),
    }
    source_only_count = counts.get(SOURCE_OBSERVATIONS_ONLY_DATA_LAYER, 0)
    if source_only_count:
        summary["source_observations_only_disease_count"] = source_only_count
    return summary


def _compact_source_series_metadata(source_series: list[dict]) -> list[dict]:
    """Keep definition/projection semantics in chart payloads without facts duplication."""

    omitted = {
        "dates", "values", "quality_statuses", "point_quality_statuses",
        "incidence_rates", "incidence_sources", "population_values",
        "population_sources", "period_starts", "period_ends_exclusive",
    }
    return [
        {key: value for key, value in item.items() if key not in omitted}
        for item in source_series
        if isinstance(item, dict)
    ]


def _country_series_data_layer_summary(country_series: dict[str, dict]) -> dict:
    registry_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == SERIES_DATA_LAYER
    )
    legacy_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == LEGACY_DATA_LAYER
    )
    mixed_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == MIXED_DATA_LAYER
    )
    source_only_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == SOURCE_OBSERVATIONS_ONLY_DATA_LAYER
    )
    risky_countries = sorted(
        code for code, series in country_series.items() if series.get("loss_risk")
    )
    summary = {
        "series_registry_country_count": len(registry_countries),
        "mixed_country_count": len(mixed_countries),
        "legacy_fallback_country_count": len(legacy_countries),
        "series_registry_country_codes": registry_countries,
        "mixed_country_codes": mixed_countries,
        "legacy_fallback_country_codes": legacy_countries,
        "loss_risk_country_codes": risky_countries,
    }
    if source_only_countries:
        summary["source_observations_only_country_count"] = len(
            source_only_countries
        )
        summary["source_observations_only_country_codes"] = source_only_countries
    return summary
