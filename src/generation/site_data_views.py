"""Pure builders for public site data transfer objects and chart payloads."""

from __future__ import annotations

import math
from collections import defaultdict

from src.core.country_library import get_country_display_name
from src.core.reporting_period import infer_display_cadence
from src.generation.site_series_projection import (
    LEGACY_DATA_LAYER,
    MIXED_DATA_LAYER,
    SERIES_DATA_LAYER,
    _legacy_projection_context,
    _source_series_details,
)

from src.generation.site_export.country_views import (
    build_national_monthly_summary, build_country_site_data, build_country_source_series_data,
)
from src.generation.site_export.disease_views import build_disease_site_data
from src.generation.site_export.provenance_views import (
    SOURCE_OBSERVATIONS_ONLY_DATA_LAYER,
    _series_context_for_records, _series_provenance_fields, _source_observation_provenance,
    _provenance_with_source_records, _data_layer_summary, _compact_source_series_metadata,
    _country_series_data_layer_summary,
)


def resolve_country_display_names(
    code: str, row: dict | None = None
) -> tuple[str, str]:
    """Resolve stable English and Chinese country names for public exports."""
    normalized = (code or "").strip().upper()
    row = row or {}
    profile_name_en = get_country_display_name(normalized, "en")
    # Subdivision labels in stored metadata can end in an ISO code (for
    # example, "Hokkaido, JP"). The registry has the canonical parent name.
    name_en = (
        (profile_name_en if profile_name_en != normalized else None)
        if "-" in normalized
        else None
    ) or row.get("name_en") or row.get("name") or profile_name_en
    name_en = name_en or normalized
    local_name = str(row.get("name_local") or "").strip()
    chinese_local_name = (
        local_name
        if any("\u4e00" <= character <= "\u9fff" for character in local_name)
        else None
    )
    name_zh = (
        row.get("name_zh")
        or chinese_local_name
        or get_country_display_name(normalized, "zh")
        or local_name
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


def sum_or_none(values: list[int | float | None]) -> int | float | None:
    """Sum observed values without silently converting all-missing to zero."""

    observed = [value for value in values if value is not None]
    return sum(observed) if observed else None


def latest_or_none(values: list[int | float | None]) -> int | float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def trailing_provisional_from(
    dates: list[str], quality_statuses: list[str | None]
) -> str | None:
    """Return a tail boundary only for publisher-declared preliminary data."""

    provisional = {"provisional", "preliminary"}
    start: str | None = None
    for date, status in reversed(list(zip(dates, quality_statuses))):
        if str(status or "").strip().lower() not in provisional:
            break
        start = date
    return start


def calculate_weekly_equivalent(
    dates: list[str],
    values: list[int | float | None],
    temporal_granularity: str | None = None,
) -> list[float | None]:
    """Expose weekly counts only when the source explicitly declares weekly grain.

    Monthly, quarterly, and annual period totals are not rates. Dividing them by
    elapsed days invents a measure that the source did not report, so those
    series intentionally return no weekly-equivalent values.
    """
    if not dates or not values or len(dates) != len(values):
        return []
    if str(temporal_granularity or "").strip().lower() != "weekly":
        return []
    return [float(value) if value is not None else None for value in values]


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

    total_cases = sum(r["cases"] for r in records if r.get("cases") is not None)
    total_deaths = sum(r["deaths"] for r in records if r.get("deaths") is not None)
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
                    "cases": [],
                    "deaths": [],
                    "incidence_rates": [],
                    "incidence_sources": [],
                    "mortality_rates": [],
                    "quality_statuses": [],
                    "temporal_granularities": [],
                    "series_codes": [],
                    "data_layers": [],
                }
            points[d]["cases"].append(rec.get("cases"))
            points[d]["deaths"].append(rec.get("deaths"))
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))
            points[d]["mortality_rates"].append(rec.get("mortality_rate"))
            points[d]["quality_statuses"].append(
                rec.get("quality_status") or rec.get("data_quality")
            )
            context = (
                rec.get("_series_context")
                if isinstance(rec.get("_series_context"), dict)
                else {}
            )
            points[d]["temporal_granularities"].append(
                rec.get("temporal_granularity")
                or context.get("period_granularity")
            )
            points[d]["series_codes"].append(rec.get("series_code"))
            points[d]["data_layers"].append(
                rec.get("data_layer") or context.get("data_layer")
            )

        series_dates = sorted(points.keys())
        series_cases = [sum_or_none(points[d]["cases"]) for d in series_dates]
        series_deaths = [sum_or_none(points[d]["deaths"]) for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]
        series_mortality = [
            avg_or_none(points[d]["mortality_rates"]) for d in series_dates
        ]
        series_quality = [
            dominant_value(points[d]["quality_statuses"]) for d in series_dates
        ]
        series_point_granularities = [
            dominant_value(points[d]["temporal_granularities"]) for d in series_dates
        ]
        series_point_series_codes = [
            dominant_value(points[d]["series_codes"]) for d in series_dates
        ]
        series_point_data_layers = [
            dominant_value(points[d]["data_layers"]) for d in series_dates
        ]
        provenance = _provenance_with_source_records(
            recs,
            source_by_disease.get(disease_id) if source_records is not None else None,
        )
        declared_granularity = provenance.get("period_granularity")
        display_granularity = declared_granularity or infer_display_cadence(series_dates)
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
            "quality_statuses": series_quality,
            "point_granularities": series_point_granularities,
            "point_series_codes": series_point_series_codes,
            "point_data_layers": series_point_data_layers,
            "provisional_from": trailing_provisional_from(
                series_dates, series_quality
            ),
            "total_cases": sum(value for value in series_cases if value is not None),
            "total_deaths": sum(value for value in series_deaths if value is not None),
            "latest_cases": latest_or_none(series_cases),
            "latest_deaths": latest_or_none(series_deaths),
            "metric_type": dominant_value([rec.get("metric_type") for rec in recs]),
            "reporting_basis": dominant_value(
                [rec.get("reporting_basis") for rec in recs]
            ),
            "time_basis": dominant_value([rec.get("time_basis") for rec in recs]),
            "comparability": dominant_value(
                [rec.get("comparability") for rec in recs]
            ),
            "definition_version": dominant_value(
                [rec.get("definition_version") for rec in recs]
            ),
            **provenance,
            "period_granularity": display_granularity,
            "period_granularity_basis": (
                "source" if declared_granularity else
                "date_pattern" if display_granularity else "unknown"
            ),
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
            "population_source": " / ".join(sorted({source.removesuffix("_computed").upper() for source in incidence_source_counts if source.endswith("_computed")})) or None,
            "source_counts": dict(incidence_source_counts),
            "period_semantics": "per_source_reporting_period",
            "note_en": "Values are cases per 100,000 population for each source reporting period (for example, per week, month, or year); they are not annualized. When population is unavailable, original database incidence is shown.",
            "note_zh": "数值表示各来源报告期间内每10万人病例数（如每周、每月或每年），未作年化；若缺少人口数据则回退显示数据库原始发病率。",
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


