#!/usr/bin/env python3
"""Import WPP population CSV into population_records.

Default input:
  data/processed/wpp/unpopulation_dataportal_20260317220803.csv

Filter rule (as requested):
- Sex = Both sexes
- Age = Total (all ages)
- Indicator = Total population
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db

DEFAULT_INPUT = ROOT / "data/processed/wpp/unpopulation_dataportal_20260317220803.csv"


@dataclass(frozen=True)
class PopulationRow:
    iso2: str
    year: int
    population: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import WPP population CSV into population_records")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Path to WPP CSV")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate, do not write DB")
    return parser.parse_args()


def norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def parse_int(value: object) -> int | None:
    txt = norm_text(value)
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def parse_float(value: object) -> float | None:
    txt = norm_text(value).replace(",", "")
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def is_target_indicator(row: dict[str, str]) -> bool:
    indicator = norm_text(row.get("IndicatorName") or row.get("IndicatorShortName")).lower()
    return "total population" in indicator


def is_target_age(row: dict[str, str]) -> bool:
    age = norm_text(row.get("Age")).lower()
    if age in {"total", "all ages"}:
        return True
    age_start = parse_int(row.get("AgeStart"))
    age_end = parse_int(row.get("AgeEnd"))
    return age_start == 0 and age_end == -1


def load_rows(csv_path: Path) -> list[PopulationRow]:
    merged: dict[tuple[str, int], PopulationRow] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            sex = norm_text(row.get("Sex"))
            if sex != "Both sexes":
                continue
            if not is_target_age(row):
                continue
            if not is_target_indicator(row):
                continue

            iso2 = norm_text(row.get("Iso2")).upper()
            year = parse_int(row.get("Time"))
            population = parse_float(row.get("Value"))
            if not iso2 or year is None or population is None or population <= 0:
                continue

            merged[(iso2, year)] = PopulationRow(iso2=iso2, year=year, population=population)

    return sorted(merged.values(), key=lambda r: (r.iso2, r.year))


async def ensure_table(db) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS population_records (
                id SERIAL PRIMARY KEY,
                country_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                population DOUBLE PRECISION NOT NULL,
                source VARCHAR(100) NOT NULL DEFAULT 'WPP',
                metadata JSON NOT NULL DEFAULT '{}'::json,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_population_country_year UNIQUE (country_id, year)
            )
            """
        )
    )
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_population_country ON population_records(country_id)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_population_year ON population_records(year)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_population_country_year ON population_records(country_id, year)"))


async def import_rows(rows: list[PopulationRow], dry_run: bool) -> None:
    async with get_db() as db:
        await ensure_table(db)

        countries_result = await db.execute(text("SELECT id, code FROM countries"))
        country_by_code = {str(code).upper(): int(country_id) for country_id, code in countries_result.fetchall()}

        mapped = 0
        skipped = 0

        for row in rows:
            country_id = country_by_code.get(row.iso2)
            if country_id is None:
                skipped += 1
                continue

            mapped += 1
            if dry_run:
                continue

            await db.execute(
                text(
                    """
                    INSERT INTO population_records (country_id, year, population, source, metadata, created_at, updated_at)
                    VALUES (:country_id, :year, :population, :source, '{}'::json, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (country_id, year) DO UPDATE SET
                        population = EXCLUDED.population,
                        source = EXCLUDED.source,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "country_id": country_id,
                    "year": row.year,
                    "population": row.population,
                    "source": "WPP",
                },
            )

    print(f"Parsed rows: {len(rows)}")
    print(f"Mapped rows: {mapped}")
    print(f"Skipped rows (unknown country code): {skipped}")
    if dry_run:
        print("Dry-run mode: no data written")


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input CSV not found: {args.input}")

    rows = load_rows(args.input)
    if not rows:
        raise SystemExit("No rows matched WPP filter rules")

    asyncio.run(import_rows(rows, args.dry_run))


if __name__ == "__main__":
    main()
