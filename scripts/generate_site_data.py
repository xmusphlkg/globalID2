#!/usr/bin/env python3
"""
Generate static JSON data files for the Astro-based report site.

Usage:
    python scripts/generate_site_data.py
    python scripts/generate_site_data.py --publish-downloads
    python scripts/generate_site_data.py --download-output astro-site/public/downloads --download-url-base /downloads

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
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# Make sure project root is on PYTHONPATH
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core.database import get_db, init_database  # noqa: E402
from src.core.data_share import (  # noqa: E402
    get_data_share_raw_base_url,
    get_data_share_repo_branch,
    get_data_share_repo_url,
)
from src.core.country_library import (  # noqa: E402
    get_country_bootstrap_config,
    get_country_profile,
    get_standard_country_codes,
)
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema  # noqa: E402
from src.core.source_scopes import scope_display_label  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Config defaults
# ─────────────────────────────────────────────────────────────
DEFAULT_OUTPUT = ROOT / "astro-site" / "src" / "data"
DEFAULT_DOWNLOAD_OUTPUT = ROOT / "exports" / "site-downloads"
DEFAULT_PUBLIC_DOWNLOAD_OUTPUT = ROOT / "astro-site" / "public" / "downloads"
DEFAULT_DOWNLOAD_MANIFEST = ROOT / "astro-site" / "src" / "data" / "downloads.json"
DEFAULT_DOWNLOAD_REPO_URL = get_data_share_repo_url()
DEFAULT_DOWNLOAD_REPO_BRANCH = get_data_share_repo_branch()
DEFAULT_DOWNLOAD_REPO_WORKDIR = Path("/tmp/globalid2-data-download-publish")
DEFAULT_DOWNLOAD_COMMIT_MESSAGE = "chore: update generated data downloads"
DOWNLOAD_REPO_MANAGED_PATHS = ("countries", "diseases", "manifest.json")
DEFAULT_DOWNLOAD_URL_BASE = get_data_share_raw_base_url(
    DEFAULT_DOWNLOAD_REPO_URL,
    DEFAULT_DOWNLOAD_REPO_BRANCH,
)

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
}

DOWNLOAD_CSV_FIELDS = [
    "dataset_kind",
    "dataset_id",
    "dataset_slug",
    "dataset_name",
    "country_code",
    "country_name",
    "disease_id",
    "disease_name_en",
    "disease_name_zh",
    "category",
    "date",
    "year_month",
    "cases",
    "weekly_equiv_cases",
    "deaths",
    "incidence_rate_per_100k",
    "incidence_rate_source",
    "mortality_rate",
    "coverage_start",
    "coverage_end",
    "generated_at",
    "primary_source_scope",
    "primary_source_label",
    "primary_source_url",
    "primary_source_type",
    "source_scopes",
    "source_labels",
    "source_urls",
    "source_types",
]

ABOUT_COUNTRY_NAMES_ZH: dict[str, str] = {
    "AU": "澳大利亚",
    "CN": "中国",
    "JP": "日本",
    "US": "美国",
}

ABOUT_SOURCE_LABELS_ZH: dict[tuple[str, str], str] = {
    ("AU", "all"): "澳大利亚 NINDSS",
    ("CN", "cdc_weekly"): "中国疾控中心周报",
    ("CN", "nhc"): "国家疾病预防控制局",
    ("CN", "pubmed"): "PubMed 生物医学文献库",
    ("JP", "jp_weekly"): "日本 NIID/JIHS 周报",
    ("US", "nndss_api"): "美国 CDC NNDSS",
}

ABOUT_SOURCE_DESCRIPTIONS_ZH: dict[tuple[str, str], str] = {
    ("AU", "all"): "澳大利亚国家法定传染病监测系统仪表板。",
    ("CN", "cdc_weekly"): "中国疾控中心发布的月度法定传染病报告。",
    ("CN", "nhc"): "中国官方公共卫生公报与查询门户。",
    ("CN", "pubmed"): "作为补充上下文使用的生物医学文献发现源。",
    ("JP", "jp_weekly"): "日本 NIID/JIHS 的周度传染病监测数据。",
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


def safe_float(v) -> float | None:
    """Return float or None for non-finite values."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


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
                    "slug": row["standard_name_en"].lower().replace(" ", "-").replace("/", "-"),
                }
            )
    return diseases


def clean_generated_dir(dir_path: Path) -> None:
    """Remove stale generated CSV/JSON files before rewriting."""
    if not dir_path.exists():
        return
    for pattern in ("*.json", "*.csv"):
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                file_path.unlink()


def build_download_url(base_url: str, relative_path: str) -> str:
    """Build a public URL for a generated download file."""
    clean_relative = relative_path.lstrip("/")
    clean_base = (base_url or DEFAULT_DOWNLOAD_URL_BASE).rstrip("/")
    return f"{clean_base}/{clean_relative}"