_PUBLISHED_FREQUENCY_ORDER = {
    "DAILY": 0,
    "WEEKLY": 1,
    "MONTHLY": 2,
    "QUARTERLY": 3,
    "ANNUAL": 4,
}


def frequency_meta_from_granularities(granularities: list[str | None]) -> dict:
    """Describe the published curves without scanning raw observations."""

    normalized: set[str] = set()
    for value in granularities:
        text = str(value or "").strip().upper()
        if not text or text == "UNKNOWN":
            continue
        normalized.add("ANNUAL" if text == "YEARLY" else text)
    source_frequencies = sorted(
        normalized,
        key=lambda item: (_PUBLISHED_FREQUENCY_ORDER.get(item, 99), item),
    )
    if not source_frequencies:
        source_frequency = "UNKNOWN"
    elif len(source_frequencies) == 1:
        source_frequency = source_frequencies[0]
    else:
        source_frequency = "MIXED"
    return {
        "source_frequency": source_frequency,
        "source_frequencies": source_frequencies,
        "canonical_frequency": "SOURCE_REPORTED_PERIODS",
        "aggregation_rule": "preserve_source_period_counts",
    }


def _iso_period(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _series_entry_from_published_row(row: dict, diseases_by_id: dict) -> dict:
    """Rebuild one public curve from a publish-table row."""

    disease_id = str(row.get("disease_id") or "")
    disease_info = diseases_by_id.get(disease_id, {})
    dates = [_iso_period(value) for value in (row.get("period_starts") or [])]
    cases = list(row.get("cases") or [])
    deaths = list(row.get("deaths") or [])
    incidence = list(row.get("incidence") or [])
    granularity = str(row.get("granularity") or "unknown")
    data_layer = str(row.get("data_layer") or LEGACY_DATA_LAYER)
    source_code = row.get("source_series_code") or None
    selected = [source_code] if source_code else []
    observed_cases = [value for value in cases if value is not None]
    observed_deaths = [value for value in deaths if value is not None]
    total_cases = row.get("total_cases")
    total_deaths = row.get("total_deaths")
    return {
        "disease_id": disease_id,
        "name_en": disease_info.get("name_en", disease_id),
        "name_zh": disease_info.get("name_zh", disease_id),
        "category": disease_info.get("category", "Unknown"),
        "slug": disease_info.get("slug", disease_id.lower()),
        "dates": dates,
        "cases": cases,
        "weekly_equiv_cases": calculate_weekly_equivalent(dates, cases, granularity),
        "deaths": deaths,
        "incidence_rates": incidence,
        "incidence_sources": [],
        "mortality_rates": [],
        "quality_statuses": [],
        "point_granularities": [],
        "point_series_codes": [source_code] * len(dates) if source_code else [],
        "point_data_layers": [],
        "provisional_from": None,
        "total_cases": int(total_cases if total_cases is not None else sum(observed_cases)),
        "total_deaths": int(
            total_deaths if total_deaths is not None else sum(observed_deaths)
        ),
        "latest_cases": latest_or_none(cases),
        "latest_deaths": latest_or_none(deaths),
        "data_layer": data_layer,
        "period_granularity": granularity,
        "period_granularity_basis": (
            "source" if granularity and granularity != "unknown" else "unknown"
        ),
        "selected_series_codes": selected,
        "projection_policy": "published_series",
        "available_series_count": 1 if source_code else 0,
        "content_hash": row.get("content_hash"),
    }


def _heatmap_from_published_series(disease_series: dict[str, dict]) -> dict:
    eligible = {
        disease_id: series
        for disease_id, series in disease_series.items()
        if str(series.get("period_granularity") or "").lower()
        not in {"annual", "yearly", "quarterly"}
    }
    months = sorted(
        {
            str(value)[:7]
            for series in eligible.values()
            for value in (series.get("dates") or [])
            if value and len(str(value)) >= 7
        }
    )
    diseases = sorted(
        eligible,
        key=lambda disease_id: eligible[disease_id].get("total_cases") or 0,
        reverse=True,
    )[:50]
    heatmap_z = []
    for disease_id in diseases:
        series = eligible[disease_id]
        month_totals: dict[str, int] = defaultdict(int)
        for value_date, cases in zip(series.get("dates") or [], series.get("cases") or []):
            if not value_date:
                continue
            month_totals[str(value_date)[:7]] += cases or 0
        heatmap_z.append([math.log10(month_totals.get(month, 0) + 1) for month in months])
    return {
        "diseases": diseases,
        "disease_labels": [eligible[disease_id].get("name_en") for disease_id in diseases],
        "months": months,
        "z": heatmap_z,
    }


def build_country_data_from_published_series(
    country_code: str,
    country_name: str,
    rows: list[dict],
    diseases_by_id: dict,
    frequency_meta: dict | None = None,
) -> dict:
    """Build the country snapshot from publish-table curves.

    Names and heatmap totals are derived here. The stored row already resolved
    mapping, population, and period alignment, so this path does not read
    observations.
    """

    disease_series = {}
    for row in rows:
        entry = _series_entry_from_published_row(row, diseases_by_id)
        if entry["dates"]:
            disease_series[entry["disease_id"]] = entry
    dates = sorted(
        {
            value
            for entry in disease_series.values()
            for value in entry["dates"]
            if value
        }
    )
    resolved_frequency = frequency_meta or frequency_meta_from_granularities(
        [entry.get("period_granularity") for entry in disease_series.values()]
    )
    country_name_en, country_name_zh = resolve_country_display_names(
        country_code,
        {"name": country_name},
    )
    return {
        "country_code": country_code,
        "country_name": country_name_en,
        "country_name_en": country_name_en,
        "country_name_zh": country_name_zh,
        "total_cases": sum(entry["total_cases"] for entry in disease_series.values()),
        "total_deaths": sum(entry["total_deaths"] for entry in disease_series.values()),
        "disease_count": len(disease_series),
        "frequency_meta": resolved_frequency,
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
            "population_source": None,
            "source_counts": {},
            "period_semantics": "per_source_reporting_period",
            "note_en": "Values are cases per 100,000 population for each source reporting period (for example, per week, month, or year); they are not annualized. Incidence was resolved when the curve was published.",
            "note_zh": "数值表示各来源报告期间内每10万人病例数（如每周、每月或每年），未作年化。发病率在曲线发布时已经算好。",
        },
        "disease_series": disease_series,
        "data_layer_summary": _data_layer_summary(disease_series),
        "heatmap": _heatmap_from_published_series(disease_series),
    }


