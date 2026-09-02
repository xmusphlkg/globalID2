"""Shared source-scope helpers for crawl tasks and dashboard source views."""

from __future__ import annotations

from typing import Optional

from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES

EXPECTED_SCOPES_BY_COUNTRY = {
    "CN": ["cdc_weekly", "nhc", "pubmed"],
    "US": ["nndss_api", "nhss_hiv"],
    "JP": ["jp_weekly"],
    "AU": ["all"],
    "AU-ACT": ["all"],
    "AU-NSW": ["all"],
    "AU-NT": ["all"],
    "AU-QLD": ["all"],
    "AU-SA": ["all"],
    "AU-TAS": ["all"],
    "AU-VIC": ["all"],
    "AU-WA": ["all"],
    "CA": ["phac_cndss_annual"],
    "CA-ON": ["pho_idto_monthly"],
    "NZ": ["phf_monthly"],
    "FI": ["thl_ttr"],
    "AT": ["ages_radar"],
    "DE": ["rki_survstat"],
    "IE": ["hpsc_ndh", "hpsc_weekly_archive", "hpsc_annual"],
    "TW": ["nidss_open_data"],
    "HK": ["chp_notifiable"],
    "BR": ["sinan_datasus"],
    "KR": ["kdca_open_api"],
    "CH": ["foph_idd"],
    "NO": ["fhi_msis"],
    "SE": ["fohm_sminet"],
    "SG": ["cda_weekly_bulletin"],
    "FR": ["ecdc_atlas_annual"],
    "ES": ["ecdc_atlas_annual"],
    "IT": ["ecdc_atlas_annual"],
    "PT": ["ecdc_atlas_annual"],
    "PL": ["ecdc_atlas_annual"],
    "CZ": ["ecdc_atlas_annual"],
    "GR": ["ecdc_atlas_annual"],
    "RO": ["ecdc_atlas_annual"],
    "IS": [
        "is_doh_annual",
        "is_doh_sti",
        "is_doh_respiratory",
        "is_doh_history",
        "is_doh_legacy_icd",
    ],
}

for _ecdc_country_code in ECDC_BASELINE_COUNTRY_CODES:
    _scopes = EXPECTED_SCOPES_BY_COUNTRY.setdefault(_ecdc_country_code, [])
    if "ecdc_atlas_annual" not in _scopes:
        _scopes.append("ecdc_atlas_annual")

