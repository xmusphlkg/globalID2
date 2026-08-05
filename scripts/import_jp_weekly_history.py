#!/usr/bin/env python3
"""Import Japan weekly historical data into disease_records.

Source file example:
  data/history/jp/weekly_cases_standardized.csv

This importer is designed for the current schema where disease_records primary key is
(time, disease_id, country_id). To avoid collisions from prefecture-level rows, the
default behavior ingests only national aggregate rows (Reporting Area = "総数").
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.country_library import (  # noqa: E402
    get_country_bootstrap_config,
    get_country_profile,
)
from src.core.database import get_db  # noqa: E402
from src.core.db_schema import (  # noqa: E402
    ensure_country_scope,
    ensure_country_scope_schema,
)
from src.core.logging import get_logger  # noqa: E402


logger = get_logger(__name__)

DEFAULT_INPUT = ROOT / "data/history/jp/weekly_cases_standardized.csv"
DEFAULT_REPORTING_AREAS = ["総数"]
DEFAULT_SOURCE = "Japan NIID Weekly Sentinel"


@dataclass(frozen=True)
class WeeklyRow:
    report_area: str
    year: int
    week: int
    disease: str
    cases: int
    flag: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import JP weekly historical CSV into disease_records.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to JP historical weekly CSV.",
    )
    parser.add_argument(
        "--reporting-area",
        action="append",
        default=None,
        help="Reporting Area to include (repeatable). Defaults to 総数.",
    )
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE,
        help="Value written into disease_records.data_source.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing JP disease_records before import.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and prepare rows but do not write to database.",
    )
    return parser.parse_args()


def norm_text(value: str) -> str:
    cleaned = " ".join((value or "").replace("\ufeff", "").strip().split())
    return cleaned


def parse_int(value: str) -> int | None:
    text_value = norm_text(value)
    if not text_value:
        return None
    try:
        return int(float(text_value))
    except ValueError:
        return None


def mmwr_week_end_date(year: int, week: int) -> date:
    """Return the Sunday ending the Japanese IDWR ISO epidemiological week."""

    return date.fromisocalendar(year, week, 7)


def load_rows(input_file: Path, reporting_areas: set[str]) -> list[WeeklyRow]:
    rows: list[WeeklyRow] = []
    with input_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for src in reader:
            report_area = norm_text(src.get("Reporting Area", ""))
            if report_area not in reporting_areas:
                continue

            disease = norm_text(src.get("Disease", ""))
            if not disease:
                continue

            year = parse_int(src.get("Current MMWR Year", ""))
            week = parse_int(src.get("MMWR WEEK", ""))
            cases = parse_int(src.get("Current week", ""))
            if year is None or week is None or cases is None:
                continue
            if week <= 0 or week > 53:
                continue

            rows.append(
                WeeklyRow(
                    report_area=report_area,
                    year=year,
                    week=week,
                    disease=disease,
                    cases=max(0, cases),
                    flag=norm_text(src.get("Current week, flag", "")),
                )
            )
    return rows


async def ensure_country_jp(db) -> int:
    await ensure_country_scope_schema(db)

    profile = get_country_profile("JP")
    bootstrap = get_country_bootstrap_config("JP")

    await db.execute(
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
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "code": "JP",
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
            "metadata": json.dumps({"origin": "scripts/import_jp_weekly_history.py"}),
            "notes": bootstrap.get("notes", "Bootstrapped by JP weekly importer"),
        },
    )

    await ensure_country_scope(
        db,
        scope_code="JP",
        country_code="JP",
        scope_type="canonical",
        language_code="ja-jp",
        display_name=profile.name,
        is_default=True,
        is_active=True,
        metadata={"origin": "scripts/import_jp_weekly_history.py"},
    )

    result = await db.execute(text("SELECT id FROM countries WHERE code = 'JP'"))
    row = result.fetchone()
    if not row:
        raise RuntimeError("Failed to ensure countries.code=JP")
    return int(row[0])


async def load_disease_id_by_name(db) -> dict[str, int]:
    result = await db.execute(
        text(
            """
            SELECT dm.local_name, d.id
            FROM disease_mappings dm
            JOIN diseases d ON dm.disease_id = d.name
            WHERE dm.country_code = 'JP' AND dm.is_active = true
            """
        )
    )
    mapping: dict[str, int] = {}
    for local_name, disease_db_id in result.fetchall():
        key = norm_text(local_name).lower()
        if key:
            mapping[key] = int(disease_db_id)
    return mapping


async def upsert_records(
    db,
    *,
    country_id: int,
    rows: list[WeeklyRow],
    disease_id_by_name: dict[str, int],
    source_name: str,
    input_file: Path,
) -> int:
    payload: list[dict[str, object]] = []
    for row in rows:
        disease_id = disease_id_by_name.get(norm_text(row.disease).lower())
        if disease_id is None:
            continue

        week_end = mmwr_week_end_date(row.year, row.week)
        row_meta = {
            "source_csv": input_file.name,
            "reporting_area": row.report_area,
            "mmwr_year": row.year,
            "mmwr_week": row.week,
            "current_week_flag": row.flag,
            "death_reporting": "not_provided_by_source",
            "death_reporting_note": "Japan IDWR weekly feed used here reports cases only.",
        }
        raw_obj = {
            "Reporting Area": row.report_area,
            "Current MMWR Year": row.year,
            "MMWR WEEK": row.week,
            "Disease": row.disease,
            "Current week": row.cases,
            "Current week, flag": row.flag,
        }

        payload.append(
            {
                "time": datetime.combine(week_end, time.min),
                "disease_id": disease_id,
                "country_id": country_id,
                "cases": row.cases,
                "deaths": None,
                "region": None,
                "data_source": source_name,
                "metadata": json.dumps(row_meta),
                "raw_data": json.dumps(raw_obj),
            }
        )

    if not payload:
        return 0

    await db.execute(
        text(
            """
            INSERT INTO disease_records (
                time, disease_id, country_id, cases, deaths,
                region, data_source,
                new_cases, new_deaths, recoveries, active_cases, new_recoveries,
                metadata, raw_data
            ) VALUES (
                :time, :disease_id, :country_id, :cases, :deaths,
                :region, :data_source,
                0, 0, 0, 0, 0,
                CAST(:metadata AS json), CAST(:raw_data AS json)
            )
            ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                cases = EXCLUDED.cases,
                deaths = EXCLUDED.deaths,
                region = EXCLUDED.region,
                data_source = EXCLUDED.data_source,
                metadata = EXCLUDED.metadata,
                raw_data = EXCLUDED.raw_data
            """
        ),
        payload,
    )
    return len(payload)


async def prune_overlapping_history_rows(
    db,
    *,
    country_id: int,
    preferred_source_file: str,
    legacy_source_csv: str,
) -> int:
    """
    Remove legacy JP history rows when the standardized weekly file already
    provides the same disease one day later.

    The duplicate pattern in JP history is:
    - legacy history row on Friday
    - standardized weekly row for the same disease on Sunday

    We keep the standardized weekly row from the current updater because it extends
    later into 2026 and avoids mixing Friday/Sunday variants on the public site.
    """
    result = await db.execute(
        text(
            """
            DELETE FROM disease_records legacy
            USING disease_records preferred
            WHERE legacy.country_id = :country_id
              AND preferred.country_id = legacy.country_id
              AND preferred.disease_id = legacy.disease_id
              AND legacy.metadata->>'source_csv' = :legacy_source_csv
              AND preferred.metadata->>'source_file' = :preferred_source_file
              AND timezone('UTC', preferred.time)::date = timezone('UTC', legacy.time)::date + 2
            """
        ),
        {
            "country_id": country_id,
            "legacy_source_csv": legacy_source_csv,
            "preferred_source_file": preferred_source_file,
        },
    )
    return result.rowcount or 0


async def run_import(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    areas = set(DEFAULT_REPORTING_AREAS)
    if args.reporting_area:
        areas = {norm_text(v) for v in args.reporting_area if norm_text(v)}

    rows = load_rows(args.input, areas)
    if not rows:
        logger.warning("No rows matched filters, nothing to import.")
        return

    diseases = {row.disease for row in rows}
    logger.info(
        f"Prepared {len(rows):,} rows ({len(diseases):,} diseases) "
        f"for reporting areas: {', '.join(sorted(areas))}"
    )

    if args.dry_run:
        logger.info("Dry run enabled: skip database writes.")
        return

    async with get_db() as db:
        country_id = await ensure_country_jp(db)
        disease_id_by_name = await load_disease_id_by_name(db)

        if args.replace_existing:
            deleted = await db.execute(
                text("DELETE FROM disease_records WHERE country_id = :country_id"),
                {"country_id": country_id},
            )
            logger.info(f"Deleted existing JP disease_records: {deleted.rowcount or 0:,}")

        unmapped_names = sorted({d for d in diseases if norm_text(d).lower() not in disease_id_by_name})
        if unmapped_names:
            logger.warning(
                "Unmapped JP diseases skipped ({}): {}",
                len(unmapped_names),
                ", ".join(unmapped_names[:20]),
            )
        inserted = await upsert_records(
            db,
            country_id=country_id,
            rows=rows,
            disease_id_by_name=disease_id_by_name,
            source_name=args.source_name,
            input_file=args.input,
        )
        pruned_merged = await prune_overlapping_history_rows(
            db,
            country_id=country_id,
            preferred_source_file=args.input.name,
            legacy_source_csv="weekly_cases_total_merged_standardized.csv",
        )
        pruned_history = await prune_overlapping_history_rows(
            db,
            country_id=country_id,
            preferred_source_file=args.input.name,
            legacy_source_csv=args.input.name,
        )

        await db.commit()
        logger.info(f"Imported/updated disease_records rows: {inserted:,}")
        if pruned_merged:
            logger.info(f"Pruned overlapping JP merged-history rows: {pruned_merged:,}")
        if pruned_history:
            logger.info(f"Pruned overlapping JP history-standard rows: {pruned_history:,}")


def main() -> None:
    args = parse_args()
    asyncio.run(run_import(args))


if __name__ == "__main__":
    main()
