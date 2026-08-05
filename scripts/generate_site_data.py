#!/usr/bin/env python3
"""
Generate static JSON data files for the Astro-based report site.

Usage:
    python scripts/generate_site_data.py
    python scripts/generate_site_data.py --sharded-download-output exports/site-downloads-v2

Reads from the PostgreSQL database and writes structured JSON files that
the Astro build process consumes at build time.
"""

import argparse
import asyncio
import csv
import json
import math
import shutil
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# Make sure project root is on PYTHONPATH
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core.database import get_db, init_database  # noqa: E402
from src.core.disease_cutover import get_disease_cutover_config  # noqa: E402
from src.core.data_share import (  # noqa: E402
    derive_github_raw_base_url,
    get_data_share_repo_url,
)
from src.core.country_library import (  # noqa: E402
    get_country_bootstrap_config,
    get_country_display_name,
    get_country_profile,
    get_standard_country_codes,
)
from src.core.db_schema import (  # noqa: E402
    ensure_country_scope,
    ensure_country_scope_schema,
)
from src.core.source_scopes import scope_display_label  # noqa: E402
from src.generation.download_package_v2 import (  # noqa: E402
    build_frontend_download_manifest,
    build_globalid_canonical_download_package,
    source_reference,
)
from src.generation.github_data_snapshot import (  # noqa: E402
    DEFAULT_RETAIN_RELEASES,
    build_github_snapshot,
)
from src.knowledge.catalogue import (  # noqa: E402
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from src.knowledge.citations import normalize_knowledge_citation_group  # noqa: E402
from src.knowledge.quality import (  # noqa: E402
    KNOWLEDGE_TEXT_FIELDS,
    assess_knowledge_brief,
    strip_unavailable_knowledge_sentences,
)
from src.knowledge.profile_schema import resolve_knowledge_profile_schema  # noqa: E402
from src.ontology import DiseaseOntology, load_disease_ontology  # noqa: E402
from src.generation.sharded_data_package import (  # noqa: E402
    DEFAULT_MAX_UNCOMPRESSED_BYTES,
)
from src.services.disease_series_policy import (  # noqa: E402
    is_case_count_series,
    select_series_projection,
)

# ─────────────────────────────────────────────────────────────
# Config defaults
# ─────────────────────────────────────────────────────────────
DEFAULT_OUTPUT = ROOT / "astro-site" / "src" / "data"
DEFAULT_PUBLIC_SITE_DATA_OUTPUT = ROOT / "astro-site" / "public" / "site-data"
DEFAULT_SHARDED_DOWNLOAD_OUTPUT = ROOT / "exports" / "site-downloads-v2"
DEFAULT_GITHUB_SNAPSHOT_OUTPUT = ROOT / "exports" / "github-data-snapshot-v2"
DEFAULT_DOWNLOAD_MANIFEST = ROOT / "astro-site" / "src" / "data" / "downloads.json"
DEFAULT_DOWNLOAD_REPO_URL = get_data_share_repo_url()
DEFAULT_GITHUB_SNAPSHOT_BRANCH = "snapshot-v2"
DEFAULT_GITHUB_SNAPSHOT_URL_BASE = (
    derive_github_raw_base_url(
        DEFAULT_DOWNLOAD_REPO_URL,
        DEFAULT_GITHUB_SNAPSHOT_BRANCH,
    )
    or "/downloads-v2"
)
AUTHORITATIVE_KNOWLEDGE_SOURCE_TYPES = frozenset({"who", "who_don"})
AUTHORITATIVE_KNOWLEDGE_URL_MARKERS = ("who.int",)

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
}

SERIES_DATA_LAYER = "series_registry"
LEGACY_DATA_LAYER = "legacy_fallback"
MIXED_DATA_LAYER = "mixed"
LEGACY_GAP_FILL_DATA_LAYER = "legacy_gap_fill"

ABOUT_COUNTRY_NAMES_ZH: dict[str, str] = {
    "AU": "澳大利亚",
    "BR": "巴西",
    "CH": "瑞士",
    "CN": "中国",
    "HK": "中国香港",
    "JP": "日本",
    "KR": "韩国",
    "NZ": "新西兰",
    "TW": "中国台湾",
    "US": "美国",
}

ABOUT_SOURCE_LABELS_ZH: dict[tuple[str, str], str] = {
    ("AU", "all"): "澳大利亚 NINDSS",
    ("BR", "sinan_datasus"): "巴西 DATASUS SINAN",
    ("CH", "foph_idd"): "瑞士 FOPH/BAG IDD",
    ("CN", "cdc_weekly"): "中国疾控中心周报",
    ("CN", "nhc"): "国家疾病预防控制局",
    ("CN", "pubmed"): "PubMed 生物医学文献库",
    ("HK", "chp_notifiable"): "中国香港 CHP 法定传染病",
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
    ("CH", "foph_idd"): "瑞士 FOPH/BAG IDD 法定传染病报告 API，标准化为全国病例记录。",
    ("CN", "cdc_weekly"): "中国疾控中心发布的月度法定传染病报告。",
    ("CN", "nhc"): "中国官方公共卫生公报与查询门户。",
    ("CN", "pubmed"): "作为补充上下文使用的生物医学文献发现源。",
    (
        "HK",
        "chp_notifiable",
    ): "中国香港 CHP 年度法定传染病 CSV，标准化为全国月度病例数。",
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
    "daily": "每日",
    "monthly": "每月",
    "quarterly": "每季度",
    "unknown": "按来源更新",
    "weekly": "每周",
    "yearly": "每年",
}


def resolve_country_display_names(
    code: str, row: dict | None = None
) -> tuple[str, str]:
    """Resolve stable English and Chinese country names for public exports."""
    normalized = (code or "").strip().upper()
    row = row or {}
    name_en = (
        row.get("name_en")
        or row.get("name")
        or get_country_display_name(normalized, "en")
        or normalized
    )
    name_zh = (
        row.get("name_zh")
        or get_country_display_name(normalized, "zh")
        or row.get("name_local")
        or name_en
    )
    return name_en, name_zh


