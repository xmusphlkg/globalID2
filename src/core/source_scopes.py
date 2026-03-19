"""Shared source-scope helpers for crawl tasks and dashboard source views."""

from __future__ import annotations

from typing import Optional

EXPECTED_SCOPES_BY_COUNTRY = {
    "CN": ["cdc_weekly", "nhc", "pubmed"],
    "US": ["nndss_api"],
    "JP": ["jp_weekly"],
    "AU": ["all"],
}

_EXACT_SCOPE_BY_DATA_SOURCE = {
    "china cdc: notifiable infectious diseases reports": "cdc_weekly",
    "china cdc weekly: notifiable infectious diseases reports": "cdc_weekly",
    "us cdc nndss": "nndss_api",
    "us cdc nndss weekly": "nndss_api",
    "japan niid weekly sentinel": "jp_weekly",
    "jp niid weekly sentinel": "jp_weekly",
    "nhc": "nhc",
    "gov data": "nhc",
    "pubmed": "pubmed",
    "australia nindss (location aggregated)": "all",
}

_TASK_SOURCE_ALIASES = {
    "gov": "nhc",
    "jp_idwr": "jp_weekly",
    "pubmed_rss": "pubmed",
    "au": "all",
    "au_nindss": "all",
    "location": "all",
    "external": "all",
}


def canonicalize_task_source(
    source: Optional[str],
    *,
    country_code: Optional[str] = None,
) -> str:
    """Normalize task/input source values to the dashboard's canonical scope keys."""
    normalized = (source or "all").strip().lower()
    if not normalized:
        normalized = "all"
    normalized = _TASK_SOURCE_ALIASES.get(normalized, normalized)

    if normalized == "local" and (country_code or "").strip().upper() == "JP":
        return "jp_weekly"

    return normalized


def scope_from_data_source(data_source: Optional[str]) -> str:
    """Map persisted disease_records.data_source text to a canonical scope key."""
    text = (data_source or "").strip().lower()
    if text in _EXACT_SCOPE_BY_DATA_SOURCE:
        return _EXACT_SCOPE_BY_DATA_SOURCE[text]

    if "pubmed" in text:
        return "pubmed"
    if "niid" in text or "japan" in text:
        return "jp_weekly"
    if "nndss" in text:
        return "nndss_api"
    if "nhc" in text or "gov" in text or "ndcpa" in text or "卫健" in text or "疾控局" in text:
        return "nhc"
    if "cdc" in text or "weekly" in text:
        return "cdc_weekly"
    if "nindss" in text or "australia" in text:
        return "all"
    return "all"


def scope_display_label(scope: str, *, country_code: Optional[str] = None) -> str:
    """Return a stable UI label for a canonical scope key."""
    normalized_scope = canonicalize_task_source(scope, country_code=country_code)
    upper_country = (country_code or "").strip().upper()

    if normalized_scope == "nndss_api":
        return "US CDC NNDSS"
    if normalized_scope == "jp_weekly":
        return "JP NIID Weekly"
    if normalized_scope == "pubmed":
        return "PubMed"
    if normalized_scope == "nhc":
        return "NHC"
    if normalized_scope == "cdc_weekly":
        return "China CDC Weekly"
    if normalized_scope == "all" and upper_country == "AU":
        return "Australia NINDSS"
    return "All Sources"


def canonical_data_source_label(
    data_source: Optional[str],
    *,
    country_code: Optional[str] = None,
) -> str:
    """Normalize persisted data_source text to a stable display label."""
    text = (data_source or "").strip().lower()
    if not text:
        return "Unknown"
    if text in {"gov data", "nhc"}:
        return "NHC"
    if text == "australia nindss (location aggregated)":
        return "Australia NINDSS"

    scope = scope_from_data_source(data_source)
    if scope != "all":
        return scope_display_label(scope, country_code=country_code)
    return data_source or "Unknown"
