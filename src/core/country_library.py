"""Country metadata resolver based on ISO country library."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

try:
    import pycountry
except Exception:  # pragma: no cover - graceful fallback when optional dep is missing
    pycountry = None


@dataclass
class CountryProfile:
    code: str
    name: str
    name_en: str
    name_local: str
    language: str
    timezone: str
    source: str


COUNTRY_OVERRIDES: dict[str, dict[str, str]] = {
    "CN": {
        "name": "China",
        "name_local": "中国",
        "language": "zh-CN",
        "timezone": "Asia/Shanghai",
    },
    "US": {
        "name": "United States",
        "name_local": "United States",
        "language": "en-US",
        "timezone": "America/New_York",
    },
    "AU": {
        "name": "Australia",
        "name_local": "Australia",
        "language": "en-AU",
        "timezone": "Australia/Sydney",
    },
    "JP": {
        "name": "Japan",
        "name_local": "日本",
        "language": "ja-JP",
        "timezone": "Asia/Tokyo",
    },
    "TW": {
        "name": "Taiwan, China",
        "name_en": "Taiwan, China",
        "name_local": "中国台湾",
        "language": "zh-TW",
        "timezone": "Asia/Taipei",
    },
}


COUNTRY_BOOTSTRAP_CONFIGS: dict[str, dict] = {
    "CN": {
        "data_source_url": "http://weekly.chinacdc.cn",
        "data_source_type": "web",
        "crawler_config": {
            "sources": ["cdc_weekly", "nhc", "pubmed"],
        },
        "parser_config": {
            "primary": "china_cdc_weekly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "WEEKLY",
            "lang": "zh-CN",
        },
        "notes": "Auto bootstrapped by country library",
    },
    "US": {
        "data_source_url": "https://data.cdc.gov/browse?category=NNDSS",
        "data_source_type": "api",
        "crawler_config": {
            "sources": ["nndss_api"],
            "cadence": "weekly",
            "reporting_area": "TOTAL",
        },
        "parser_config": {
            "primary": "us_nndss_weekly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "WEEKLY",
            "lang": "en-US",
        },
        "notes": "CDC NNDSS weekly provisional data, bootstrapped for national TOTAL ingestion",
    },
    "JP": {
        "data_source_url": "https://www.niid.go.jp/niid/ja/data.html",
        "data_source_type": "web",
        "crawler_config": {
            "sources": ["jp_weekly"],
            "cadence": "weekly",
            "reporting_area": "総数",
            "weekly_csv_url": "",
            "max_candidate_csvs": 5,
        },
        "parser_config": {
            "primary": "jp_weekly_internal_crawler",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "WEEKLY",
            "lang": "ja-JP",
        },
        "notes": "NIID weekly data via internal globalID2 crawler (TOTAL-only ingestion)",
    },
    "AU": {
        "data_source_url": "https://www.health.gov.au/topics/national-notifiable-diseases-surveillance-system-nndss",
        "data_source_type": "microsoft_bi",
        "crawler_config": {
            "sources": ["all"],
            "cadence": "monthly",
            "dashboard_url": "https://nindss.health.gov.au/pbi-dashboard/",
            "capacity_id": "86715F84-E812-421E-972F-2211ACC9903A",
            "report_id": "bc027587-5e9e-4920-bf03-a45fd3079f25",
            "dataset_id": "3471d96b-c14c-403f-b3a6-016f1deac28e",
            "model_id": 3305775,
            "query_url": "",
            "query_payload": {
                "version": "1.0.0",
                "queries": [],
                "modelId": 3305775,
                "cancelRequests": True,
            },
            "auth_token": "",
            "headers": {
                "X-PowerBI-ReportId": "bc027587-5e9e-4920-bf03-a45fd3079f25",
            },
        },
        "parser_config": {
            "primary": "au_nindss_internal_crawler",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "en-AU",
        },
        "notes": "NINDSS Microsoft BI feed aggregated to national via internal globalID2 crawler",
    },
    "TW": {
        "data_source_url": "https://nidss.cdc.gov.tw/Home/Index",
        "data_source_type": "open_data_csv",
        "crawler_config": {
            "sources": ["nidss_open_data"],
            "cadence": "monthly",
            "index_url": "https://nidss.cdc.gov.tw/Home/Index",
            "monthly_csv_url_template": "https://od.cdc.gov.tw/eic/Age_County_Gender_{disease_code}.csv",
            "weekly_csv_url_template": "https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_{disease_code}.csv",
            "refresh_recent_months": 3,
            "reporting_area": "national",
        },
        "parser_config": {
            "primary": "tw_nidss_open_data_monthly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "zh-TW",
        },
        "notes": "Taiwan, China CDC NIDSS open data CSV aggregated to national monthly totals",
    },
}


ROOT = Path(__file__).resolve().parents[2]
COUNTRY_BOOTSTRAP_FILE = ROOT / "configs" / "country_bootstrap.json"


def _deep_merge_dict(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base and return a new dict."""
    merged = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


