"""Compact international disease chart projections."""
from __future__ import annotations

from src.generation.site_series_projection import LEGACY_DATA_LAYER
from .provenance_views import _compact_source_series_metadata


def build_disease_site_data(
    disease_data: dict,
    country_name_by_code: dict[str, str] | None = None,
    country_name_zh_by_code: dict[str, str] | None = None,
) -> dict:
    """Build a compact disease payload used only by the site charts."""
    country_series = disease_data.get("country_series") or {}
    shared_dates = sorted(
        {
            date
            for series in country_series.values()
            for date in (series.get("dates") or [])
            if date
        }
    )
    date_index = {date: index for index, date in enumerate(shared_dates)}
    source_labels: list[str] = []
    source_codes: dict[str, int] = {}

    def register_source(label: str | None) -> int | None:
        if not label:
            return None
        existing = source_codes.get(label)
        if existing is not None:
            return existing
        code = len(source_labels)
        source_codes[label] = code
        source_labels.append(label)
        return code

    compact_series = []
    for country_code, entry in country_series.items():
        dates = entry.get("dates") or []
        if not dates and not entry.get("source_series"):
            continue
        incidence_rates = entry.get("incidence_rates") or []
        incidence_sources = entry.get("incidence_sources") or []

        ri: list[int] = []
        rv: list[float] = []
        rs: list[int | None] = []
        for point_index, value in enumerate(incidence_rates):
            if value is None:
                continue
            ri.append(point_index)
            rv.append(round(float(value), 4))
            source_label = (
                incidence_sources[point_index]
                if point_index < len(incidence_sources)
                else None
            )
            rs.append(register_source(source_label))

        compact_entry = {
            "cc": country_code,
            "n": (country_name_by_code or {}).get(country_code) or country_code,
            "n_zh": (country_name_zh_by_code or {}).get(country_code) or country_code,
            "tc": entry.get("total_cases", 0),
            "td": entry.get("total_deaths", 0),
            "pf": entry.get("provisional_from"),
            "mt": entry.get("metric_type"),
            "rb": entry.get("reporting_basis"),
            "tb": entry.get("time_basis"),
            "cmp": entry.get("comparability"),
            "dv": entry.get("definition_version"),
            "x": [date_index[date] for date in dates if date in date_index],
            "c": entry.get("cases") or [],
            "w": [
                round(float(value), 2) if value is not None else None
                for value in (entry.get("weekly_equiv_cases") or [])
            ],
            "d": entry.get("deaths") or [],
            "data_layer": entry.get("data_layer") or LEGACY_DATA_LAYER,
            "projection_policy": entry.get("projection_policy") or "legacy_fallback",
            "loss_risk": entry.get("loss_risk"),
            "period_granularity": entry.get("period_granularity"),
            "period_granularity_basis": entry.get("period_granularity_basis"),
            "available_series_count": entry.get("available_series_count") or 0,
            "coverage_status": entry.get("coverage_status"),
            "selected_series_codes": entry.get("selected_series_codes") or [],
            "metric_layers": (entry.get("data_provenance") or {}).get("metric_layers")
            or {},
            "source_series": _compact_source_series_metadata(
                entry.get("source_series") or []
            ),
            "point_granularities": entry.get("point_granularities") or [],
            "point_series_codes": entry.get("point_series_codes") or [],
            "point_data_layers": entry.get("point_data_layers") or [],
        }
        if ri:
            compact_entry["ri"] = ri
            compact_entry["rv"] = rv
            if any(code is not None for code in rs):
                compact_entry["rs"] = rs
        compact_series.append(compact_entry)

    return {
        "v": 1,
        "meta": {
            "id": disease_data.get("disease_id"),
            "en": disease_data.get("name_en"),
            "zh": disease_data.get("name_zh"),
            "cat": disease_data.get("category"),
            "cc": len(country_series),
            "data_layer_summary": disease_data.get("data_layer_summary") or {},
            "recorded_at_as_of": disease_data.get("recorded_at_as_of"),
            "recorded_history_complete": disease_data.get(
                "recorded_history_complete"
            ),
        },
        "dates": shared_dates,
        "sources": source_labels,
        "series": compact_series,
    }
