"""Canonical v2 fact projection for generated country datasets."""

from __future__ import annotations

from src.generation.download_package_v2 import source_reference
from src.generation.site_series_projection import LEGACY_DATA_LAYER


def build_country_canonical_facts(
    country_data: dict,
    source_info: dict,
) -> list[dict]:
    """Build the compact v2 fact shape directly from one country view."""

    facts: list[dict] = []
    country_code = str(country_data.get("country_code") or "").strip().upper()
    primary_scope = str(source_info.get("primary_scope") or "").strip()
    source_scopes = {
        str(source.get("scope") or "").strip()
        for source in (source_info.get("sources") or [])
        if str(source.get("scope") or "").strip()
    }
    if primary_scope:
        source_scopes.add(primary_scope)
    primary_source_ref = (
        source_reference(country_code, primary_scope) if primary_scope else None
    )
    source_refs = sorted(
        source_reference(country_code, scope) for scope in source_scopes
    )

    for series in (country_data.get("disease_series") or {}).values():
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        weekly_equiv = series.get("weekly_equiv_cases") or []
        deaths = series.get("deaths") or []
        incidence_rates = series.get("incidence_rates") or []
        incidence_sources = series.get("incidence_sources") or []
        mortality_rates = series.get("mortality_rates") or []
        series_codes = sorted(
            {
                str(code).strip()
                for code in (series.get("selected_series_codes") or [])
                if str(code).strip()
            }
        )

        for idx, date in enumerate(dates):
            facts.append(
                {
                    "country_code": country_code,
                    "disease_id": str(series.get("disease_id") or "").upper(),
                    "date": date,
                    "cases": cases[idx] if idx < len(cases) else 0,
                    "weekly_equiv_cases": (
                        weekly_equiv[idx] if idx < len(weekly_equiv) else None
                    ),
                    "deaths": deaths[idx] if idx < len(deaths) else 0,
                    "incidence_rate_per_100k": (
                        incidence_rates[idx] if idx < len(incidence_rates) else None
                    ),
                    "incidence_rate_source": (
                        incidence_sources[idx] if idx < len(incidence_sources) else None
                    ),
                    "mortality_rate": (
                        mortality_rates[idx] if idx < len(mortality_rates) else None
                    ),
                    "data_layer": series.get("data_layer") or LEGACY_DATA_LAYER,
                    "projection_policy": series.get("projection_policy")
                    or "legacy_fallback",
                    "series_codes": series_codes,
                    "loss_risk": series.get("loss_risk"),
                    "coverage_status": series.get("coverage_status"),
                    "legacy_gap_fill_count": series.get("legacy_gap_fill_count", 0),
                    "coverage_ratio_against_legacy": series.get(
                        "coverage_ratio_against_legacy"
                    ),
                    "primary_source_ref": primary_source_ref,
                    "source_refs": source_refs,
                }
            )

    return facts
