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
DEFAULT_DOWNLOAD_REPO_URL = "git@github.com:xmusphlkg/globalID2_data_download.git"
DEFAULT_DOWNLOAD_REPO_BRANCH = "main"
DEFAULT_DOWNLOAD_REPO_WORKDIR = Path("/tmp/globalid2-data-download-publish")
DEFAULT_DOWNLOAD_COMMIT_MESSAGE = "chore: update generated data downloads"
DOWNLOAD_REPO_MANAGED_PATHS = ("countries", "diseases", "manifest.json")
DEFAULT_DOWNLOAD_URL_BASE = (
    "https://raw.githubusercontent.com/xmusphlkg/globalID2_data_download/main"
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

- Generated at: `{generated_at}`
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
) -> None:
    await ensure_site_export_database_ready()
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
    generated_at = datetime.now(timezone.utc).isoformat()

    # Load static disease list from CSV (no DB needed)
    csv_path = ROOT / "configs" / "standard_diseases.csv"
    diseases = load_standard_diseases(csv_path)
    diseases_by_id = {d["disease_id"]: d for d in diseases}

    # Write disease index
    (output_dir / "diseases" / "index.json").write_text(
        json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ diseases/index.json ({len(diseases)} diseases)")

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
            country_data["generated_at"] = generated_at
            country_data["source_info"] = country_source_info
            # Augment countries_simple with stats
            for c in countries_simple:
                if c["code"] == code:
                    c["total_cases"] = country_data["total_cases"]
                    c["total_deaths"] = country_data["total_deaths"]
                    c["disease_count"] = country_data["disease_count"]
                    c["date_range"] = country_data["date_range"]
                    c["source_info"] = country_source_info

            (output_dir / "countries" / f"{code.lower()}.json").write_text(
                json.dumps(country_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  ✓ countries/{code.lower()}.json ({len(records)} records)")

            country_download_rows = build_country_download_rows(
                country_data, generated_at, country_source_info
            )
            country_download_payload = {
                "metadata": {
                    "dataset_kind": "country",
                    "dataset_id": code.lower(),
                    "dataset_slug": code.lower(),
                    "dataset_name": country["name"],
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
                    "country_name": country["name"],
                    "total_cases": country_data.get("total_cases"),
                    "total_deaths": country_data.get("total_deaths"),
                    "disease_count": country_data.get("disease_count"),
                },
                "records": country_download_rows,
            }
            country_json_path = download_output_dir / "countries" / f"{code.lower()}.json"
            country_csv_path = download_output_dir / "countries" / f"{code.lower()}.csv"
            country_json_path.write_text(
                json.dumps(country_download_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_csv(country_csv_path, country_download_rows)
            country_download_entries.append(
                {
                    "kind": "country",
                    "id": code.lower(),
                    "code": code,
                    "name": country["name"],
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
            (output_dir / "diseases" / f"{did.lower()}.json").write_text(
                json.dumps(disease_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
            disease_json_path = download_output_dir / "diseases" / f"{did.lower()}.json"
            disease_csv_path = download_output_dir / "diseases" / f"{did.lower()}.csv"
            disease_json_path.write_text(
                json.dumps(disease_download_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_csv(disease_csv_path, disease_download_rows)
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
        print(f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)")
        print(
            "  ✓ downloads/countries/*.json,csv "
            f"({len(country_download_entries)} countries)"
        )
        print(
            "  ✓ downloads/diseases/*.json,csv "
            f"({len(disease_download_entries)} diseases)"
        )

        # ── Reports ──
        reports = await fetch_reports(session)
        (output_dir / "reports" / "index.json").write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  ✓ reports/index.json ({len(reports)} reports)")

        for rep in reports:
            detail = await fetch_report_detail(session, rep["id"])
            if detail:
                (output_dir / "reports" / f"{rep['id']}.json").write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        print(f"  ✓ reports/<id>.json ({len(reports)} files)")

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
