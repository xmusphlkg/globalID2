"""Pure builders for site source metadata and the About snapshot."""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import scope_display_label
from src.generation.site_data_views import resolve_country_display_names

SOURCE_DETAILS_BY_SCOPE: dict[tuple[str, str], dict[str, str]] = {
    ("CN", "cdc_weekly"): {
        "label": "China CDC Weekly",
        "url": "https://weekly.chinacdc.cn",
        "type": "web",
        "description": "Monthly notifiable infectious disease reports published by China CDC.",
    },
    ("CN", "nhc"): {
        "label": "National Disease Control and Prevention Administration",
        "url": "https://www.ndcpa.gov.cn",
        "machine_url": "https://www.ndcpa.gov.cn/queryList",
        "type": "web",
        "description": "Official China public health bulletin and query portal.",
    },
    ("CN", "pubmed"): {
        "label": "PubMed",
        "url": "https://pubmed.ncbi.nlm.nih.gov",
        "machine_url": (
            "https://pubmed.ncbi.nlm.nih.gov/rss/search/"
            "1tQjT4yH2iuqFpDL7Y1nShJmC4kDC5_BJYgw4R1O0BCs-_Nemt/"
        ),
        "type": "web",
        "description": "Biomedical literature discovery feed used as supplementary context.",
    },
    ("US", "nndss_api"): {
        "label": "US CDC NNDSS",
        "url": "https://data.cdc.gov/browse?category=NNDSS",
        "machine_url": "https://data.cdc.gov/resource/x9gk-5huc.csv",
        "type": "api",
        "description": "CDC National Notifiable Diseases Surveillance System provisional data.",
    },
    ("JP", "jp_weekly"): {
        "label": "JP NIID Weekly",
        "url": "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/index.html",
        "type": "web",
        "description": "Japan weekly infectious disease surveillance via NIID/JIHS.",
    },
    ("AU", "all"): {
        "label": "Australia NINDSS",
        "url": "https://www.health.gov.au/topics/national-notifiable-diseases-surveillance-system-nndss",
        "machine_url": "https://nindss.health.gov.au/pbi-dashboard/",
        "type": "microsoft_bi",
        "description": "Australian national notifiable diseases surveillance dashboard.",
    },
    ("CA-ON", "pho_idto_monthly"): {
        "label": "Public Health Ontario IDTO Monthly",
        "url": (
            "https://www.publichealthontario.ca/en/data-and-analysis/"
            "infectious-disease/reportable-disease-trends-annually"
        ),
        "machine_url": (
            "https://ws-rpt1.publichealthontario.ca/Home/EmbedReport/"
            "14b5691a-c95d-46b2-84f1-9119080e083b"
        ),
        "type": "microsoft_bi",
        "cadence": "monthly",
        "description": (
            "Ontario-level current-year preliminary monthly case counts from "
            "Public Health Ontario's IDTO report."
        ),
    },
    ("TW", "nidss_open_data"): {
        "label": "Taiwan, China CDC NIDSS",
        "url": "https://nidss.cdc.gov.tw/Home/Index",
        "machine_url": "https://od.cdc.gov.tw/eic/Age_County_Gender_{disease_code}.csv",
        "type": "open_data_csv",
        "description": "Taiwan, China monthly notifiable infectious disease open-data CSV feed.",
    },
    ("BR", "sinan_datasus"): {
        "label": "Brazil DATASUS SINAN",
        "url": "http://siab.datasus.gov.br/DATASUS/index.php?acao=41&area=0901&item=1",
        "machine_url": "ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/FINAIS/",
        "type": "ftp_dbc",
        "description": (
            "Brazil Ministry of Health DATASUS/SINAN public DBC microdata "
            "aggregated to national monthly notification counts."
        ),
    },
    ("KR", "kdca_open_api"): {
        "label": "Korea KDCA EID",
        "url": "https://www.data.go.kr/data/15139178/openapi.do",
        "machine_url": "https://apis.data.go.kr/1790387/EIDAPIService/PeriodRegion",
        "type": "open_api_or_portal_download",
        "description": (
            "Korea KDCA notifiable infectious disease OpenAPI or portal/KOSIS downloads "
            "aggregated to national monthly notification counts."
        ),
    },
    ("IS", "is_doh_annual"): {
        "label": "Iceland Directorate of Health Annual Dashboard",
        "url": "https://island.is/en/smitsjukdomar-tolur",
        "machine_url": "https://app.powerbi.com/view?r=eyJrIjoiY2Q4Mjk2NDQtNDA1MS00YTcxLTk1NzEtZTBlZDYwMTU3ZDNiIiwidCI6IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9",
        "type": "microsoft_bi",
        "cadence": "annual",
        "description": (
            "National annual case notifications by disease. Published values "
            "can be revised retrospectively and remain annual period totals."
        ),
    },
    ("IS", "is_doh_sti"): {
        "label": "Iceland Directorate of Health STI Dashboard",
        "url": "https://island.is/en/smitsjukdomar-tolur",
        "machine_url": "https://app.powerbi.com/view?r=eyJrIjoiNTNmN2ViYTEtZjdiZi00MmRkLWFjYWQtOWI0ZmEwNjhjYmQyIiwidCI6IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9",
        "type": "microsoft_bi",
        "cadence": "monthly facts · quarterly publication",
        "description": (
            "Monthly STI diagnosis facts from laboratory and registry surveillance; "
            "the official dashboard is published on a quarterly schedule."
        ),
    },
    ("IS", "is_doh_respiratory"): {
        "label": "Iceland Directorate of Health Respiratory Dashboard",
        "url": "https://island.is/en/respiratory-tract-infections",
        "machine_url": "https://app.powerbi.com/view?r=eyJrIjoiZjgyOWI0YzgtNjNkZC00Y2QzLTllMzctMWIxMTAxZThlMDJkIiwidCI6IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9",
        "type": "microsoft_bi",
        "cadence": "weekly",
        "description": (
            "ISO-week respiratory diagnosis counts. Hospitalizations, samples, "
            "and vaccination indicators are separate measures and are not counted as diagnoses."
        ),
    },
    ("IS", "is_doh_history"): {
        "label": "Iceland Directorate of Health Historical Registry",
        "url": "https://island.is/en/smitsjukdomar-tolur",
        "type": "official_excel",
        "cadence": "annual and monthly",
        "description": (
            "Historical official registry tables for 1997–2021, with annual totals "
            "and disease-specific monthly notification facts retained at source grain."
        ),
    },
    ("IS", "is_doh_legacy_icd"): {
        "label": "Iceland Directorate of Health Legacy ICD Monthly",
        "url": "https://island.is/en/smitsjukdomar-tolur",
        "type": "official_excel",
        "cadence": "monthly",
        "description": (
            "Historical Saga EHR ICD encounter counts for 1997–2020. This clinical "
            "measure is non-comparable with registry notifications and is kept separate."
        ),
    },
}

