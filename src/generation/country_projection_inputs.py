"""Small exact input aggregates needed to merge independently read diseases."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .site_data_views import _data_layer_summary

INPUT_KEY = "_incremental_projection_inputs"


def input_summaries(records: list[dict], allowed: set[str]) -> dict:
    grouped = {}
    for row in records:
        did = str(row.get("disease_id") or "")
        if did not in allowed:
            continue
        item = grouped.setdefault(did, {"record_count": 0, "first_date": None, "monthly_cases": {}, "incidence_sources": {}})
        item["record_count"] += 1
        when = row.get("date")
        if when:
            item["first_date"] = min(item["first_date"] or when, when)
        month = row.get("year_month")
        if month:
            item["monthly_cases"][month] = item["monthly_cases"].get(month, 0) + (row.get("cases") or 0)
        source = row.get("incidence_rate_source") or "missing_population"
        item["incidence_sources"][source] = item["incidence_sources"].get(source, 0) + 1
    return grouped


def attach_inputs(data: dict, records: list[dict]) -> None:
    data[INPUT_KEY] = {"schema_version": 1, "diseases": input_summaries(records, set(data.get("disease_series") or {}))}


def eligible_inputs(data: dict) -> bool:
    value = data.get(INPUT_KEY)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("diseases"), dict):
        return False
    for did, series in (data.get("disease_series") or {}).items():
        summary = value["diseases"].get(did)
        if series.get("dates") and not isinstance(summary, dict):
            return False
    return all(isinstance(summary, dict) and isinstance(summary.get("record_count"), int)
               and isinstance(summary.get("monthly_cases"), dict) and isinstance(summary.get("incidence_sources"), dict)
               for summary in value["diseases"].values())


def merge_country_diseases(previous: dict, current: dict, disease_ids: set[str], records: list[dict]) -> dict:
    """Replace whole disease histories and exactly rebuild country aggregates.

    No source observations are reconstructed from a public cases curve. Source
    arrays remain intact for unchanged diseases; changed diseases come from the
    current lossless SQL projection, including deletions and hidden measures.
    """
    if not eligible_inputs(previous):
        raise ValueError("Disease-scoped projection needs a complete input-summary baseline")
    merged = {**previous, **{key: value for key, value in current.items() if key not in {"disease_series", INPUT_KEY}}}
    series = {did: value for did, value in (previous.get("disease_series") or {}).items() if did not in disease_ids}
    series.update(current.get("disease_series") or {})
    series = dict(sorted(series.items()))
    summaries = {did: value for did, value in previous[INPUT_KEY]["diseases"].items() if did not in disease_ids}
    summaries.update(input_summaries(records, set(series)))
    active = {did: item for did, item in summaries.items() if did in series and item.get("record_count")}
    dates = sorted({when for item in series.values() for when in item.get("dates") or [] if when})
    merged.update({
        "disease_series": series,
        "total_cases": sum(item.get("total_cases") or 0 for item in series.values()),
        "total_deaths": sum(item.get("total_deaths") or 0 for item in series.values()),
        "disease_count": len(active),
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "data_layer_summary": _data_layer_summary(series),
        INPUT_KEY: {"schema_version": 1, "diseases": summaries},
    })
    sources = Counter()
    for item in active.values():
        sources.update(item["incidence_sources"])
    basis = dict(previous.get("incidence_rate_basis") or {})
    basis["source_counts"] = dict(sources)
    basis["population_source"] = " / ".join(sorted({source.removesuffix("_computed").upper() for source in sources if source.endswith("_computed")})) or None
    merged["incidence_rate_basis"] = basis
    eligible = {did: active[did] for did in sorted(active, key=lambda did: (active[did].get("first_date") or "", did))
                if str(series[did].get("period_granularity") or "").lower() not in {"annual", "yearly", "quarterly"}}
    months = sorted({month for item in eligible.values() for month in item["monthly_cases"]})
    ids = sorted(eligible, key=lambda did: series[did].get("total_cases") or 0, reverse=True)[:50]
    merged["heatmap"] = {
        "diseases": ids, "disease_labels": [series[did]["name_en"] for did in ids], "months": months,
        "z": [[math.log10(eligible[did]["monthly_cases"].get(month, 0) + 1) for month in months] for did in ids],
    }
    return merged


def precise_disease_scope(code: str, markers: list[dict] | None, baseline: dict, catalogue_ids: set[str]) -> set[str] | None:
    """Use only complete marker scopes whose source identities are understood."""
    if not markers or not eligible_inputs(baseline):
        return None
    for row in markers:
        scope = str(row.get("scope") or "")
        change_set = row.get("change_set")
        if scope and code.startswith(scope + "-") and (
            not isinstance(change_set, dict) or change_set.get("version") != 1 or change_set.get("precise") is not True
        ):
            return None
    relevant = [row for row in markers if row.get("scope") == code]
    if not relevant:
        return None
    by_series = defaultdict(set)
    for did, series in (baseline.get("disease_series") or {}).items():
        for source in series.get("source_series") or []:
            if source.get("series_code"):
                by_series[str(source["series_code"])].add(did)
        for source_code in series.get("selected_series_codes") or []:
            by_series[str(source_code)].add(did)
    changed = set()
    for row in relevant:
        change_set = row.get("change_set")
        if not isinstance(change_set, dict) or change_set.get("version") != 1 or change_set.get("precise") is not True or not isinstance(change_set.get("changes"), list) or not change_set["changes"]:
            return None
        for entry in change_set["changes"]:
            if not isinstance(entry, dict) or entry.get("operation") not in {"upsert", "delete"} or entry.get("geography_key") != f"country:{code}:national":
                return None
            series_code = str(entry.get("series_code") or "")
            # A new/unknown holding series cannot be inferred from a public
            # curve; keep its entire country scope conservative.
            if not by_series.get(series_code):
                return None
            changed.update(by_series[series_code])
    return changed if changed and changed <= catalogue_ids else None