SOURCE_SCOPE_LABELS: dict[str, dict[str, str]] = {
    "all": {
        "en": "All Sources",
        "zh": "全部来源",
    },
    "cdc_weekly": {
        "en": "China CDC Weekly",
        "zh": "中国疾控中心周报",
    },
    "cn_province_datacenter": {
        "en": "China Public Health Science Data Center — Province Monthly",
        "zh": "公共卫生科学数据中心分省月度数据",
    },
    "cn_province_monthly_report": {
        "en": "Provincial Statutory Infectious Disease Monthly Reports",
        "zh": "省级法定传染病月报",
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
    "nhss_hiv": {
        "en": "US CDC NHSS HIV",
        "zh": "美国 CDC NHSS HIV 监测",
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
    "pho_idto_monthly": {
        "en": "Public Health Ontario IDTO Monthly",
        "zh": "安大略省公共卫生局 IDTO 月度数据",
    },
    "phac_cndss_annual": {
        "en": "Canada PHAC CNDSS Annual",
        "zh": "加拿大公共卫生署 CNDSS 年度数据",
    },
    "phf_monthly": {
        "en": "New Zealand PHF Science Monthly Notifiable Diseases",
        "zh": "新西兰 PHF Science 法定传染病月度监测",
    },
    "thl_ttr": {
        "en": "Finland THL Infectious Diseases Register",
        "zh": "芬兰 THL 传染病登记",
    },
    "ages_radar": {"en": "Austria AGES Radar for Infectious Diseases", "zh": "奥地利 AGES 传染病雷达"},
    "rki_survstat": {"en": "Germany RKI SurvStat 2.0", "zh": "德国 RKI SurvStat 2.0"},
    "hpsc_ndh": {
        "en": "Ireland HPSC Notifiable Diseases Hub",
        "zh": "爱尔兰 HPSC 法定传染病中心",
    },
    "hpsc_annual": {
        "en": "Ireland HPSC Annual Statistics (2004–2020)",
        "zh": "爱尔兰 HPSC 年度历史统计（2004–2020）",
    },
    "hpsc_weekly_archive": {
        "en": "Ireland HPSC Weekly Report Archive (2015–2021 W29)",
        "zh": "爱尔兰 HPSC 周报档案（2015–2021年第29周）",
    },
    "fhi_msis": {
        "en": "Norway FHI MSIS Statistics Bank",
        "zh": "挪威 FHI MSIS 统计库",
    },
    "fohm_sminet": {
        "en": "Sweden Public Health Agency SmiNet",
        "zh": "瑞典公共卫生局 SmiNet",
    },
    "cda_weekly_bulletin": {
        "en": "Singapore CDA Weekly Infectious Diseases Bulletin",
        "zh": "新加坡 CDA 每周传染病通报",
    },
    "ecdc_atlas_annual": {
        "en": "ECDC Surveillance Atlas Annual Baseline",
        "zh": "ECDC 传染病监测图谱年度基线",
    },
    "is_doh_annual": {
        "en": "Iceland Directorate of Health Annual Dashboard",
        "zh": "冰岛卫生署年度传染病看板",
    },
    "is_doh_sti": {
        "en": "Iceland Directorate of Health STI Dashboard",
        "zh": "冰岛卫生署性病月度看板",
    },
    "is_doh_respiratory": {
        "en": "Iceland Directorate of Health Respiratory Dashboard",
        "zh": "冰岛卫生署呼吸道周度看板",
    },
    "is_doh_history": {
        "en": "Iceland Directorate of Health Historical Registry",
        "zh": "冰岛卫生署历史传染病登记",
    },
    "is_doh_legacy_icd": {
        "en": "Iceland Directorate of Health Legacy ICD Monthly",
        "zh": "冰岛卫生署历史 ICD 临床月报",
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
    "us cdc nhss": "nhss_hiv",
    "us cdc nhss hiv": "nhss_hiv",
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
    "public health ontario idto monthly preliminary data": "pho_idto_monthly",
    "public health ontario idto monthly": "pho_idto_monthly",
    "pho idto monthly": "pho_idto_monthly",
    "canadian notifiable disease surveillance system (cndss)": "phac_cndss_annual",
    "canadian notifiable disease surveillance system": "phac_cndss_annual",
    "phac cndss": "phac_cndss_annual",
    "nz phf science monthly notifiable disease surveillance": "phf_monthly",
    "nz phf science monthly notifiable disease surveillance (pdf)": "phf_monthly",
    "new zealand phf science monthly notifiable disease surveillance": "phf_monthly",
    "finland thl infectious diseases register": "thl_ttr",
    "finland thl ttr": "thl_ttr",
    "thl infectious diseases register": "thl_ttr",
    "austria ages radar for infectious diseases": "ages_radar",
    "ages radar": "ages_radar",
    "germany rki survstat 2.0": "rki_survstat",
    "rki survstat": "rki_survstat",
    "ireland hpsc notifiable diseases hub": "hpsc_ndh",
    "ireland hpsc ndh": "hpsc_ndh",
    "hpsc notifiable diseases hub": "hpsc_ndh",
    "ireland hpsc annual infectious disease statistics": "hpsc_annual",
    "ireland hpsc annual statistics": "hpsc_annual",
    "ireland hpsc weekly infectious disease report archive": "hpsc_weekly_archive",
    "norway fhi msis statistics bank": "fhi_msis",
    "norway fhi msis": "fhi_msis",
    "fhi msis": "fhi_msis",
    "sweden public health agency sminet": "fohm_sminet",
    "sweden fohm sminet": "fohm_sminet",
    "fohm sminet": "fohm_sminet",
    "singapore cda weekly infectious diseases bulletin": "cda_weekly_bulletin",
    "singapore data.gov.sg weekly infectious diseases bulletin (2012-2022)": "cda_weekly_bulletin",
    "cda weekly infectious diseases bulletin": "cda_weekly_bulletin",
    "ecdc surveillance atlas of infectious diseases": "ecdc_atlas_annual",
    "ecdc surveillance atlas annual baseline": "ecdc_atlas_annual",
    "iceland directorate of health annual dashboard": "is_doh_annual",
    "iceland directorate of health sti dashboard": "is_doh_sti",
    "iceland directorate of health respiratory dashboard": "is_doh_respiratory",
    "iceland directorate of health historical registry": "is_doh_history",
    "iceland directorate of health historical registry annual": "is_doh_history",
    "iceland directorate of health disease-specific monthly workbooks": "is_doh_history",
    "iceland directorate of health legacy icd monthly": "is_doh_legacy_icd",
    "iceland directorate of health legacy icd monthly reports": "is_doh_legacy_icd",
    "nhc": "nhc",
    "gov data": "nhc",
    "pubmed": "pubmed",
    "australia nindss (location aggregated)": "all",
}

_TASK_SOURCE_ALIASES = {
    "gov": "nhc",
    "nndss": "nndss_api",
    "nhss": "nhss_hiv",
    "nhss_hiv": "nhss_hiv",
    "hiv_nhss": "nhss_hiv",
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
    "pho": "pho_idto_monthly",
    "pho_idto": "pho_idto_monthly",
    "pho_idto_monthly": "pho_idto_monthly",
    "idto": "pho_idto_monthly",
    "ontario": "pho_idto_monthly",
    "ca-on": "pho_idto_monthly",
    "cndss": "phac_cndss_annual",
    "phac": "phac_cndss_annual",
    "phac_cndss": "phac_cndss_annual",
    "phac_cndss_annual": "phac_cndss_annual",
    "canada": "phac_cndss_annual",
    "phf": "phf_monthly",
    "phf_monthly": "phf_monthly",
    "nz": "phf_monthly",
    "new_zealand": "phf_monthly",
    "thl": "thl_ttr",
    "thl_ttr": "thl_ttr",
    "ttr": "thl_ttr",
    "fi": "thl_ttr",
    "finland": "thl_ttr",
    "ages": "ages_radar",
    "at": "ages_radar",
    "austria": "ages_radar",
    "survstat": "rki_survstat",
    "rki": "rki_survstat",
    "de": "rki_survstat",
    "germany": "rki_survstat",
    "hpsc": "hpsc_ndh",
    "hpsc_ndh": "hpsc_ndh",
    "ndh": "hpsc_ndh",
    "hpsc_annual": "hpsc_annual",
    "hpsc_annual_history": "hpsc_annual",
    "ie_annual": "hpsc_annual",
    "hpsc_weekly_archive": "hpsc_weekly_archive",
    "hpsc_archive": "hpsc_weekly_archive",
    "ie_weekly_archive": "hpsc_weekly_archive",
    "ie": "hpsc_ndh",
    "ireland": "hpsc_ndh",
    "fhi": "fhi_msis",
    "fhi_msis": "fhi_msis",
    "msis": "fhi_msis",
    "no": "fhi_msis",
    "norway": "fhi_msis",
    "fohm": "fohm_sminet",
    "fohm_sminet": "fohm_sminet",
    "sminet": "fohm_sminet",
    "se": "fohm_sminet",
    "sweden": "fohm_sminet",
    "cda": "cda_weekly_bulletin",
    "cda_weekly_bulletin": "cda_weekly_bulletin",
    "widb": "cda_weekly_bulletin",
    "sg": "cda_weekly_bulletin",
    "singapore": "cda_weekly_bulletin",
    "ecdc": "ecdc_atlas_annual",
    "atlas": "ecdc_atlas_annual",
    "ecdc_atlas_annual": "ecdc_atlas_annual",
    "fr": "ecdc_atlas_annual",
    "france": "ecdc_atlas_annual",
    "es": "ecdc_atlas_annual",
    "spain": "ecdc_atlas_annual",
    "it": "ecdc_atlas_annual",
    "italy": "ecdc_atlas_annual",
    "pt": "ecdc_atlas_annual",
    "portugal": "ecdc_atlas_annual",
    "pl": "ecdc_atlas_annual",
    "poland": "ecdc_atlas_annual",
    "cz": "ecdc_atlas_annual",
    "czechia": "ecdc_atlas_annual",
    "gr": "ecdc_atlas_annual",
    "greece": "ecdc_atlas_annual",
    "ro": "ecdc_atlas_annual",
    "romania": "ecdc_atlas_annual",
    "is_annual": "is_doh_annual",
    "is_doh_annual": "is_doh_annual",
    "is_sti": "is_doh_sti",
    "is_doh_sti": "is_doh_sti",
    "is_respiratory": "is_doh_respiratory",
    "is_doh_respiratory": "is_doh_respiratory",
    "is_history": "is_doh_history",
    "is_doh_history": "is_doh_history",
    "is_legacy_icd": "is_doh_legacy_icd",
    "is_doh_legacy_icd": "is_doh_legacy_icd",
    "iceland": "all",
    "is": "all",
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
    if normalized == "all" and (country_code or "").strip().upper() == "CA-ON":
        return "pho_idto_monthly"
    if normalized == "all" and (country_code or "").strip().upper() == "CA":
        return "phac_cndss_annual"
    if normalized == "all" and (country_code or "").strip().upper() == "NZ":
        return "phf_monthly"
    if normalized == "all" and (country_code or "").strip().upper() == "FI":
        return "thl_ttr"
    if normalized == "all" and (country_code or "").strip().upper() == "AT":
        return "ages_radar"
    if normalized == "all" and (country_code or "").strip().upper() == "DE":
        return "rki_survstat"
    if normalized == "all" and (country_code or "").strip().upper() == "IE":
        return "hpsc_ndh"
    if normalized == "all" and (country_code or "").strip().upper() == "NO":
        return "fhi_msis"
    if normalized == "all" and (country_code or "").strip().upper() == "SE":
        return "fohm_sminet"
    if normalized == "all" and (country_code or "").strip().upper() == "SG":
        return "cda_weekly_bulletin"
    if normalized == "all" and (
        (country_code or "").strip().upper()
        in ECDC_BASELINE_COUNTRY_CODES - {"AT", "DE", "FI", "IE", "IS", "NO", "SE"}
    ):
        return "ecdc_atlas_annual"

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


def get_known_task_sources(country_code: Optional[str] = None) -> set[str]:
    """Return source values accepted by task/source views.

    Source scopes are declared in the country bootstrap registry. This helper
    keeps dashboard task parsing in sync with that registry so new country
    sources do not need a second hand-maintained whitelist.
    """
    code = (country_code or "").strip().upper()
    scopes: set[str] = set(SOURCE_SCOPE_LABELS.keys()) | {"all"}
    if code:
        scopes.update(get_expected_scopes_for_country(code))
    else:
        scopes.update(scope for values in EXPECTED_SCOPES_BY_COUNTRY.values() for scope in values)
        try:
            from src.core.country_library import get_standard_country_codes

            for country in get_standard_country_codes():
                scopes.update(get_expected_scopes_for_country(country))
        except Exception:
            pass
    return scopes


def source_scope_label(
    scope: str,
    *,
    country_code: Optional[str] = None,
    lang: str = "en",
) -> str:
    """Return a localized UI label for a canonical source scope."""
    upper_country = (country_code or "").strip().upper()
    normalized_scope = canonicalize_task_source(scope, country_code=country_code)
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
    if len(scopes) > 1 and "all" not in option_scopes and code != "IE":
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
    if len(scopes) > 1 and code != "IE":
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
    if "nhss" in text:
        return "nhss_hiv"
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
    if "public health ontario" in text or "pho idto" in text:
        return "pho_idto_monthly"
    if "cndss" in text or "canadian notifiable disease surveillance" in text:
        return "phac_cndss_annual"
    if "phf science" in text or "nz phf" in text:
        return "phf_monthly"
    if "finland thl" in text or "thl infectious" in text or "thl ttr" in text:
        return "thl_ttr"
    if "fhi msis" in text or "norway fhi" in text:
        return "fhi_msis"
    if "hpsc weekly infectious disease report archive" in text:
        return "hpsc_weekly_archive"
    if "ireland hpsc annual" in text or "hpsc annual infectious" in text:
        return "hpsc_annual"
    if "sminet" in text or "sweden public health agency" in text or "sweden fohm" in text:
        return "fohm_sminet"
    if "iceland directorate of health" in text:
        if "annual" in text:
            return "is_doh_annual"
        if "sti" in text:
            return "is_doh_sti"
        if "respiratory" in text:
            return "is_doh_respiratory"
        if "legacy icd" in text:
            return "is_doh_legacy_icd"
        if "histor" in text or "registry" in text:
            return "is_doh_history"
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
    if text in {
        "public health ontario idto monthly preliminary data",
        "public health ontario idto monthly",
        "pho idto monthly",
    }:
        return "Public Health Ontario IDTO Monthly"
    if text in {
        "canadian notifiable disease surveillance system (cndss)",
        "canadian notifiable disease surveillance system",
        "phac cndss",
    }:
        return "Canada PHAC CNDSS Annual"
    if text in {
        "nz phf science monthly notifiable disease surveillance",
        "nz phf science monthly notifiable disease surveillance (pdf)",
        "new zealand phf science monthly notifiable disease surveillance",
    }:
        return "New Zealand PHF Science Monthly Notifiable Diseases"
    if text in {
        "finland thl infectious diseases register",
        "finland thl ttr",
        "thl infectious diseases register",
    }:
        return "Finland THL Infectious Diseases Register"
    if text in {
        "norway fhi msis statistics bank",
        "norway fhi msis",
        "fhi msis",
    }:
        return "Norway FHI MSIS Statistics Bank"
    if text in {
        "sweden public health agency sminet",
        "sweden fohm sminet",
        "fohm sminet",
    }:
        return "Sweden Public Health Agency SmiNet"

    scope = scope_from_data_source(data_source)
    if scope != "all":
        return scope_display_label(scope, country_code=country_code)
    return data_source or "Unknown"