ABOUT_COUNTRY_NAMES_ZH: dict[str, str] = {
    "AU": "澳大利亚",
    "BR": "巴西",
    "CA": "加拿大",
    "CA-ON": "加拿大安大略省",
    "CH": "瑞士",
    "CN": "中国",
    "HK": "中国香港",
    "IS": "冰岛",
    "JP": "日本",
    "KR": "韩国",
    "NZ": "新西兰",
    "TW": "中国台湾",
    "US": "美国",
}

ABOUT_SOURCE_LABELS_ZH: dict[tuple[str, str], str] = {
    ("AU", "all"): "澳大利亚 NINDSS",
    ("BR", "sinan_datasus"): "巴西 DATASUS SINAN",
    ("CA-ON", "pho_idto_monthly"): "安大略省公共卫生局 IDTO 月度数据",
    ("CH", "foph_idd"): "瑞士 FOPH/BAG IDD",
    ("CN", "cdc_weekly"): "中国疾控中心周报",
    ("CN", "nhc"): "国家疾病预防控制局",
    ("CN", "pubmed"): "PubMed 生物医学文献库",
    ("HK", "chp_notifiable"): "中国香港 CHP 法定传染病",
    ("IS", "is_doh_annual"): "冰岛卫生署年度传染病看板",
    ("IS", "is_doh_sti"): "冰岛卫生署性病监测看板",
    ("IS", "is_doh_respiratory"): "冰岛卫生署呼吸道感染周度看板",
    ("IS", "is_doh_history"): "冰岛卫生署历史传染病登记",
    ("IS", "is_doh_legacy_icd"): "冰岛卫生署历史 ICD 临床月报",
    ("JP", "jp_weekly"): "日本 NIID/JIHS 周报",
    ("KR", "kdca_open_api"): "韩国 KDCA EID",
    ("NZ", "phf_monthly"): "新西兰 PHF Science 法定传染病",
    ("TW", "nidss_open_data"): "中国台湾 CDC NIDSS",
    ("US", "nndss_api"): "美国 CDC NNDSS",
}

