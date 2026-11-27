"""Project Brazil's SINAN HIV and AIDS notifications across both age groups."""

from collections import defaultdict

from src.generation.site_incidence import aggregate_incidence
from src.generation.site_series_projection import (
    SERIES_DATA_LAYER,
    _collapse_selected_series_records,
    _source_series_details,
)
from src.services.disease_series_policy import is_case_count_series


BR_SINAN_AGE_SERIES = {
    "D005": ("SER_BR_AIDS_ADULT_AIDA", "SER_BR_AIDS_CHILD_AIDC"),
    "D162": ("SER_BR_HIV_ADULT_HIVA", "SER_BR_HIV_CHILD_HIVC"),
}


def br_sinan_age_totals(records: list[dict]) -> list[dict]:
    """Require a numeric adult and child notification count in the same month."""
    totals = []
    for disease_id, codes in BR_SINAN_AGE_SERIES.items():
        components = [
            row for row in records
            if row.get("disease_id") == disease_id
            and row.get("series_code") in codes
            and row.get("source_system") == "SRC_BR_SINAN"
            and row.get("geography_key") == "country:BR:national"
            and row.get("dimension_key") == "all"
            and row.get("temporal_granularity") == "monthly"
            and is_case_count_series(row)
        ]
        sources = _source_series_details(components)
        context = {
            "data_layer": SERIES_DATA_LAYER,
            "projection_policy": "sum_disjoint",
            "loss_risk": None,
            "period_granularity": "monthly",
            "selected_series_codes": list(codes),
            "available_series_count": len(sources),
            "source_series": sources,
            "coverage_status": "registry_only",
            "metric_layers": {
                "cases": SERIES_DATA_LAYER,
                "deaths": "not_available",
                "recoveries": "not_available",
            },
            "note_en": "Monthly SINAN notifications combine the adult and child series when both counts are available.",
            "note_zh": "SINAN 每月报告数为成人与儿童病例之和。两组都有报告数时才计算合计。",
        }
        observed = [row for row in components if row.get("cases") is not None]
        by_month = defaultdict(list)
        for row in observed:
            by_month[row["date"][:7]].append(row)
        for point in _collapse_selected_series_records(observed, context):
            point["incidence_rate"], point["incidence_rate_source"] = aggregate_incidence(
                point["cases"], by_month[point["date"][:7]]
            )
            totals.append(point)
    return totals