def safe_float(v) -> float | None:
    """Return float or None for non-finite values."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def iso_or_none(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def compact_report_metadata(
    metadata: dict | None, *, include_figures: bool = False
) -> dict:
    """Return site-facing report metadata without heavyweight agent internals."""
    if not isinstance(metadata, dict):
        return {}

    document = metadata.get("report_document_v4")
    if isinstance(document, dict):
        compact = {
            "report_layout": "report_v4",
            "schema_version": metadata.get("schema_version")
            or document.get("schema_version"),
            "method_version": metadata.get("method_version")
            or document.get("schema_version"),
            "default_locale": document.get("default_locale"),
            "locales": document.get("locales") or ["zh", "en"],
            "quality_gate": metadata.get("quality_gate") or {},
            "data_quality": document.get("data_quality")
            or metadata.get("data_quality")
            or {},
            "summary_metrics": {
                **(document.get("metrics") or {}),
                "death_reporting": document.get("death_reporting") or {},
            },
            "death_reporting": document.get("death_reporting") or {},
            "disease_directory": document.get("disease_directory") or [],
            "risk_ranking": document.get("risk_ranking") or [],
            "references": document.get("references") or [],
        }
        if include_figures:
            compact["figures"] = document.get("figures") or []
            compact["figure_data"] = metadata.get("figure_data") or {}
            compact["report_document_v4"] = document
        return compact

    return {}


def source_metadata_field(source: dict, key: str):
    metadata = source.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def enrich_source_attribution(
    attribution: list, sources_by_id: dict[int, dict]
) -> list[dict]:
    enriched: list[dict] = []
    metadata_keys = (
        "pmid",
        "doi",
        "first_author",
        "journal",
        "pub_date",
        "container_title",
        "publisher",
        "year",
        "provider",
        "content_kind",
    )
    direct_keys = (
        "source_name",
        "source_type",
        "title",
        "url",
        "resolved_url",
        "license",
        "fetched_at",
    )

    for item in attribution or []:
        if not isinstance(item, dict):
            continue
        source_id = safe_int(item.get("source_id") or item.get("id"))
        source = sources_by_id.get(source_id) if source_id is not None else None
        if not source:
            enriched.append(dict(item))
            continue
        source_metadata = (
            source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        )
        item_metadata = (
            item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        )
        merged = {
            **item,
            "id": item.get("id") or source.get("id"),
            "source_id": item.get("source_id") or source.get("id"),
            "metadata": {**source_metadata, **item_metadata},
        }
        for key in direct_keys:
            if not merged.get(key):
                merged[key] = source.get(key)
        for key in metadata_keys:
            if not merged.get(key):
                merged[key] = source_metadata_field(source, key)
        enriched.append(merged)
    return enriched


def avg_or_none(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def dominant_value(values: list[str | None]) -> str | None:
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    counts: dict[str, int] = defaultdict(int)
    for value in cleaned:
        counts[value] += 1
    return max(counts.items(), key=lambda item: item[1])[0]


def calculate_weekly_equivalent(dates: list[str], values: list[int]) -> list[float]:
    """Convert reported counts to 7-day equivalent counts using report interval length."""
    if not dates or not values or len(dates) != len(values):
        return []

    parsed_dates = [datetime.fromisoformat(d).date() for d in dates]
    forward_diffs = [
        (parsed_dates[i] - parsed_dates[i - 1]).days
        for i in range(1, len(parsed_dates))
        if (parsed_dates[i] - parsed_dates[i - 1]).days > 0
    ]
    # Ignore boundary/duplicate artifacts (1-2 day gaps) when inferring typical cadence.
    cadence_diffs = [d for d in forward_diffs if d >= 3]
    typical_interval = int(statistics.median(cadence_diffs)) if cadence_diffs else 7

    weekly_equiv: list[float] = []

    for i, val in enumerate(values):
        if i == 0:
            if len(parsed_dates) > 1:
                interval_days = (parsed_dates[1] - parsed_dates[0]).days
            else:
                interval_days = typical_interval
        else:
            interval_days = (parsed_dates[i] - parsed_dates[i - 1]).days

        if interval_days < 3:
            interval_days = typical_interval
        interval_days = max(1, interval_days)
        weekly_equiv.append((float(val) / interval_days) * 7.0)

    return weekly_equiv


def load_standard_diseases(csv_path: Path) -> list[dict]:
    diseases = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            diseases.append(
                {
                    "disease_id": row["disease_id"],
                    "name_en": row["standard_name_en"],
                    "name_zh": row["standard_name_zh"],
                    "category": row["category"],
                    "icd_10": row["icd_10"],
                    "icd_11": row["icd_11"],
                    "description": row.get("description", ""),
                    "slug": row["standard_name_en"]
                    .lower()
                    .replace(" ", "-")
                    .replace("/", "-"),
                }
            )
    return diseases


def enrich_diseases_with_ontology(
    diseases: list[dict], ontology: DiseaseOntology
) -> list[dict]:
    """Attach compact faceted metadata without duplicating the full registry."""

    concept_ids = set(ontology.concept_ids)
    for disease in diseases:
        disease_id = disease["disease_id"]
        if disease_id not in concept_ids:
            continue
        detail = ontology.concept_detail(disease_id)
        disease["ontology"] = {
            "status": detail["status"],
            "rollup_policy": detail["rollup_policy"],
            "facet_tags": detail.get("facet_tags", {}),
            "group_ids": detail.get("group_ids", []),
            "relations": detail.get("relations", {}),
            "source_series_count": len(detail.get("source_series", [])),
            "availability": detail.get("availability", []),
        }
    return diseases


def validate_record_catalogue_coverage(
    records: list[dict],
    catalogue_ids: set[str],
    public_ids: set[str],
) -> None:
    """Fail visibly when facts would otherwise disappear from site exports."""

    observed_ids = {
        str(record.get("disease_id") or "").strip()
        for record in records
        if str(record.get("disease_id") or "").strip()
    }
    unknown_ids = sorted(observed_ids - catalogue_ids)
    if unknown_ids:
        raise RuntimeError(
            "Disease records reference IDs missing from standard_diseases.csv: "
            + ", ".join(unknown_ids)
        )

    unsupported_non_public = sorted(observed_ids - public_ids - {"D999"})
    if unsupported_non_public:
        raise RuntimeError(
            "Disease records still reference deprecated/non-public concepts; "
            "run the disease ontology migration before export: "
            + ", ".join(unsupported_non_public)
        )


def clean_generated_dir(dir_path: Path) -> None:
    """Remove stale generated CSV/JSON files before rewriting."""
    if not dir_path.exists():
        return
    for pattern in ("*.json", "*.csv"):
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                file_path.unlink()


def reset_public_data_dir(dir_path: Path) -> None:
    """Replace generated public data files while preserving other public assets."""
    if dir_path.exists():
        shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)


def existing_site_export_has_content(output_dir: Path) -> bool:
    """Return True when the current on-disk site export already contains usable data."""
    meta_path = output_dir / "meta.json"
    if not meta_path.exists():
        return False

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if int(meta.get("total_reports") or 0) > 0:
        return True

    for country in meta.get("countries") or []:
        if int(country.get("disease_count") or 0) > 0:
            return True
        if int(country.get("total_cases") or 0) > 0:
            return True
    return False


def build_country_source_info(
    country_code: str, frequency_meta: dict | None = None
) -> dict:
    """Build structured source metadata for downloads and UI badges."""
    cfg = get_country_bootstrap_config(country_code)
    crawler_cfg = cfg.get("crawler_config", {})
    source_scopes = list(crawler_cfg.get("sources") or ["all"])
    fallback_cadence = (
        crawler_cfg.get("cadence")
        or (frequency_meta or {}).get("source_frequency")
        or "UNKNOWN"
    )
    sources: list[dict] = []

    for scope in source_scopes:
        details = SOURCE_DETAILS_BY_SCOPE.get((country_code, scope), {})
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
                or scope_display_label(scope, country_code=country_code),
                "url": url,
                "machine_url": machine_url,
                "type": source_type,
                "cadence": crawler_cfg.get("cadence") or fallback_cadence,
                "description": details.get("description") or cfg.get("notes"),
            }
        )

    primary = sources[0] if sources else None
    return {
        "country_code": country_code,
        "primary_scope": primary.get("scope") if primary else None,
        "primary_label": primary.get("label") if primary else None,
        "primary_url": primary.get("url") if primary else None,
        "primary_type": primary.get("type") if primary else None,
        "parser_primary": cfg.get("parser_config", {}).get("primary"),
        "notes": cfg.get("notes"),
        "sources": sources,
    }


def normalize_cadence_label(cadence: str | None) -> str:
    """Return a stable English label for feed cadence."""
    value = (cadence or "").strip()
    if not value:
        return "Variable"
    normalized = value.lower()
    mapping = {
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
                "title_en": "Database-backed builds",
                "title_zh": "数据库驱动构建",
                "description_en": (
                    f"Official feeds currently update on {cadence_summary_en.lower()} cadences, and "
                    "every site build regenerates the public snapshot from PostgreSQL."
                ),
                "description_zh": f"当前官方来源按{cadence_summary_zh}节奏更新，每次站点构建都会从 PostgreSQL 重建公开快照。",
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


async def ensure_standard_country_rows(session) -> None:
    """Seed canonical country rows required by the public site export."""
    for code in get_standard_country_codes():
        profile = get_country_profile(code)
        bootstrap = get_country_bootstrap_config(code)
        await session.execute(
            text("""
                INSERT INTO countries (
                    code, name, name_en, name_local, language, timezone,
                    data_source_url, data_source_type,
                    crawler_config, parser_config, disease_mapping_rules, report_config,
                    is_active, metadata, notes, created_at, updated_at
                ) VALUES (
                    :code, :name, :name_en, :name_local, :language, :timezone,
                    :data_source_url, :data_source_type,
                    CAST(:crawler_config AS json), CAST(:parser_config AS json),
                    CAST(:disease_mapping_rules AS json), CAST(:report_config AS json),
                    true, CAST(:metadata AS json), :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    name_en = EXCLUDED.name_en,
                    name_local = EXCLUDED.name_local,
                    language = COALESCE(NULLIF(countries.language, ''), EXCLUDED.language),
                    timezone = COALESCE(NULLIF(countries.timezone, ''), EXCLUDED.timezone),
                    data_source_url = COALESCE(NULLIF(countries.data_source_url, ''), EXCLUDED.data_source_url),
                    data_source_type = COALESCE(NULLIF(countries.data_source_type, ''), EXCLUDED.data_source_type),
                    crawler_config = CASE
                        WHEN countries.crawler_config IS NULL OR countries.crawler_config::text = '{}' THEN EXCLUDED.crawler_config
                        ELSE countries.crawler_config
                    END,
                    parser_config = CASE
                        WHEN countries.parser_config IS NULL OR countries.parser_config::text = '{}' THEN EXCLUDED.parser_config
                        ELSE countries.parser_config
                    END,
                    disease_mapping_rules = CASE
                        WHEN countries.disease_mapping_rules IS NULL OR countries.disease_mapping_rules::text = '{}' THEN EXCLUDED.disease_mapping_rules
                        ELSE countries.disease_mapping_rules
                    END,
                    report_config = CASE
                        WHEN countries.report_config IS NULL OR countries.report_config::text = '{}' THEN EXCLUDED.report_config
                        ELSE countries.report_config
                    END,
                    metadata = CASE
                        WHEN countries.metadata IS NULL OR countries.metadata::text = '{}' THEN EXCLUDED.metadata
                        ELSE countries.metadata
                    END,
                    notes = COALESCE(NULLIF(countries.notes, ''), EXCLUDED.notes),
                    is_active = true,
                    updated_at = CURRENT_TIMESTAMP
                """),
            {
                "code": profile.code,
                "name": profile.name,
                "name_en": profile.name_en,
                "name_local": profile.name_local,
                "language": profile.language,
                "timezone": profile.timezone,
                "data_source_url": bootstrap.get("data_source_url"),
                "data_source_type": bootstrap.get("data_source_type"),
                "crawler_config": json.dumps(bootstrap.get("crawler_config", {})),
                "parser_config": json.dumps(bootstrap.get("parser_config", {})),
                "disease_mapping_rules": json.dumps(
                    bootstrap.get("disease_mapping_rules", {})
                ),
                "report_config": json.dumps(bootstrap.get("report_config", {})),
                "metadata": json.dumps(
                    {
                        "standard_source": profile.source,
                        "iso_alpha2": profile.code,
                        "site_export_bootstrap": True,
                    }
                ),
                "notes": bootstrap.get("notes"),
            },
        )
        await ensure_country_scope(
            session,
            scope_code=profile.code,
            country_code=profile.code,
            scope_type="canonical",
            language_code=profile.language,
            display_name=profile.name,
            is_default=True,
            is_active=True,
            metadata={
                "origin": "generate_site_data",
                "source": profile.source,
            },
        )


async def ensure_site_export_database_ready() -> None:
    """Create missing tables and seed standard countries for export."""
    await init_database()
    async with get_db() as session:
        await ensure_country_scope_schema(session)
        await ensure_standard_country_rows(session)
        result = await session.execute(text("SELECT COUNT(*) FROM countries"))
        country_count = int(result.scalar() or 0)
        print(f"  ✓ database schema ready ({country_count} countries)")


# ─────────────────────────────────────────────────────────────
# Database queries
# ─────────────────────────────────────────────────────────────
async def fetch_countries(session) -> list[dict]:
    rows = await session.execute(text("""
            SELECT code, name, name_en, name_local, language, timezone
            FROM countries
            ORDER BY code
            """))
    return [dict(row._mapping) for row in rows]


async def has_population_table(session) -> bool:
    return await has_table(session, "population_records")


async def has_table(session, table_name: str) -> bool:
    row = await session.execute(
        text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = :table_name
            ) AS has_table
            """),
        {"table_name": table_name},
    )
    result = row.fetchone()
    return bool(result[0]) if result else False


async def fetch_disease_records(
    session, country_code: str, use_population_table: bool
) -> list[dict]:
    """Return a loss-aware Series-first projection for one country.

    The flat ``disease_records`` table remains a controlled fallback while the
    registry migration is incomplete.  Eligible source-series facts replace
    legacy facts only at the same national period key.  Legacy-only periods are
    retained as explicit gap fills, so a partial registry backfill cannot erase
    earlier history, reporting gaps, or a newer legacy tail.
    """

    legacy_records = await fetch_disease_records_direct(
        session, country_code, use_population_table
    )
    registry_tables_exist = await has_table(
        session, "disease_surveillance_series"
    ) and await has_table(session, "disease_series_observations")
    if not registry_tables_exist:
        return apply_disease_cutover_projection(
            legacy_records, [], country_code=country_code
        )

    series_records = await fetch_disease_series_records(
        session, country_code, use_population_table
    )
    return apply_disease_cutover_projection(
        legacy_records, series_records, country_code=country_code
    )


async def fetch_disease_records_direct(
    session, country_code: str, use_population_table: bool
) -> list[dict]:
    """
    Query disease_records joining diseases table to get the standard D-code.
    disease_records.disease_id is an integer FK to diseases.id;
    diseases.name holds the "D001" style code.
    """
    incidence_expr = "dr.incidence_rate"
    incidence_source_expr = "CASE WHEN dr.incidence_rate IS NOT NULL THEN 'original_db' ELSE 'missing_population' END"
    population_join = ""
    if use_population_table:
        incidence_expr = """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN (dr.cases::double precision / pr.population) * 100000.0
                ELSE dr.incidence_rate
            END
            """
        incidence_source_expr = """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN 'wpp_computed'
                WHEN dr.incidence_rate IS NOT NULL
                    THEN 'original_db'
                ELSE 'missing_population'
            END
            """
        population_join = (
            "LEFT JOIN population_records pr ON pr.country_id = dr.country_id "
            "AND pr.year = EXTRACT(YEAR FROM dr.time)::int"
        )

    rows = await session.execute(
        text(f"""
            SELECT
                timezone('UTC', dr.time)::date AS "date",
                to_char(timezone('UTC', dr.time), 'YYYY-MM') AS year_month,
                d.name                 AS disease_id,
                COALESCE(dr.cases, 0)::bigint AS cases,
                COALESCE(dr.deaths, 0)::bigint AS deaths,
                COALESCE(dr.recoveries, 0)::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                dr.mortality_rate AS mortality_rate,
                dr.data_quality AS data_quality
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            JOIN diseases d ON d.id = dr.disease_id
            {population_join}
            WHERE c.code = :code
            ORDER BY timezone('UTC', dr.time)::date ASC, d.name
            """),
        {"code": country_code},
    )
    result = []
    for row in rows:
        r = dict(row._mapping)
        r["date"] = r["date"].isoformat() if r["date"] else None
        r["cases"] = r["cases"] or 0
        r["deaths"] = r["deaths"] or 0
        r["incidence_rate"] = safe_float(r["incidence_rate"])
        r["incidence_rate_source"] = (
            r.get("incidence_rate_source") or "missing_population"
        )
        r["mortality_rate"] = safe_float(r["mortality_rate"])
        result.append(r)
    return result


async def fetch_disease_series_records(
    session,
    country_code: str,
    use_population_table: bool,
) -> list[dict]:
    """Read national, unstratified registry facts suitable for site export.

    Dimensioned or subnational observations are intentionally not flattened
    into national totals.  Suppressed/rejected observations are also retained
    in the registry only; treating them as zero here would be misleading.
    """

    incidence_expr = "NULL::double precision"
    incidence_source_expr = "'missing_population'"
    population_join = ""
    if use_population_table:
        incidence_expr = (
            "CASE WHEN pr.population IS NOT NULL AND pr.population > 0 "
            "THEN (dso.value::double precision / pr.population) * 100000.0 "
            "ELSE NULL END"
        )
        incidence_source_expr = (
            "CASE WHEN pr.population IS NOT NULL AND pr.population > 0 "
            "THEN 'wpp_computed' ELSE 'missing_population' END"
        )
        population_join = (
            "LEFT JOIN countries registry_country "
            "ON registry_country.code = dss.country_code "
            "LEFT JOIN population_records pr "
            "ON pr.country_id = registry_country.id "
            "AND pr.year = EXTRACT(YEAR FROM dso.time)::int"
        )

    rows = await session.execute(
        text(f"""
            SELECT
                timezone('UTC', dso.time)::date AS "date",
                to_char(timezone('UTC', dso.time), 'YYYY-MM') AS year_month,
                dss.disease_id,
                dso.value::double precision AS cases,
                0::bigint AS deaths,
                0::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                NULL::double precision AS mortality_rate,
                dso.quality_status AS data_quality,
                dso.series_code,
                dss.source_system,
                dss.source_series_code,
                dss.source_label,
                dss.definition_version,
                dss.case_definition,
                dss.case_definition_uri,
                dss.metric_type,
                dss.reporting_basis,
                dss.temporal_granularity,
                dss.unit AS series_unit,
                dso.unit AS observation_unit,
                dss.mapping_relation,
                dss.comparability,
                dss.aggregation_policy,
                dss.availability_status,
                dss.missing_value_policy,
                dss.valid_from,
                dss.valid_to,
                dso.quality_status,
                dso.geography_key,
                dso.dimension_key
            FROM disease_series_observations dso
            JOIN disease_surveillance_series dss
              ON dss.series_code = dso.series_code
            {population_join}
            WHERE dss.country_code = :code
              AND dss.disease_id IS NOT NULL
              AND dso.geography_key = :geography_key
              AND dso.dimension_key = 'all'
              AND dso.suppressed IS FALSE
              AND dso.value IS NOT NULL
              AND dso.unit = dss.unit
              AND dso.quality_status <> 'rejected'
            ORDER BY dss.disease_id, dso.series_code,
                     timezone('UTC', dso.time)::date ASC
            """),
        {
            "code": country_code,
            "geography_key": f"country:{country_code}:national",
        },
    )

    result: list[dict] = []
    for row in rows:
        record = dict(row._mapping)
        record["date"] = record["date"].isoformat() if record.get("date") else None
        record["cases"] = _normalise_count(record.get("cases"))
        record["deaths"] = record.get("deaths") or 0
        record["incidence_rate"] = safe_float(record.get("incidence_rate"))
        record["incidence_rate_source"] = (
            record.get("incidence_rate_source") or "missing_population"
        )
        record["mortality_rate"] = safe_float(record.get("mortality_rate"))
        for field in ("valid_from", "valid_to"):
            value = record.get(field)
            record[field] = value.isoformat() if value else None
        result.append(record)
    return result