ABOUT_SOURCE_DESCRIPTIONS_ZH: dict[tuple[str, str], str] = {
    ("AU", "all"): "澳大利亚国家法定传染病监测系统仪表板。",
    (
        "BR",
        "sinan_datasus",
    ): "巴西卫生部 DATASUS/SINAN 的 SUS 开放 DBC 微数据，按通报月份聚合为全国月度病例数。",
    (
        "CA-ON",
        "pho_idto_monthly",
    ): "安大略省公共卫生局 IDTO 报告中的当年月度初步病例数，与加拿大全国数据分开保留。",
    ("CH", "foph_idd"): "瑞士 FOPH/BAG IDD 法定传染病报告 API，标准化为全国病例记录。",
    ("CN", "cdc_weekly"): "中国疾控中心发布的月度法定传染病报告。",
    ("CN", "nhc"): "中国官方公共卫生公报与查询门户。",
    ("CN", "pubmed"): "作为补充上下文使用的生物医学文献发现源。",
    (
        "HK",
        "chp_notifiable",
    ): "中国香港 CHP 年度法定传染病 CSV，标准化为全国月度病例数。",
    ("IS", "is_doh_annual"): "冰岛全国分病种年度病例通报；来源可能追溯修订，数据保持年度总量口径。",
    ("IS", "is_doh_sti"): "来自实验室与登记监测的月度性病诊断事实；官方看板按季度发布。",
    ("IS", "is_doh_respiratory"): "按 ISO 周记录的呼吸道疾病诊断数；住院、样本和疫苗指标保持独立，不计入诊断数。",
    ("IS", "is_doh_history"): "1997—2021 年官方历史登记表，年度总量和分病种月度通报按原始粒度分别保留。",
    ("IS", "is_doh_legacy_icd"): "1997—2020 年 Saga 电子病历 ICD 就诊数；该临床口径与登记通报不可比，保持独立。",
    ("JP", "jp_weekly"): "日本 NIID/JIHS 的周度传染病监测数据。",
    (
        "KR",
        "kdca_open_api",
    ): "韩国 KDCA 法定传染病 OpenAPI 或门户/KOSIS 导出，按月聚合为全国通报病例数。",
    ("NZ", "phf_monthly"): "新西兰 PHF Science 法定传染病月度监测数据。",
    ("TW", "nidss_open_data"): "中国台湾月度法定传染病开放数据 CSV。",
    ("US", "nndss_api"): "美国 CDC 国家法定传染病监测系统的临时数据。",
}

CADENCE_LABELS_ZH: dict[str, str] = {
    "annual": "每年",
    "annual and monthly": "年度与月度",
    "daily": "每日",
    "monthly": "每月",
    "monthly facts · quarterly publication": "月度事实（按季度发布）",
    "quarterly": "每季度",
    "unknown": "按来源更新",
    "weekly": "每周",
    "yearly": "每年",
}

