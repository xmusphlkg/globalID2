"""Shared source-scope helpers for crawl tasks and dashboard source views."""

from __future__ import annotations

from typing import Optional

EXPECTED_SCOPES_BY_COUNTRY = {
    "CN": ["cdc_weekly", "nhc", "pubmed"],
    "US": ["nndss_api"],
    "JP": ["jp_weekly"],
    "AU": ["all"],
    "TW": ["nidss_open_data"],
    "HK": ["chp_notifiable"],
    "BR": ["sinan_datasus"],
    "KR": ["kdca_open_api"],
    "CH": ["foph_idd"],
}

SOURCE_SCOPE_LABELS: dict[str, dict[str, str]] = {
    "all": {
        "en": "All Sources",
        "zh": "全部来源",
    },
    "cdc_weekly": {
        "en": "China CDC Weekly",
        "zh": "中国疾控中心周报",
    },
    "nhc": {
        "en": "NHC",
        "zh": "国家卫健委",
    },
    "pubmed": {
        "en": "PubMed",
        "zh": "PubMed 生物医学文献库",
    },
    "nndss_api": {
        "en": "US CDC NNDSS",
        "zh": "美国 CDC NNDSS",
    },
    "jp_weekly": {
        "en": "JP NIID Weekly",
        "zh": "日本 NIID/JIHS 周报",
    },
    "nidss_open_data": {
        "en": "Taiwan, China CDC NIDSS",
        "zh": "中国台湾 CDC NIDSS",
    },
    "chp_notifiable": {
        "en": "Hong Kong, China CHP Notifiable Diseases",
        "zh": "中国香港 CHP 法定传染病",
    },
    "sinan_datasus": {
        "en": "Brazil DATASUS SINAN",
        "zh": "巴西 DATASUS SINAN",
    },
    "kdca_open_api": {
        "en": "Korea KDCA EID",
        "zh": "韩国 KDCA EID",
    },
    "foph_idd": {
        "en": "Switzerland FOPH IDD",
        "zh": "瑞士 FOPH/BAG IDD",
    },
}

COUNTRY_SOURCE_LABEL_OVERRIDES: dict[tuple[str, str], dict[str, str]] = {
    ("AU", "all"): {
        "en": "Australia NINDSS",
        "zh": "澳大利亚 NINDSS",
    },
}