def remove_stale_public_downloads(active_download_output: Path) -> None:
    """Prevent Astro from copying large legacy downloads into dist/."""
    public_dir = DEFAULT_PUBLIC_DOWNLOAD_OUTPUT.resolve()
    active_dir = active_download_output.resolve()
    if active_dir == public_dir or not public_dir.exists():
        return
    shutil.rmtree(public_dir)
    print(f"  ✓ removed stale public downloads: {public_dir}")


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout."""
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def remote_branch_exists(repo_url: str, branch: str) -> bool:
    """Return True when the remote branch already exists."""
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", repo_url, branch],
        check=True,
        text=True,
        capture_output=True,
    )
    return bool(completed.stdout.strip())


def ensure_download_repo(repo_url: str, branch: str, workdir: Path) -> None:
    """Clone or update the dedicated download repository."""
    if not (workdir / ".git").exists():
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.parent.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["git", "clone"]
        if remote_branch_exists(repo_url, branch):
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([repo_url, str(workdir)])
        subprocess.run(clone_cmd, check=True, text=True)
        run_git(["checkout", "-B", branch], workdir)
        return

    run_git(["fetch", "origin"], workdir)
    run_git(["checkout", "-B", branch], workdir)
    if remote_branch_exists(repo_url, branch):
        run_git(["pull", "--ff-only", "origin", branch], workdir)


def clean_download_repo_paths(workdir: Path) -> None:
    """Remove previously published managed files."""
    for relative in DOWNLOAD_REPO_MANAGED_PATHS:
        target = workdir / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def copy_download_repo_assets(source_dir: Path, workdir: Path) -> None:
    """Copy generated download assets into the target repo."""
    for relative in DOWNLOAD_REPO_MANAGED_PATHS:
        source = source_dir / relative
        target = workdir / relative
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.exists():
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError(f"Expected generated asset missing: {source}")


def write_download_repo_readme(workdir: Path, manifest: dict) -> None:
    """Write a simple README for humans browsing the data repo."""
    generated_at = manifest.get("generated_at") or datetime.now(timezone.utc).isoformat()
    countries = len(manifest.get("countries") or [])
    diseases = len(manifest.get("diseases") or [])
    base = manifest.get("download_url_base") or ""
    readme = f"""# GlobalID Data Downloads

This repository stores the generated download artifacts for the GlobalID public site.

- Data version: `{generated_at}`
- Country datasets: `{countries}`
- Disease datasets: `{diseases}`
- Manifest: [`manifest.json`](./manifest.json)

The publishing pipeline copies:

- `countries/*.json`
- `countries/*.csv`
- `diseases/*.json`
- `diseases/*.csv`
- `manifest.json`

Primary public base configured during generation:

`{base}`
"""
    (workdir / "README.md").write_text(readme, encoding="utf-8")


def publish_download_assets(
    source_dir: Path,
    repo_url: str,
    branch: str,
    workdir: Path,
    commit_message: str,
) -> bool:
    """Publish generated downloads to a dedicated git repository."""
    if not repo_url.strip():
        raise RuntimeError(
            "Missing download repo URL. Set GITHUB_DATA_SHARE_REPO_URL or pass --download-repo-url."
        )
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing generated manifest: {manifest_path}. Export downloads before publishing."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_download_repo(repo_url, branch, workdir)
    clean_download_repo_paths(workdir)
    copy_download_repo_assets(source_dir, workdir)
    write_download_repo_readme(workdir, manifest)

    status = run_git(["status", "--short"], workdir)
    if not status:
        print("  No download repo changes to publish.")
        return False

    run_git(["add", "countries", "diseases", "manifest.json", "README.md"], workdir)
    run_git(["commit", "-m", commit_message], workdir)
    run_git(["push", "origin", branch], workdir)
    print(f"  ✓ published download assets to {repo_url} ({branch})")
    return True


def export_contains_download_records(source_dir: Path) -> bool:
    """Return True when the generated download manifest contains at least one record."""
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = list(manifest.get("countries") or []) + list(manifest.get("diseases") or [])
    return any(int(entry.get("record_count") or 0) > 0 for entry in entries)


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


def build_country_source_info(country_code: str, frequency_meta: dict | None = None) -> dict:
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
                "label": details.get("label") or scope_display_label(scope, country_code=country_code),
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


def build_source_columns(source_info: dict) -> dict:
    """Flatten source metadata for CSV rows."""
    sources = source_info.get("sources") or []
    return {
        "primary_source_scope": source_info.get("primary_scope"),
        "primary_source_label": source_info.get("primary_label"),
        "primary_source_url": source_info.get("primary_url"),
        "primary_source_type": source_info.get("primary_type"),
        "source_scopes": "; ".join(src.get("scope") or "" for src in sources if src.get("scope")),
        "source_labels": "; ".join(src.get("label") or "" for src in sources if src.get("label")),
        "source_urls": "; ".join(src.get("url") or "" for src in sources if src.get("url")),
        "source_types": "; ".join(src.get("type") or "" for src in sources if src.get("type")),
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
    total_cases = sum(int(country.get("total_cases") or 0) for country in countries_simple)
    total_deaths = sum(int(country.get("total_deaths") or 0) for country in countries_simple)
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
        country_name_en = country.get("name") or code or "Unknown"
        try:
            profile = get_country_profile(code) if code else None
        except Exception:
            profile = None
        country_name_zh = (
            ABOUT_COUNTRY_NAMES_ZH.get(code)
            or (profile.name_local if profile else None)
            or country_name_en
        )

        source_info = country.get("source_info") or {}
        sources = source_info.get("sources") or []
        primary_source = sources[0] if sources else {}
        primary_scope = primary_source.get("scope") or source_info.get("primary_scope") or "all"
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
                    "description_zh": ABOUT_SOURCE_DESCRIPTIONS_ZH.get((code, scope), description_en),
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
                "primary_source_label_en": primary_source.get("label") or source_info.get("primary_label"),
                "primary_source_label_zh": ABOUT_SOURCE_LABELS_ZH.get(
                    (code, primary_scope),
                    primary_source.get("label") or source_info.get("primary_label") or "",
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
    source_type_summary = " / ".join(type_name.upper() for type_name in dict.fromkeys(source_types)) or "WEB"
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
                "description_en": "Country and disease datasets are regenerated as JSON and CSV from the latest database state.",
                "description_zh": "国家与疾病数据集会依据最新数据库状态重新生成 JSON 和 CSV 导出文件。",
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


def build_country_download_rows(country_data: dict, generated_at: str, source_info: dict) -> list[dict]:
    """Flatten a country dataset into release-friendly CSV/JSON rows."""
    rows: list[dict] = []
    base_source_columns = build_source_columns(source_info)
    coverage = country_data.get("date_range") or {}
    dataset_id = (country_data.get("country_code") or "").lower()

    for series in (country_data.get("disease_series") or {}).values():
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        weekly_equiv = series.get("weekly_equiv_cases") or []
        deaths = series.get("deaths") or []
        incidence_rates = series.get("incidence_rates") or []
        incidence_sources = series.get("incidence_sources") or []
        mortality_rates = series.get("mortality_rates") or []

        for idx, date in enumerate(dates):
            rows.append(
                {
                    "dataset_kind": "country",
                    "dataset_id": dataset_id,
                    "dataset_slug": dataset_id,
                    "dataset_name": country_data.get("country_name"),
                    "country_code": country_data.get("country_code"),
                    "country_name": country_data.get("country_name"),
                    "disease_id": series.get("disease_id"),
                    "disease_name_en": series.get("name_en"),
                    "disease_name_zh": series.get("name_zh"),
                    "category": series.get("category"),
                    "date": date,
                    "year_month": date[:7] if date else None,
                    "cases": cases[idx] if idx < len(cases) else 0,
                    "weekly_equiv_cases": weekly_equiv[idx] if idx < len(weekly_equiv) else None,
                    "deaths": deaths[idx] if idx < len(deaths) else 0,
                    "incidence_rate_per_100k": incidence_rates[idx] if idx < len(incidence_rates) else None,
                    "incidence_rate_source": incidence_sources[idx] if idx < len(incidence_sources) else None,
                    "mortality_rate": mortality_rates[idx] if idx < len(mortality_rates) else None,
                    "coverage_start": coverage.get("start"),
                    "coverage_end": coverage.get("end"),
                    "generated_at": generated_at,
                    **base_source_columns,
                }
            )

    return rows


def build_disease_download_rows(
    disease_data: dict,
    generated_at: str,
    source_info_by_country: dict[str, dict],
    country_name_by_code: dict[str, str],
) -> list[dict]:
    """Flatten a disease dataset into release-friendly CSV/JSON rows."""
    rows: list[dict] = []
    dataset_id = disease_data.get("disease_id")
    dataset_slug = disease_data.get("slug")
    dataset_name = disease_data.get("name_en")

    all_dates: list[str] = []
    for country_code, series in (disease_data.get("country_series") or {}).items():
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        weekly_equiv = series.get("weekly_equiv_cases") or []
        deaths = series.get("deaths") or []
        incidence_rates = series.get("incidence_rates") or []
        incidence_sources = series.get("incidence_sources") or []
        source_columns = build_source_columns(source_info_by_country.get(country_code, {"sources": []}))
        all_dates.extend(dates)

        for idx, date in enumerate(dates):
            rows.append(
                {
                    "dataset_kind": "disease",
                    "dataset_id": dataset_id,
                    "dataset_slug": dataset_slug,
                    "dataset_name": dataset_name,
                    "country_code": country_code,
                    "country_name": country_name_by_code.get(country_code),
                    "disease_id": dataset_id,
                    "disease_name_en": disease_data.get("name_en"),
                    "disease_name_zh": disease_data.get("name_zh"),
                    "category": disease_data.get("category"),
                    "date": date,
                    "year_month": date[:7] if date else None,
                    "cases": cases[idx] if idx < len(cases) else 0,
                    "weekly_equiv_cases": weekly_equiv[idx] if idx < len(weekly_equiv) else None,
                    "deaths": deaths[idx] if idx < len(deaths) else 0,
                    "incidence_rate_per_100k": incidence_rates[idx] if idx < len(incidence_rates) else None,
                    "incidence_rate_source": incidence_sources[idx] if idx < len(incidence_sources) else None,
                    "mortality_rate": None,
                    "coverage_start": min(dates) if dates else None,
                    "coverage_end": max(dates) if dates else None,
                    "generated_at": generated_at,
                    **source_columns,
                }
            )

    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write rows to CSV with a stable schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=DOWNLOAD_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in DOWNLOAD_CSV_FIELDS})


async def ensure_standard_country_rows(session) -> None:
    """Seed canonical country rows required by the public site export."""
    for code in get_standard_country_codes():
        profile = get_country_profile(code)
        bootstrap = get_country_bootstrap_config(code)
        await session.execute(
            text(
                """
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
                """
            ),
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
                "disease_mapping_rules": json.dumps(bootstrap.get("disease_mapping_rules", {})),
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
    rows = await session.execute(
        text(
            """
            SELECT code, name, language, timezone
            FROM countries
            ORDER BY code
            """
        )
    )
    return [dict(row._mapping) for row in rows]


async def has_population_table(session) -> bool:
    row = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'population_records'
            ) AS has_table
            """
        )
    )
    result = row.fetchone()
    return bool(result[0]) if result else False