def _normalise_count(value) -> int | float:
    """Keep integer counts compact without discarding valid fractional values."""

    numeric = safe_float(value)
    if numeric is None:
        return 0
    return int(numeric) if numeric.is_integer() else numeric


def _series_is_case_count(record: dict) -> bool:
    return is_case_count_series(record)


def _source_series_details(records: list[dict]) -> list[dict]:
    """Build lossless, JSON-ready source-series payloads for one concept."""

    by_series: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        series_code = str(record.get("series_code") or "").strip()
        if series_code:
            by_series[series_code].append(record)

    details: list[dict] = []
    metadata_fields = (
        "source_system",
        "source_series_code",
        "source_label",
        "definition_version",
        "case_definition",
        "case_definition_uri",
        "metric_type",
        "reporting_basis",
        "temporal_granularity",
        "mapping_relation",
        "comparability",
        "aggregation_policy",
        "availability_status",
        "missing_value_policy",
        "valid_from",
        "valid_to",
    )
    for series_code in sorted(by_series):
        series_records = sorted(
            by_series[series_code], key=lambda item: str(item.get("date") or "")
        )
        first = series_records[0]
        dates = [item.get("date") for item in series_records if item.get("date")]
        values = [item.get("cases") or 0 for item in series_records if item.get("date")]
        quality_statuses = sorted(
            {
                str(item.get("quality_status") or item.get("data_quality") or "")
                for item in series_records
                if item.get("quality_status") or item.get("data_quality")
            }
        )
        detail = {
            "series_code": series_code,
            **{field: first.get(field) for field in metadata_fields},
            "unit": first.get("series_unit") or first.get("unit"),
            "geography_key": first.get("geography_key"),
            "dimension_key": first.get("dimension_key"),
            "dates": dates,
            "values": values,
            "total_value": sum(values),
            "observation_count": len(values),
            "quality_statuses": quality_statuses,
        }
        details.append(detail)
    return details


def _representative_series_code(source_series: list[dict]) -> str:
    """Choose one deterministic conservative view when rollup is unsafe.

    An explicit reported aggregate is preferred.  Otherwise the series with
    the broadest fact coverage is selected; ties prefer the most recent fact
    and finally the stable series code.  The unselected series remain present
    in ``source_series`` and the projection is flagged as lossy.
    """

    selection = select_series_projection(source_series)
    if len(selection.selected_codes) != 1:
        raise ValueError("representative series selection must choose one series")
    return next(iter(selection.selected_codes))


def _projection_context(series_records: list[dict]) -> tuple[set[str], dict]:
    source_series = _source_series_details(series_records)
    selection = select_series_projection(source_series)
    selected_codes = set(selection.selected_codes)
    projection_policy = selection.projection_policy
    loss_risk = selection.loss_risk

    if projection_policy == "single_series":
        note_en = "The public curve is read directly from one registered source series."
        note_zh = "公开曲线直接读取自一个已注册的来源序列。"
    elif projection_policy == "sum_disjoint":
        note_en = "Registered source series are summed under an explicit disjoint-series policy."
        note_zh = "多个来源序列依据明确的互斥可加策略进行汇总。"
    else:
        note_en = (
            "Multiple registered series are not declared safely additive. "
            "The compatibility curve uses one representative series; inspect "
            "source_series for every retained definition."
        )
        note_zh = (
            "多个已注册序列未声明为可安全相加。兼容曲线仅采用一个代表序列；"
            "所有独立口径均保留在 source_series 中。"
        )

    context = {
        "data_layer": SERIES_DATA_LAYER,
        "projection_policy": projection_policy,
        "loss_risk": loss_risk,
        "selected_series_codes": sorted(selected_codes),
        "available_series_count": len(source_series),
        "source_series": source_series,
        "note_en": note_en,
        "note_zh": note_zh,
    }
    return selected_codes, context


def _collapse_selected_series_records(
    records: list[dict],
    context: dict,
) -> list[dict]:
    """Collapse only the explicitly selected series to the legacy chart grain."""

    selected_codes = set(context.get("selected_series_codes") or [])
    selected = [
        record for record in records if record.get("series_code") in selected_codes
    ]
    seen_source_identities: set[tuple[str, str]] = set()
    for record in selected:
        source_identity = (
            str(record.get("series_code") or ""),
            str(record.get("date") or ""),
        )
        if source_identity in seen_source_identities:
            raise RuntimeError(
                "Source series has multiple observations at the public date grain: "
                f"{source_identity[0]} {source_identity[1]}"
            )
        seen_source_identities.add(source_identity)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for record in selected:
        if record.get("date"):
            by_date[str(record["date"])].append(record)

    projected: list[dict] = []
    for report_date in sorted(by_date):
        date_records = by_date[report_date]
        # ``sum_disjoint`` describes how complete sibling series may be rolled
        # up; it does not make an absent sibling equivalent to zero.  Project a
        # multi-series period only when every selected series is present.
        if (
            len(selected_codes) > 1
            and {str(record.get("series_code") or "") for record in date_records}
            != selected_codes
        ):
            continue
        first = date_records[0]
        incidence_values = [
            safe_float(record.get("incidence_rate")) for record in date_records
        ]
        incidence_values = [value for value in incidence_values if value is not None]
        projected.append(
            {
                "date": report_date,
                "year_month": first.get("year_month") or report_date[:7],
                "disease_id": first.get("disease_id"),
                "cases": _normalise_count(
                    sum(safe_float(record.get("cases")) or 0 for record in date_records)
                ),
                "deaths": 0,
                "recoveries": 0,
                # Summing disjoint counts over the same population also sums
                # their per-capita rates.  Single/representative projections
                # contain only one value at each date.
                "incidence_rate": (sum(incidence_values) if incidence_values else None),
                "incidence_rate_source": dominant_value(
                    [record.get("incidence_rate_source") for record in date_records]
                )
                or "missing_population",
                "mortality_rate": None,
                "data_quality": dominant_value(
                    [
                        record.get("quality_status") or record.get("data_quality")
                        for record in date_records
                    ]
                ),
                "data_layer": SERIES_DATA_LAYER,
                "series_code": (
                    first.get("series_code") if len(selected_codes) == 1 else None
                ),
                "_series_context": context,
            }
        )
    return projected


def _attach_legacy_supplemental_metrics(
    projected: list[dict],
    context: dict,
    legacy_records: list[dict],
) -> dict:
    """Retain non-case legacy metrics without reintroducing case duplication."""

    legacy_by_date: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_date[str(record["date"])].append(record)

    supplemental = {
        "dates": sorted(legacy_by_date),
        "deaths": [
            sum(item.get("deaths") or 0 for item in legacy_by_date[date])
            for date in sorted(legacy_by_date)
        ],
        "recoveries": [
            sum(item.get("recoveries") or 0 for item in legacy_by_date[date])
            for date in sorted(legacy_by_date)
        ],
        "mortality_rates": [
            avg_or_none([item.get("mortality_rate") for item in legacy_by_date[date]])
            for date in sorted(legacy_by_date)
        ],
    }
    context["supplemental_legacy_metrics"] = supplemental
    safe_metric_alignment = context.get("projection_policy") in {
        "single_series",
        "sum_disjoint",
    }
    context["metric_layers"] = {
        "cases": SERIES_DATA_LAYER,
        "deaths": (
            LEGACY_DATA_LAYER
            if safe_metric_alignment and legacy_records
            else "supplemental_legacy_only" if legacy_records else "not_available"
        ),
        "recoveries": (
            LEGACY_DATA_LAYER
            if safe_metric_alignment and legacy_records
            else "supplemental_legacy_only" if legacy_records else "not_available"
        ),
    }

    if safe_metric_alignment:
        for projected_record in projected:
            legacy_at_date = legacy_by_date.get(str(projected_record.get("date"))) or []
            projected_record["deaths"] = sum(
                item.get("deaths") or 0 for item in legacy_at_date
            )
            projected_record["recoveries"] = sum(
                item.get("recoveries") or 0 for item in legacy_at_date
            )
            projected_record["mortality_rate"] = avg_or_none(
                [item.get("mortality_rate") for item in legacy_at_date]
            )
    return context


def _overlay_legacy_coverage_gaps(
    projected: list[dict],
    context: dict,
    legacy_records: list[dict],
) -> list[dict]:
    """Overlay registry facts by period without sacrificing legacy coverage.

    The country export has already constrained both layers to the same national
    geography and unstratified dimension.  Therefore ``date`` is the remaining
    public identity key.  Registry wins at an identical date; a legacy-only
    date is retained and marked ``legacy_gap_fill``.  This is deliberately a
    parity gate rather than a wholesale disease-level switch.
    """

    registry_dates = {
        str(record.get("date") or "") for record in projected if record.get("date")
    }
    legacy_by_date: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        if record.get("date"):
            legacy_by_date[str(record["date"])].append(record)

    legacy_dates = set(legacy_by_date)
    gap_dates = sorted(legacy_dates - registry_dates)
    overlap_dates = legacy_dates & registry_dates
    coverage_ratio = len(overlap_dates) / len(legacy_dates) if legacy_dates else 1.0

    context.update(
        {
            "coverage_policy": "period_key_overlay",
            "coverage_status": "legacy_gap_fill" if gap_dates else "parity",
            "legacy_period_count": len(legacy_dates),
            "registry_period_count": len(registry_dates),
            "overlap_period_count": len(overlap_dates),
            "legacy_gap_fill_count": len(gap_dates),
            "registry_only_period_count": len(registry_dates - legacy_dates),
            "coverage_ratio_against_legacy": round(coverage_ratio, 6),
        }
    )
    if not gap_dates:
        return projected

    if not registry_dates:
        # No date has a complete safe registry projection (for example, every
        # ``sum_disjoint`` period is missing a sibling).  Keep the public curve
        # wholly legacy and expose why the candidate registry facts were gated.
        context["registry_projection_policy"] = context.get("projection_policy")
        context["data_layer"] = LEGACY_DATA_LAYER
        context["projection_policy"] = "legacy_fallback"
        context["coverage_status"] = "registry_no_complete_periods"
        context["fallback_reason"] = "incomplete_registered_rollup_periods"
        context["coverage_risk"] = "registry_history_incomplete"
        context["metric_layers"]["cases"] = LEGACY_DATA_LAYER
        result: list[dict] = []
        for report_date in gap_dates:
            date_records = legacy_by_date[report_date]
            if len(date_records) != 1:
                raise RuntimeError(
                    "Legacy layer has multiple observations at the public date grain: "
                    f"{date_records[0].get('disease_id')} {report_date}"
                )
            legacy_record = dict(date_records[0])
            legacy_record["data_layer"] = LEGACY_DATA_LAYER
            legacy_record["_series_context"] = context
            result.append(legacy_record)
        return result

    context["data_layer"] = MIXED_DATA_LAYER
    context["coverage_risk"] = "registry_history_incomplete"
    context["metric_layers"]["cases"] = MIXED_DATA_LAYER
    context["note_en"] = (
        f"{context.get('note_en') or ''} Registry facts replace matching periods; "
        "legacy-only periods are retained as explicit coverage gap fills."
    ).strip()
    context["note_zh"] = (
        f"{context.get('note_zh') or ''} 注册序列替换同一期旧事实；仅旧表存在的期间"
        "作为明确的覆盖缺口补全予以保留。"
    ).strip()

    result = list(projected)
    for report_date in gap_dates:
        date_records = legacy_by_date[report_date]
        if len(date_records) != 1:
            raise RuntimeError(
                "Legacy layer has multiple observations at the public date grain: "
                f"{date_records[0].get('disease_id')} {report_date}"
            )
        gap_record = dict(date_records[0])
        gap_record["data_layer"] = LEGACY_GAP_FILL_DATA_LAYER
        gap_record["_series_context"] = context
        gap_record["gap_fill_reason"] = "registry_period_missing"
        result.append(gap_record)
    return result


