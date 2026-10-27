#!/usr/bin/env python3
"""Restore official NINDSS state counts from archived national location responses.

Archive responses already contain source state counts. Preserve only explicitly
reported counts (including zero), never split national totals or fill blanks.
The import writes both legacy projections and registered source observations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_state_count(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a notification count")  # noqa: TRY004 -- invalid source value
    raw = str(value).strip().strip("'\"").replace(",", "")
    if re.fullmatch(r"<\s*\d+(?:\.\d+)?", raw):
        return None  # Source suppression is unknown, never zero.
    raw = raw.removesuffix("L")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid notification count: {value!r}") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError(f"Invalid notification count: {value!r}")
    return int(number)


def restore_state_rows(archive_root: Path, start_year: int, end_year: int):
    from src.data.crawlers.au import AU_STATE_SUBDIVISIONS, normalize_au_state_code
    from src.data.processors.au import AUMonthlyUpdater

    updaters = {
        code: AUMonthlyUpdater(country_code=code) for code in AU_STATE_SUBDIVISIONS
    }
    rows = {code: {} for code in updaters}
    files = 0
    for year in range(start_year, end_year + 1):
        for month_dir in sorted((archive_root / str(year)).glob("[01][0-9]")):
            month = int(month_dir.name)
            if not 1 <= month <= 12:
                continue
            for path in sorted(month_dir.glob("*.json")):
                payload = json.loads(path.read_text())
                disease = str(
                    payload.get("disease") or payload.get("Disease") or ""
                ).strip()
                counts = payload.get("parsed_counts")
                if not disease or not isinstance(counts, dict):
                    raise ValueError(f"Archive has no disease or state counts: {path}")
                files += 1
                for label, value in counts.items():
                    code = normalize_au_state_code(label)
                    if code is None:
                        if (
                            str(label).upper()
                            not in {"AUS", "AU", "UNKNOWN", "TOTAL", "ALL"}
                            and value
                        ):
                            raise ValueError(f"Unknown populated state label: {label}")
                        continue
                    parsed = parse_state_count(value)
                    if parsed is None and value is None:
                        continue
                    row = updaters[code]._normalized_output_row(
                        year=year,
                        month=month,
                        disease=disease,
                        cases=parsed or 0,
                        source_file=str(path.relative_to(ROOT))
                        if path.is_relative_to(ROOT)
                        else str(path),
                    )
                    if parsed is None:
                        row["Cases"] = str(value).strip().strip("'\"")
                        row["SuppressionReason"] = "source_value_suppressed"
                    row.update(
                        {
                            "DatasetStatus": "closed_revisable",
                            "IsProvisional": "false",
                            "RevisionSemantics": "authoritative_revision",
                            "AuthoritativeRevision": "true",
                        }
                    )
                    key = (row["Date"], disease)
                    if key in rows[code] and rows[code][key]["Cases"] != row["Cases"]:
                        raise ValueError(f"Conflicting archive rows: {code}/{key}")
                    rows[code][key] = row
    return {code: list(values.values()) for code, values in rows.items()}, files


async def apply_rows(rows_by_code):
    from src.core.database import dispose_database, get_db
    from src.data.processors.au import AUMonthlyUpdater
    from src.services.crawl_service import CrawlService

    results = {}
    try:
        for code, rows in rows_by_code.items():
            updater = AUMonthlyUpdater(country_code=code)
            updater._validate_subdivision_rows(rows)
            if not rows:
                continue
            # Preserve existing CSV history as well as the restored source rows.
            current = (
                updater._load_rows(updater.output_csv)
                if updater.output_csv.exists()
                else []
            )
            merged = {(row["Date"], row["RawDiseaseLabel"]): row for row in current}
            merged.update({(row["Date"], row["RawDiseaseLabel"]): row for row in rows})
            updater._write_rows_to_output_csv(list(merged.values()))
            async with get_db() as db:
                result = await CrawlService._import_rows_with_series(
                    db,
                    updater,
                    rows,
                    db_latest_date=await updater.get_db_latest_date(db),
                    source_latest_date=updater._latest_row_date(rows),
                    force=True,
                )
                results[code] = {
                    "upserted": result.inserted_or_updated,
                    "unmapped": result.skipped_unmapped,
                }
            print(json.dumps({code: results[code]}), flush=True)
    finally:
        await dispose_database()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=ROOT / "data/raw/au")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "data/processed/au/subdivision_backfill_report.json",
    )
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("start-year must be before end-year")
    rows, files = restore_state_rows(args.archive_root, args.start_year, args.end_year)
    coverage = {
        code: {
            "rows": len(items),
            "first_date": min((r["Date"] for r in items), default=None),
            "last_date": max((r["Date"] for r in items), default=None),
        }
        for code, items in rows.items()
    }
    results = asyncio.run(apply_rows(rows)) if args.apply else {}
    report = {
        "applied": args.apply,
        "source_files": files,
        "coverage": coverage,
        "import_results": results,
        "source_url": "https://nindss.health.gov.au/pbi-dashboard/",
        "missing_policy": "only explicit source state counts are imported; no allocation of national totals",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
