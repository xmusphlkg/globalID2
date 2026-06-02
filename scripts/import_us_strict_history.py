#!/usr/bin/env python3
"""Import strict US historical weekly rows into disease_records without clearing recent US data."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.core.database import get_db
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema
from src.core.logging import get_logger


logger = get_logger(__name__)
REPORT_TIME_UTC = time(hour=12)
DEFAULT_INPUT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_import_ready_strict.csv"
)
DEFAULT_SOURCE = "US CDC NNDSS"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import strict US historical weekly CSV into disease_records.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Import-ready US CSV.")
    parser.add_argument("--source-name", default=DEFAULT_SOURCE, help="Fallback data_source value.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and prepare, do not write DB.")
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


def load_rows(input_file: Path) -> list[dict[str, str]]:
    with input_file.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{k: normalize_text(v) for k, v in row.items()} for row in csv.DictReader(handle)]


async def ensure_country_us(db) -> int:
    await ensure_country_scope_schema(db)

    profile = get_country_profile("US")
    bootstrap = get_country_bootstrap_config("US")

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
            "code": "US",
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
            "metadata": json.dumps({"origin": "scripts/import_us_strict_history.py"}),
            "notes": bootstrap.get("notes", "Bootstrapped by US strict historical importer"),
        },
    )

    await ensure_country_scope(
        db,
        scope_code="US",
        country_code="US",
        scope_type="canonical",
        language_code="en-us",
        display_name=profile.name,
        is_default=True,
        is_active=True,
        metadata={"origin": "scripts/import_us_strict_history.py"},
    )

    result = await db.execute(text("SELECT id FROM countries WHERE code = 'US'"))
    row = result.fetchone()
    if not row:
        raise RuntimeError("Failed to ensure countries.code=US")
    return int(row[0])


async def load_disease_db_ids(db, disease_codes: set[str]) -> dict[str, int]:
    if not disease_codes:
        return {}
    result = await db.execute(
        text("SELECT id, name FROM diseases WHERE name = ANY(:codes)"),
        {"codes": sorted(disease_codes)},
    )
    return {normalize_text(name): int(db_id) for db_id, name in result.fetchall()}


async def run_import(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    rows = load_rows(args.input)
    if not rows:
        logger.warning("No input rows found, nothing to import.")
        return

    diseases = {normalize_text(row.get("CanonicalDiseaseId")) for row in rows if normalize_text(row.get("CanonicalDiseaseId"))}
    logger.info(f"Prepared {len(rows):,} rows across {len(diseases):,} disease codes")

    if args.dry_run:
        logger.info("Dry run enabled: skip database writes.")
        return

    async with get_db() as db:
        country_id = await ensure_country_us(db)
        disease_db_ids = await load_disease_db_ids(db, diseases)

        unmapped_codes = sorted(diseases - set(disease_db_ids))
        if unmapped_codes:
            logger.warning("Missing diseases table rows for {} code(s): {}", len(unmapped_codes), ", ".join(unmapped_codes[:20]))

        payload: list[dict[str, object]] = []
        skipped_bad_rows = 0
        seen_keys: set[tuple[datetime, int, int]] = set()

        for idx, row in enumerate(rows):
            disease_code = normalize_text(row.get("CanonicalDiseaseId"))
            disease_db_id = disease_db_ids.get(disease_code)
            if disease_db_id is None:
                skipped_bad_rows += 1
                continue

            date_text = normalize_text(row.get("Date"))
            cases = parse_int(row.get("Cases"))
            if not date_text or cases is None:
                skipped_bad_rows += 1
                continue

            try:
                report_time = datetime.combine(
                    datetime.strptime(date_text, "%Y-%m-%d").date(),
                    REPORT_TIME_UTC,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                skipped_bad_rows += 1
                continue

            key = (report_time, disease_db_id, country_id)
            if key in seen_keys:
                skipped_bad_rows += 1
                continue
            seen_keys.add(key)

            metadata_obj = {
                "source_csv": args.input.name,
                "row_index": idx,
                "raw_disease_label": normalize_text(row.get("RawDiseaseLabel")),
                "canonical_disease_id": disease_code,
                "canonical_label": normalize_text(row.get("CanonicalLabel")),
                "canonical_resolution": normalize_text(row.get("CanonicalResolution")),
                "selected_reason": normalize_text(row.get("SelectedReason")),
                "handling_bucket": normalize_text(row.get("HandlingBucket")),
                "reporting_area": normalize_text(row.get("Reporting Area")),
                "mmwr_year": normalize_text(row.get("Current MMWR Year")),
                "mmwr_week": normalize_text(row.get("MMWR WEEK")),
                "update_mode": normalize_text(row.get("UpdateMode")),
                "import_policy": "strict_us_history_v1",
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": "NNDSS case notification feed used here does not provide death counts.",
            }

            payload.append(
                {
                    "time": report_time,
                    "disease_id": disease_db_id,
                    "country_id": country_id,
                    "cases": max(0, cases),
                    "deaths": None,
                    "data_source": normalize_text(row.get("Source")) or args.source_name,
                    "metadata": json.dumps(metadata_obj),
                    "raw_data": json.dumps(row),
                }
            )

        if payload:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths,
                        data_source, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths,
                        :data_source, CAST(:metadata AS json), CAST(:raw_data AS json),
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        data_source = EXCLUDED.data_source,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                payload,
            )
            await db.commit()

        logger.info(
            "US strict history import complete: upserted {} row(s), skipped {} row(s)",
            len(payload),
            skipped_bad_rows,
        )


def main() -> None:
    args = parse_args()
    asyncio.run(run_import(args))


if __name__ == "__main__":
    main()
