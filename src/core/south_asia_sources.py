"""Independent official surveillance contracts for four South Asian countries.

Discovery coverage is distinct from numerical coverage. SIDAS and IEDCR are
not registered as data APIs without verified public observation endpoints.
"""
from __future__ import annotations

SOUTH_ASIA_SOURCES = {
    "BD": {
        "name": "Bangladesh", "name_zh": "孟加拉国", "name_local": "বাংলাদেশ",
        "language": "bn-BD", "timezone": "Asia/Dhaka",
        "scope": "bd_dghs_dengue_daily", "source_id": "SRC_BD_DGHS_DENGUE",
        "module": "bd_dengue", "updater": "BangladeshDGHSDengueUpdater",
        "label_en": "Bangladesh DGHS Daily Dengue Hospital Surveillance",
        "label_zh": "孟加拉国 DGHS 登革热每日住院与死亡监测",
        "official_url": "https://dghs.gov.bd/pages/miscellaneous-infos?filters=%7B%22miscellaneous_info_type%22:%226a9cf0471fa8cd87d1f50227%22%7D",
        "cadence": "daily", "start_year": 2026, "priority": "P1",
    },
    "BT": {
        "name": "Bhutan", "name_zh": "不丹", "name_local": "འབྲུག",
        "language": "en-BT", "timezone": "Asia/Thimphu",
        "scope": "bt_rcdc_publications", "source_id": "SRC_BT_RCDC_PUBLICATIONS",
        "module": "bt", "updater": "BhutanRCDCPublicationsUpdater",
        "label_en": "Bhutan RCDC Publications and Sentinel Surveillance",
        "label_zh": "不丹 RCDC 出版物与哨点监测",
        "official_url": "https://www.rcdc.gov.bt/web/",
        "cadence": "biweekly_monthly_quarterly_and_events", "start_year": 2015,
        "priority": "P1",
    },
    "MV": {
        "name": "Maldives", "name_zh": "马尔代夫", "name_local": "Maldives",
        "language": "dv-MV", "timezone": "Indian/Maldives",
        "scope": "mv_hpa_communicable", "source_id": "SRC_MV_HPA_COMMUNICABLE",
        "module": "mv", "updater": "MaldivesHPAUpdater",
        "label_en": "Maldives HPA Communicable Disease Reports",
        "label_zh": "马尔代夫 HPA 传染病报告",
        "official_url": "https://health.gov.mv/en/publications?search=monthly",
        "cadence": "monthly", "start_year": 2015, "priority": "P1",
    },
    "AF": {
        "name": "Afghanistan", "name_zh": "阿富汗", "name_local": "افغانستان",
        "language": "en-AF", "timezone": "Asia/Kabul",
        "scope": "af_ndsr_weekly", "source_id": "SRC_AF_MOPH_NDSR",
        "module": "af", "updater": "AfghanistanNDSRUpdater",
        "label_en": "Afghanistan MoPH NDSR Weekly Epidemiological Report",
        "label_zh": "阿富汗卫生部 NDSR 流行病学周报",
        "official_url": "https://moph.gov.af/en/reports",
        "cadence": "weekly", "start_year": 2025, "priority": "P1",
    },
}



# Weekly integrations retain their own contract definitions.
from src.core.south_asia_weekly_sources import SOUTH_ASIA_WEEKLY_SOURCES
SOUTH_ASIA_SOURCES.update(SOUTH_ASIA_WEEKLY_SOURCES)

SOUTH_ASIA_FOUR_COUNTRIES = frozenset({"BD", "BT", "MV", "AF"})

SOUTH_ASIA_EXTRA_SOURCES = {
    ("AF", "af_who_outbreaks_weekly"): {
        **SOUTH_ASIA_SOURCES["AF"],
        "scope": "af_who_outbreaks_weekly", "source_id": "SRC_AF_WHO_OUTBREAKS",
        "module": "af_who_outbreaks", "updater": "AfghanistanWHOOutbreaksUpdater",
        "label_en": "Afghanistan WHO Weekly Infectious Disease Outbreak Situation Reports",
        "label_zh": "阿富汗 WHO 传染病疫情周报",
        "official_url": "https://www.emro.who.int/afg/information-resources/infectious-disease-outbreak-situation-reports.html",
        "source_kind": "international_surveillance", "start_year": 2025,
    },
}
SOUTH_ASIA_ALL_SOURCES = {
    **{(code, spec["scope"]): spec for code, spec in SOUTH_ASIA_SOURCES.items()},
    **SOUTH_ASIA_EXTRA_SOURCES,
}


def south_asia_source(code: str, scope: str | None = None) -> dict:
    if scope is None:
        scope = "af_who_outbreaks_weekly" if code == "AF" else SOUTH_ASIA_SOURCES[code]["scope"]
    return SOUTH_ASIA_ALL_SOURCES[(code, scope)]


def bootstrap_config(code: str) -> dict:
    spec = south_asia_source(code)
    scopes = [spec["scope"]] + [scope for country, scope in SOUTH_ASIA_ALL_SOURCES if country == code and scope != spec["scope"]]
    return {
        "public_release_enabled": False,
        "data_source_url": spec["official_url"],
        "data_source_type": "official_html_pdf",
        "crawler_config": {
            "sources": scopes, "preferred_source": spec["scope"], "cadence": spec["cadence"],
            "public_release_enabled": False,
            "full_history_start_year": spec["start_year"],
            "supports_start_year": True, "supports_fill_missing": False,
            "default_fill_missing": False, "dynamic_revision_enabled": True,
            "refresh_recent_months": 3, "reporting_area": "source_defined",
            "geography_key": "source:AF:who_outbreaks:reporting_sites" if code == "AF" else f"country:{code}:national",
            "source_kind": spec.get("source_kind", "national_primary"), "priority": spec["priority"],
        },
        "parser_config": {"primary": spec["scope"]},
        "disease_mapping_rules": {"strategy": "source_series_registry", "fallback": "quarantine_unmapped"},
        "report_config": {"default_type": "DAILY" if code == "BD" else "MONTHLY" if code == "MV" else "WEEKLY", "lang": spec["language"]},
        "notes": "Official report discovery and verified source-native observations; numerical, temporal and geographic coverage gaps remain explicit in the source audit.",
    }
