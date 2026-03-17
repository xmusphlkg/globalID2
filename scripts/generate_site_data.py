#!/usr/bin/env python3
"""
Generate static JSON data files for the Astro-based report site.

Usage:
    python scripts/generate_site_data.py
    python scripts/generate_site_data.py --output astro-site/src/data

Reads from the PostgreSQL database and writes structured JSON files that
the Astro build process consumes at build time.
"""

import argparse
import asyncio
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# Make sure project root is on PYTHONPATH
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config import get_config  # noqa: E402
from src.core.database import get_db  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Config defaults
# ─────────────────────────────────────────────────────────────
DEFAULT_OUTPUT = ROOT / "astro-site" / "src" / "data"


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
                dr.time::date AS "date",
                to_char(dr.time, 'YYYY-MM') AS year_month,
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
            ORDER BY dr.time::date ASC, dm.disease_id
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
                dr.time::date AS "date",
                to_char(dr.time, 'YYYY-MM') AS year_month,
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
            ORDER BY dr.time::date ASC, d.name
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
            SELECT DISTINCT dr.time::date AS report_date
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
async def export(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "countries").mkdir(exist_ok=True)
    (output_dir / "diseases").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)

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
        for country in countries:
            code = country["code"]
            print(f"  Fetching records for {code}…")
            frequency_meta = await fetch_country_frequency_meta(session, code)
            try:
                records = await fetch_disease_records(session, code, population_enabled)
                if not records:
                    await session.rollback()
                    records = await fetch_disease_records_direct(session, code, population_enabled)
            except Exception:
                await session.rollback()
                records = await fetch_disease_records_direct(session, code, population_enabled)

            all_records_by_country[code] = records
            country_data = build_country_data(
                code, country["name"], records, diseases_by_id, frequency_meta
            )
            # Augment countries_simple with stats
            for c in countries_simple:
                if c["code"] == code:
                    c["total_cases"] = country_data["total_cases"]
                    c["total_deaths"] = country_data["total_deaths"]
                    c["disease_count"] = country_data["disease_count"]
                    c["date_range"] = country_data["date_range"]

            (output_dir / "countries" / f"{code.lower()}.json").write_text(
                json.dumps(country_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  ✓ countries/{code.lower()}.json ({len(records)} records)")

        # ── Per-disease files ──
        for disease in diseases:
            did = disease["disease_id"]
            disease_data = build_disease_data(did, disease, all_records_by_country)
            (output_dir / "diseases" / f"{did.lower()}.json").write_text(
                json.dumps(disease_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(f"  ✓ diseases/{diseases[0]['disease_id'].lower()}.json … ({len(diseases)} files)")

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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_countries": len(countries_simple),
        "total_diseases": len(diseases),
        "total_reports": len(reports),
        "countries": countries_simple,
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ meta.json")
    print(f"\nDone. Data written to: {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export site data to JSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    print(f"Exporting site data to {args.output} …\n")
    asyncio.run(export(args.output))


if __name__ == "__main__":
    main()