def _legacy_projection_context(
    *,
    registry_series_fact_count: int = 0,
    reason: str = "no_eligible_registered_series_facts",
) -> dict:
    return {
        "data_layer": LEGACY_DATA_LAYER,
        "projection_policy": "legacy_fallback",
        "loss_risk": "legacy_identity_may_be_lossy",
        "selected_series_codes": [],
        "available_series_count": registry_series_fact_count,
        "source_series": [],
        "fallback_reason": reason,
        "metric_layers": {
            "cases": LEGACY_DATA_LAYER,
            "deaths": LEGACY_DATA_LAYER,
            "recoveries": LEGACY_DATA_LAYER,
        },
        "note_en": (
            "No eligible registered case-count series facts are available for "
            "this disease and country; the public curve uses the legacy flat table."
        ),
        "note_zh": (
            "该疾病与国家尚无可用的注册病例计数序列事实；公开曲线受控回退到旧扁平事实表。"
        ),
    }


def apply_series_first_projection(
    legacy_records: list[dict],
    series_records: list[dict],
) -> list[dict]:
    """Overlay eligible registry facts without mixing or double counting layers."""

    legacy_by_disease: dict[str, list[dict]] = defaultdict(list)
    for record in legacy_records:
        disease_id = str(record.get("disease_id") or "").strip()
        if disease_id:
            legacy_by_disease[disease_id].append(record)

    all_series_by_disease: dict[str, list[dict]] = defaultdict(list)
    eligible_series_by_disease: dict[str, list[dict]] = defaultdict(list)
    for record in series_records:
        disease_id = str(record.get("disease_id") or "").strip()
        if not disease_id:
            continue
        all_series_by_disease[disease_id].append(record)
        if _series_is_case_count(record):
            eligible_series_by_disease[disease_id].append(record)

    result: list[dict] = []
    all_disease_ids = sorted(set(legacy_by_disease) | set(eligible_series_by_disease))
    for disease_id in all_disease_ids:
        eligible = eligible_series_by_disease.get(disease_id) or []
        if eligible:
            _selected_codes, context = _projection_context(eligible)
            projected = _collapse_selected_series_records(eligible, context)
            _attach_legacy_supplemental_metrics(
                projected,
                context,
                legacy_by_disease.get(disease_id) or [],
            )
            projected = _overlay_legacy_coverage_gaps(
                projected,
                context,
                legacy_by_disease.get(disease_id) or [],
            )
            result.extend(projected)
            continue

        raw_registry_count = len(all_series_by_disease.get(disease_id) or [])
        reason = (
            "registered_facts_not_case_count_compatible"
            if raw_registry_count
            else "no_eligible_registered_series_facts"
        )
        context = _legacy_projection_context(
            registry_series_fact_count=raw_registry_count,
            reason=reason,
        )
        for legacy_record in legacy_by_disease.get(disease_id) or []:
            projected = dict(legacy_record)
            projected["data_layer"] = LEGACY_DATA_LAYER
            projected["_series_context"] = context
            result.append(projected)

    projected_records = sorted(
        result,
        key=lambda item: (
            str(item.get("date") or ""),
            str(item.get("disease_id") or ""),
        ),
    )
    validate_series_first_projection(projected_records)
    return projected_records


def apply_disease_cutover_projection(
    legacy_records: list[dict],
    series_records: list[dict],
    *,
    country_code: str,
) -> list[dict]:
    """Apply the versioned per-concept cutover policy to a country export.

    This adapter keeps the existing static payload intact while enforcing the
    same strict rule as the API: a ``series_only`` target receives no legacy
    rows, so missing Registry facts stay missing instead of becoming a silent
    compatibility fallback.
    """

    config = get_disease_cutover_config()
    normalized_country = str(country_code or "").strip().upper()
    disease_ids = {
        str(record.get("disease_id") or "").strip().upper()
        for record in [*legacy_records, *series_records]
        if record.get("disease_id")
    }
    legacy_modes = {
        disease_id: config.resolve_read_policy(normalized_country, disease_id)
        for disease_id in disease_ids
    }

    filtered_legacy = [
        record
        for record in legacy_records
        if legacy_modes[str(record.get("disease_id") or "").strip().upper()].read_mode
        != "series_only"
    ]
    filtered_series = [
        record
        for record in series_records
        if legacy_modes[str(record.get("disease_id") or "").strip().upper()].read_mode
        != "legacy"
    ]
    projected = apply_series_first_projection(filtered_legacy, filtered_series)

    accepted: list[dict] = []
    for record in projected:
        disease_id = str(record.get("disease_id") or "").strip().upper()
        policy = legacy_modes[disease_id]
        context = record.get("_series_context") or {}
        selected_codes = set(context.get("selected_series_codes") or [])
        blocked_reasons: list[str] = []
        missing_required = sorted(set(policy.required_series) - selected_codes)
        if missing_required:
            blocked_reasons.append(
                "missing_required_series:" + ",".join(missing_required)
            )
        if (
            policy.allowed_projection_policy
            and context.get("projection_policy") != policy.allowed_projection_policy
        ):
            blocked_reasons.append(
                "projection_policy_mismatch:"
                f"expected={policy.allowed_projection_policy},"
                f"actual={context.get('projection_policy')}"
            )
        context["cutover"] = {
            "release_version": config.release_version,
            "read_mode": policy.read_mode,
            "shadow_compare": policy.shadow_compare,
            "target_override": policy.target_override,
            "required_series": list(policy.required_series),
            "allowed_projection_policy": policy.allowed_projection_policy,
            "blocked_reasons": blocked_reasons,
        }
        if policy.read_mode == "series_only" and blocked_reasons:
            continue
        accepted.append(record)

    validate_series_first_projection(accepted)
    return accepted


def validate_series_first_projection(records: list[dict]) -> None:
    """Enforce public-export invariants before any chart can silently sum rows."""

    seen_identities: set[tuple[str, str]] = set()
    layers_by_disease: dict[str, set[str]] = defaultdict(set)
    record_layers_by_disease: dict[str, set[str]] = defaultdict(set)
    for record in records:
        disease_id = str(record.get("disease_id") or "").strip()
        report_date = str(record.get("date") or "").strip()
        if not disease_id or not report_date:
            continue
        identity = (disease_id, report_date)
        if identity in seen_identities:
            raise RuntimeError(
                "Series-first site projection produced duplicate disease/date "
                f"identity: {disease_id} {report_date}"
            )
        seen_identities.add(identity)

        context = record.get("_series_context")
        if not isinstance(context, dict):
            raise RuntimeError(
                f"Site projection lacks data-layer provenance for {disease_id}"
            )
        layer = str(context.get("data_layer") or "").strip()
        layers_by_disease[disease_id].add(layer)
        record_layers_by_disease[disease_id].add(
            str(record.get("data_layer") or layer).strip()
        )
        policy = str(context.get("projection_policy") or "")
        selected_codes = context.get("selected_series_codes") or []
        if layer in {SERIES_DATA_LAYER, MIXED_DATA_LAYER}:
            if not selected_codes:
                raise RuntimeError(
                    f"Registry projection has no selected series for {disease_id}"
                )
            if len(selected_codes) > 1 and policy != "sum_disjoint":
                raise RuntimeError(
                    "Multiple source series reached the flat public curve without "
                    f"an explicit sum_disjoint policy for {disease_id}"
                )
            if policy in {
                "representative_series",
                "reported_aggregate_preferred",
            } and not context.get("loss_risk"):
                raise RuntimeError(
                    f"Conservative series selection is not risk-labelled for {disease_id}"
                )
        if layer == MIXED_DATA_LAYER:
            if context.get("coverage_status") != "legacy_gap_fill":
                raise RuntimeError(
                    f"Mixed projection lacks a gap-fill coverage status for {disease_id}"
                )
            if int(context.get("legacy_gap_fill_count") or 0) < 1:
                raise RuntimeError(
                    f"Mixed projection lacks an explicit legacy gap count for {disease_id}"
                )

    # Mixed coverage is valid only when declared by the shared disease-level
    # context.  A hidden mixture of independent contexts remains an error.
    invalid_mixed = sorted(
        disease_id
        for disease_id, layers in layers_by_disease.items()
        if len(layers) > 1 and MIXED_DATA_LAYER not in layers
    )
    if invalid_mixed:
        raise RuntimeError(
            "Site projection mixed independent provenance contexts: "
            + ", ".join(invalid_mixed)
        )
    malformed_mixed = sorted(
        disease_id
        for disease_id, layers in layers_by_disease.items()
        if MIXED_DATA_LAYER in layers
        and record_layers_by_disease[disease_id]
        != {SERIES_DATA_LAYER, LEGACY_GAP_FILL_DATA_LAYER}
    )
    if malformed_mixed:
        raise RuntimeError(
            "Mixed projection does not contain both registry and explicit gap-fill rows: "
            + ", ".join(malformed_mixed)
        )


async def fetch_country_frequency_meta(session, country_code: str) -> dict:
    """Infer source reporting frequency from raw (non-truncated) timestamps."""
    rows = await session.execute(
        text("""
            SELECT DISTINCT timezone('UTC', dr.time)::date AS report_date
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            WHERE c.code = :code
            ORDER BY report_date ASC
            """),
        {"code": country_code},
    )
    report_dates = [
        dict(row._mapping)["report_date"]
        for row in rows
        if dict(row._mapping).get("report_date")
    ]
    if len(report_dates) < 2:
        return {
            "source_frequency": "UNKNOWN",
            "canonical_frequency": "WEEKLY_EQUIVALENT_7D",
            "aggregation_rule": "normalize_counts_to_7_day_equivalent",
        }

    diffs = []
    for i in range(1, len(report_dates)):
        delta_days = (report_dates[i] - report_dates[i - 1]).days
        if delta_days > 0:
            diffs.append(delta_days)

    if not diffs:
        source_frequency = "UNKNOWN"
    else:
        median_days = statistics.median(diffs)
        pct_month_start = sum(1 for d in report_dates if d.day == 1) / len(report_dates)
        if median_days >= 25 and pct_month_start >= 0.5:
            source_frequency = "MONTHLY"
        elif 5 <= median_days <= 10:
            source_frequency = "WEEKLY"
        else:
            source_frequency = "DAILY"

    return {
        "source_frequency": source_frequency,
        "canonical_frequency": "WEEKLY_EQUIVALENT_7D",
        "aggregation_rule": "normalize_counts_to_7_day_equivalent",
    }