async def fetch_disease_records(session, country_code: str, use_population_table: bool) -> list[dict]:
    incidence_expr = "dr.incidence_rate"
    incidence_source_expr = (
        "CASE WHEN dr.incidence_rate IS NOT NULL THEN 'original_db' ELSE 'missing_population' END"
    )
    population_join = ""
    if use_population_table:
        incidence_expr = (
            """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN (dr.cases::double precision / pr.population) * 100000.0
                ELSE dr.incidence_rate
            END
            """
        )
        incidence_source_expr = (
            """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN 'wpp_computed'
                WHEN dr.incidence_rate IS NOT NULL
                    THEN 'original_db'
                ELSE 'missing_population'
            END
            """
        )
        population_join = (
            "LEFT JOIN population_records pr ON pr.country_id = dr.country_id "
            "AND pr.year = EXTRACT(YEAR FROM dr.time)::int"
        )

    rows = await session.execute(
        text(
            f"""
            SELECT
                timezone('UTC', dr.time)::date AS "date",
                to_char(timezone('UTC', dr.time), 'YYYY-MM') AS year_month,
                dm.disease_id,
                COALESCE(dr.cases, 0)::bigint AS cases,
                COALESCE(dr.deaths, 0)::bigint AS deaths,
                COALESCE(dr.recoveries, 0)::bigint AS recoveries,
                {incidence_expr} AS incidence_rate,
                {incidence_source_expr} AS incidence_rate_source,
                dr.mortality_rate AS mortality_rate,
                dr.data_quality AS data_quality
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            JOIN disease_mappings dm ON dm.local_name = dr.disease_id
                AND dm.country_code = c.code
            {population_join}
            WHERE c.code = :code
            ORDER BY timezone('UTC', dr.time)::date ASC, dm.disease_id
            """
        ),
        {"code": country_code},
    )
    result = []
    for row in rows:
        r = dict(row._mapping)
        r["date"] = r["date"].isoformat() if r["date"] else None
        r["cases"] = r["cases"] or 0
        r["deaths"] = r["deaths"] or 0
        r["incidence_rate"] = safe_float(r["incidence_rate"])
        r["incidence_rate_source"] = r.get("incidence_rate_source") or "missing_population"
        r["mortality_rate"] = safe_float(r["mortality_rate"])
        result.append(r)
    return result


