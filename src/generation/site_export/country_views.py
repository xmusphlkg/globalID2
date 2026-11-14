"""National monthly summaries and compact country/source-series chart payloads."""
from __future__ import annotations

from collections import defaultdict
from itertools import pairwise
from src.generation.site_series_projection import LEGACY_DATA_LAYER
from .provenance_views import _compact_source_series_metadata


def build_national_monthly_summary(
    country_series: dict[str, dict],
    jurisdiction_codes: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Build a national-only monthly subtotal with explicit reporting coverage.

    A missing country-month is unknown, never an implicit zero. The subtotal
    remains useful when partial, but its coverage is carried beside every
    monthly value so consumers cannot mistake it for a complete global total.
    """
    eligible: dict[str, dict] = {}
    excluded_weekly = 0
    excluded_unknown = 0
    inferred_monthly = 0
    for code, series in country_series.items():
        if "-" in code:
            continue
        granularity = str(series.get("period_granularity") or "").strip().lower()
        dates = series.get("dates") or []
        if granularity == "weekly":
            excluded_weekly += bool(dates)
            continue
        if granularity in {"annual", "yearly", "quarterly"}:
            continue
        if not dates:
            continue
        if granularity == "monthly" and series.get("period_granularity_basis") == "date_pattern":
            inferred_monthly += 1
        if granularity in {"", "unknown"}:
            month_ordinals = [
                int(date[:4]) * 12 + int(date[5:7])
                for date in dates
                if len(date) >= 10
                and date[:4].isdigit()
                and date[4:5] == "-"
                and date[5:7].isdigit()
                and date[7:10] == "-01"
            ]
            month_aligned = (
                len(month_ordinals) == len(dates)
                and len(set(month_ordinals)) == len(dates)
                and any(
                    right - left == 1
                    for left, right in pairwise(month_ordinals)
                )
            )
            if not month_aligned:
                excluded_unknown += 1
                continue
            inferred_monthly += 1
        eligible[code] = series

    by_month: dict[str, dict] = defaultdict(
        lambda: {
            "cases": 0,
            "deaths": 0,
            "reported_cases": set(),
            "reported_deaths": set(),
            "case_definitions": set(),
            "death_definitions": set(),
            "case_comparability": set(),
            "death_comparability": set(),
        }
    )
    for code, series in eligible.items():
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        deaths = series.get("deaths") or []
        definition = (
            series.get("metric_type"),
            series.get("reporting_basis"),
            series.get("definition_version"),
        )
        comparability = str(series.get("comparability") or "").lower()
        for index, value_date in enumerate(dates):
            month = str(value_date or "")[:7]
            if len(month) != 7 or month[4] != "-":
                continue
            bucket = by_month[month]
            if index < len(cases) and cases[index] is not None:
                bucket["cases"] += cases[index]
                bucket["reported_cases"].add(code)
                bucket["case_definitions"].add(definition)
                bucket["case_comparability"].add(comparability)
            if index < len(deaths) and deaths[index] is not None:
                bucket["deaths"] += deaths[index]
                bucket["reported_deaths"].add(code)
                bucket["death_definitions"].add(definition)
                bucket["death_comparability"].add(comparability)

    observed_months = sorted(by_month)
    months = observed_months
    if observed_months:
        try:
            first_year, first_month = map(int, observed_months[0].split("-"))
            last_year, last_month = map(int, observed_months[-1].split("-"))
            first_ordinal = first_year * 12 + first_month - 1
            last_ordinal = last_year * 12 + last_month - 1
            months = [
                f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"
                for ordinal in range(first_ordinal, last_ordinal + 1)
            ]
        except (ValueError, IndexError):
            # Malformed date labels remain visible as observed input labels;
            # the normal dataset validator reports their source separately.
            months = observed_months
    universe = {
        str(code).strip().upper()
        for code in (jurisdiction_codes or tuple(country_series))
        if str(code).strip() and "-" not in str(code)
    }
    expected = len(universe or eligible)
    cases_reported = [len(by_month[month]["reported_cases"]) for month in months]
    deaths_reported = [len(by_month[month]["reported_deaths"]) for month in months]

    def incompatible(bucket: dict, metric: str) -> bool:
        reported = bucket[f"reported_{metric}"]
        if len(reported) < 2:
            return False
        definitions = bucket[f"{metric[:-1]}_definitions"]
        comparability = bucket[f"{metric[:-1]}_comparability"]
        return (
            len(definitions) != 1
            or not all(next(iter(definitions)))
            or not comparability <= {"direct", "conditional"}
        )

    cases_incompatible = [incompatible(by_month[month], "cases") for month in months]
    deaths_incompatible = [incompatible(by_month[month], "deaths") for month in months]

    return {
        "months": months,
        "cases": [
            by_month[month]["cases"]
            if cases_reported[i] and not cases_incompatible[i]
            else None
            for i, month in enumerate(months)
        ],
        "deaths": [
            by_month[month]["deaths"]
            if deaths_reported[i] and not deaths_incompatible[i]
            else None
            for i, month in enumerate(months)
        ],
        "coverage": {
            "scope": "national_jurisdictions_only",
            "jurisdiction_universe_size": expected,
            "excluded_weekly_jurisdictions": excluded_weekly,
            "excluded_unknown_jurisdictions": excluded_unknown,
            "inferred_monthly_jurisdictions": inferred_monthly,
            "cases_reported_jurisdictions": cases_reported,
            "deaths_reported_jurisdictions": deaths_reported,
            "cases_incompatible_months": cases_incompatible,
            "deaths_incompatible_months": deaths_incompatible,
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
            "id": entry.get("disease_id"),
            "en": entry.get("name_en"),
            "zh": entry.get("name_zh"),
            "cat": entry.get("category"),
            "slug": entry.get("slug"),
            "tc": entry.get("total_cases", 0),
            "td": entry.get("total_deaths", 0),
            "lc": entry.get("latest_cases", 0),
            "ld": entry.get("latest_deaths", 0),
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
            "recorded_at_as_of": entry.get("recorded_at_as_of"),
            "recorded_history_complete": entry.get("recorded_history_complete"),
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
            "lt": country_data.get("location_type") or "country",
            "pc": country_data.get("parent_code"),
            "tc": country_data.get("total_cases"),
            "td": country_data.get("total_deaths"),
            "dc": country_data.get("disease_count"),
            "dr": country_data.get("date_range"),
            "data_layer_summary": country_data.get("data_layer_summary") or {},
            "recorded_at_as_of": country_data.get("recorded_at_as_of"),
            "recorded_history_complete": country_data.get(
                "recorded_history_complete"
            ),
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


def build_country_source_series_data(country_data: dict) -> dict:
    """Build the lazy-only source-observation payload for one country chart.

    Source observations can be much larger than the public projection.  Keeping
    them out of the document and the regular chart payload makes the page cheap
    to parse while retaining the source-series selector once the curve is in
    view.  Only complete, plottable series are emitted here.
    """

    source_series_by_disease: dict[str, list[dict]] = {}
    for disease_id, entry in (country_data.get("disease_series") or {}).items():
        plottable = [
            source
            for source in (entry.get("source_series") or [])
            if isinstance(source, dict)
            and isinstance(source.get("dates"), list)
            and isinstance(source.get("values"), list)
            and source["dates"]
            and len(source["dates"]) == len(source["values"])
        ]
        if plottable:
            source_series_by_disease[disease_id] = plottable

    return {
        "v": 1,
        "country_code": country_data.get("country_code"),
        "series": source_series_by_disease,
    }