async def fetch_reports(session) -> list[dict]:
    rows = await session.execute(text("""
            SELECT
                r.id, r.title, r.report_type, r.status,
                r.period_start::date  AS period_start,
                r.period_end::date    AS period_end,
                r.created_at,
                r.summary,
                r.quality_score,
                r.metadata,
                r.generation_config,
                c.code                AS country_code,
                c.name                AS country_name,
                c.name_en             AS country_name_en,
                c.name_local          AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.status IN ('COMPLETED', 'APPROVED', 'PUBLISHED')
            ORDER BY r.created_at DESC
            """))
    result = []
    for row in rows:
        r = dict(row._mapping)
        metadata = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        document = metadata.get("report_document_v4")
        if not isinstance(document, dict):
            continue
        r["period_start"] = r["period_start"].isoformat() if r["period_start"] else None
        r["period_end"] = r["period_end"].isoformat() if r["period_end"] else None
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["quality_score"] = safe_float(r["quality_score"])
        report_language = "zh"
        r["title"] = (document.get("title") or {}).get("zh") or r.get("title")
        r["summary"] = (document.get("summary") or {}).get("zh") or r.get("summary")
        r["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
        r["metadata"] = compact_report_metadata(metadata, include_figures=False)
        r["metadata"]["language"] = report_language
        r["language"] = report_language
        r.pop("generation_config", None)
        r["analysis_summary"] = None
        r["quality_gate"] = r["metadata"].get("quality_gate")
        r["data_quality"] = r["metadata"].get("data_quality")
        r["method_version"] = r["metadata"].get("method_version")
        result.append(r)
    return result


async def fetch_report_detail(session, report_id: int) -> dict | None:
    row = await session.execute(
        text("""
            SELECT
                r.id, r.title, r.report_type,
                r.period_start::date AS period_start,
                r.period_end::date   AS period_end,
                r.created_at, r.ai_model_used, r.quality_score,
                r.summary, r.key_findings,
                r.metadata,
                r.generation_config,
                c.code               AS country_code,
                c.name               AS country_name,
                c.name_en            AS country_name_en,
                c.name_local         AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.id = :id
            """),
        {"id": report_id},
    )
    rrow = row.fetchone()
    if not rrow:
        return None
    report = dict(rrow._mapping)
    report["period_start"] = (
        report["period_start"].isoformat() if report["period_start"] else None
    )
    report["period_end"] = (
        report["period_end"].isoformat() if report["period_end"] else None
    )
    report["created_at"] = (
        report["created_at"].isoformat() if report["created_at"] else None
    )
    report["quality_score"] = safe_float(report["quality_score"])
    metadata = (
        report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    )
    document = metadata.get("report_document_v4")
    if not isinstance(document, dict):
        return None
    report_language = "zh"
    report["title"] = (document.get("title") or {}).get("zh") or report.get("title")
    report["summary"] = (document.get("summary") or {}).get("zh") or report.get(
        "summary"
    )
    report["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
    report["report_document_v4"] = document
    report["metadata"] = compact_report_metadata(metadata, include_figures=True)
    report["metadata"]["language"] = report_language
    report["language"] = report_language
    report.pop("generation_config", None)
    report["analysis_summary"] = None
    report["quality_gate"] = report["metadata"].get("quality_gate")
    report["data_quality"] = report["metadata"].get("data_quality")
    report["method_version"] = report["metadata"].get("method_version")
    report["sections"] = [
        {
            "section_type": section.get("type"),
            "section_order": section.get("order"),
            "title": (section.get("title") or {}).get("zh"),
            "content": (section.get("body") or {}).get("zh"),
            "charts": section.get("figures") or [],
            "metadata": {
                "locales": {
                    "zh": {
                        "title": (section.get("title") or {}).get("zh"),
                        "content": (section.get("body") or {}).get("zh"),
                    },
                    "en": {
                        "title": (section.get("title") or {}).get("en"),
                        "content": (section.get("body") or {}).get("en"),
                    },
                },
                "evidence_refs": section.get("evidence_refs") or [],
                "quality_flags": section.get("quality_flags") or [],
            },
        }
        for section in document.get("sections") or []
    ]
    return report


async def fetch_disease_knowledge_briefs(session) -> dict[str, dict[str, dict]]:
    """Load reviewed or published knowledge briefs keyed by disease_id and language."""
    if not await has_table(session, "disease_knowledge_briefs"):
        return {}
    sources_by_disease_id: dict[str, dict[int, dict]] = defaultdict(dict)
    if await has_table(session, "disease_knowledge_sources"):
        source_rows = await session.execute(text("""
                SELECT id, disease_id, source_type, source_name, url, resolved_url,
                       title, license, status, review_status, fetched_at, metadata
                FROM disease_knowledge_sources
                """))
        for row in source_rows:
            source = dict(row._mapping)
            source_id = safe_int(source.get("id"))
            disease_id = source.get("disease_id")
            if source_id is None or not disease_id:
                continue
            source["fetched_at"] = iso_or_none(source.get("fetched_at"))
            if not source.get("resolved_url"):
                metadata = (
                    source.get("metadata")
                    if isinstance(source.get("metadata"), dict)
                    else {}
                )
                source["resolved_url"] = metadata.get("resolved_url") or source.get(
                    "url"
                )
            sources_by_disease_id[str(disease_id)][source_id] = source

    rows = await session.execute(text("""
            SELECT disease_id, language, brief, definition, clinical_features, epidemiology,
                   clinical_summary, transmission, prevention, surveillance_note, risk_groups,
                   source_attribution, disclaimer, status, source_confidence, updated_at, metadata
            FROM disease_knowledge_briefs
            WHERE status IN ('published', 'requires_review')
            """))
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        item = dict(row._mapping)
        if item.get("updated_at"):
            item["updated_at"] = item["updated_at"].isoformat()
        item["source_attribution"] = enrich_source_attribution(
            item.get("source_attribution") or [],
            sources_by_disease_id.get(str(item.get("disease_id")), {}),
        )
        result[item["disease_id"]][item["language"]] = item
    return result


async def fetch_country_briefs(session) -> dict[str, dict[str, dict]]:
    """Load published country briefs keyed by country code and language."""
    if not await has_table(session, "country_briefs"):
        return {}
    rows = await session.execute(text("""
            SELECT country_code, language, brief, surveillance_system,
                   coverage_interpretation, reporting_cadence, data_limitations,
                   source_summary, status, updated_at
            FROM country_briefs
            WHERE status = 'published'
            """))
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        item = dict(row._mapping)
        if item.get("updated_at"):
            item["updated_at"] = item["updated_at"].isoformat()
        result[(item["country_code"] or "").upper()][item["language"]] = item
    return result


def build_disease_knowledge_fields(
    disease: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Build a field-aware, gracefully degradable public knowledge payload."""
    brief_by_language = brief_by_language or {}
    profile_schema = resolve_knowledge_profile_schema(disease)

    def localized_brief(language: str) -> dict:
        raw = {**(brief_by_language.get(language) or {}), "language": language}
        raw["metadata"] = {
            **(raw.get("metadata") or {}),
            "profile_schema": profile_schema.to_dict(),
        }
        return raw

    raw_en = localized_brief("en")
    raw_zh = localized_brief("zh")

    # A refresh can produce a different source set for each language. Preserve
    # the union and normalize both briefs against one stable citation order.
    merged_sources: list[dict] = []
    seen_source_keys: set[str] = set()
    for brief in (raw_en, raw_zh):
        for source in brief.get("source_attribution") or []:
            if not isinstance(source, dict):
                continue
            key = str(
                source.get("source_id")
                or source.get("id")
                or source.get("resolved_url")
                or source.get("url")
                or source.get("title")
                or ""
            ).strip()
            if not key or key in seen_source_keys:
                continue
            seen_source_keys.add(key)
            merged_sources.append(source)
    if merged_sources:
        raw_en["source_attribution"] = merged_sources
        raw_zh["source_attribution"] = merged_sources

    en, zh = normalize_knowledge_citation_group([raw_en, raw_zh])
    localized_briefs = {"en": en, "zh": zh}
    assessments = {
        language: assess_knowledge_brief(brief, language)
        for language, brief in localized_briefs.items()
    }
    language_tiers = {
        language: knowledge_brief_publication_tier(
            brief_by_language.get(language) or {}
        )
        for language in ("en", "zh")
    }
    language_is_public = {
        language: language_tiers[language] == "published" for language in ("en", "zh")
    }

    raw_knowledge_sources = (
        en.get("source_attribution") or zh.get("source_attribution") or []
    )
    original_status = resolve_disease_knowledge_status(brief_by_language.values())
    profile_languages = [
        language
        for language in ("en", "zh")
        if language_is_public[language] and assessments[language].profile_available
    ]
    available_languages = [
        language
        for language in ("en", "zh")
        if language_is_public[language] and assessments[language].available_fields
    ]
    knowledge_profile_available = bool(profile_languages)
    knowledge_status = "published" if knowledge_profile_available else "blocked"
    knowledge_tier = "published" if knowledge_profile_available else "blocked"
    block_reason = next(
        (
            knowledge_brief_block_reason(brief)
            for brief in brief_by_language.values()
            if knowledge_brief_block_reason(brief)
        ),
        None,
    )
    updated_values = [
        str(brief.get("updated_at"))
        for brief in localized_briefs.values()
        if brief.get("updated_at")
    ]
    knowledge_updated_at = max(updated_values) if updated_values else None
    knowledge_sources = raw_knowledge_sources if knowledge_profile_available else []
    has_authoritative_sources = knowledge_profile_available and (
        any(_is_authoritative_knowledge_source(source) for source in knowledge_sources)
        or any(
            str(brief.get("source_confidence") or "") == "high"
            for brief in brief_by_language.values()
            if isinstance(brief, dict)
        )
    )
    if knowledge_profile_available:
        profile_reason = (
            "partial_profile"
            if any(
                assessments[language].display_mode == "partial"
                for language in profile_languages
            )
            or len(profile_languages) < 2
            else None
        )
    elif not brief_by_language:
        profile_reason = "no_published_brief"
    elif original_status == "published":
        profile_reason = "insufficient_evidence"
    elif block_reason:
        profile_reason = block_reason
    else:
        profile_reason = "requires_review"

    def public_text(language: str, field: str, *aliases: str) -> str | None:
        if not language_is_public[language]:
            return None
        brief = localized_briefs[language]
        for candidate in (field, *aliases):
            result = assessments[language].fields.get(candidate)
            value = strip_unavailable_knowledge_sentences(
                brief.get(candidate), language
            )
            if result and result.available and value:
                return value
        return None

    completeness_values = [
        assessments[language].completeness for language in profile_languages
    ]
    knowledge_completeness = (
        round(sum(completeness_values) / len(completeness_values), 3)
        if completeness_values
        else 0.0
    )
    if not knowledge_profile_available:
        display_mode = "blocked"
    elif len(profile_languages) == 2 and all(
        assessments[language].display_mode == "full" for language in profile_languages
    ):
        display_mode = "full"
    else:
        display_mode = "partial"

    field_status = {
        field: {
            language: assessments[language].fields[field].status
            for language in ("en", "zh")
        }
        for field in KNOWLEDGE_TEXT_FIELDS
    }
    repair_sections = [
        field
        for field in ("brief", *profile_schema.required_fields)
        if any(
            not assessments[language].fields[field].available
            for language in ("en", "zh")
        )
    ]

    payload = {
        "disease_id": disease["disease_id"],
        "name_en": disease.get("name_en"),
        "name_zh": disease.get("name_zh"),
        "category": disease.get("category"),
        "description": disease.get("description"),
        "official_intro_en": public_text("en", "brief", "definition"),
        "official_intro_zh": public_text("zh", "brief", "definition"),
        "official_summary_en": public_text("en", "brief", "definition"),
        "official_summary_zh": public_text("zh", "brief", "definition"),
        "official_definition_en": public_text("en", "definition"),
        "official_definition_zh": public_text("zh", "definition"),
        "clinical_features_en": public_text("en", "clinical_features"),
        "clinical_features_zh": public_text("zh", "clinical_features"),
        "epidemiology_en": public_text("en", "epidemiology"),
        "epidemiology_zh": public_text("zh", "epidemiology"),
        "clinical_summary_en": public_text("en", "clinical_features"),
        "clinical_summary_zh": public_text("zh", "clinical_features"),
        "transmission_en": public_text("en", "transmission"),
        "transmission_zh": public_text("zh", "transmission"),
        "prevention_en": public_text("en", "prevention"),
        "prevention_zh": public_text("zh", "prevention"),
        "surveillance_note_en": public_text("en", "surveillance_note"),
        "surveillance_note_zh": public_text("zh", "surveillance_note"),
        "risk_groups_en": public_text("en", "risk_groups"),
        "risk_groups_zh": public_text("zh", "risk_groups"),
        "knowledge_sources": knowledge_sources,
        "knowledge_source_count": len(knowledge_sources),
        "knowledge_updated_at": knowledge_updated_at,
        "knowledge_status": knowledge_status,
        "knowledge_tier": knowledge_tier,
        "knowledge_block_reason": block_reason
        or (profile_reason if not knowledge_profile_available else None),
        "knowledge_profile_available": knowledge_profile_available,
        "knowledge_profile_reason": profile_reason,
        "knowledge_has_authoritative_sources": has_authoritative_sources,
        "knowledge_display_mode": display_mode,
        "knowledge_completeness": knowledge_completeness,
        "knowledge_available_languages": available_languages,
        "knowledge_profile_languages": profile_languages,
        "knowledge_profile_type": profile_schema.profile_type,
        "knowledge_profile_schema": profile_schema.to_dict(),
        "knowledge_section_labels": profile_schema.labels,
        "knowledge_applicable_section_count": len(profile_schema.applicable_fields),
        "knowledge_repair_sections": repair_sections,
        "knowledge_field_status": field_status,
        "knowledge_language_quality": {
            language: assessments[language].to_dict() for language in ("en", "zh")
        },
    }
    return payload


def _is_authoritative_knowledge_source(source: object) -> bool:
    """Infer whether a source should unlock the public disease profile."""
    if not isinstance(source, dict):
        return False

    source_type = str(source.get("source_type") or "").strip().lower()
    if source_type in AUTHORITATIVE_KNOWLEDGE_SOURCE_TYPES:
        return True

    source_names = {
        str(source.get(field) or "").strip().lower()
        for field in ("source_name", "title", "label")
        if str(source.get(field) or "").strip()
    }
    if any(
        name == "who" or name.startswith("who ") or "world health organization" in name
        for name in source_names
    ):
        return True

    source_url = (
        str(source.get("url") or source.get("source_url") or "").strip().lower()
    )
    return any(marker in source_url for marker in AUTHORITATIVE_KNOWLEDGE_URL_MARKERS)


def apply_disease_knowledge_fields(
    disease: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Backward-compatible alias for the knowledge payload builder."""
    return build_disease_knowledge_fields(disease, brief_by_language)


def apply_country_brief_fields(
    country_data: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Attach country page interpretive text, falling back to generated source context."""
    brief_by_language = brief_by_language or {}
    en = brief_by_language.get("en") or {}
    zh = brief_by_language.get("zh") or {}
    source_info = country_data.get("source_info") or {}
    source_labels = [
        src.get("label") for src in source_info.get("sources") or [] if src.get("label")
    ]
    source_label_en = (
        ", ".join(source_labels)
        or source_info.get("primary_label")
        or "official surveillance sources"
    )
    country_code = str(country_data.get("country_code") or "").upper()
    country_name_zh = (
        country_data.get("country_name_zh")
        or ABOUT_COUNTRY_NAMES_ZH.get(country_code)
        or get_country_display_name(country_code, "zh")
        or country_data.get("country_name")
        or country_code
    )
    source_labels_zh = [
        ABOUT_SOURCE_LABELS_ZH.get((country_code, src.get("scope")), src.get("label"))
        for src in source_info.get("sources") or []
        if src.get("label")
    ]
    source_label_zh = (
        ", ".join(label for label in source_labels_zh if label) or source_label_en
    )
    country_name = (
        country_data.get("country_name_en")
        or country_data.get("country_name")
        or country_data.get("country_code")
    )
    date_range = country_data.get("date_range") or {}
    frequency = (country_data.get("frequency_meta") or {}).get(
        "source_frequency"
    ) or "UNKNOWN"

    country_data["brief_en"] = en.get("brief") or (
        f"{country_name} page consolidates infectious disease surveillance records from {source_label_en}. "
        "It combines source metadata, time-series charts, and downloadable machine-readable datasets."
    )
    country_data["brief_zh"] = zh.get("brief") or (
        f"{country_name_zh}页面整合来自{source_label_zh} 的传染病监测记录，包含来源信息、时间序列图表和可下载数据。"
    )
    country_data["surveillance_system_en"] = en.get("surveillance_system") or (
        f"The dataset is built from configured official feeds for {country_name}; current primary sources include {source_label_en}."
    )
    country_data["surveillance_system_zh"] = zh.get("surveillance_system") or (
        f"该数据集来自{country_name_zh}已配置的官方数据源；当前主要来源包括{source_label_zh}。"
    )
    country_data["interpretation_en"] = en.get("coverage_interpretation") or (
        f"Coverage currently spans {date_range.get('start') or 'N/A'} to {date_range.get('end') or 'N/A'} "
        f"across {country_data.get('disease_count') or 0} tracked diseases."
    )
    country_data["interpretation_zh"] = zh.get("coverage_interpretation") or (
        f"当前覆盖区间为 {date_range.get('start') or 'N/A'} 至 {date_range.get('end') or 'N/A'}，"
        f"覆盖 {country_data.get('disease_count') or 0} 种追踪疾病。"
    )
    country_data["reporting_cadence_en"] = en.get("reporting_cadence") or (
        f"Source reporting frequency is inferred as {frequency}; charts use weekly-equivalent normalization where applicable."
    )
    country_data["reporting_cadence_zh"] = zh.get("reporting_cadence") or (
        f"来源报告频率推断为 {frequency}；相关图表在适用时使用周等价归一化。"
    )
    country_data["limitations_en"] = en.get("data_limitations") or (
        "Counts reflect reported surveillance records and may be affected by case definitions, reporting lag, source cadence, and missing population denominators."
    )
    country_data["limitations_zh"] = zh.get("data_limitations") or (
        "病例数反映已报告的监测记录，可能受病例定义、报告延迟、来源频率和人口分母缺失影响。"
    )
    country_data["source_summary_en"] = en.get("source_summary") or source_label_en
    country_data["source_summary_zh"] = zh.get("source_summary") or source_label_zh
    country_data["country_brief_status"] = "published" if en or zh else "fallback"
    country_data["country_brief_updated_at"] = en.get("updated_at") or zh.get(
        "updated_at"
    )
    return country_data


# ─────────────────────────────────────────────────────────────
# Data processors
# ─────────────────────────────────────────────────────────────
def _series_context_for_records(records: list[dict]) -> dict:
    for record in records:
        context = record.get("_series_context")
        if isinstance(context, dict):
            return context
    # Direct callers of the pure builders may still provide historical record
    # dictionaries.  Treat those explicitly as legacy rather than leaving the
    # output provenance ambiguous.
    return _legacy_projection_context(reason="builder_received_unmarked_legacy_rows")


def _series_provenance_fields(records: list[dict]) -> dict:
    context = _series_context_for_records(records)
    return {
        "data_layer": context.get("data_layer") or LEGACY_DATA_LAYER,
        "projection_policy": context.get("projection_policy") or "legacy_fallback",
        "loss_risk": context.get("loss_risk"),
        "selected_series_codes": context.get("selected_series_codes") or [],
        "available_series_count": context.get("available_series_count") or 0,
        "source_series": context.get("source_series") or [],
        "coverage_status": context.get("coverage_status"),
        "coverage_policy": context.get("coverage_policy"),
        "legacy_gap_fill_count": context.get("legacy_gap_fill_count") or 0,
        "coverage_ratio_against_legacy": context.get("coverage_ratio_against_legacy"),
        "data_provenance": {
            key: value for key, value in context.items() if key != "source_series"
        },
    }


def _data_layer_summary(disease_series: dict[str, dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    risky_diseases: list[str] = []
    non_additive_diseases: list[str] = []
    for disease_id, series in disease_series.items():
        layer = str(series.get("data_layer") or LEGACY_DATA_LAYER)
        counts[layer] += 1
        if series.get("loss_risk") or series.get("data_provenance", {}).get(
            "coverage_risk"
        ):
            risky_diseases.append(disease_id)
        if series.get("loss_risk") == "non_additive_series_not_rolled_up":
            non_additive_diseases.append(disease_id)
    return {
        "series_registry_disease_count": counts.get(SERIES_DATA_LAYER, 0),
        "mixed_disease_count": counts.get(MIXED_DATA_LAYER, 0),
        "legacy_fallback_disease_count": counts.get(LEGACY_DATA_LAYER, 0),
        "loss_risk_disease_count": len(risky_diseases),
        "loss_risk_disease_ids": sorted(risky_diseases),
        "non_additive_series_disease_ids": sorted(non_additive_diseases),
    }


def _compact_source_series_metadata(source_series: list[dict]) -> list[dict]:
    """Keep definition/projection semantics in chart payloads without facts duplication."""

    omitted = {"dates", "values", "quality_statuses"}
    return [
        {key: value for key, value in item.items() if key not in omitted}
        for item in source_series
        if isinstance(item, dict)
    ]


def _country_series_data_layer_summary(country_series: dict[str, dict]) -> dict:
    registry_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == SERIES_DATA_LAYER
    )
    legacy_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == LEGACY_DATA_LAYER
    )
    mixed_countries = sorted(
        code
        for code, series in country_series.items()
        if series.get("data_layer") == MIXED_DATA_LAYER
    )
    risky_countries = sorted(
        code for code, series in country_series.items() if series.get("loss_risk")
    )
    return {
        "series_registry_country_count": len(registry_countries),
        "mixed_country_count": len(mixed_countries),
        "legacy_fallback_country_count": len(legacy_countries),
        "series_registry_country_codes": registry_countries,
        "mixed_country_codes": mixed_countries,
        "legacy_fallback_country_codes": legacy_countries,
        "loss_risk_country_codes": risky_countries,
    }


def build_country_data(
    country_code: str,
    country_name: str,
    records: list[dict],
    diseases_by_id: dict,
    frequency_meta: dict | None = None,
) -> dict:
    """Build the full country JSON blob with time-series per disease."""
    records = [rec for rec in records if rec.get("disease_id") in diseases_by_id]
    # Group records by disease_id
    by_disease: dict[str, list] = defaultdict(list)
    for rec in records:
        by_disease[rec["disease_id"]].append(rec)

    total_cases = sum(r["cases"] for r in records)
    total_deaths = sum(r["deaths"] for r in records)
    dates = sorted({r["date"] for r in records if r["date"]})

    # Build time series per disease
    disease_series = {}
    for disease_id, recs in by_disease.items():
        points: dict[str, dict] = {}
        for rec in recs:
            d = rec.get("date")
            if not d:
                continue
            if d not in points:
                points[d] = {
                    "cases": 0,
                    "deaths": 0,
                    "incidence_rates": [],
                    "incidence_sources": [],
                    "mortality_rates": [],
                }
            points[d]["cases"] += rec.get("cases") or 0
            points[d]["deaths"] += rec.get("deaths") or 0
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))
            points[d]["mortality_rates"].append(rec.get("mortality_rate"))

        series_dates = sorted(points.keys())
        series_cases = [points[d]["cases"] for d in series_dates]
        series_deaths = [points[d]["deaths"] for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]
        series_mortality = [
            avg_or_none(points[d]["mortality_rates"]) for d in series_dates
        ]
        weekly_equiv_cases = calculate_weekly_equivalent(series_dates, series_cases)

        disease_info = diseases_by_id.get(disease_id, {})
        disease_series[disease_id] = {
            "disease_id": disease_id,
            "name_en": disease_info.get("name_en", disease_id),
            "name_zh": disease_info.get("name_zh", disease_id),
            "category": disease_info.get("category", "Unknown"),
            "slug": disease_info.get("slug", disease_id.lower()),
            "dates": series_dates,
            "cases": series_cases,
            "weekly_equiv_cases": weekly_equiv_cases,
            "deaths": series_deaths,
            "incidence_rates": series_incidence,
            "incidence_sources": series_incidence_sources,
            "mortality_rates": series_mortality,
            "total_cases": sum(series_cases),
            "total_deaths": sum(series_deaths),
            "latest_cases": series_cases[-1] if series_cases else 0,
            "latest_deaths": series_deaths[-1] if series_deaths else 0,
            **_series_provenance_fields(recs),
        }

    incidence_source_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        source = rec.get("incidence_rate_source") or "missing_population"
        incidence_source_counts[source] += 1

    # Heatmap data: diseases (rows) × months (cols)
    all_months = sorted({r["year_month"] for r in records if r["year_month"]})
    heatmap_diseases = sorted(
        disease_series.keys(),
        key=lambda d: disease_series[d]["total_cases"],
        reverse=True,
    )[
        :50
    ]  # Cap at top 50 diseases for readability

    heatmap_z = []
    for did in heatmap_diseases:
        month_totals: dict[str, int] = defaultdict(int)
        for rec in by_disease[did]:
            ym = rec.get("year_month")
            if ym:
                month_totals[ym] += rec.get("cases") or 0
        row_z = []
        for month in all_months:
            cases = month_totals.get(month, 0)
            row_z.append(math.log10(cases + 1))  # log scale
        heatmap_z.append(row_z)

    heatmap_labels = [disease_series[d]["name_en"] for d in heatmap_diseases]

    country_name_en, country_name_zh = resolve_country_display_names(
        country_code,
        {"name": country_name},
    )

    return {
        "country_code": country_code,
        "country_name": country_name_en,
        "country_name_en": country_name_en,
        "country_name_zh": country_name_zh,
        "total_cases": total_cases,
        "total_deaths": total_deaths,
        "disease_count": len(by_disease),
        "frequency_meta": frequency_meta
        or {
            "source_frequency": "UNKNOWN",
            "canonical_frequency": "WEEKLY_EQUIVALENT_7D",
            "aggregation_rule": "normalize_counts_to_7_day_equivalent",
        },
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
        "comparison_basis": {
            "frequency": "WEEKLY_EQUIVALENT_7D",
            "metric": "weekly_equiv_cases",
        },
        "incidence_rate_basis": {
            "formula": "cases / population * 100000",
            "unit": "per_100k",
            "population_source": "WPP",
            "source_counts": dict(incidence_source_counts),
            "note_en": "Incidence Rate is computed from WPP population data during site generation; when population is unavailable, original database incidence is shown.",
            "note_zh": "发病率在网页数据生成阶段按 WPP 人口重算（每10万人）；若缺少人口数据则回退显示数据库原始发病率。",
        },
        "disease_series": disease_series,
        "data_layer_summary": _data_layer_summary(disease_series),
        "heatmap": {
            "diseases": heatmap_diseases,
            "disease_labels": heatmap_labels,
            "months": all_months,
            "z": heatmap_z,
        },
    }


def build_country_site_data(country_data: dict) -> dict:
    """Build a compact country payload used only by the site charts."""
    disease_series = country_data.get("disease_series") or {}
    shared_dates = sorted(
        {
            date
            for series in disease_series.values()
            for date in (series.get("dates") or [])
            if date
        }
    )
    date_index = {date: index for index, date in enumerate(shared_dates)}
    source_labels: list[str] = []
    source_codes: dict[str, int] = {}

    def register_source(label: str | None) -> int | None:
        if not label:
            return None
        existing = source_codes.get(label)
        if existing is not None:
            return existing
        code = len(source_labels)
        source_codes[label] = code
        source_labels.append(label)
        return code

    compact_series = []
    for entry in disease_series.values():
        dates = entry.get("dates") or []
        incidence_rates = entry.get("incidence_rates") or []
        incidence_sources = entry.get("incidence_sources") or []

        ri: list[int] = []
        rv: list[float] = []
        rs: list[int | None] = []
        for point_index, value in enumerate(incidence_rates):
            if value is None:
                continue
            ri.append(point_index)
            rv.append(round(float(value), 4))
            source_label = (
                incidence_sources[point_index]
                if point_index < len(incidence_sources)
                else None
            )
            rs.append(register_source(source_label))

        compact_entry = {
            "id": entry.get("disease_id"),
            "en": entry.get("name_en"),
            "zh": entry.get("name_zh"),
            "cat": entry.get("category"),
            "slug": entry.get("slug"),
            "tc": entry.get("total_cases", 0),
            "td": entry.get("total_deaths", 0),
            "lc": entry.get("latest_cases", 0),
            "ld": entry.get("latest_deaths", 0),
            "x": [date_index[date] for date in dates if date in date_index],
            "c": entry.get("cases") or [],
            "w": [
                round(float(value), 2)
                for value in (entry.get("weekly_equiv_cases") or [])
            ],
            "d": entry.get("deaths") or [],
            "data_layer": entry.get("data_layer") or LEGACY_DATA_LAYER,
            "projection_policy": entry.get("projection_policy") or "legacy_fallback",
            "loss_risk": entry.get("loss_risk"),
            "selected_series_codes": entry.get("selected_series_codes") or [],
            "metric_layers": (entry.get("data_provenance") or {}).get("metric_layers")
            or {},
            "source_series": _compact_source_series_metadata(
                entry.get("source_series") or []
            ),
        }
        if ri:
            compact_entry["ri"] = ri
            compact_entry["rv"] = rv
            if any(code is not None for code in rs):
                compact_entry["rs"] = rs
        compact_series.append(compact_entry)

    heatmap = country_data.get("heatmap") or {}
    return {
        "v": 1,
        "meta": {
            "cc": country_data.get("country_code"),
            "cn": country_data.get("country_name"),
            "cn_zh": country_data.get("country_name_zh"),
            "tc": country_data.get("total_cases"),
            "td": country_data.get("total_deaths"),
            "dc": country_data.get("disease_count"),
            "dr": country_data.get("date_range"),
            "data_layer_summary": country_data.get("data_layer_summary") or {},
        },
        "dates": shared_dates,
        "sources": source_labels,
        "series": compact_series,
        "heatmap": {
            "months": heatmap.get("months") or [],
            "disease_ids": heatmap.get("diseases") or [],
            "z": [
                [round(float(value), 4) for value in row]
                for row in (heatmap.get("z") or [])
            ],
        },
    }


def build_disease_data(
    disease_id: str,
    disease_info: dict,
    all_records_by_country: dict[str, list],
) -> dict:
    """Build per-disease JSON with time-series across all countries."""
    country_series = {}
    for country_code, records in all_records_by_country.items():
        disease_records = [r for r in records if r["disease_id"] == disease_id]
        if not disease_records:
            continue

        points: dict[str, dict] = {}
        for rec in disease_records:
            d = rec.get("date")
            if not d:
                continue
            if d not in points:
                points[d] = {
                    "cases": 0,
                    "deaths": 0,
                    "incidence_rates": [],
                    "incidence_sources": [],
                }
            points[d]["cases"] += rec.get("cases") or 0
            points[d]["deaths"] += rec.get("deaths") or 0
            points[d]["incidence_rates"].append(rec.get("incidence_rate"))
            points[d]["incidence_sources"].append(rec.get("incidence_rate_source"))

        series_dates = sorted(points.keys())
        series_cases = [points[d]["cases"] for d in series_dates]
        series_deaths = [points[d]["deaths"] for d in series_dates]
        series_incidence = [
            avg_or_none(points[d]["incidence_rates"]) for d in series_dates
        ]
        series_incidence_sources = [
            dominant_value(points[d]["incidence_sources"]) for d in series_dates
        ]

        country_series[country_code] = {
            "dates": series_dates,
            "cases": series_cases,
            "weekly_equiv_cases": calculate_weekly_equivalent(
                series_dates, series_cases
            ),
            "deaths": series_deaths,
            "incidence_rates": series_incidence,
            "incidence_sources": series_incidence_sources,
            "total_cases": sum(series_cases),
            "total_deaths": sum(series_deaths),
            **_series_provenance_fields(disease_records),
        }

    all_disease_records = [
        r
        for recs in all_records_by_country.values()
        for r in recs
        if r["disease_id"] == disease_id
    ]
    monthly: dict[str, dict] = defaultdict(lambda: {"cases": 0, "deaths": 0})
    for r in all_disease_records:
        if r["year_month"]:
            monthly[r["year_month"]]["cases"] += r["cases"]
            monthly[r["year_month"]]["deaths"] += r["deaths"]
    months_sorted = sorted(monthly.keys())

    return {
        **disease_info,
        "country_series": country_series,
        "global_monthly": {
            "months": months_sorted,
            "cases": [monthly[m]["cases"] for m in months_sorted],
            "deaths": [monthly[m]["deaths"] for m in months_sorted],
        },
        "total_cases": sum(cs["total_cases"] for cs in country_series.values()),
        "total_deaths": sum(cs["total_deaths"] for cs in country_series.values()),
        "data_layer_summary": _country_series_data_layer_summary(country_series),
    }


def build_disease_site_data(
    disease_data: dict,
    country_name_by_code: dict[str, str] | None = None,
    country_name_zh_by_code: dict[str, str] | None = None,
) -> dict:
    """Build a compact disease payload used only by the site charts."""
    country_series = disease_data.get("country_series") or {}
    shared_dates = sorted(
        {
            date
            for series in country_series.values()
            for date in (series.get("dates") or [])
            if date
        }
    )
    date_index = {date: index for index, date in enumerate(shared_dates)}
    source_labels: list[str] = []
    source_codes: dict[str, int] = {}

    def register_source(label: str | None) -> int | None:
        if not label:
            return None
        existing = source_codes.get(label)
        if existing is not None:
            return existing
        code = len(source_labels)
        source_codes[label] = code
        source_labels.append(label)
        return code

    compact_series = []
    for country_code, entry in country_series.items():
        dates = entry.get("dates") or []
        incidence_rates = entry.get("incidence_rates") or []
        incidence_sources = entry.get("incidence_sources") or []

        ri: list[int] = []
        rv: list[float] = []
        rs: list[int | None] = []
        for point_index, value in enumerate(incidence_rates):
            if value is None:
                continue
            ri.append(point_index)
            rv.append(round(float(value), 4))
            source_label = (
                incidence_sources[point_index]
                if point_index < len(incidence_sources)
                else None
            )
            rs.append(register_source(source_label))

        compact_entry = {
            "cc": country_code,
            "n": (country_name_by_code or {}).get(country_code) or country_code,
            "n_zh": (country_name_zh_by_code or {}).get(country_code) or country_code,
            "tc": entry.get("total_cases", 0),
            "td": entry.get("total_deaths", 0),
            "x": [date_index[date] for date in dates if date in date_index],
            "c": entry.get("cases") or [],
            "w": [
                round(float(value), 2)
                for value in (entry.get("weekly_equiv_cases") or [])
            ],
            "d": entry.get("deaths") or [],
            "data_layer": entry.get("data_layer") or LEGACY_DATA_LAYER,
            "projection_policy": entry.get("projection_policy") or "legacy_fallback",
            "loss_risk": entry.get("loss_risk"),
            "selected_series_codes": entry.get("selected_series_codes") or [],
            "metric_layers": (entry.get("data_provenance") or {}).get("metric_layers")
            or {},
            "source_series": _compact_source_series_metadata(
                entry.get("source_series") or []
            ),
        }
        if ri:
            compact_entry["ri"] = ri
            compact_entry["rv"] = rv
            if any(code is not None for code in rs):
                compact_entry["rs"] = rs
        compact_series.append(compact_entry)

    global_monthly = disease_data.get("global_monthly") or {}
    return {
        "v": 1,
        "meta": {
            "id": disease_data.get("disease_id"),
            "en": disease_data.get("name_en"),
            "zh": disease_data.get("name_zh"),
            "cat": disease_data.get("category"),
            "tc": disease_data.get("total_cases"),
            "td": disease_data.get("total_deaths"),
            "cc": len(country_series),
            "data_layer_summary": disease_data.get("data_layer_summary") or {},
        },
        "dates": shared_dates,
        "sources": source_labels,
        "series": compact_series,
        "monthly": {
            "months": global_monthly.get("months") or [],
            "cases": global_monthly.get("cases") or [],
            "deaths": global_monthly.get("deaths") or [],
        },
    }


# ─────────────────────────────────────────────────────────────
# Main export
# ─────────────────────────────────────────────────────────────
async def export(
    output_dir: Path,
    manifest_output: Path,
    allow_empty_export: bool = False,
    *,
    public_site_data_dir: Path = DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
    sharded_download_output_dir: Path = DEFAULT_SHARDED_DOWNLOAD_OUTPUT,
    shard_max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    github_snapshot_output_dir: Path = DEFAULT_GITHUB_SNAPSHOT_OUTPUT,
    github_snapshot_retain_releases: int = DEFAULT_RETAIN_RELEASES,
    github_snapshot_url_base: str = DEFAULT_GITHUB_SNAPSHOT_URL_BASE,
) -> None:
    await ensure_site_export_database_ready()
    generated_at = ""

    # Load the stable catalogue and the independently versioned ontology.
    csv_path = ROOT / "configs" / "standard_diseases.csv"
    catalogue_diseases = load_standard_diseases(csv_path)
    catalogue_ids = {disease["disease_id"] for disease in catalogue_diseases}
    ontology = load_disease_ontology()
    ontology_document = ontology.to_dict()
    diseases = [
        disease
        for disease in catalogue_diseases
        if should_generate_public_disease_page(disease)
    ]
    enrich_diseases_with_ontology(diseases, ontology)
    diseases_by_id = {d["disease_id"]: d for d in diseases}
    disease_knowledge_briefs: dict[str, dict[str, dict]] = {}
    country_briefs: dict[str, dict[str, dict]] = {}
    countries_simple: list[dict] = []
    country_exports: list[dict] = []
    disease_exports: list[dict] = []
    reports: list[dict] = []
    report_details: dict[int, dict] = {}

    async with get_db() as session:
        population_enabled = await has_population_table(session)
        if population_enabled:
            print(
                "  Population table detected: incidence will use WPP-based computation"
            )
        else:
            print(
                "  Population table not found: incidence falls back to database values"
            )

        disease_knowledge_briefs = await fetch_disease_knowledge_briefs(session)
        country_briefs = await fetch_country_briefs(session)
        if disease_knowledge_briefs:
            print(
                f"  Knowledge briefs detected: {len(disease_knowledge_briefs)} diseases"
            )
        else:
            print("  Knowledge briefs not found: disease profiles will remain blocked")
        if country_briefs:
            print(f"  Country briefs detected: {len(country_briefs)} countries")
        else:
            print(
                "  Country briefs not found: using generated country context fallback"
            )

        # ── Countries ──
        countries = await fetch_countries(session)
        countries_simple = []
        for country in countries:
            name_en, name_zh = resolve_country_display_names(country["code"], country)
            countries_simple.append(
                {
                    "code": country["code"],
                    "name": name_en,
                    "name_en": name_en,
                    "name_zh": name_zh,
                    "language": country["language"],
                }
            )

        all_records_by_country: dict[str, list] = {}
        country_sources_by_code: dict[str, dict] = {}
        country_name_by_code = {c["code"]: c["name"] for c in countries_simple}
        country_name_zh_by_code = {c["code"]: c["name_zh"] for c in countries_simple}
        country_download_entries: list[dict] = []
        disease_download_entries: list[dict] = []
        for country in countries:
            code = country["code"]
            country_name_en = country_name_by_code.get(code) or country["name"]
            print(f"  Fetching records for {code}…")
            frequency_meta = await fetch_country_frequency_meta(session, code)
            country_source_info = build_country_source_info(code, frequency_meta)
            records = await fetch_disease_records(session, code, population_enabled)
            validate_record_catalogue_coverage(
                records,
                catalogue_ids,
                set(diseases_by_id),
            )

            all_records_by_country[code] = records
            country_sources_by_code[code] = country_source_info
            country_data = build_country_data(
                code, country_name_en, records, diseases_by_id, frequency_meta
            )
            layer_summary = country_data["data_layer_summary"]
            print(
                "    Series-first: "
                f"registry={layer_summary['series_registry_disease_count']}, "
                f"mixed_gap_fill={layer_summary['mixed_disease_count']}, "
                f"legacy_fallback={layer_summary['legacy_fallback_disease_count']}, "
                f"risk_labelled={layer_summary['loss_risk_disease_count']}"
            )
            if layer_summary["non_additive_series_disease_ids"]:
                print(
                    "    Non-additive series kept separate: "
                    + ", ".join(layer_summary["non_additive_series_disease_ids"])
                )
            country_data["source_info"] = country_source_info
            country_data = apply_country_brief_fields(
                country_data, country_briefs.get(code.upper())
            )
            # Augment countries_simple with stats
            for c in countries_simple:
                if c["code"] == code:
                    c["total_cases"] = country_data["total_cases"]
                    c["total_deaths"] = country_data["total_deaths"]
                    c["disease_count"] = country_data["disease_count"]
                    c["date_range"] = country_data["date_range"]
                    c["source_info"] = country_source_info
                    c["data_layer_summary"] = country_data["data_layer_summary"]

            country_exports.append(
                {
                    "code": code,
                    "country_name": country_name_en,
                    "country_name_zh": country_name_zh_by_code.get(code),
                    "country_data": country_data,
                    "source_info": country_source_info,
                }
            )

        reports = await fetch_reports(session)
        total_record_count = sum(
            len(records) for records in all_records_by_country.values()
        )
        if total_record_count == 0 and not allow_empty_export:
            message = (
                "Refusing to overwrite site data with an empty export because no disease "
                f"records were found in the database across {len(countries)} countries."
            )
            if existing_site_export_has_content(output_dir):
                message += f" Existing files in {output_dir} were left untouched."
            message += " Import data first, or pass --allow-empty-export if this is intentional."
            raise RuntimeError(message)

        generated_at = resolve_snapshot_version(countries_simple, reports)

        for country_export in country_exports:
            code = country_export["code"]
            country_name = country_export["country_name"]
            country_name_zh = country_export.get(
                "country_name_zh"
            ) or country_name_zh_by_code.get(code)
            country_data = country_export["country_data"]
            country_source_info = country_export["source_info"]
            country_data["generated_at"] = generated_at
            country_site_data = build_country_site_data(country_data)
            country_canonical_facts = build_country_canonical_facts(
                country_data,
                country_source_info,
            )
            country_export["site_data"] = country_site_data
            country_export["canonical_facts"] = country_canonical_facts
            country_download_entries.append(
                {
                    "kind": "country",
                    "id": code.lower(),
                    "code": code,
                    "name": country_name,
                    "name_en": country_name,
                    "name_zh": country_name_zh,
                    "generated_at": generated_at,
                    "record_count": len(country_canonical_facts),
                    "date_range": country_data.get("date_range"),
                    "site_json_path": f"/site-data/countries/{code.lower()}.json",
                }
            )

        # ── Per-disease files ──
        for disease in diseases:
            did = disease["disease_id"]
            disease_data = build_disease_data(did, disease, all_records_by_country)
            disease_site_data = build_disease_site_data(
                disease_data,
                country_name_by_code,
                country_name_zh_by_code,
            )
            disease_countries = sorted(
                (disease_data.get("country_series") or {}).keys()
            )
            disease_source_info = []
            for country_code in disease_countries:
                country_source = dict(country_sources_by_code.get(country_code, {}))
                country_source["country_name"] = country_name_by_code.get(country_code)
                country_source["country_name_en"] = country_name_by_code.get(
                    country_code
                )
                country_source["country_name_zh"] = country_name_zh_by_code.get(
                    country_code
                )
                disease_source_info.append(country_source)
            disease_data["generated_at"] = generated_at
            disease_data["source_info"] = disease_source_info
            disease_record_count = sum(
                len(series.get("dates") or [])
                for series in (disease_data.get("country_series") or {}).values()
            )
            disease_exports.append(
                {
                    "disease_id": did,
                    "disease_data": disease_data,
                    "site_data": disease_site_data,
                }
            )
            disease_download_entries.append(
                {
                    "kind": "disease",
                    "id": did.lower(),
                    "disease_id": did,
                    "slug": disease.get("slug"),
                    "name_en": disease.get("name_en"),
                    "name_zh": disease.get("name_zh"),
                    "generated_at": generated_at,
                    "record_count": disease_record_count,
                    "country_count": len(disease_countries),
                    "site_json_path": f"/site-data/diseases/{did.lower()}.json",
                }
            )

        for rep in reports:
            detail = await fetch_report_detail(session, rep["id"])
            if detail:
                report_details[rep["id"]] = detail

    country_fact_count = sum(
        len(country_export["canonical_facts"])
        for country_export in country_exports
    )
    disease_view_count = sum(
        int(entry["record_count"]) for entry in disease_download_entries
    )
    if disease_view_count != country_fact_count:
        raise RuntimeError(
            "Cannot build canonical v2 downloads because country and disease "
            "views disagree: "
            f"countries={country_fact_count}, diseases={disease_view_count}"
        )

    print(
        "  Building canonical v2 download package "
        f"({country_fact_count:,} unique facts)…"
    )
    sharded_manifest = build_globalid_canonical_download_package(
        (
            fact
            for country_export in country_exports
            for fact in country_export["canonical_facts"]
        ),
        sharded_download_output_dir,
        generated_at=generated_at,
        country_entries=country_download_entries,
        disease_entries=disease_download_entries,
        source_info_by_country=country_sources_by_code,
        max_uncompressed_bytes=shard_max_uncompressed_bytes,
    )
    sharded_totals = sharded_manifest["totals"]
    if sharded_totals["record_count"] != country_fact_count:
        raise RuntimeError(
            "Canonical v2 record total changed during packaging: "
            f"expected={country_fact_count}, "
            f"actual={sharded_totals['record_count']}"
        )
    print(
        "  ✓ canonical v2 package: "
        f"{sharded_totals['shard_count']:,} shards, "
        f"{sharded_totals['compressed_bytes']:,} compressed bytes"
    )
    snapshot_result = build_github_snapshot(
        sharded_download_output_dir,
        github_snapshot_output_dir,
        retain_releases=github_snapshot_retain_releases,
    )
    print(
        "  ✓ GitHub-ready snapshot tree: "
        f"{snapshot_result.release_count} releases, "
        f"{snapshot_result.file_count:,} files, "
        f"{snapshot_result.total_bytes:,} bytes"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "countries").mkdir(exist_ok=True)
    (output_dir / "diseases").mkdir(exist_ok=True)
    (output_dir / "disease-knowledge").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)
    reset_public_data_dir(public_site_data_dir / "countries")
    reset_public_data_dir(public_site_data_dir / "diseases")
    clean_generated_dir(output_dir / "countries")
    clean_generated_dir(output_dir / "diseases")
    clean_generated_dir(output_dir / "disease-knowledge")
    clean_generated_dir(output_dir / "reports")

    # Write disease index
    (output_dir / "diseases" / "index.json").write_text(
        json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ diseases/index.json ({len(diseases)} diseases)")

    ontology_json = json.dumps(ontology_document, ensure_ascii=False, indent=2)
    (output_dir / "disease-ontology.json").write_text(ontology_json, encoding="utf-8")
    (public_site_data_dir / "disease-ontology.json").write_text(
        ontology_json, encoding="utf-8"
    )
    print(
        "  ✓ disease-ontology.json "
        f"({len(ontology.concept_ids)} concepts, {len(ontology.series_ids)} series)"
    )

    for country_export in country_exports:
        code = country_export["code"]
        country_data = country_export["country_data"]
        site_data = country_export["site_data"]
        country_json = json.dumps(country_data, ensure_ascii=False, indent=2)
        (output_dir / "countries" / f"{code.lower()}.json").write_text(
            country_json, encoding="utf-8"
        )
        (public_site_data_dir / "countries" / f"{code.lower()}.json").write_text(
            json.dumps(site_data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(
            f"  ✓ countries/{code.lower()}.json ({len(all_records_by_country[code])} records)"
        )

    knowledge_mode_counts: dict[str, int] = defaultdict(int)
    knowledge_completeness_values: list[float] = []
    for disease_export in disease_exports:
        did = disease_export["disease_id"]
        disease_data = disease_export["disease_data"]
        disease_site_data = disease_export["site_data"]
        disease_knowledge_payload = build_disease_knowledge_fields(
            diseases_by_id[did],
            disease_knowledge_briefs.get(did),
        )
        knowledge_mode_counts[
            str(disease_knowledge_payload.get("knowledge_display_mode") or "blocked")
        ] += 1
        knowledge_completeness_values.append(
            float(disease_knowledge_payload.get("knowledge_completeness") or 0.0)
        )
        (output_dir / "diseases" / f"{did.lower()}.json").write_text(
            json.dumps(disease_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "disease-knowledge" / f"{did.lower()}.json").write_text(
            json.dumps(disease_knowledge_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (public_site_data_dir / "diseases" / f"{did.lower()}.json").write_text(
            json.dumps(disease_site_data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    print(
        f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)"
    )
    print(
        f"  ✓ disease-knowledge/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)"
    )
    print(
        "  Knowledge quality: "
        + ", ".join(
            f"{mode}={knowledge_mode_counts.get(mode, 0)}"
            for mode in ("full", "partial", "blocked")
        )
    )
    print(
        "  ✓ v2 dataset indexes "
        f"({len(country_download_entries)} countries, "
        f"{len(disease_download_entries)} diseases)"
    )

    (output_dir / "reports" / "index.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ reports/index.json ({len(reports)} reports)")

    for report_id, detail in report_details.items():
        (output_dir / "reports" / f"{report_id}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"  ✓ reports/<id>.json ({len(report_details)} files)")

    # ── Meta ──
    meta = {
        "generated_at": generated_at,
        "total_countries": len(countries_simple),
        "total_diseases": len(diseases),
        "total_reports": len(reports),
        "countries": countries_simple,
        "knowledge_quality": {
            "display_modes": dict(sorted(knowledge_mode_counts.items())),
            "average_completeness": (
                round(
                    sum(knowledge_completeness_values)
                    / len(knowledge_completeness_values),
                    3,
                )
                if knowledge_completeness_values
                else 0.0
            ),
            "schema_version": 3,
        },
        "disease_ontology": {
            "registry_id": ontology_document["registry_id"],
            "schema_version": ontology_document["schema_version"],
            "default_rollup_policy": ontology_document["default_rollup_policy"],
            "concept_count": len(ontology.concept_ids),
            "source_series_count": len(ontology.series_ids),
        },
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  ✓ meta.json")

    about_snapshot = build_about_snapshot(
        countries_simple=countries_simple,
        diseases=diseases,
        reports=reports,
        generated_at=generated_at,
    )
    (output_dir / "about.json").write_text(
        json.dumps(about_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  ✓ about.json")

    downloads_manifest = build_frontend_download_manifest(
        sharded_manifest,
        snapshot_url_base=github_snapshot_url_base,
        country_entries=country_download_entries,
        disease_entries=disease_download_entries,
    )
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(
        json.dumps(downloads_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("  ✓ v2 frontend downloads manifest")
    print(f"\nDone. Data written to: {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export site data to JSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--public-site-data-output",
        type=Path,
        default=DEFAULT_PUBLIC_SITE_DATA_OUTPUT,
        help=(
            "Compact browser data output directory "
            f"(default: {DEFAULT_PUBLIC_SITE_DATA_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--sharded-download-output",
        type=Path,
        default=DEFAULT_SHARDED_DOWNLOAD_OUTPUT,
        help=(
            "Canonical v2 sharded package output directory "
            f"(default: {DEFAULT_SHARDED_DOWNLOAD_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--shard-max-uncompressed-bytes",
        type=int,
        default=DEFAULT_MAX_UNCOMPRESSED_BYTES,
        help=(
            "Maximum uncompressed NDJSON bytes per v2 shard "
            f"(default: {DEFAULT_MAX_UNCOMPRESSED_BYTES})"
        ),
    )
    parser.add_argument(
        "--github-snapshot-output",
        type=Path,
        default=DEFAULT_GITHUB_SNAPSHOT_OUTPUT,
        help=(
            "Local GitHub-ready bounded snapshot tree "
            f"(default: {DEFAULT_GITHUB_SNAPSHOT_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--github-snapshot-retain-releases",
        type=int,
        default=DEFAULT_RETAIN_RELEASES,
        help=(
            "Number of complete v2 releases retained in the GitHub snapshot "
            f"tree (default: {DEFAULT_RETAIN_RELEASES})"
        ),
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=DEFAULT_DOWNLOAD_MANIFEST,
        help=f"Frontend manifest output path (default: {DEFAULT_DOWNLOAD_MANIFEST})",
    )
    parser.add_argument(
        "--github-snapshot-url-base",
        default=DEFAULT_GITHUB_SNAPSHOT_URL_BASE,
        help=(
            "Public raw URL of the v2 snapshot branch used by the frontend "
            f"(default: {DEFAULT_GITHUB_SNAPSHOT_URL_BASE})"
        ),
    )
    parser.add_argument(
        "--allow-empty-export",
        action="store_true",
        help="Allow overwriting site data even when the database currently exports zero disease records",
    )
    args = parser.parse_args()
    print(f"Exporting site data to {args.output} …")
    print(f"Writing canonical v2 package to {args.sharded_download_output} …")
    print(f"Preparing GitHub snapshot tree at {args.github_snapshot_output} …")
    print(f"Writing download manifest to {args.manifest_output} …\n")
    asyncio.run(
        export(
            args.output,
            args.manifest_output,
            args.allow_empty_export,
            public_site_data_dir=args.public_site_data_output,
            sharded_download_output_dir=args.sharded_download_output,
            shard_max_uncompressed_bytes=args.shard_max_uncompressed_bytes,
            github_snapshot_output_dir=args.github_snapshot_output,
            github_snapshot_retain_releases=args.github_snapshot_retain_releases,
            github_snapshot_url_base=args.github_snapshot_url_base,
        )
    )


if __name__ == "__main__":
    main()