async def fetch_disease_records_direct(session, country_code: str, use_population_table: bool) -> list[dict]:
    """
    Query disease_records joining diseases table to get the standard D-code.
    disease_records.disease_id is an integer FK to diseases.id;
    diseases.name holds the "D001" style code.
    """
    incidence_expr = "dr.incidence_rate"
    incidence_source_expr = (
        "CASE WHEN dr.incidence_rate IS NOT NULL THEN 'original_db' ELSE 'missing_population' END"
    )
    population_join = ""
    if use_population_table:
        incidence_expr = (
            """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN (dr.cases::double precision / pr.population) * 100000.0
                ELSE dr.incidence_rate
            END
            """
        )
        incidence_source_expr = (
            """
            CASE
                WHEN pr.population IS NOT NULL AND pr.population > 0 AND dr.cases IS NOT NULL
                    THEN 'wpp_computed'
                WHEN dr.incidence_rate IS NOT NULL
                    THEN 'original_db'
                ELSE 'missing_population'
            END
            """
        )
        population_join = (
            "LEFT JOIN population_records pr ON pr.country_id = dr.country_id "
            "AND pr.year = EXTRACT(YEAR FROM dr.time)::int"
        )

    rows = await session.execute(
        text(
            f"""
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
            """
        ),
        {"code": country_code},
    )
    result = []
    for row in rows:
        r = dict(row._mapping)
        r["date"] = r["date"].isoformat() if r["date"] else None
        r["cases"] = r["cases"] or 0
        r["deaths"] = r["deaths"] or 0
        r["incidence_rate"] = safe_float(r["incidence_rate"])
        r["incidence_rate_source"] = r.get("incidence_rate_source") or "missing_population"
        r["mortality_rate"] = safe_float(r["mortality_rate"])
        result.append(r)
    return result