@lru_cache(maxsize=1)
def _load_country_bootstrap_registry() -> dict[str, dict]:
    """Load optional country bootstrap registry from configs/country_bootstrap.json."""
    if not COUNTRY_BOOTSTRAP_FILE.exists():
        return {}

    try:
        payload = json.loads(COUNTRY_BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict] = {}
    for raw_key, raw_val in payload.items():
        if not isinstance(raw_val, dict):
            continue
        key = str(raw_key).strip().upper()
        if not key:
            continue
        normalized[key] = raw_val
    return normalized


def get_country_profile(code: str) -> CountryProfile:
    """Resolve country profile from ISO alpha-2 code using pycountry first."""
    normalized = (code or "").strip().upper()
    if not normalized:
        raise ValueError("country code is required")

    iso_name_en = normalized
    source = "fallback"
    if pycountry is not None:
        hit = pycountry.countries.get(alpha_2=normalized)
        if hit:
            iso_name_en = hit.name
            source = "pycountry"

    override = COUNTRY_OVERRIDES.get(normalized, {})
    name_en = override.get("name_en", iso_name_en)
    name = override.get("name", name_en)
    name_local = override.get("name_local", name_en)
    language = override.get("language", "en")
    timezone = override.get("timezone", "UTC")

    return CountryProfile(
        code=normalized,
        name=name,
        name_en=name_en,
        name_local=name_local,
        language=language,
        timezone=timezone,
        source=source,
    )


def get_country_bootstrap_config(code: str) -> dict:
    """Return optional bootstrap config for known countries.

    Merge priority (low -> high):
    1) hardcoded fallback in source code
    2) registry-level default from bootstrap.json key "_DEFAULT"
    3) country-specific override from bootstrap.json key like "CN"
    """
    normalized = (code or "").strip().upper()
    fallback = COUNTRY_BOOTSTRAP_CONFIGS.get(normalized, {})
    registry = _load_country_bootstrap_registry()
    default_cfg = registry.get("_DEFAULT", {})
    country_cfg = registry.get(normalized, {})

    merged = _deep_merge_dict(fallback, default_cfg)
    merged = _deep_merge_dict(merged, country_cfg)
    return merged


def get_standard_country_codes() -> list[str]:
    """Return sorted country codes declared in the standard library.

    This union includes hardcoded overrides and bootstrap config entries.
    Special config keys such as "_DEFAULT" are ignored.
    """
    registry = _load_country_bootstrap_registry()
    registry_codes = {
        code
        for code in registry.keys()
        if code and code != "_DEFAULT"
    }
    codes = set(COUNTRY_OVERRIDES.keys()) | set(COUNTRY_BOOTSTRAP_CONFIGS.keys()) | registry_codes
    return sorted(codes)


def validate_standard_country_registry() -> list[str]:
    """Return human-readable validation warnings for standard country definitions."""
    warnings: list[str] = []

    hardcoded = set(COUNTRY_OVERRIDES.keys())
    fallback = set(COUNTRY_BOOTSTRAP_CONFIGS.keys())
    registry = _load_country_bootstrap_registry()
    configured = {k for k in registry.keys() if k != "_DEFAULT"}

    pattern = re.compile(r"^[A-Z]{2}$")
    for code in sorted(hardcoded | fallback | configured):
        if not pattern.match(code):
            warnings.append(f"invalid country code format: {code}")

    # These sets can drift over time; flag it explicitly for maintainers.
    for code in sorted(hardcoded - (fallback | configured)):
        warnings.append(f"{code} exists in COUNTRY_OVERRIDES but has no bootstrap config")
    for code in sorted((fallback | configured) - hardcoded):
        warnings.append(f"{code} has bootstrap config but no COUNTRY_OVERRIDES profile")

    return warnings