_EXACT_SCOPE_BY_DATA_SOURCE = {
    "china cdc: notifiable infectious diseases reports": "cdc_weekly",
    "china cdc weekly: notifiable infectious diseases reports": "cdc_weekly",
    "us cdc nndss": "nndss_api",
    "us cdc nndss weekly": "nndss_api",
    "japan niid weekly sentinel": "jp_weekly",
    "jp niid weekly sentinel": "jp_weekly",
    "taiwan, china cdc nidss open data": "nidss_open_data",
    "taiwan, china cdc nidss": "nidss_open_data",
    "taiwan cdc nidss open data": "nidss_open_data",
    "taiwan cdc nidss": "nidss_open_data",
    "hong kong, china chp notifiable infectious diseases": "chp_notifiable",
    "hong kong, china chp notifiable diseases": "chp_notifiable",
    "hong kong, china chp": "chp_notifiable",
    "hong kong chp notifiable infectious diseases": "chp_notifiable",
    "hong kong chp notifiable diseases": "chp_notifiable",
    "hong kong chp": "chp_notifiable",
    "brazil datasus sinan open data": "sinan_datasus",
    "brazil datasus sinan": "sinan_datasus",
    "korea kdca eid open api": "kdca_open_api",
    "korea kdca eid portal download": "kdca_open_api",
    "korea kosis download": "kdca_open_api",
    "korea kdca eid": "kdca_open_api",
    "korea kdca": "kdca_open_api",
    "switzerland foph idd mandatory reporting system": "foph_idd",
    "switzerland foph idd": "foph_idd",
    "switzerland bag idd": "foph_idd",
    "foph idd": "foph_idd",
    "bag idd": "foph_idd",
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
    "nidss": "nidss_open_data",
    "nidss_open_data": "nidss_open_data",
    "tw": "nidss_open_data",
    "taiwan": "nidss_open_data",
    "taiwan_cdc": "nidss_open_data",
    "chp": "chp_notifiable",
    "chp_notifiable": "chp_notifiable",
    "hk": "chp_notifiable",
    "hk_chp": "chp_notifiable",
    "hong_kong": "chp_notifiable",
    "sinan": "sinan_datasus",
    "sinan_datasus": "sinan_datasus",
    "datasus": "sinan_datasus",
    "br": "sinan_datasus",
    "kdca": "kdca_open_api",
    "kdca_open_api": "kdca_open_api",
    "kr": "kdca_open_api",
    "korea": "kdca_open_api",
    "data_go_kr": "kdca_open_api",
    "kdca_dportal": "kdca_open_api",
    "kdca_portal": "kdca_open_api",
    "kosis": "kdca_open_api",
    "kosis_file": "kdca_open_api",
    "foph": "foph_idd",
    "foph_idd": "foph_idd",
    "bag": "foph_idd",
    "bag_idd": "foph_idd",
    "idd": "foph_idd",
    "ch": "foph_idd",
    "switzerland": "foph_idd",
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
    if normalized == "all" and (country_code or "").strip().upper() == "HK":
        return "chp_notifiable"
    if normalized == "all" and (country_code or "").strip().upper() == "KR":
        return "kdca_open_api"
    if normalized == "all" and (country_code or "").strip().upper() == "CH":
        return "foph_idd"

    return normalized


def get_expected_scopes_for_country(country_code: Optional[str]) -> list[str]:
    """Return canonical data-source scopes declared for a country.

    The JSON bootstrap registry is the preferred source. The constant above is
    kept as a compatibility fallback for older deployments and tests.
    """
    code = (country_code or "").strip().upper()
    if not code:
        return []

    try:
        from src.core.country_library import get_country_bootstrap_config

        cfg = get_country_bootstrap_config(code)
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        raw_scopes = crawler_cfg.get("sources") or []
    except Exception:
        raw_scopes = []

    scopes = [
        canonicalize_task_source(str(scope), country_code=code)
        for scope in raw_scopes
        if str(scope).strip()
    ] or EXPECTED_SCOPES_BY_COUNTRY.get(code, [])

    seen: set[str] = set()
    ordered: list[str] = []
    for scope in scopes:
        if scope not in seen:
            seen.add(scope)
            ordered.append(scope)
    return ordered


def source_scope_label(
    scope: str,
    *,
    country_code: Optional[str] = None,
    lang: str = "en",
) -> str:
    """Return a localized UI label for a canonical source scope."""
    normalized_scope = canonicalize_task_source(scope, country_code=country_code)
    upper_country = (country_code or "").strip().upper()
    lang_key = "zh" if (lang or "").lower().startswith("zh") else "en"

    country_override = COUNTRY_SOURCE_LABEL_OVERRIDES.get((upper_country, normalized_scope))
    if country_override:
        return country_override.get(lang_key) or country_override.get("en") or normalized_scope

    labels = SOURCE_SCOPE_LABELS.get(normalized_scope)
    if labels:
        return labels.get(lang_key) or labels.get("en") or normalized_scope

    return normalized_scope or ("未知来源" if lang_key == "zh" else "Unknown Source")


def source_options_for_country(country_code: Optional[str]) -> list[dict[str, str]]:
    """Return source selector options with bilingual labels for a country."""
    code = (country_code or "").strip().upper()
    scopes = get_expected_scopes_for_country(code)
    if not scopes:
        scopes = ["all"]

    option_scopes = list(scopes)
    if len(scopes) > 1 and "all" not in option_scopes:
        option_scopes.insert(0, "all")

    return [
        {
            "value": scope,
            "label_en": source_scope_label(scope, country_code=code, lang="en"),
            "label_zh": source_scope_label(scope, country_code=code, lang="zh"),
        }
        for scope in option_scopes
    ]


def default_source_for_country(country_code: Optional[str]) -> str:
    """Return the preferred source value for forms and automation presets."""
    code = (country_code or "").strip().upper()
    scopes = get_expected_scopes_for_country(code)
    if len(scopes) > 1:
        return "all"
    if scopes:
        return scopes[0]
    return "all"


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
    if "nidss" in text or "taiwan cdc" in text or "taiwan, china cdc" in text:
        return "nidss_open_data"
    if "hong kong, china chp" in text or "hong kong chp" in text or "chp notifiable" in text:
        return "chp_notifiable"
    if "sinan" in text or "datasus" in text:
        return "sinan_datasus"
    if "kdca" in text or "korea" in text or "data.go.kr" in text:
        return "kdca_open_api"
    if "foph" in text or "bag" in text or "idd" in text or "switzerland" in text:
        return "foph_idd"
    if "nhc" in text or "gov" in text or "ndcpa" in text or "卫健" in text or "疾控局" in text:
        return "nhc"
    if "cdc" in text or "weekly" in text:
        return "cdc_weekly"
    if "nindss" in text or "australia" in text:
        return "all"
    return "all"


def scope_display_label(scope: str, *, country_code: Optional[str] = None) -> str:
    """Return a stable UI label for a canonical scope key."""
    return source_scope_label(scope, country_code=country_code, lang="en")


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
    if text in {
        "taiwan, china cdc nidss open data",
        "taiwan, china cdc nidss",
        "taiwan cdc nidss open data",
        "taiwan cdc nidss",
    }:
        return "Taiwan, China CDC NIDSS"
    if text in {
        "hong kong, china chp notifiable infectious diseases",
        "hong kong, china chp notifiable diseases",
        "hong kong, china chp",
        "hong kong chp notifiable infectious diseases",
        "hong kong chp notifiable diseases",
        "hong kong chp",
    }:
        return "Hong Kong, China CHP Notifiable Diseases"
    if text in {"brazil datasus sinan open data", "brazil datasus sinan"}:
        return "Brazil DATASUS SINAN"
    if text in {
        "korea kdca eid open api",
        "korea kdca eid portal download",
        "korea kosis download",
        "korea kdca eid",
        "korea kdca",
    }:
        return "Korea KDCA EID"
    if text in {
        "switzerland foph idd mandatory reporting system",
        "switzerland foph idd",
        "switzerland bag idd",
        "foph idd",
        "bag idd",
    }:
        return "Switzerland FOPH IDD"

    scope = scope_from_data_source(data_source)
    if scope != "all":
        return scope_display_label(scope, country_code=country_code)
    return data_source or "Unknown"
