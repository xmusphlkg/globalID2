"""Country metadata resolver based on ISO country library."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRIES

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
    "AT": {"name": "Austria", "name_local": "Österreich", "language": "de-AT", "timezone": "Europe/Vienna"},
    "DE": {"name": "Germany", "name_local": "Deutschland", "language": "de-DE", "timezone": "Europe/Berlin"},
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
    "AU-ACT": {
        "name": "Australian Capital Territory, Australia",
        "name_en": "Australian Capital Territory, Australia",
        "name_local": "Australian Capital Territory, Australia",
        "language": "en-AU",
        "timezone": "Australia/Sydney",
    },
    "AU-NSW": {
        "name": "New South Wales, Australia",
        "name_en": "New South Wales, Australia",
        "name_local": "New South Wales, Australia",
        "language": "en-AU",
        "timezone": "Australia/Sydney",
    },
    "AU-NT": {
        "name": "Northern Territory, Australia",
        "name_en": "Northern Territory, Australia",
        "name_local": "Northern Territory, Australia",
        "language": "en-AU",
        "timezone": "Australia/Darwin",
    },
    "AU-QLD": {
        "name": "Queensland, Australia",
        "name_en": "Queensland, Australia",
        "name_local": "Queensland, Australia",
        "language": "en-AU",
        "timezone": "Australia/Brisbane",
    },
    "AU-SA": {
        "name": "South Australia, Australia",
        "name_en": "South Australia, Australia",
        "name_local": "South Australia, Australia",
        "language": "en-AU",
        "timezone": "Australia/Adelaide",
    },
    "AU-TAS": {
        "name": "Tasmania, Australia",
        "name_en": "Tasmania, Australia",
        "name_local": "Tasmania, Australia",
        "language": "en-AU",
        "timezone": "Australia/Hobart",
    },
    "AU-VIC": {
        "name": "Victoria, Australia",
        "name_en": "Victoria, Australia",
        "name_local": "Victoria, Australia",
        "language": "en-AU",
        "timezone": "Australia/Melbourne",
    },
    "AU-WA": {
        "name": "Western Australia, Australia",
        "name_en": "Western Australia, Australia",
        "name_local": "Western Australia, Australia",
        "language": "en-AU",
        "timezone": "Australia/Perth",
    },
    "CA": {
        "name": "Canada",
        "name_local": "Canada",
        "language": "en-CA",
        "timezone": "America/Toronto",
    },
    "CA-ON": {
        "name": "Ontario, Canada",
        "name_en": "Ontario, Canada",
        "name_local": "Ontario, Canada",
        "language": "en-CA",
        "timezone": "America/Toronto",
    },
    "NZ": {
        "name": "New Zealand",
        "name_local": "New Zealand",
        "language": "en-NZ",
        "timezone": "Pacific/Auckland",
    },
    "FI": {
        "name": "Finland",
        "name_local": "Suomi",
        "language": "fi-FI",
        "timezone": "Europe/Helsinki",
    },
    "FR": {
        "name": "France",
        "name_local": "France",
        "language": "fr-FR",
        "timezone": "Europe/Paris",
    },
    "ES": {"name": "Spain", "name_local": "España", "language": "es-ES", "timezone": "Europe/Madrid"},
    "IT": {"name": "Italy", "name_local": "Italia", "language": "it-IT", "timezone": "Europe/Rome"},
    "PT": {"name": "Portugal", "name_local": "Portugal", "language": "pt-PT", "timezone": "Europe/Lisbon"},
    "PL": {"name": "Poland", "name_local": "Polska", "language": "pl-PL", "timezone": "Europe/Warsaw"},
    "CZ": {"name": "Czechia", "name_local": "Česko", "language": "cs-CZ", "timezone": "Europe/Prague"},
    "GR": {"name": "Greece", "name_local": "Ελλάδα", "language": "el-GR", "timezone": "Europe/Athens"},
    "RO": {"name": "Romania", "name_local": "România", "language": "ro-RO", "timezone": "Europe/Bucharest"},
    "IE": {
        "name": "Ireland",
        "name_local": "Éire / Ireland",
        "language": "en-IE",
        "timezone": "Europe/Dublin",
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
    "NO": {
        "name": "Norway",
        "name_local": "Norge",
        "language": "nb-NO",
        "timezone": "Europe/Oslo",
    },
    "SE": {
        "name": "Sweden",
        "name_local": "Sverige",
        "language": "sv-SE",
        "timezone": "Europe/Stockholm",
    },
    "SG": {
        "name": "Singapore",
        "name_local": "Singapore",
        "language": "en-SG",
        "timezone": "Asia/Singapore",
    },
    "IS": {
        "name": "Iceland",
        "name_local": "Ísland",
        "language": "is-IS",
        "timezone": "Atlantic/Reykjavik",
    },
}

for _ecdc_code, _ecdc_meta in ECDC_BASELINE_COUNTRIES.items():
    COUNTRY_OVERRIDES.setdefault(
        _ecdc_code,
        {
            "name": _ecdc_meta["name"],
            "name_local": _ecdc_meta["name_local"],
            "language": _ecdc_meta["language"],
            "timezone": _ecdc_meta["timezone"],
        },
    )

COUNTRY_NAMES_ZH: dict[str, str] = {
    "AT": "奥地利",
    "AU": "澳大利亚",
    "AU-ACT": "澳大利亚首都领地",
    "AU-NSW": "澳大利亚新南威尔士州",
    "AU-NT": "澳大利亚北领地",
    "AU-QLD": "澳大利亚昆士兰州",
    "AU-SA": "澳大利亚南澳大利亚州",
    "AU-TAS": "澳大利亚塔斯马尼亚州",
    "AU-VIC": "澳大利亚维多利亚州",
    "AU-WA": "澳大利亚西澳大利亚州",
    "BR": "巴西",
    "CA": "加拿大",
    "CA-ON": "加拿大安大略省",
    "CH": "瑞士",
    "CN": "中国",
    "DE": "德国",
    "FI": "芬兰",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "PT": "葡萄牙",
    "PL": "波兰",
    "CZ": "捷克",
    "GR": "希腊",
    "RO": "罗马尼亚",
    "IE": "爱尔兰",
    "JP": "日本",
    "KR": "韩国",
    "NZ": "新西兰",
    "NO": "挪威",
    "SE": "瑞典",
    "SG": "新加坡",
    "TW": "中国台湾",
    "HK": "中国香港",
    "IS": "冰岛",
    "US": "美国",
}

for _ecdc_code, _ecdc_meta in ECDC_BASELINE_COUNTRIES.items():
    COUNTRY_NAMES_ZH.setdefault(_ecdc_code, _ecdc_meta["name_zh"])


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
    "CA": {
        "location_type": "country",
        "iso_country_code": "CA",
        "data_source_url": "https://diseases.canada.ca/notifiable/extract-dataset",
        "data_source_type": "open_data_json",
        "crawler_config": {
            "sources": ["phac_cndss_annual"],
            "cadence": "annual",
            "describe_url": "https://diseases.canada.ca/ndc/json/en_US/1924/describe.json",
            "raw_url": "https://diseases.canada.ca/ndc/s/raw",
            "reporting_area": "national",
            "geography_key": "country:CA:national",
            "required_attribution": (
                "Contains information licensed under the Open Government Licence – Canada; "
                "source: Public Health Agency of Canada, Canadian Notifiable Disease "
                "Surveillance System (CNDSS)."
            ),
            "reuse_terms_url": "https://open.canada.ca/en/open-government-licence-canada",
            "public_release_enabled": True,
        },
        "parser_config": {"primary": "ca_phac_cndss_national_annual"},
        "disease_mapping_rules": {
            "strategy": "source_series_registry",
            "fallback": "quarantine_unmapped",
        },
        "report_config": {"default_type": "ANNUAL", "lang": "en-CA"},
        "notes": (
            "PHAC CNDSS national annual reported counts under the Open Government "
            "Licence – Canada. A national aggregate is not an all-jurisdiction "
            "completeness claim: disease/year inclusion varies, and Manitoba "
            "2023 data were unavailable for 44 disease contracts. Subdivision "
            "feeds remain separately registered."
        ),
    },
    "CA-ON": {
        "parent_country_code": "CA",
        "location_type": "subdivision",
        "iso_subdivision_code": "CA-ON",
        "data_source_url": (
            "https://www.publichealthontario.ca/en/data-and-analysis/"
            "infectious-disease/reportable-disease-trends-annually"
        ),
        "data_source_type": "microsoft_bi",
        "crawler_config": {
            "sources": ["pho_idto_monthly"],
            "cadence": "monthly",
            "landing_url": (
                "https://www.publichealthontario.ca/en/data-and-analysis/"
                "infectious-disease/reportable-disease-trends-annually"
            ),
            "embed_url": (
                "https://ws-rpt1.publichealthontario.ca/Home/EmbedReport/"
                "14b5691a-c95d-46b2-84f1-9119080e083b"
            ),
            "report_id": "14b5691a-c95d-46b2-84f1-9119080e083b",
            "page_display_name": "Monthly Data Table",
            "visual_name": "8533f6960c0f199b51ae",
            "refresh_recent_months": 12,
            "supports_fill_missing": False,
            "default_fill_missing": False,
            "reporting_area": "Ontario",
            "geocode": "CA-ON",
            "geography_key": "country:CA-ON:national",
        },
        "parser_config": {
            "primary": "ca_on_pho_idto_monthly",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "quarantine_unmapped",
        },
        "report_config": {
            "default_type": "MONTHLY",
            "lang": "en-CA",
        },
        "notes": (
            "Public Health Ontario IDTO current-year preliminary monthly case "
            "counts for Ontario, published as an independent country/region "
            "dataset linked to parent Canada."
        ),
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
    "AT": {
        "public_release_enabled": False,
        "data_source_url": "https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases",
        "data_source_type": "official_monthly_csv",
        "crawler_config": {"sources": ["ages_radar"], "cadence": "monthly", "source_update_cadence": "monthly_revisable", "refresh_recent_months": 3, "dynamic_revision_enabled": True, "reporting_area": "national", "geography_key": "country:AT:national", "reuse_status": "license_review_pending"},
        "parser_config": {"primary": "at_ages_radar_monthly"},
        "disease_mapping_rules": {"strategy": "db_first", "fallback": "quarantine_unmapped"},
        "report_config": {"default_type": "MONTHLY", "lang": "de-AT"},
        "notes": "AGES Radar monthly national source. Internal ingestion retains native categories; public release is disabled pending licence review.",
    },
    "DE": {
        "public_release_enabled": True,
        "data_source_url": "https://survstat.rki.de/Content/Query/Create.aspx",
        "data_source_type": "official_webforms_csv_export",
        "crawler_config": {"sources": ["rki_survstat"], "cadence": "weekly", "source_update_cadence": "weekly_revisable", "full_history_start_year": 2001, "refresh_recent_weeks": 12, "dynamic_revision_enabled": True, "reporting_area": "national", "geography_key": "country:DE:national", "export_format": "zip_csv"},
        "parser_config": {"primary": "de_rki_survstat_weekly"},
        "disease_mapping_rules": {"strategy": "db_first", "fallback": "quarantine_unmapped"},
        "report_config": {"default_type": "WEEKLY", "lang": "de-DE"},
        "notes": "RKI SurvStat national weekly notification export; raw ZIP exports are retained and recent weeks are overwritten for revisions.",
    },
    "FI": {
        "public_release_enabled": True,
        "data_source_url": "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases",
        "data_source_type": "cube_csv",
        "crawler_config": {
            "sources": ["thl_ttr"],
            "cadence": "monthly",
            "source_update_cadence": "daily",
            "cube_url": "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases",
            "dimensions_url": "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases.dimensions.json",
            "full_history_start_year": 1995,
            "refresh_recent_months": 3,
            "supports_current_month": True,
            "default_include_current_month": True,
            "dynamic_revision_enabled": True,
            "current_month_status": "provisional",
            "publish_closed_months_only": False,
            "reporting_area": "national",
            "geography_key": "country:FI:national",
            "license": "CC BY 4.0",
        },
        "parser_config": {"primary": "fi_thl_ttr_monthly"},
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {"default_type": "MONTHLY", "lang": "fi-FI"},
        "notes": "Finland THL Infectious Diseases Register national monthly case counts. The current month is ingested as provisional and the latest three months are refreshed for source revisions.",
    },
    "IE": {
        "public_release_enabled": False,
        "data_source_url": "https://notifiabledisease.hpsc.ie/",
        "data_source_type": "official_arcgis_and_reviewed_pdf_catalogue",
        "crawler_config": {
            "sources": ["hpsc_ndh", "hpsc_weekly_archive", "hpsc_annual"],
            "cadence": "weekly",
            "source_update_cadence": "weekly_revisable",
            "service_url": "https://services3.arcgis.com/dQsP3byyKkTT53Ep/arcgis/rest/services/IDHUB_AllCasesTS_L/FeatureServer/0",
            "full_history_start_year": 2021,
            "history_start_week": 30,
            "refresh_recent_weeks": 12,
            "dynamic_revision_enabled": True,
            "reporting_area": "national",
            "geography_key": "country:IE:national",
            "api_contract": "unversioned_official_arcgis_table",
            "reuse_status": "written_permission_required",
            "annual_index_url": "https://www.hpsc.ie/notifiablediseases/annualidstatistics/",
            "annual_history_start_year": 2004,
            "annual_history_end_year": 2020,
            "supports_fill_missing": True,
            "default_fill_missing": True,
            "source_policies": {
                "hpsc_ndh": {
                    "source_kind": "current",
                    "temporal_granularity": "weekly",
                    "full_history_start_year": 2021,
                    "supports_start_year": True,
                    "supports_fill_missing": True,
                    "default_fill_missing": True,
                    "dynamic_revision_enabled": True,
                    "revision_window_unit": "weeks",
                    "default_revision_window": 12,
                    "source_update_cadence": "weekly_revisable",
                    "public_release_enabled": False,
                },
                "hpsc_annual": {
                    "source_kind": "history",
                    "temporal_granularity": "annual",
                    "full_history_start_year": 2004,
                    "history_end_year": 2020,
                    "supports_start_year": True,
                    "supports_fill_missing": True,
                    "default_fill_missing": True,
                    "dynamic_revision_enabled": False,
                    "revision_window_unit": "years",
                    "default_revision_window": 3,
                    "source_update_cadence": "reviewed_annual_history",
                    "public_release_enabled": False,
                },
                "hpsc_weekly_archive": {
                    "source_kind": "history",
                    "temporal_granularity": "weekly",
                    "full_history_start_year": 2015,
                    "history_end_year": 2021,
                    "supports_start_year": True,
                    "supports_fill_missing": True,
                    "default_fill_missing": True,
                    "dynamic_revision_enabled": False,
                    "revision_window_unit": "weeks",
                    "default_revision_window": 1,
                    "source_update_cadence": "immutable_archive_reconciliation",
                    "public_release_enabled": False,
                },
            },
        },
        "parser_config": {
            "primary": "ie_hpsc_ndh_weekly",
            "history": "ie_hpsc_annual_pdf",
            "weekly_archive": "ie_hpsc_lenus_wayback_pdf",
        },
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "quarantine_unmapped",
        },
        "report_config": {"default_type": "WEEKLY", "lang": "en-IE"},
        "notes": "Ireland HPSC weekly totals from 2021 W30 onward, sparse reconstructed weekly PDF snapshots for 2015–2021 W29, and separate annual history for 2004–2020. The grains are not spliced and archive public release is disabled.",
    },
    "NO": {
        "public_release_enabled": True,
        "data_source_url": "https://allvis.fhi.no/msis",
        "data_source_type": "official_json_api",
        "crawler_config": {
            "sources": ["fhi_msis"],
            "cadence": "monthly",
            "source_update_cadence": "daily",
            "diagnoses_url": "https://allvis.fhi.no/api/msis/kodeverk/diagnoser",
            "monthly_url": "https://allvis.fhi.no/api/msis/etterDiagnoseFordeltPaaMaaned",
            "full_history_start_year": 1977,
            "refresh_recent_months": 3,
            "supports_current_month": True,
            "default_include_current_month": True,
            "dynamic_revision_enabled": True,
            "current_month_status": "provisional",
            "publish_closed_months_only": False,
            "reporting_area": "national",
            "geography_key": "country:NO:national",
            "api_contract": "unversioned_official_frontend",
        },
        "parser_config": {"primary": "no_fhi_msis_monthly"},
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {"default_type": "MONTHLY", "lang": "nb-NO"},
        "notes": "Norway FHI MSIS national monthly notifications from the official Allvis frontend API; adapter contract checks guard the unversioned endpoint.",
    },
    "SE": {
        "public_release_enabled": True,
        "data_source_url": "https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/",
        "data_source_type": "official_html_csv",
        "crawler_config": {
            "sources": ["fohm_sminet"],
            "cadence": "monthly",
            "source_update_cadence": "daily_revisions",
            "publication_day": 8,
            "catalog_url": "https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/",
            "full_history_start_year": 2016,
            "refresh_recent_months": 3,
            "supports_current_month": True,
            "default_include_current_month": True,
            "dynamic_revision_enabled": True,
            "current_month_status": "provisional_when_source_evidence_exists",
            "publish_closed_months_only": True,
            "reporting_area": "national",
            "geography_key": "country:SE:national",
            "preferred_transport": "csv_with_html_fallback",
            "reuse_status": "approved_for_public_release",
        },
        "parser_config": {"primary": "se_fohm_sminet_monthly"},
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "learning_suggestions",
        },
        "report_config": {"default_type": "MONTHLY", "lang": "sv-SE"},
        "notes": "Sweden SmiNet national monthly reported cases from the official Public Health Agency statistics pages; public release is enabled with closed-month publication and revisable recent-month refreshes.",
    },
    "SG": {
        "public_release_enabled": True,
        "data_source_url": "https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-2026/",
        "data_source_type": "official_csv_xlsx_pdf",
        "crawler_config": {
            "sources": ["cda_weekly_bulletin"],
            "cadence": "weekly",
            "full_history_start_year": 2012,
            "historical_csv_end_year": 2022,
            "cda_workbook_start_year": 2023,
            "cda_pdf_fallback_years": [2023],
            "cda_xlsx_start_year": 2024,
            "refresh_recent_weeks": 12,
            "reporting_area": "national",
            "geography_key": "country:SG:national",
            "reuse_status": "operator_authorized_public_release",
            "historical_reuse_status": "singapore_open_data_licence",
            "current_source_terms_status": "cda_written_permission_required",
            "series_quality_guard": {
                "mode": "fail_closed",
                "registry_coverage": "required",
                "history_lookback_days": 730
            }
        },
        "parser_config": {"primary": "sg_cda_weekly_notifications"},
        "disease_mapping_rules": {
            "strategy": "db_first",
            "fallback": "quarantine_unmapped"
        },
        "report_config": {"default_type": "WEEKLY", "lang": "en-SG"},
        "notes": "Singapore weekly case notifications: 2012-2022 official data.gov.sg CSV history and 2023+ CDA annual workbooks, with 2023 weekly PDFs as fallback. Public release is enabled by explicit operator authorization; CDA source terms remain recorded as requiring written permission."
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
    """Resolve a country or registered subdivision profile."""
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
    if normalized in ECDC_BASELINE_COUNTRIES:
        merged = _merge_ecdc_baseline_config(normalized, merged)
    return merged


def _merge_ecdc_baseline_config(code: str, config: dict) -> dict:
    """Attach the independent ECDC fallback without replacing national feeds."""

    meta = ECDC_BASELINE_COUNTRIES[code]
    merged = dict(config)
    crawler = dict(merged.get("crawler_config") or {})
    existing_sources = list(crawler.get("sources") or [])
    if "ecdc_atlas_annual" not in existing_sources:
        existing_sources.append("ecdc_atlas_annual")
    crawler["sources"] = existing_sources
    source_policies = dict(crawler.get("source_policies") or {})
    source_policies["ecdc_atlas_annual"] = {
        "source_kind": "regional_baseline",
        "temporal_granularity": "annual",
        "full_history_start_year": 1990,
        "supports_start_year": True,
        "supports_fill_missing": False,
        "default_fill_missing": False,
        "dynamic_revision_enabled": True,
        "revision_window_unit": "years",
        "default_revision_window": 3,
        "source_update_cadence": "daily_availability_check",
        "public_release_enabled": True,
        "dataset_id": 27,
        "dataset_code": "CURRENT.GENERAL",
        "required_attribution": (
            "Data provided by ECDC based on data reported by EU/EEA Member States."
        ),
        "reuse_terms_url": (
            "https://www.ecdc.europa.eu/en/publications-data/"
            "access-eueea-surveillance-data-third-parties"
        ),
    }
    crawler["source_policies"] = source_policies
    crawler.setdefault("reporting_area", "national")
    crawler.setdefault("geography_key", f"country:{code}:national")
    crawler.setdefault(
        "series_quality_guard",
        {
            "mode": "fail_closed",
            "registry_coverage": "required",
            "history_lookback_days": 7300,
        },
    )
    merged["crawler_config"] = crawler
    if len(existing_sources) == 1:
        crawler.update(source_policies["ecdc_atlas_annual"])
        merged.update(
            {
                "data_source_url": "https://atlas.ecdc.europa.eu/public/index.aspx/",
                "data_source_type": "official_ecdc_rest_aggregate",
                "parser_config": {"primary": "ecdc_atlas_annual_reported_cases"},
                "disease_mapping_rules": {
                    "strategy": "db_first",
                    "fallback": "quarantine_unmapped",
                },
                "report_config": {
                    "default_type": "ANNUAL",
                    "lang": meta["language"],
                },
            }
        )
    merged["public_release_enabled"] = True
    merged["ecdc_baseline"] = {
        "source_id": f"SRC_{code}_ECDC_ATLAS",
        "dataset_id": 27,
        "dataset_code": "CURRENT.GENERAL",
        "required_attribution": (
            "Data provided by ECDC based on data reported by EU/EEA Member States."
        ),
        "reuse_terms_url": (
            "https://www.ecdc.europa.eu/en/publications-data/"
            "access-eueea-surveillance-data-third-parties"
        ),
    }
    if code in {"AT", "IE"}:
        # These national adapters remain permission-gated. Public export is
        # enabled only for the independently licensed ECDC source facts.
        merged["public_source_systems"] = [f"SRC_{code}_ECDC_ATLAS"]
        merged["public_legacy_enabled"] = False
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
    fallback = set(COUNTRY_BOOTSTRAP_CONFIGS.keys()) | set(ECDC_BASELINE_COUNTRIES)
    registry = _load_country_bootstrap_registry()
    configured = {k for k in registry.keys() if k != "_DEFAULT"}

    pattern = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]{1,3})?$")
    for code in sorted(hardcoded | fallback | configured):
        if not pattern.match(code):
            warnings.append(f"invalid jurisdiction code format: {code}")

    # These sets can drift over time; flag it explicitly for maintainers.
    for code in sorted(hardcoded - (fallback | configured)):
        warnings.append(f"{code} exists in COUNTRY_OVERRIDES but has no bootstrap config")
    for code in sorted((fallback | configured) - hardcoded):
        warnings.append(f"{code} has bootstrap config but no COUNTRY_OVERRIDES profile")

    return warnings
