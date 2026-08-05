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
    "HK": {
        "name": "Hong Kong, China",
        "name_en": "Hong Kong, China",
        "name_local": "中国香港",
        "language": "zh-HK",
        "timezone": "Asia/Hong_Kong",
    },
    "KR": {
        "name": "South Korea",
        "name_en": "South Korea",
        "name_local": "대한민국",
        "language": "ko-KR",
        "timezone": "Asia/Seoul",
    },
    "BR": {
        "name": "Brazil",
        "name_local": "Brasil",
        "language": "pt-BR",
        "timezone": "America/Sao_Paulo",
    },
    "CH": {
        "name": "Switzerland",
        "name_local": "Schweiz / Suisse / Svizzera",
        "language": "en-CH",
        "timezone": "Europe/Zurich",
    },
}

COUNTRY_NAMES_ZH: dict[str, str] = {
    "AU": "澳大利亚",
    "BR": "巴西",
    "CH": "瑞士",
    "CN": "中国",
    "JP": "日本",
    "KR": "韩国",
    "NZ": "新西兰",
    "TW": "中国台湾",
    "HK": "中国香港",
    "US": "美国",
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
        "data_source_url": "https://www.cdc.gov/hiv-data/nhss/",
        "data_source_type": "api",
        "crawler_config": {
            "sources": ["nndss_api", "nhss_hiv"],
            "cadence": "mixed_weekly_annual",
            "reporting_area": "TOTAL",
        },
        "parser_config": {
            "primary": "us_cdc_multi_source",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "WEEKLY",
            "lang": "en-US",
        },
        "notes": "CDC NNDSS weekly data plus NHSS annual national HIV diagnoses",
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
    "HK": {
        "data_source_url": "https://www.chp.gov.hk/en/static/24012.html",
        "data_source_type": "open_data_csv",
        "crawler_config": {
            "sources": ["chp_notifiable"],
            "cadence": "monthly",
            "index_url": "https://www.chp.gov.hk/en/static/24012.html",
            "annual_csv_url_template": "https://www.chp.gov.hk/files/misc/nid{year}en.csv",
            "refresh_recent_months": 3,
            "full_history_start_year": 1997,
            "reporting_area": "national",
        },
        "parser_config": {
            "primary": "hk_chp_notifiable_monthly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "zh-HK",
        },
        "notes": "Hong Kong, China CHP annual notifiable infectious disease CSVs normalized to national monthly totals",
    },
    "KR": {
        "data_source_url": "https://www.data.go.kr/data/15139178/openapi.do",
        "data_source_type": "open_api_or_portal_download",
        "crawler_config": {
            "sources": ["kdca_open_api"],
            "cadence": "monthly",
            "base_url": "https://apis.data.go.kr/1790387/EIDAPIService",
            "portal_url": "https://dportal.kdca.go.kr/pot/is/inftnsdsEDW.do",
            "portal_stats_url": "https://dportal.kdca.go.kr/pot/is/selectBassDissStatsListEDWAjax.do",
            "regional_portal_url": "https://dportal.kdca.go.kr/pot/is/summaryRginEDW.do",
            "primary_operation": "PeriodRegion",
            "service_key_env": "DATA_GO_KR_SERVICE_KEY",
            "dportal_file_env": "KR_DPORTAL_FILE",
            "dportal_dir_env": "KR_DPORTAL_DIR",
            "kosis_file_env": "KR_KOSIS_FILE",
            "page_size": 1000,
            "refresh_recent_months": 3,
            "full_history_start_year": 2001,
            "reporting_area": "national",
        },
        "parser_config": {
            "primary": "kr_kdca_period_region_monthly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "ko-KR",
        },
        "notes": "Korea KDCA EID data from data.go.kr OpenAPI or KDCA/KOSIS portal downloads aggregated to national monthly totals",
    },
    "BR": {
        "data_source_url": "http://siab.datasus.gov.br/DATASUS/index.php?acao=41&area=0901&item=1",
        "data_source_type": "ftp_dbc",
        "crawler_config": {
            "sources": ["sinan_datasus"],
            "cadence": "monthly",
            "final_ftp_url": "ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/FINAIS/",
            "prelim_ftp_url": "ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/PRELIM/",
            "full_history_start_year": 2000,
            "refresh_recent_months": 3,
            "history_batch_months": 120,
            "max_workers": 6,
            "request_delay_seconds": 0.0,
            "max_retries": 3,
            "reporting_area": "national",
        },
        "parser_config": {
            "primary": "br_sinan_datasus_dbc_monthly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "pt-BR",
        },
        "notes": "Brazil Ministry of Health DATASUS/SINAN public DBC microdata aggregated to national monthly notification counts.",
    },
    "CH": {
        "data_source_url": "https://www.idd.bag.admin.ch/en/portal-data",
        "data_source_type": "rest_api",
        "crawler_config": {
            "sources": ["foph_idd"],
            "cadence": "weekly",
            "portal_url": "https://www.idd.bag.admin.ch/en/portal-data",
            "api_base_url": "https://www.idd.bag.admin.ch/api/v1",
            "full_history_start_year": 2013,
            "refresh_recent_months": 6,
            "refresh_recent_weeks": 12,
            "refresh_recent_years": 2,
            "reporting_area": "national",
            "primary_geography": "CH",
            "fallback_geography": "CHFL",
        },
        "parser_config": {
            "primary": "ch_foph_idd_cases",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {
            "default_type": "WEEKLY",
            "lang": "en-CH",
        },
        "notes": "Switzerland FOPH/BAG IDD mandatory reporting API normalized to national case rows. Monthly series may use the dashboard CHFL aggregate where CH-only monthly series are not exposed.",
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


def get_country_display_name(code: str, lang: str = "en") -> str:
    """Return a stable country display name for dashboard/site languages."""
    normalized = (code or "").strip().upper()
    if not normalized:
        return ""

    profile = get_country_profile(normalized)
    if (lang or "").lower().startswith("zh"):
        return COUNTRY_NAMES_ZH.get(normalized) or profile.name_local or profile.name_en or normalized
    return profile.name_en or profile.name or profile.name_local or normalized


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
