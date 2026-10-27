#!/usr/bin/env python3
"""Import official Chinese province and Australian state annual population.

Uses only a location's own population in the same calendar year. ABS June ERP
is preferred; for a year without June, the latest observed quarter is retained
and explicitly identified in metadata. There is no interpolation or carry-forward.
Run without --apply to review coverage; --apply performs an idempotent upsert.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import csv
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "external-data/subdivision-population"
ABS_URL = "https://www.abs.gov.au/statistics/people/population/national-state-and-territory-population/mar-2026/310104.xlsx"
ABS_STATES = {
    "New South Wales": "AU-NSW",
    "Victoria": "AU-VIC",
    "Queensland": "AU-QLD",
    "South Australia": "AU-SA",
    "Western Australia": "AU-WA",
    "Tasmania": "AU-TAS",
    "Northern Territory": "AU-NT",
    "Australian Capital Territory": "AU-ACT",
}


@dataclass(frozen=True)
class SubdivisionPopulation:
    country_code: str
    year: int
    population: int
    source: str
    reference_date: str
    source_url: str
    basis: str


def validate_rows(rows: list[SubdivisionPopulation]) -> list[SubdivisionPopulation]:
    seen = set()
    for row in rows:
        if row.country_code not in {"CN", "BR"} and not row.country_code.startswith(
            ("CN-", "AU-", "BR-")
        ):
            raise ValueError(f"Not a supported subdivision: {row.country_code}")
        if not math.isfinite(row.population) or row.population <= 0:
            raise ValueError(f"Invalid population: {row.country_code}/{row.year}")
        reference = date.fromisoformat(row.reference_date)
        if reference.year != row.year:
            raise ValueError("Reference date must be in the denominator year")
        key = (row.country_code, row.year)
        if key in seen:
            raise ValueError(f"Duplicate population: {key}")
        seen.add(key)
    return sorted(rows, key=lambda row: (row.country_code, row.year))


def load_cn_population(path: Path) -> list[SubdivisionPopulation]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            SubdivisionPopulation(
                country_code=row["country_code"],
                year=int(row["year"]),
                population=int(row["population"]),
                source=row["source"],
                reference_date=row["reference_date"],
                source_url=row["source_url"],
                basis=row.get("basis") or "year_end_resident_population",
            )
            for row in csv.DictReader(handle)
        ]
    return validate_rows(rows)


def load_abs_population(
    path: Path, source_url: str = ABS_URL
) -> list[SubdivisionPopulation]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Data1"]
        records = iter(sheet.values)
        labels = next(records)
        units = next(records)
        columns = {}
        for index, label in enumerate(labels):
            parts = [
                part.strip() for part in str(label or "").split(";") if part.strip()
            ]
            if (
                len(parts) == 3
                and parts[:2] == ["Estimated Resident Population", "Persons"]
                and parts[2] in ABS_STATES
            ):
                if units[index] != "Persons":
                    raise ValueError("ABS population units must be persons")
                columns[index] = ABS_STATES[parts[2]]
        if set(columns.values()) != set(ABS_STATES.values()):
            raise ValueError(
                "ABS workbook must contain all eight states and territories"
            )
        candidates: dict[tuple[str, int], list[SubdivisionPopulation]] = {}
        for record in records:
            reference = record[0]
            if not isinstance(reference, datetime):
                continue
            for index, code in columns.items():
                value = record[index]
                if value is None:
                    continue
                if (
                    not isinstance(value, (float, int))
                    or not math.isfinite(value)
                    or value <= 0
                    or int(value) != value
                ):
                    raise ValueError(f"Invalid ABS population for {code}/{reference}")
                item = SubdivisionPopulation(
                    code,
                    reference.year,
                    int(value),
                    "ABS",
                    f"{reference.year:04d}-{reference.month:02d}-{calendar.monthrange(reference.year, reference.month)[1]:02d}",
                    source_url,
                    "june_estimated_resident_population"
                    if reference.month == 6
                    else "latest_observed_quarter_same_year",
                )
                candidates.setdefault((code, reference.year), []).append(item)
        rows = [
            max(
                items,
                key=lambda item: (
                    item.reference_date[5:7] == "06",
                    item.reference_date,
                ),
            )
            for items in candidates.values()
        ]
        return validate_rows(rows)
    finally:
        workbook.close()


async def apply_rows(
    rows: list[SubdivisionPopulation], checksums: dict[str, str]
) -> None:
    from sqlalchemy import text

    from scripts.import_wpp_population import ensure_table
    from src.core.database import dispose_database, get_db

    try:
        async with get_db() as db:
            await ensure_table(db)
            countries = {
                code: country_id
                for country_id, code in (
                    await db.execute(text("SELECT id, code FROM countries"))
                ).all()
            }
            missing = sorted({row.country_code for row in rows} - countries.keys())
            if missing:
                raise ValueError(f"Unregistered subdivisions: {missing}")
            parameters = [
                {
                    "country_id": countries[row.country_code],
                    "year": row.year,
                    "population": row.population,
                    "source": row.source,
                    "metadata": json.dumps(
                        {
                            "location_code": row.country_code,
                            "location_type": "subdivision"
                            if "-" in row.country_code
                            else "country",
                            "reference_date": row.reference_date,
                            "population_basis": row.basis,
                            "source_url": row.source_url,
                            "input_sha256": checksums[row.source],
                            "annual_denominator_policy": "same_calendar_year_no_interpolation",
                        }
                    ),
                }
                for row in rows
            ]
            await db.execute(
                text("""
                INSERT INTO population_records (country_id, year, population, source, metadata, created_at, updated_at)
                VALUES (:country_id, :year, :population, :source, CAST(:metadata AS json), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (country_id, year) DO UPDATE SET population = EXCLUDED.population,
                    source = EXCLUDED.source, metadata = EXCLUDED.metadata, updated_at = CURRENT_TIMESTAMP
            """),
                parameters,
            )
    finally:
        await dispose_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cn-input", type=Path, default=DATA / "cn_nbs_year_end_2000_2024.csv"
    )
    parser.add_argument(
        "--abs-input", type=Path, default=DATA / "abs_310104_mar2026.xlsx"
    )
    parser.add_argument("--abs-source-url", default=ABS_URL)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "data/processed/subdivision_population/import_report.json",
    )
    args = parser.parse_args()
    rows = validate_rows(
        load_cn_population(args.cn_input)
        + load_abs_population(args.abs_input, args.abs_source_url)
    )
    checksums = {
        "NBS": hashlib.sha256(args.cn_input.read_bytes()).hexdigest(),
        "ABS": hashlib.sha256(args.abs_input.read_bytes()).hexdigest(),
    }
    coverage = {}
    for row in rows:
        stats = coverage.setdefault(
            row.country_code, {"rows": 0, "first_year": row.year, "last_year": row.year}
        )
        stats["rows"] += 1
        stats["last_year"] = row.year
    report = {
        "applied": args.apply,
        "row_count": len(rows),
        "location_count": len(coverage),
        "input_sha256": checksums,
        "coverage": coverage,
        "non_june_abs_references": [
            asdict(row)
            for row in rows
            if row.source == "ABS" and row.basis != "june_estimated_resident_population"
        ],
        "missing_year_policy": "missing stays missing; no national population or carry-forward",
    }
    if args.apply:
        asyncio.run(apply_rows(rows, checksums))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "applied": args.apply,
                "rows": len(rows),
                "locations": len(coverage),
                "report": str(args.report),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
