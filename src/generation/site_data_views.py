"""Pure builders for public site data transfer objects and chart payloads."""

from __future__ import annotations

import math
from collections import defaultdict

from src.core.country_library import get_country_display_name
from src.generation.site_series_projection import (
    LEGACY_DATA_LAYER,
    MIXED_DATA_LAYER,
    SERIES_DATA_LAYER,
    _legacy_projection_context,
    _source_series_details,
)

SOURCE_OBSERVATIONS_ONLY_DATA_LAYER = "source_observations_only"


def resolve_country_display_names(
    code: str, row: dict | None = None
) -> tuple[str, str]:
    """Resolve stable English and Chinese country names for public exports."""
    normalized = (code or "").strip().upper()
    row = row or {}
    name_en = (
        row.get("name_en")
        or row.get("name")
        or get_country_display_name(normalized, "en")
        or normalized
    )
    name_zh = (
        row.get("name_zh")
        or get_country_display_name(normalized, "zh")
        or row.get("name_local")
        or name_en
    )
    return name_en, name_zh


def avg_or_none(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def dominant_value(values: list[str | None]) -> str | None:
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    counts: dict[str, int] = defaultdict(int)
    for value in cleaned:
        counts[value] += 1
    return max(counts.items(), key=lambda item: item[1])[0]


def calculate_weekly_equivalent(
    dates: list[str],
    values: list[int | float],
    temporal_granularity: str | None = None,
) -> list[float]:
    """Expose weekly counts only when the source explicitly declares weekly grain.

    Monthly, quarterly, and annual period totals are not rates. Dividing them by
    elapsed days invents a measure that the source did not report, so those
    series intentionally return no weekly-equivalent values.
    """
    if not dates or not values or len(dates) != len(values):
        return []
    if str(temporal_granularity or "").strip().lower() != "weekly":
        return []
    return [float(value) for value in values]


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

    omitted = {"dates", "values", "quality_statuses"}
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


def build_country_data(
    country_code: str,
    country_name: str,
    records: list[dict],
    diseases_by_id: dict,
    frequency_meta: dict | None = None,
    source_records: list[dict] | None = None,
) -> dict:
    """Build the full country JSON blob with time-series per disease."""
    records = [rec for rec in records if rec.get("disease_id") in diseases_by_id]
    filtered_source_records = [
        rec
        for rec in (source_records or [])
        if rec.get("disease_id") in diseases_by_id
    ]
    # Group records by disease_id
    by_disease: dict[str, list] = defaultdict(list)
    for rec in records:
        by_disease[rec["disease_id"]].append(rec)
    source_by_disease: dict[str, list] = defaultdict(list)
    for rec in filtered_source_records:
        source_by_disease[rec["disease_id"]].append(rec)

    total_cases = sum(r["cases"] for r in records)
    total_deaths = sum(r["deaths"] for r in records)
    dates = sorted({r["date"] for r in records if r["date"]})

    # Build time series per disease
    disease_series = {}
    for disease_id in sorted(set(by_disease) | set(source_by_disease)):
        recs = by_disease.get(disease_id) or []
        points: dict[str, dict] = {}
        for rec in recs:
            d = rec.get("date")
            if not d:
                continue
            if d not in points:
                points[d] = {
                    "cases": 0,
                    "deaths": 0,
                    "incidence_rates": [],
                    "incidence_sources": [],
                    "mortality_rates": [],
                }
            points[d]["cases"] += rec.get("cases") or 0
            points[d]["deaths"] += rec.get("deaths") or 0
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))
            points[d]["mortality_rates"].append(rec.get("mortality_rate"))

        series_dates = sorted(points.keys())
        series_cases = [points[d]["cases"] for d in series_dates]
        series_deaths = [points[d]["deaths"] for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]
        series_mortality = [
            avg_or_none(points[d]["mortality_rates"]) for d in series_dates
        ]
        provenance = _provenance_with_source_records(
            recs,
            source_by_disease.get(disease_id) if source_records is not None else None,
        )
        weekly_equiv_cases = calculate_weekly_equivalent(
            series_dates,
            series_cases,
            provenance.get("period_granularity"),
        )

        disease_info = diseases_by_id.get(disease_id, {})
        disease_series[disease_id] = {
            "disease_id": disease_id,
            "name_en": disease_info.get("name_en", disease_id),
            "name_zh": disease_info.get("name_zh", disease_id),
            "category": disease_info.get("category", "Unknown"),
            "slug": disease_info.get("slug", disease_id.lower()),
            "dates": series_dates,
            "cases": series_cases,
            "weekly_equiv_cases": weekly_equiv_cases,
            "deaths": series_deaths,
            "incidence_rates": series_incidence,
            "incidence_sources": series_incidence_sources,
            "mortality_rates": series_mortality,
            "total_cases": sum(series_cases),
            "total_deaths": sum(series_deaths),
            "latest_cases": series_cases[-1] if series_cases else 0,
            "latest_deaths": series_deaths[-1] if series_deaths else 0,
            **provenance,
        }

    incidence_source_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        source = rec.get("incidence_rate_source") or "missing_population"
        incidence_source_counts[source] += 1

    # Heatmap data: diseases (rows) × months (cols). Annual and quarterly
    # totals cannot describe seasonality and would otherwise appear as false
    # January spikes merely because of their storage date.
    heatmap_records_by_disease = {
        disease_id: recs
        for disease_id, recs in by_disease.items()
        if str(
            disease_series[disease_id].get("period_granularity") or ""
        ).lower()
        not in {"annual", "yearly", "quarterly"}
    }
    all_months = sorted(
        {
            record["year_month"]
            for recs in heatmap_records_by_disease.values()
            for record in recs
            if record.get("year_month")
        }
    )
    heatmap_diseases = sorted(
        heatmap_records_by_disease.keys(),
        key=lambda d: disease_series[d]["total_cases"],
        reverse=True,
    )[
        :50
    ]  # Cap at top 50 diseases for readability

    heatmap_z = []
    for did in heatmap_diseases:
        month_totals: dict[str, int] = defaultdict(int)
        for rec in heatmap_records_by_disease[did]:
            ym = rec.get("year_month")
            if ym:
                month_totals[ym] += rec.get("cases") or 0
        row_z = []
        for month in all_months:
            cases = month_totals.get(month, 0)
            row_z.append(math.log10(cases + 1))  # log scale
        heatmap_z.append(row_z)

    heatmap_labels = [disease_series[d]["name_en"] for d in heatmap_diseases]

    country_name_en, country_name_zh = resolve_country_display_names(
        country_code,
        {"name": country_name},
    )

    return {
        "country_code": country_code,
        "country_name": country_name_en,
        "country_name_en": country_name_en,
        "country_name_zh": country_name_zh,
        "total_cases": total_cases,
        "total_deaths": total_deaths,
        "disease_count": len(by_disease),
        "frequency_meta": frequency_meta
        or {
            "source_frequency": "UNKNOWN",
            "source_frequencies": [],
            "canonical_frequency": "SOURCE_REPORTED_PERIODS",
            "aggregation_rule": "preserve_source_period_counts",
        },
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
        "comparison_basis": {
            "frequency": "SOURCE_REPORTED_PERIODS",
            "metric": "cases",
            "note_en": "Counts are shown at each source's declared reporting period and are not normalized across annual, quarterly, monthly, and weekly series.",
            "note_zh": "病例数按各来源声明的报告期间展示，不在年度、季度、月度和周度序列之间进行频率归一化。",
        },
        "incidence_rate_basis": {
            "formula": "cases / population * 100000",
            "unit": "per_100k",
            "population_source": "WPP",
            "source_counts": dict(incidence_source_counts),
            "note_en": "Incidence Rate is computed from WPP population data during site generation; when population is unavailable, original database incidence is shown.",
            "note_zh": "发病率在网页数据生成阶段按 WPP 人口重算（每10万人）；若缺少人口数据则回退显示数据库原始发病率。",
        },
        "disease_series": disease_series,
        "data_layer_summary": _data_layer_summary(disease_series),
        "heatmap": {
            "diseases": heatmap_diseases,
            "disease_labels": heatmap_labels,
            "months": all_months,
            "z": heatmap_z,
        },
    }