def build_country_source_info(
    country_code: str, frequency_meta: dict | None = None
) -> dict:
    """Build structured source metadata for downloads and UI badges."""
    normalized_code = (country_code or "").strip().upper()
    cfg = get_country_bootstrap_config(normalized_code)
    crawler_cfg = cfg.get("crawler_config", {})
    source_scopes = list(crawler_cfg.get("sources") or ["all"])
    fallback_cadence = (
        crawler_cfg.get("cadence")
        or (frequency_meta or {}).get("source_frequency")
        or "UNKNOWN"
    )
    sources: list[dict] = []

    for scope in source_scopes:
        details = SOURCE_DETAILS_BY_SCOPE.get((normalized_code, scope), {})
        url = details.get("url") or cfg.get("data_source_url")
        machine_url = details.get("machine_url")
        if not machine_url:
            if scope == "all" and crawler_cfg.get("dashboard_url"):
                machine_url = crawler_cfg.get("dashboard_url")
            elif scope == "jp_weekly" and crawler_cfg.get("weekly_csv_url"):
                machine_url = crawler_cfg.get("weekly_csv_url")

        source_type = details.get("type") or cfg.get("data_source_type") or "web"
        sources.append(
            {
                "scope": scope,
                "label": details.get("label")
                or scope_display_label(scope, country_code=normalized_code),
                "url": url,
                "machine_url": machine_url,
                "type": source_type,
                "cadence": details.get("cadence")
                or crawler_cfg.get("cadence")
                or fallback_cadence,
                "description": details.get("description") or cfg.get("notes"),
            }
        )

    primary = sources[0] if sources else None
    result = {
        "country_code": normalized_code,
        "primary_scope": primary.get("scope") if primary else None,
        "primary_label": primary.get("label") if primary else None,
        "primary_url": primary.get("url") if primary else None,
        "primary_type": primary.get("type") if primary else None,
        "parser_primary": cfg.get("parser_config", {}).get("primary"),
        "notes": cfg.get("notes"),
        "sources": sources,
    }
    for field in (
        "parent_country_code",
        "location_type",
        "iso_country_code",
        "iso_subdivision_code",
    ):
        if cfg.get(field):
            result[field] = cfg[field]
    return result


def normalize_cadence_label(cadence: str | None) -> str:
    """Return a stable English label for feed cadence."""
    value = (cadence or "").strip()
    if not value:
        return "Variable"
    normalized = value.lower()
    mapping = {
        "annual": "Annual",
        "daily": "Daily",
        "monthly": "Monthly",
        "quarterly": "Quarterly",
        "unknown": "Variable",
        "weekly": "Weekly",
        "yearly": "Yearly",
    }
    return mapping.get(normalized, value.replace("_", " ").title())