def build_disease_data_from_country_series(
    disease_id: str,
    disease_info: dict,
    country_series: dict[str, dict],
    *,
    jurisdiction_codes: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Build one disease snapshot from country curves already in memory."""

    return {
        **disease_info,
        "country_series": country_series,
        "subdivision_country_codes": sorted(
            code for code in country_series if "-" in code
        ),
        "data_layer_summary": _country_series_data_layer_summary(country_series),
    }


def build_disease_data(
    disease_id: str,
    disease_info: dict,
    all_records_by_country: dict[str, list],
    source_records_by_country: dict[str, list] | None = None,
    *,
    jurisdiction_codes: list[str] | tuple[str, ...] | None = None,
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
                    "cases": [],
                    "deaths": [],
                    "incidence_rates": [],
                    "incidence_sources": [],
                    "quality_statuses": [],
                    "temporal_granularities": [],
                    "series_codes": [],
                    "data_layers": [],
                }
            points[d]["cases"].append(rec.get("cases"))
            points[d]["deaths"].append(rec.get("deaths"))
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))
            points[d]["quality_statuses"].append(
                rec.get("quality_status") or rec.get("data_quality")
            )
            context = (
                rec.get("_series_context")
                if isinstance(rec.get("_series_context"), dict)
                else {}
            )
            points[d]["temporal_granularities"].append(
                rec.get("temporal_granularity")
                or context.get("period_granularity")
            )
            points[d]["series_codes"].append(rec.get("series_code"))
            points[d]["data_layers"].append(
                rec.get("data_layer") or context.get("data_layer")
            )

        series_dates = sorted(points.keys())
        series_cases = [sum_or_none(points[d]["cases"]) for d in series_dates]
        series_deaths = [sum_or_none(points[d]["deaths"]) for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]
        series_quality = [
            dominant_value(points[d]["quality_statuses"]) for d in series_dates
        ]
        series_point_granularities = [
            dominant_value(points[d]["temporal_granularities"]) for d in series_dates
        ]
        series_point_series_codes = [
            dominant_value(points[d]["series_codes"]) for d in series_dates
        ]
        series_point_data_layers = [
            dominant_value(points[d]["data_layers"]) for d in series_dates
        ]

        provenance = _provenance_with_source_records(
            disease_records,
            disease_source_records if source_records_by_country else None,
        )
        declared_granularity = provenance.get("period_granularity")
        display_granularity = declared_granularity or infer_display_cadence(series_dates)
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
            "quality_statuses": series_quality,
            "point_granularities": series_point_granularities,
            "point_series_codes": series_point_series_codes,
            "point_data_layers": series_point_data_layers,
            "provisional_from": trailing_provisional_from(
                series_dates, series_quality
            ),
            "total_cases": sum(value for value in series_cases if value is not None),
            "total_deaths": sum(value for value in series_deaths if value is not None),
            "metric_type": dominant_value(
                [rec.get("metric_type") for rec in disease_records]
            ),
            "reporting_basis": dominant_value(
                [rec.get("reporting_basis") for rec in disease_records]
            ),
            "time_basis": dominant_value(
                [rec.get("time_basis") for rec in disease_records]
            ),
            "comparability": dominant_value(
                [rec.get("comparability") for rec in disease_records]
            ),
            "definition_version": dominant_value(
                [rec.get("definition_version") for rec in disease_records]
            ),
            "recorded_at_as_of": dominant_value(
                [rec.get("recorded_at_as_of") for rec in disease_source_records]
            ),
            "recorded_history_complete": (
                bool(disease_source_records)
                and all(
                    rec.get("recorded_history_complete") is True
                    for rec in disease_source_records
                )
                if any(
                    rec.get("recorded_at_as_of") for rec in disease_source_records
                )
                else None
            ),
            **provenance,
            "period_granularity": display_granularity,
            "period_granularity_basis": (
                "source" if declared_granularity else
                "date_pattern" if display_granularity else "unknown"
            ),
        }

    subdivision_country_codes = sorted(
        code for code in country_series if "-" in code
    )

    return {
        **disease_info,
        "country_series": country_series,
        "subdivision_country_codes": subdivision_country_codes,
        "data_layer_summary": _country_series_data_layer_summary(country_series),
    }
