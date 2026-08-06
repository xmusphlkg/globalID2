#!/usr/bin/env python3
"""Import WPP population CSV into population_records.

Default input is resolved from the repository's history or processed WPP
directory so the import continues to work across rebuild layouts.

Filter rule (as requested):
- Sex = Both sexes
- Age = Total (all ages)
- Indicator = Total population
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db

DEFAULT_INPUT_CANDIDATES = (
    ROOT / "data/history/wpp/unpopulation_dataportal_20260317220803.csv",
    ROOT / "data/processed/wpp/unpopulation_dataportal_20260317220803.csv",
)


def resolve_default_input() -> Path:
    return next(
        (path for path in DEFAULT_INPUT_CANDIDATES if path.exists()),
        DEFAULT_INPUT_CANDIDATES[0],
    )


DEFAULT_INPUT = resolve_default_input()

# Keep project country codes ISO Alpha-2. These aliases cover common legacy
# spellings while retaining the project code in public output.
COUNTRY_CODE_TO_WPP_ISO2 = {
    "UK": "GB",
}


@dataclass(frozen=True)
class PopulationRow:
    iso2: str
    year: int
    population: float


@dataclass(frozen=True)
class PlannedPopulationRow:
    country_id: int
    country_code: str
    wpp_iso2: str
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


def build_population_import_plan(
    rows: list[PopulationRow],
    country_by_code: dict[str, int],
) -> dict[str, Any]:
    """Match every enabled database country to its complete WPP time series."""

    rows_by_iso2: dict[str, list[PopulationRow]] = {}
    for row in rows:
        rows_by_iso2.setdefault(row.iso2, []).append(row)

    expected_years = sorted({row.year for row in rows})
    expected_year_set = set(expected_years)
    planned_rows: list[PlannedPopulationRow] = []
    mapped_country_codes: list[str] = []
    missing_country_codes: list[str] = []
    incomplete_country_years: dict[str, list[int]] = {}

    for raw_code, country_id in sorted(country_by_code.items()):
        country_code = norm_text(raw_code).upper()
        wpp_iso2 = COUNTRY_CODE_TO_WPP_ISO2.get(country_code, country_code)
        source_rows = rows_by_iso2.get(wpp_iso2) or []
        if not source_rows:
            missing_country_codes.append(country_code)
            continue

        available_years = {row.year for row in source_rows}
        missing_years = sorted(expected_year_set - available_years)
        if missing_years:
            incomplete_country_years[country_code] = missing_years
            continue

        mapped_country_codes.append(country_code)
        planned_rows.extend(
            PlannedPopulationRow(
                country_id=int(country_id),
                country_code=country_code,
                wpp_iso2=wpp_iso2,
                year=row.year,
                population=row.population,
            )
            for row in source_rows
        )

    return {
        "rows": planned_rows,
        "expected_years": expected_years,
        "available_wpp_codes": sorted(rows_by_iso2),
        "target_country_codes": sorted(country_by_code),
        "mapped_country_codes": mapped_country_codes,
        "missing_country_codes": missing_country_codes,
        "incomplete_country_years": incomplete_country_years,
    }


def validate_population_import_plan(plan: dict[str, Any]) -> None:
    """Fail loudly when an enabled country cannot receive complete WPP data."""

    issues: list[str] = []
    missing = plan.get("missing_country_codes") or []
    if missing:
        issues.append(f"no WPP ISO2 match: {', '.join(missing)}")
    incomplete = plan.get("incomplete_country_years") or {}
    if incomplete:
        details = "; ".join(
            f"{code} missing {len(years)} year(s)"
            for code, years in sorted(incomplete.items())
        )
        issues.append(f"incomplete WPP coverage: {details}")
    if issues:
        raise ValueError("Population onboarding failed: " + " | ".join(issues))


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


async def ensure_wpp_population(
    input_path: Path | None = None,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Idempotently provision complete WPP denominators for enabled countries.

    This intentionally reparses the full WPP snapshot on every readiness run.
    A country added after an earlier import is therefore discovered and
    backfilled automatically without maintaining a separate country list.
    """

    csv_path = input_path or resolve_default_input()
    if not csv_path.exists():
        raise FileNotFoundError(f"WPP population CSV not found: {csv_path}")
    rows = load_rows(csv_path)
    if not rows:
        raise ValueError("No rows matched WPP filter rules")

    async with get_db() as db:
        await ensure_table(db)
        countries_result = await db.execute(
            text("SELECT id, code FROM countries WHERE is_active = TRUE")
        )
        country_by_code = {
            str(code).upper(): int(country_id)
            for country_id, code in countries_result.fetchall()
        }
        plan = build_population_import_plan(rows, country_by_code)
        if strict:
            validate_population_import_plan(plan)
        planned_rows: list[PlannedPopulationRow] = plan["rows"]
        for row in planned_rows:
            await db.execute(
                text(
                    """
                    INSERT INTO population_records (country_id, year, population, source, metadata, created_at, updated_at)
                    VALUES (:country_id, :year, :population, :source, CAST(:metadata AS json), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (country_id, year) DO UPDATE SET
                        population = EXCLUDED.population,
                        source = EXCLUDED.source,
                        metadata = EXCLUDED.metadata,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "country_id": row.country_id,
                    "year": row.year,
                    "population": row.population,
                    "source": "WPP",
                    "metadata": json.dumps(
                        {
                            "country_code": row.country_code,
                            "wpp_iso2": row.wpp_iso2,
                            "wpp_snapshot": csv_path.name,
                            "auto_provisioned": True,
                        }
                    ),
                },
            )
    expected_years = plan["expected_years"]
    return {
        "parsed_rows": len(rows),
        "available_wpp_codes": len(plan["available_wpp_codes"]),
        "target_countries": len(plan["target_country_codes"]),
        "mapped_rows": len(planned_rows),
        "mapped_countries": len(plan["mapped_country_codes"]),
        "missing_country_codes": plan["missing_country_codes"],
        "incomplete_country_codes": sorted(plan["incomplete_country_years"]),
        "year_min": min(expected_years) if expected_years else None,
        "year_max": max(expected_years) if expected_years else None,
        "source_path": str(csv_path),
    }


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
