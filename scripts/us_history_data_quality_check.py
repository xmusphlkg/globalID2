#!/usr/bin/env python3
"""Validate US strict historical import against the import-ready CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db


REPORT_TIME_UTC = time(hour=12)
DEFAULT_INPUT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_import_ready_strict.csv"
)
DEFAULT_SUMMARY = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_import_ready_strict_quality_check.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate US strict historical import in disease_records.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Import-ready CSV used for DB import.")
    parser.add_argument("--output-summary", type=Path, default=DEFAULT_SUMMARY, help="Summary JSON output.")
    return parser.parse_args()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def parse_int(value: object) -> int | None:
    text = normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    return int(numeric)


def load_expected_rows(csv_path: Path) -> dict[tuple[str, str], dict[str, object]]:
    expected: dict[tuple[str, str], dict[str, object]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for idx, row in enumerate(csv.DictReader(handle)):
            date_text = normalize_text(row.get("Date"))
            disease_code = normalize_text(row.get("CanonicalDiseaseId"))
            cases = parse_int(row.get("Cases"))
            if not date_text or not disease_code or cases is None:
                continue
            expected[(date_text, disease_code)] = {
                "cases": cases,
                "label": normalize_text(row.get("Label")),
                "bucket": normalize_text(row.get("HandlingBucket")),
                "selected_reason": normalize_text(row.get("SelectedReason")),
                "row_index": idx,
            }
    return expected


async def main_async(args: argparse.Namespace) -> int:
    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    expected = load_expected_rows(args.input)
    if not expected:
        raise RuntimeError("No expected rows loaded from import-ready CSV.")

    dates = sorted({key[0] for key in expected})
    disease_codes = sorted({key[1] for key in expected})
    date_min = dates[0]
    date_max = dates[-1]

    async with get_db() as db:
        country_result = await db.execute(text("SELECT id FROM countries WHERE code = 'US'"))
        country_row = country_result.fetchone()
        if not country_row:
            raise RuntimeError("Country US not found in database.")
        country_id = int(country_row[0])

        result = await db.execute(
            text("SELECT id, name FROM diseases WHERE name = ANY(:codes)"),
            {"codes": disease_codes},
        )
        db_id_to_code = {int(db_id): normalize_text(name) for db_id, name in result.fetchall()}
        if len(db_id_to_code) != len(disease_codes):
            missing_codes = sorted(set(disease_codes) - set(db_id_to_code.values()))
            raise RuntimeError(f"Missing disease code(s) in diseases table: {missing_codes}")

        rows_result = await db.execute(
            text(
                """
                SELECT
                    dr.time::date AS report_date,
                    d.name AS disease_code,
                    dr.cases,
                    dr.deaths,
                    dr.data_source,
                    dr.metadata
                FROM disease_records dr
                JOIN diseases d ON d.id = dr.disease_id
                WHERE dr.country_id = :country_id
                  AND dr.time >= :date_min
                  AND dr.time <= :date_max
                  AND d.name = ANY(:codes)
                """
            ),
            {
                "country_id": country_id,
                "date_min": datetime.combine(datetime.strptime(date_min, "%Y-%m-%d").date(), REPORT_TIME_UTC, tzinfo=timezone.utc),
                "date_max": datetime.combine(datetime.strptime(date_max, "%Y-%m-%d").date(), REPORT_TIME_UTC, tzinfo=timezone.utc),
                "codes": disease_codes,
            },
        )

        actual: dict[tuple[str, str], dict[str, object]] = {}
        source_counts: Counter[str] = Counter()
        negative_rows = 0
        deaths_exceed_rows = 0

        for report_date, disease_code, cases, deaths, data_source, metadata in rows_result.fetchall():
            key = (report_date.isoformat(), normalize_text(disease_code))
            actual[key] = {
                "cases": int(cases or 0),
                "deaths": int(deaths or 0),
                "data_source": normalize_text(data_source),
                "metadata": metadata or {},
            }
            source_counts[normalize_text(data_source)] += 1
            if int(cases or 0) < 0 or int(deaths or 0) < 0:
                negative_rows += 1
            if int(cases or 0) > 0 and int(deaths or 0) > int(cases or 0):
                deaths_exceed_rows += 1

    missing = []
    mismatched = []
    for key, exp in expected.items():
        act = actual.get(key)
        if act is None:
            missing.append(
                {
                    "date": key[0],
                    "disease_code": key[1],
                    "expected_cases": exp["cases"],
                    "label": exp["label"],
                }
            )
            continue
        if int(act["cases"]) != int(exp["cases"]):
            mismatched.append(
                {
                    "date": key[0],
                    "disease_code": key[1],
                    "expected_cases": exp["cases"],
                    "actual_cases": act["cases"],
                    "label": exp["label"],
                }
            )

    imported_exact = len(expected) - len(missing) - len(mismatched)
    summary = {
        "input_csv": str(args.input),
        "expected_rows": len(expected),
        "db_rows_in_expected_window": len(actual),
        "matched_rows": imported_exact,
        "missing_rows": len(missing),
        "case_mismatch_rows": len(mismatched),
        "negative_rows_in_window": negative_rows,
        "deaths_exceed_cases_rows_in_window": deaths_exceed_rows,
        "date_range": {"min": date_min, "max": date_max},
        "disease_coverage": len(disease_codes),
        "data_source_counts_in_window": dict(source_counts.most_common()),
        "sample_missing": missing[:20],
        "sample_mismatched": mismatched[:20],
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Expected rows: {len(expected)}")
    print(f"Matched rows: {imported_exact}")
    print(f"Missing rows: {len(missing)}")
    print(f"Case mismatches: {len(mismatched)}")
    print(f"Negative rows in window: {negative_rows}")
    print(f"Deaths > cases rows in window: {deaths_exceed_rows}")
    print(f"Summary JSON: {args.output_summary}")

    return 1 if missing or mismatched else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