async def fetch_country_frequency_meta(session, country_code: str) -> dict:
    """Infer source reporting frequency from raw (non-truncated) timestamps."""
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT timezone('UTC', dr.time)::date AS report_date
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            WHERE c.code = :code
            ORDER BY report_date ASC
            """
        ),
        {"code": country_code},
    )
    report_dates = [dict(row._mapping)["report_date"] for row in rows if dict(row._mapping).get("report_date")]
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
    rows = await session.execute(
        text(
            """
            SELECT
                r.id, r.title, r.report_type, r.status,
                r.period_start::date  AS period_start,
                r.period_end::date    AS period_end,
                r.created_at,
                r.quality_score,
                c.code                AS country_code,
                c.name                AS country_name
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.status = 'COMPLETED'
            ORDER BY r.created_at DESC
            """
        )
    )
    result = []
    for row in rows:
        r = dict(row._mapping)
        r["period_start"] = r["period_start"].isoformat() if r["period_start"] else None
        r["period_end"] = r["period_end"].isoformat() if r["period_end"] else None
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["quality_score"] = safe_float(r["quality_score"])
        result.append(r)
    return result


async def fetch_report_detail(session, report_id: int) -> dict | None:
    row = await session.execute(
        text(
            """
            SELECT
                r.id, r.title, r.report_type,
                r.period_start::date AS period_start,
                r.period_end::date   AS period_end,
                r.created_at, r.ai_model_used, r.quality_score,
                r.key_findings,
                c.code               AS country_code,
                c.name               AS country_name
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.id = :id
            """
        ),
        {"id": report_id},
    )
    rrow = row.fetchone()
    if not rrow:
        return None
    report = dict(rrow._mapping)
    report["period_start"] = report["period_start"].isoformat() if report["period_start"] else None
    report["period_end"] = report["period_end"].isoformat() if report["period_end"] else None
    report["created_at"] = report["created_at"].isoformat() if report["created_at"] else None
    report["quality_score"] = safe_float(report["quality_score"])

    # Fetch sections
    srows = await session.execute(
        text(
            """
            SELECT
                section_type, section_order, title,
                content, content_html
            FROM report_sections
            WHERE report_id = :id
            ORDER BY section_order
            """
        ),
        {"id": report_id},
    )
    report["sections"] = [dict(s._mapping) for s in srows]
    return report


# ─────────────────────────────────────────────────────────────
# Data processors
# ─────────────────────────────────────────────────────────────
def build_country_data(
    country_code: str,
    country_name: str,
    records: list[dict],
    diseases_by_id: dict,
    frequency_meta: dict | None = None,
) -> dict:
    """Build the full country JSON blob with time-series per disease."""
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
        series_incidence = [avg_or_none(points[d]["incidence_rates"]) for d in series_dates]
        series_incidence_sources = [dominant_value(points[d]["incidence_sources"]) for d in series_dates]
        series_mortality = [avg_or_none(points[d]["mortality_rates"]) for d in series_dates]
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
    )[:50]  # Cap at top 50 diseases for readability

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

    heatmap_labels = [
        disease_series[d]["name_en"] for d in heatmap_diseases
    ]

    return {
        "country_code": country_code,
        "country_name": country_name,
        "total_cases": total_cases,
        "total_deaths": total_deaths,
        "disease_count": len(by_disease),
        "frequency_meta": frequency_meta or {
            "source_frequency": "UNKNOWN",
            "canonical_frequency": "WEEKLY_EQUIVALENT_7D",
            "aggregation_rule": "normalize_counts_to_7_day_equivalent",
        },
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
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
        "heatmap": {
            "diseases": heatmap_diseases,
            "disease_labels": heatmap_labels,
            "months": all_months,
            "z": heatmap_z,
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
        series_incidence = [avg_or_none(points[d]["incidence_rates"]) for d in series_dates]
        series_incidence_sources = [dominant_value(points[d]["incidence_sources"]) for d in series_dates]

        country_series[country_code] = {
            "dates": series_dates,
            "cases": series_cases,
            "weekly_equiv_cases": calculate_weekly_equivalent(series_dates, series_cases),
            "deaths": series_deaths,
            "incidence_rates": series_incidence,
            "incidence_sources": series_incidence_sources,
            "total_cases": sum(series_cases),
            "total_deaths": sum(series_deaths),
        }

    all_disease_records = [r for recs in all_records_by_country.values() for r in recs if r["disease_id"] == disease_id]
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
    }


# ─────────────────────────────────────────────────────────────
# Main export
# ─────────────────────────────────────────────────────────────
async def export(
    output_dir: Path,
    download_output_dir: Path,
    manifest_output: Path,
    download_url_base: str,
    allow_empty_export: bool = False,
) -> None:
    await ensure_site_export_database_ready()
    generated_at = ""

    # Load static disease list from CSV (no DB needed)
    csv_path = ROOT / "configs" / "standard_diseases.csv"
    diseases = load_standard_diseases(csv_path)
    diseases_by_id = {d["disease_id"]: d for d in diseases}
    countries_simple: list[dict] = []
    country_exports: list[dict] = []
    disease_exports: list[dict] = []
    reports: list[dict] = []
    report_details: dict[int, dict] = {}

    async with get_db() as session:
        population_enabled = await has_population_table(session)
        if population_enabled:
            print("  Population table detected: incidence will use WPP-based computation")
        else:
            print("  Population table not found: incidence falls back to database values")

        # ── Countries ──
        countries = await fetch_countries(session)
        countries_simple = [
            {"code": c["code"], "name": c["name"], "language": c["language"]}
            for c in countries
        ]

        all_records_by_country: dict[str, list] = {}
        country_sources_by_code: dict[str, dict] = {}
        country_name_by_code = {c["code"]: c["name"] for c in countries}
        country_download_entries: list[dict] = []
        disease_download_entries: list[dict] = []
        for country in countries:
            code = country["code"]
            print(f"  Fetching records for {code}…")
            frequency_meta = await fetch_country_frequency_meta(session, code)
            country_source_info = build_country_source_info(code, frequency_meta)
            try:
                records = await fetch_disease_records(session, code, population_enabled)
                if not records:
                    await session.rollback()
                    records = await fetch_disease_records_direct(session, code, population_enabled)
            except Exception:
                await session.rollback()
                records = await fetch_disease_records_direct(session, code, population_enabled)

            all_records_by_country[code] = records
            country_sources_by_code[code] = country_source_info
            country_data = build_country_data(
                code, country["name"], records, diseases_by_id, frequency_meta
            )
            country_data["source_info"] = country_source_info
            # Augment countries_simple with stats
            for c in countries_simple:
                if c["code"] == code:
                    c["total_cases"] = country_data["total_cases"]
                    c["total_deaths"] = country_data["total_deaths"]
                    c["disease_count"] = country_data["disease_count"]
                    c["date_range"] = country_data["date_range"]
                    c["source_info"] = country_source_info

            country_exports.append(
                {
                    "code": code,
                    "country_name": country["name"],
                    "country_data": country_data,
                    "source_info": country_source_info,
                }
            )

        reports = await fetch_reports(session)
        total_record_count = sum(len(records) for records in all_records_by_country.values())
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
            country_data = country_export["country_data"]
            country_source_info = country_export["source_info"]
            country_data["generated_at"] = generated_at
            country_download_rows = build_country_download_rows(
                country_data, generated_at, country_source_info
            )
            country_download_payload = {
                "metadata": {
                    "dataset_kind": "country",
                    "dataset_id": code.lower(),
                    "dataset_slug": code.lower(),
                    "dataset_name": country_name,
                    "generated_at": generated_at,
                    "record_count": len(country_download_rows),
                    "date_range": country_data.get("date_range"),
                    "frequency_meta": country_data.get("frequency_meta"),
                    "source_info": country_source_info,
                    "comparison_basis": country_data.get("comparison_basis"),
                    "incidence_rate_basis": country_data.get("incidence_rate_basis"),
                },
                "summary": {
                    "country_code": code,
                    "country_name": country_name,
                    "total_cases": country_data.get("total_cases"),
                    "total_deaths": country_data.get("total_deaths"),
                    "disease_count": country_data.get("disease_count"),
                },
                "records": country_download_rows,
            }
            country_export["download_rows"] = country_download_rows
            country_export["download_payload"] = country_download_payload
            country_download_entries.append(
                {
                    "kind": "country",
                    "id": code.lower(),
                    "code": code,
                    "name": country_name,
                    "generated_at": generated_at,
                    "record_count": len(country_download_rows),
                    "date_range": country_data.get("date_range"),
                    "json_path": build_download_url(
                        download_url_base, f"countries/{code.lower()}.json"
                    ),
                    "csv_path": build_download_url(
                        download_url_base, f"countries/{code.lower()}.csv"
                    ),
                    "relative_json_path": f"countries/{code.lower()}.json",
                    "relative_csv_path": f"countries/{code.lower()}.csv",
                    "source_info": country_source_info,
                }
            )

        # ── Per-disease files ──
        for disease in diseases:
            did = disease["disease_id"]
            disease_data = build_disease_data(did, disease, all_records_by_country)
            disease_countries = sorted((disease_data.get("country_series") or {}).keys())
            disease_source_info = []
            for country_code in disease_countries:
                country_source = dict(country_sources_by_code.get(country_code, {}))
                country_source["country_name"] = country_name_by_code.get(country_code)
                disease_source_info.append(country_source)
            disease_data["generated_at"] = generated_at
            disease_data["source_info"] = disease_source_info
            disease_download_rows = build_disease_download_rows(
                disease_data,
                generated_at,
                country_sources_by_code,
                country_name_by_code,
            )
            disease_download_payload = {
                "metadata": {
                    "dataset_kind": "disease",
                    "dataset_id": did,
                    "dataset_slug": disease.get("slug"),
                    "dataset_name": disease.get("name_en"),
                    "generated_at": generated_at,
                    "record_count": len(disease_download_rows),
                    "country_count": len(disease_countries),
                    "countries": disease_countries,
                    "source_info": disease_source_info,
                },
                "summary": {
                    "disease_id": did,
                    "name_en": disease.get("name_en"),
                    "name_zh": disease.get("name_zh"),
                    "category": disease.get("category"),
                    "total_cases": disease_data.get("total_cases"),
                    "total_deaths": disease_data.get("total_deaths"),
                    "global_monthly": disease_data.get("global_monthly"),
                },
                "records": disease_download_rows,
            }
            disease_exports.append(
                {
                    "disease_id": did,
                    "disease_data": disease_data,
                    "download_rows": disease_download_rows,
                    "download_payload": disease_download_payload,
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
                    "record_count": len(disease_download_rows),
                    "country_count": len(disease_countries),
                    "countries": [
                        {
                            "code": country_code,
                            "name": country_name_by_code.get(country_code),
                        }
                        for country_code in disease_countries
                    ],
                    "json_path": build_download_url(
                        download_url_base, f"diseases/{did.lower()}.json"
                    ),
                    "csv_path": build_download_url(
                        download_url_base, f"diseases/{did.lower()}.csv"
                    ),
                    "relative_json_path": f"diseases/{did.lower()}.json",
                    "relative_csv_path": f"diseases/{did.lower()}.csv",
                    "source_info": disease_source_info,
                }
            )

        for rep in reports:
            detail = await fetch_report_detail(session, rep["id"])
            if detail:
                report_details[rep["id"]] = detail

    remove_stale_public_downloads(download_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "countries").mkdir(exist_ok=True)
    (output_dir / "diseases").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)
    download_output_dir.mkdir(parents=True, exist_ok=True)
    (download_output_dir / "countries").mkdir(exist_ok=True)
    (download_output_dir / "diseases").mkdir(exist_ok=True)
    clean_generated_dir(download_output_dir / "countries")
    clean_generated_dir(download_output_dir / "diseases")

    # Write disease index
    (output_dir / "diseases" / "index.json").write_text(
        json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ diseases/index.json ({len(diseases)} diseases)")

    for country_export in country_exports:
        code = country_export["code"]
        country_data = country_export["country_data"]
        country_download_rows = country_export["download_rows"]
        country_download_payload = country_export["download_payload"]
        (output_dir / "countries" / f"{code.lower()}.json").write_text(
            json.dumps(country_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        country_json_path = download_output_dir / "countries" / f"{code.lower()}.json"
        country_csv_path = download_output_dir / "countries" / f"{code.lower()}.csv"
        country_json_path.write_text(
            json.dumps(country_download_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_csv(country_csv_path, country_download_rows)
        print(f"  ✓ countries/{code.lower()}.json ({len(all_records_by_country[code])} records)")

    for disease_export in disease_exports:
        did = disease_export["disease_id"]
        disease_data = disease_export["disease_data"]
        disease_download_rows = disease_export["download_rows"]
        disease_download_payload = disease_export["download_payload"]
        (output_dir / "diseases" / f"{did.lower()}.json").write_text(
            json.dumps(disease_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        disease_json_path = download_output_dir / "diseases" / f"{did.lower()}.json"
        disease_csv_path = download_output_dir / "diseases" / f"{did.lower()}.csv"
        disease_json_path.write_text(
            json.dumps(disease_download_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_csv(disease_csv_path, disease_download_rows)
    print(f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)")
    print(
        "  ✓ downloads/countries/*.json,csv "
        f"({len(country_download_entries)} countries)"
    )
    print(
        "  ✓ downloads/diseases/*.json,csv "
        f"({len(disease_download_entries)} diseases)"
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
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ meta.json")

    about_snapshot = build_about_snapshot(
        countries_simple=countries_simple,
        diseases=diseases,
        reports=reports,
        generated_at=generated_at,
    )
    (output_dir / "about.json").write_text(
        json.dumps(about_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ about.json")

    downloads_manifest = {
        "generated_at": generated_at,
        "includes_source_info": True,
        "formats": ["json", "csv"],
        "download_url_base": download_url_base,
        "countries": country_download_entries,
        "diseases": disease_download_entries,
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(
        json.dumps(downloads_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (download_output_dir / "manifest.json").write_text(
        json.dumps(downloads_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✓ downloads manifest")
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
        "--download-output",
        type=Path,
        default=DEFAULT_DOWNLOAD_OUTPUT,
        help=f"Download assets output directory (default: {DEFAULT_DOWNLOAD_OUTPUT})",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=DEFAULT_DOWNLOAD_MANIFEST,
        help=f"Frontend manifest output path (default: {DEFAULT_DOWNLOAD_MANIFEST})",
    )
    parser.add_argument(
        "--download-url-base",
        default=DEFAULT_DOWNLOAD_URL_BASE,
        help=(
            "Public base URL used in the generated download manifest "
            f"(default: {DEFAULT_DOWNLOAD_URL_BASE})"
        ),
    )
    parser.add_argument(
        "--publish-downloads",
        action="store_true",
        help="Publish generated download assets to the dedicated git repository after export",
    )
    parser.add_argument(
        "--download-repo-url",
        default=DEFAULT_DOWNLOAD_REPO_URL,
        help=f"Target git repository URL for download assets (default: {DEFAULT_DOWNLOAD_REPO_URL})",
    )
    parser.add_argument(
        "--download-repo-branch",
        default=DEFAULT_DOWNLOAD_REPO_BRANCH,
        help=f"Target git branch for download assets (default: {DEFAULT_DOWNLOAD_REPO_BRANCH})",
    )
    parser.add_argument(
        "--download-repo-workdir",
        type=Path,
        default=DEFAULT_DOWNLOAD_REPO_WORKDIR,
        help=(
            "Temporary local checkout path for the download repo "
            f"(default: {DEFAULT_DOWNLOAD_REPO_WORKDIR})"
        ),
    )
    parser.add_argument(
        "--download-commit-message",
        default=DEFAULT_DOWNLOAD_COMMIT_MESSAGE,
        help=(
            "Git commit message used when publishing download assets "
            f"(default: {DEFAULT_DOWNLOAD_COMMIT_MESSAGE})"
        ),
    )
    parser.add_argument(
        "--allow-empty-download-publish",
        action="store_true",
        help="Allow publishing download assets even when the generated export has zero records",
    )
    parser.add_argument(
        "--allow-empty-export",
        action="store_true",
        help="Allow overwriting site data even when the database currently exports zero disease records",
    )
    args = parser.parse_args()
    print(f"Exporting site data to {args.output} …")
    print(f"Writing download assets to {args.download_output} …")
    print(f"Writing download manifest to {args.manifest_output} …\n")
    asyncio.run(
        export(
            args.output,
            args.download_output,
            args.manifest_output,
            args.download_url_base,
            args.allow_empty_export,
        )
    )
    if args.publish_downloads:
        if not export_contains_download_records(args.download_output) and not args.allow_empty_download_publish:
            raise RuntimeError(
                "Refusing to publish empty download assets because the current export has zero records. "
                "Import data first, or pass --allow-empty-download-publish if this is intentional."
            )
        print(
            "\nPublishing generated download assets to "
            f"{args.download_repo_url} ({args.download_repo_branch}) …"
        )
        publish_download_assets(
            args.download_output,
            args.download_repo_url,
            args.download_repo_branch,
            args.download_repo_workdir,
            args.download_commit_message,
        )


if __name__ == "__main__":
    main()