def build_country_site_data(country_data: dict) -> dict:
    """Build a compact country payload used only by the site charts."""
    disease_series = country_data.get("disease_series") or {}
    shared_dates = sorted(
        {
            date
            for series in disease_series.values()
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
    for entry in disease_series.values():
        dates = entry.get("dates") or []
        if not dates:
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
            "id": entry.get("disease_id"),
            "en": entry.get("name_en"),
            "zh": entry.get("name_zh"),
            "cat": entry.get("category"),
            "slug": entry.get("slug"),
            "tc": entry.get("total_cases", 0),
            "td": entry.get("total_deaths", 0),
            "lc": entry.get("latest_cases", 0),
            "ld": entry.get("latest_deaths", 0),
            "x": [date_index[date] for date in dates if date in date_index],
            "c": entry.get("cases") or [],
            "w": [
                round(float(value), 2)
                for value in (entry.get("weekly_equiv_cases") or [])
            ],
            "d": entry.get("deaths") or [],
            "data_layer": entry.get("data_layer") or LEGACY_DATA_LAYER,
            "projection_policy": entry.get("projection_policy") or "legacy_fallback",
            "loss_risk": entry.get("loss_risk"),
            "period_granularity": entry.get("period_granularity"),
            "available_series_count": entry.get("available_series_count") or 0,
            "coverage_status": entry.get("coverage_status"),
            "selected_series_codes": entry.get("selected_series_codes") or [],
            "metric_layers": (entry.get("data_provenance") or {}).get("metric_layers")
            or {},
            "source_series": _compact_source_series_metadata(
                entry.get("source_series") or []
            ),
        }
        if ri:
            compact_entry["ri"] = ri
            compact_entry["rv"] = rv
            if any(code is not None for code in rs):
                compact_entry["rs"] = rs
        compact_series.append(compact_entry)

    heatmap = country_data.get("heatmap") or {}
    return {
        "v": 1,
        "meta": {
            "cc": country_data.get("country_code"),
            "cn": country_data.get("country_name"),
            "cn_zh": country_data.get("country_name_zh"),
            "tc": country_data.get("total_cases"),
            "td": country_data.get("total_deaths"),
            "dc": country_data.get("disease_count"),
            "dr": country_data.get("date_range"),
            "data_layer_summary": country_data.get("data_layer_summary") or {},
        },
        "dates": shared_dates,
        "sources": source_labels,
        "series": compact_series,
        "heatmap": {
            "months": heatmap.get("months") or [],
            "disease_ids": heatmap.get("diseases") or [],
            "z": [
                [round(float(value), 4) for value in row]
                for row in (heatmap.get("z") or [])
            ],
        },
    }


def build_disease_data(
    disease_id: str,
    disease_info: dict,
    all_records_by_country: dict[str, list],
    source_records_by_country: dict[str, list] | None = None,
) -> dict:
    """Build per-disease JSON with time-series across all countries."""
    country_series = {}
    source_records_by_country = source_records_by_country or {}
    country_codes = list(all_records_by_country)
    country_codes.extend(
        code for code in source_records_by_country if code not in all_records_by_country
    )
    for country_code in country_codes:
        records = all_records_by_country.get(country_code) or []
        disease_records = [r for r in records if r["disease_id"] == disease_id]
        disease_source_records = [
            r
            for r in (source_records_by_country.get(country_code) or [])
            if r["disease_id"] == disease_id
        ]
        if not disease_records and not disease_source_records:
            continue

        points: dict[str, dict] = {}
        for rec in disease_records:
            d = rec.get("date")
            if not d:
                continue
            if d not in points:
                points[d] = {
                    "cases": 0,
                    "deaths": 0,
                    "incidence_rates": [],
                    "incidence_sources": [],
                }
            points[d]["cases"] += rec.get("cases") or 0
            points[d]["deaths"] += rec.get("deaths") or 0
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))

        series_dates = sorted(points.keys())
        series_cases = [points[d]["cases"] for d in series_dates]
        series_deaths = [points[d]["deaths"] for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]

        provenance = _provenance_with_source_records(
            disease_records,
            disease_source_records if source_records_by_country else None,
        )
        country_series[country_code] = {
            "dates": series_dates,
            "cases": series_cases,
            "weekly_equiv_cases": calculate_weekly_equivalent(
                series_dates,
                series_cases,
                provenance.get("period_granularity"),
            ),
            "deaths": series_deaths,
            "incidence_rates": series_incidence,
            "incidence_sources": series_incidence_sources,
            "total_cases": sum(series_cases),
            "total_deaths": sum(series_deaths),
            **provenance,
        }

    all_disease_records = [
        r
        for recs in all_records_by_country.values()
        for r in recs
        if r["disease_id"] == disease_id
    ]
    monthly: dict[str, dict] = defaultdict(lambda: {"cases": 0, "deaths": 0})
    for r in all_disease_records:
        granularity = str(
            _series_context_for_records([r]).get("period_granularity") or ""
        ).lower()
        if granularity in {"annual", "yearly", "quarterly"}:
            continue
        if r["year_month"]:
            monthly[r["year_month"]]["cases"] += r["cases"]
            monthly[r["year_month"]]["deaths"] += r["deaths"]
    months_sorted = sorted(monthly.keys())

    return {
        **disease_info,
        "country_series": country_series,
        "global_monthly": {
            "months": months_sorted,
            "cases": [monthly[m]["cases"] for m in months_sorted],
            "deaths": [monthly[m]["deaths"] for m in months_sorted],
        },
        "total_cases": sum(cs["total_cases"] for cs in country_series.values()),
        "total_deaths": sum(cs["total_deaths"] for cs in country_series.values()),
        "data_layer_summary": _country_series_data_layer_summary(country_series),
    }


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
        if not dates:
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
            "x": [date_index[date] for date in dates if date in date_index],
            "c": entry.get("cases") or [],
            "w": [
                round(float(value), 2)
                for value in (entry.get("weekly_equiv_cases") or [])
            ],
            "d": entry.get("deaths") or [],
            "data_layer": entry.get("data_layer") or LEGACY_DATA_LAYER,
            "projection_policy": entry.get("projection_policy") or "legacy_fallback",
            "loss_risk": entry.get("loss_risk"),
            "period_granularity": entry.get("period_granularity"),
            "available_series_count": entry.get("available_series_count") or 0,
            "coverage_status": entry.get("coverage_status"),
            "selected_series_codes": entry.get("selected_series_codes") or [],
            "metric_layers": (entry.get("data_provenance") or {}).get("metric_layers")
            or {},
            "source_series": _compact_source_series_metadata(
                entry.get("source_series") or []
            ),
        }
        if ri:
            compact_entry["ri"] = ri
            compact_entry["rv"] = rv
            if any(code is not None for code in rs):
                compact_entry["rs"] = rs
        compact_series.append(compact_entry)

    global_monthly = disease_data.get("global_monthly") or {}
    return {
        "v": 1,
        "meta": {
            "id": disease_data.get("disease_id"),
            "en": disease_data.get("name_en"),
            "zh": disease_data.get("name_zh"),
            "cat": disease_data.get("category"),
            "tc": disease_data.get("total_cases"),
            "td": disease_data.get("total_deaths"),
            "cc": len(country_series),
            "data_layer_summary": disease_data.get("data_layer_summary") or {},
        },
        "dates": shared_dates,
        "sources": source_labels,
        "series": compact_series,
        "monthly": {
            "months": global_monthly.get("months") or [],
            "cases": global_monthly.get("cases") or [],
            "deaths": global_monthly.get("deaths") or [],
        },
    }
