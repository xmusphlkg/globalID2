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


async def fetch_disease_records(session, country_code: str) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT
                date_trunc('month', dr.time)::date AS "date",
                to_char(dr.time, 'YYYY-MM') AS year_month,
                dm.disease_id,
                dr.cases,
                dr.deaths,
                dr.recoveries,
                dr.incidence_rate,
                dr.mortality_rate,
                dr.data_quality
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            JOIN disease_mappings dm ON dm.local_name = dr.disease_id
                AND dm.country_code = c.code
            WHERE c.code = :code
            ORDER BY dr.time ASC, dm.disease_id
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
        r["mortality_rate"] = safe_float(r["mortality_rate"])
        result.append(r)
    return result


async def fetch_disease_records_direct(session, country_code: str) -> list[dict]:
    """
    Query disease_records joining diseases table to get the standard D-code.
    disease_records.disease_id is an integer FK to diseases.id;
    diseases.name holds the "D001" style code.
    """
    rows = await session.execute(
        text(
            """
            SELECT
                date_trunc('month', dr.time)::date AS "date",
                to_char(dr.time, 'YYYY-MM') AS year_month,
                d.name                 AS disease_id,
                dr.cases,
                dr.deaths,
                dr.recoveries,
                dr.incidence_rate,
                dr.mortality_rate,
                dr.data_quality
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            JOIN diseases d ON d.id = dr.disease_id
            WHERE c.code = :code
            ORDER BY dr.time ASC, d.name
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
        r["mortality_rate"] = safe_float(r["mortality_rate"])
        result.append(r)
    return result


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
        recs_sorted = sorted(recs, key=lambda x: x["date"] or "")
        disease_info = diseases_by_id.get(disease_id, {})
        disease_series[disease_id] = {
            "disease_id": disease_id,
            "name_en": disease_info.get("name_en", disease_id),
            "name_zh": disease_info.get("name_zh", disease_id),
            "category": disease_info.get("category", "Unknown"),
            "slug": disease_info.get("slug", disease_id.lower()),
            "dates": [r["date"] for r in recs_sorted],
            "cases": [r["cases"] for r in recs_sorted],
            "deaths": [r["deaths"] for r in recs_sorted],
            "incidence_rates": [r["incidence_rate"] for r in recs_sorted],
            "mortality_rates": [r["mortality_rate"] for r in recs_sorted],
            "total_cases": sum(r["cases"] for r in recs_sorted),
            "total_deaths": sum(r["deaths"] for r in recs_sorted),
            "latest_cases": recs_sorted[-1]["cases"] if recs_sorted else 0,
            "latest_deaths": recs_sorted[-1]["deaths"] if recs_sorted else 0,
        }

    # Heatmap data: diseases (rows) × months (cols)
    all_months = sorted({r["year_month"] for r in records if r["year_month"]})
    heatmap_diseases = sorted(
        disease_series.keys(),
        key=lambda d: disease_series[d]["total_cases"],
        reverse=True,
    )[:50]  # Cap at top 50 diseases for readability

    heatmap_z = []
    for did in heatmap_diseases:
        recs_for_disease = {r["year_month"]: r for r in by_disease[did]}
        row_z = []
        for month in all_months:
            rec = recs_for_disease.get(month)
            cases = rec["cases"] if rec else 0
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
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
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
        disease_records.sort(key=lambda x: x["date"] or "")
        country_series[country_code] = {
            "dates": [r["date"] for r in disease_records],
            "cases": [r["cases"] for r in disease_records],
            "deaths": [r["deaths"] for r in disease_records],
            "incidence_rates": [r["incidence_rate"] for r in disease_records],
            "total_cases": sum(r["cases"] for r in disease_records),
            "total_deaths": sum(r["deaths"] for r in disease_records),
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
            try:
                records = await fetch_disease_records(session, code)
                if not records:
                    await session.rollback()
                    records = await fetch_disease_records_direct(session, code)
            except Exception:
                await session.rollback()
                records = await fetch_disease_records_direct(session, code)

            all_records_by_country[code] = records
            country_data = build_country_data(
                code, country["name"], records, diseases_by_id
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