def normalize_cadence_label_zh(cadence: str | None) -> str:
    """Return a stable Chinese label for feed cadence."""
    value = (cadence or "").strip()
    if not value:
        return CADENCE_LABELS_ZH["unknown"]
    return CADENCE_LABELS_ZH.get(value.lower(), value.replace("_", " "))


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse date or datetime text into a UTC-aware datetime."""
    text_value = (value or "").strip()
    if not text_value:
        return None

    normalized = text_value[:-1] + "+00:00" if text_value.endswith("Z") else text_value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_snapshot_version(countries_simple: list[dict], reports: list[dict]) -> str:
    """Resolve a stable data-version timestamp from exported content."""
    candidates: list[datetime] = []

    for country in countries_simple:
        date_range = country.get("date_range") or {}
        candidate = parse_iso_timestamp(date_range.get("end"))
        if candidate:
            candidates.append(candidate)

    for report in reports:
        for field in ("period_end", "period_start"):
            candidate = parse_iso_timestamp(report.get(field))
            if candidate:
                candidates.append(candidate)

    if not candidates:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return max(candidates).replace(microsecond=0).isoformat()


def build_about_snapshot(
    countries_simple: list[dict],
    diseases: list[dict],
    reports: list[dict],
    generated_at: str,
) -> dict:
    """Build database-backed About page content for the Astro site."""
    total_cases = sum(
        int(country.get("total_cases") or 0) for country in countries_simple
    )
    total_deaths = sum(
        int(country.get("total_deaths") or 0) for country in countries_simple
    )
    coverage_starts = sorted(
        date_range["start"]
        for country in countries_simple
        if (date_range := country.get("date_range")) and date_range.get("start")
    )
    coverage_ends = sorted(
        date_range["end"]
        for country in countries_simple
        if (date_range := country.get("date_range")) and date_range.get("end")
    )
    coverage_start = coverage_starts[0] if coverage_starts else None
    coverage_end = coverage_ends[-1] if coverage_ends else None

    data_sources: list[dict] = []
    country_coverage: list[dict] = []
    cadence_keys: list[str] = []
    source_types: list[str] = []

    for country in sorted(countries_simple, key=lambda item: item.get("code") or ""):
        code = (country.get("code") or "").upper()
        country_name_en, country_name_zh = resolve_country_display_names(code, country)

        source_info = country.get("source_info") or {}
        sources = source_info.get("sources") or []
        primary_source = sources[0] if sources else {}
        primary_scope = (
            primary_source.get("scope") or source_info.get("primary_scope") or "all"
        )
        primary_cadence_raw = primary_source.get("cadence")

        for source in sources:
            scope = source.get("scope") or "all"
            cadence_raw = source.get("cadence")
            cadence_key = (cadence_raw or "unknown").strip().lower() or "unknown"
            cadence_keys.append(cadence_key)
            source_types.append((source.get("type") or "web").strip().lower() or "web")
            label_en = source.get("label") or scope
            description_en = source.get("description") or source_info.get("notes") or ""
            data_sources.append(
                {
                    "country_code": code,
                    "country_name_en": country_name_en,
                    "country_name_zh": country_name_zh,
                    "label_en": label_en,
                    "label_zh": ABOUT_SOURCE_LABELS_ZH.get((code, scope), label_en),
                    "description_en": description_en,
                    "description_zh": ABOUT_SOURCE_DESCRIPTIONS_ZH.get(
                        (code, scope), description_en
                    ),
                    "url": source.get("url"),
                    "machine_url": source.get("machine_url"),
                    "type": source.get("type") or "web",
                    "cadence_en": normalize_cadence_label(cadence_raw),
                    "cadence_zh": normalize_cadence_label_zh(cadence_raw),
                }
            )

        country_coverage.append(
            {
                "code": code,
                "name_en": country_name_en,
                "name_zh": country_name_zh,
                "disease_count": int(country.get("disease_count") or 0),
                "total_cases": int(country.get("total_cases") or 0),
                "total_deaths": int(country.get("total_deaths") or 0),
                "coverage_start": (country.get("date_range") or {}).get("start"),
                "coverage_end": (country.get("date_range") or {}).get("end"),
                "primary_source_label_en": primary_source.get("label")
                or source_info.get("primary_label"),
                "primary_source_label_zh": ABOUT_SOURCE_LABELS_ZH.get(
                    (code, primary_scope),
                    primary_source.get("label")
                    or source_info.get("primary_label")
                    or "",
                ),
                "cadence_en": normalize_cadence_label(primary_cadence_raw),
                "cadence_zh": normalize_cadence_label_zh(primary_cadence_raw),
            }
        )

    unique_cadence_keys = list(dict.fromkeys(cadence_keys))
    cadence_summary_en = (
        " / ".join(normalize_cadence_label(value) for value in unique_cadence_keys)
        if unique_cadence_keys
        else "Variable"
    )
    cadence_summary_zh = (
        " / ".join(normalize_cadence_label_zh(value) for value in unique_cadence_keys)
        if unique_cadence_keys
        else CADENCE_LABELS_ZH["unknown"]
    )
    source_type_summary = (
        " / ".join(type_name.upper() for type_name in dict.fromkeys(source_types))
        or "WEB"
    )
    country_count = len(countries_simple)
    disease_count = len(diseases)
    report_count = len(reports)
    report_entry_en = "report entry" if report_count == 1 else "report entries"

    return {
        "generated_at": generated_at,
        "summary": {
            "total_countries": country_count,
            "total_diseases": disease_count,
            "total_reports": report_count,
            "total_cases": total_cases,
            "total_deaths": total_deaths,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "source_count": len(data_sources),
            "cadence_en": cadence_summary_en,
            "cadence_zh": cadence_summary_zh,
            "source_type_summary": source_type_summary,
        },
        "metrics": [
            {
                "label_en": "Countries in database",
                "label_zh": "数据库国家数",
                "value": country_count,
                "value_type": "number",
                "note_en": "Official surveillance feeds currently exported to the public site.",
                "note_zh": "当前已导出到公开站点的官方监测国家。",
                "accent": "brand",
            },
            {
                "label_en": "Diseases tracked",
                "label_zh": "追踪疾病数",
                "value": disease_count,
                "value_type": "number",
                "note_en": "Standard diseases normalised into the build-time snapshot.",
                "note_zh": "已标准化进入构建快照的疾病目录。",
                "accent": "teal",
            },
            {
                "label_en": "Cumulative cases",
                "label_zh": "累计病例",
                "value": total_cases,
                "value_type": "number",
                "note_en": "Summed from the latest PostgreSQL-backed export.",
                "note_zh": "来自最新 PostgreSQL 导出快照的累计病例。",
                "accent": "amber",
            },
            {
                "label_en": "Latest reporting date",
                "label_zh": "最新报告日期",
                "value": coverage_end or "N/A",
                "value_type": "date",
                "note_en": "Most recent reporting date included in this site build.",
                "note_zh": "本次站点构建纳入的最新报告日期。",
                "accent": "green",
            },
        ],
        "pipeline_steps": [
            {
                "step": 1,
                "title_en": "Data Collection",
                "title_zh": "数据采集",
                "description_en": (
                    "Python collectors ingest official web, API, and BI feeds from configured "
                    "public-health sources."
                ),
                "description_zh": "Python 采集器从已配置的官方公共卫生网页、API 与 BI 数据源抓取数据。",
                "accent": "brand",
            },
            {
                "step": 2,
                "title_en": "Parsing & Normalisation",
                "title_zh": "解析与标准化",
                "description_en": (
                    "Raw source payloads are cleaned and normalised into a shared PostgreSQL schema "
                    "covering disease, country, cases, deaths, and period."
                ),
                "description_zh": "原始数据会被清洗并标准化入统一的 PostgreSQL 模型，覆盖疾病、国家、病例、死亡和时间周期。",
                "accent": "teal",
            },
            {
                "step": 3,
                "title_en": "AI Analysis",
                "title_zh": "AI 分析",
                "description_en": (
                    f"Database-backed records drive bilingual AI summaries and {report_count} "
                    f"published {report_entry_en} in the current release."
                ),
                "description_zh": f"数据库记录会驱动中英双语 AI 摘要，并生成当前版本中的 {report_count} 份报告条目。",
                "accent": "amber",
            },
            {
                "step": 4,
                "title_en": "Build-time Snapshot & Publishing",
                "title_zh": "构建时快照与发布",
                "description_en": (
                    "Before each Astro build, the site regenerates JSON snapshots from PostgreSQL so "
                    "the published pages follow the latest database state."
                ),
                "description_zh": "每次 Astro 构建前都会从 PostgreSQL 重新生成 JSON 快照，确保发布页面与数据库最新状态保持一致。",
                "accent": "purple",
            },
        ],
        "architecture": {
            "source_label_en": "Official Health Sources",
            "source_label_zh": "官方卫生数据源",
            "source_detail_en": f"{len(data_sources)} configured feeds across {country_count} countries",
            "source_detail_zh": f"{country_count} 个国家，共 {len(data_sources)} 个已配置来源",
            "scraper_label_en": "Collectors",
            "scraper_label_zh": "采集器",
            "scraper_detail_en": f"Python ingestion for {source_type_summary} source types",
            "scraper_detail_zh": f"面向 {source_type_summary} 类型来源的 Python 采集链路",
            "database_label_en": "Parser + PostgreSQL",
            "database_label_zh": "解析器 + PostgreSQL",
            "database_detail_en": f"{disease_count} diseases normalised in the database",
            "database_detail_zh": f"{disease_count} 种疾病数据已标准化入库",
            "llm_label_en": "AI Report Engine",
            "llm_label_zh": "AI 报告引擎",
            "llm_detail_en": f"{report_count} {report_entry_en} in the current public release",
            "llm_detail_zh": f"当前公开版本含 {report_count} 份报告条目",
            "website_label_en": "This Website",
            "website_label_zh": "本网站",
            "website_detail_en": "Astro build consumes regenerated JSON snapshots",
            "website_detail_zh": "Astro 构建会读取最新生成的 JSON 快照",
        },
        "features": [
            {
                "icon": "M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z",
                "title_en": "Source update schedules",
                "title_zh": "数据更新频率",
                "description_en": (
                    "Official sources publish on different schedules. The site is refreshed when "
                    "a new public data snapshot is released."
                ),
                "description_zh": "官方来源的发布频率不同；每次发布新的公开数据快照时，网站都会同步更新。",
                "accent": "brand",
            },
            {
                "icon": "M10.5 6a7.5 7.5 0 107.5 7.5h-7.5V6z M13.5 10.5H21A7.5 7.5 0 0013.5 3v7.5z",
                "title_en": "Multi-country coverage",
                "title_zh": "多国覆盖",
                "description_en": (
                    f"The current export covers {country_count} countries and {disease_count} "
                    "standardised diseases from the database."
                ),
                "description_zh": f"当前导出覆盖 {country_count} 个国家、{disease_count} 种已标准化疾病数据。",
                "accent": "teal",
            },
            {
                "icon": "M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z",
                "title_en": "Historical trend depth",
                "title_zh": "历史趋势深度",
                "description_en": (
                    f"Cross-country time series currently span {coverage_start or 'N/A'} to "
                    f"{coverage_end or 'N/A'} for comparative trend analysis."
                ),
                "description_zh": f"跨国时间序列当前覆盖 {coverage_start or 'N/A'} 至 {coverage_end or 'N/A'}，支持趋势对比分析。",
                "accent": "amber",
            },
            {
                "icon": "M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z",
                "title_en": "AI-generated reports",
                "title_zh": "AI 生成报告",
                "description_en": (
                    f"The current public release includes {report_count} AI-generated {report_entry_en} "
                    "derived from database-backed surveillance records."
                ),
                "description_zh": f"当前公开版本包含 {report_count} 份由数据库监测记录生成的 AI 报告条目。",
                "accent": "purple",
            },
            {
                "icon": "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.625c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125m16.5 5.625c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125",
                "title_en": "Open data exports",
                "title_zh": "开放数据导出",
                "description_en": "Country and disease indexes reference the same immutable gzip NDJSON fact shards generated from the latest database state.",
                "description_zh": "国家与疾病索引共同引用依据最新数据库状态生成的不可变 gzip NDJSON 事实分片。",
                "accent": "teal",
            },
            {
                "icon": "M10.5 1.5H8.25A2.25 2.25 0 006 3.75v16.5a2.25 2.25 0 002.25 2.25h7.5A2.25 2.25 0 0018 20.25V3.75a2.25 2.25 0 00-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-3 8.25h3m-3 3.75h3m-3 3.75h3",
                "title_en": "Bilingual interface",
                "title_zh": "双语界面",
                "description_en": "The public site keeps English and Chinese presentation while reading from the same generated database snapshot.",
                "description_zh": "公开站点在读取同一份数据库生成快照的同时，保持中英双语展示。",
                "accent": "brand",
            },
        ],
        "data_sources": data_sources,
        "country_coverage": country_coverage,
    }
