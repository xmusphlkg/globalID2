"""Country metadata resolver based on ISO country library."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

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
}


COUNTRY_BOOTSTRAP_CONFIGS: dict[str, dict] = {
    "CN": {
        "data_source_url": "http://weekly.chinacdc.cn",
        "data_source_type": "web",
        "crawler_config": {
            "sources": ["cdc_weekly", "nhc", "pubmed_rss"],
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
