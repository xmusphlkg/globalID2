#!/usr/bin/env python3
"""Fetch and import Korea KDCA monthly historical data.

This script is intentionally additive: it upserts Korea country metadata,
standard disease rows, KR mappings, and KR disease_records without clearing
existing data for other countries.  It can use the data.go.kr OpenAPI when
``DATA_GO_KR_SERVICE_KEY`` is set, and if unavailable it automatically falls
back to KDCA dportal AJAX before reading manual KDCA dportal/KOSIS
CSV/XLSX/JSON exports passed via ``--source-file``/``--source-dir``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.core.database import get_db
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema
from src.core.logging import get_logger
from src.data.processors.kr import (
    DEFAULT_OUTPUT_CSV,
    DEFAULT_HISTORY_START_YEAR,
    KRMonthlyUpdater,
)

logger = get_logger(__name__)

STANDARD_DISEASES_CSV = ROOT / "configs" / "standard_diseases.csv"
KR_MAPPING_CSV = ROOT / "configs" / "mapping" / "kr.csv"


def _norm(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _split_aliases(*values: object) -> List[str]:
    aliases: List[str] = []
    for value in values:
        text_value = _norm(value)
        if not text_value:
            continue
        for piece in text_value.replace(",", "|").split("|"):
            alias = _norm(piece)
            if alias and alias not in aliases:
                aliases.append(alias)
    return aliases


async def _ensure_kr_country_and_scope(db) -> None:
    profile = get_country_profile("KR")
    bootstrap = get_country_bootstrap_config("KR")

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
                language = EXCLUDED.language,
                timezone = EXCLUDED.timezone,
                data_source_url = EXCLUDED.data_source_url,
                data_source_type = EXCLUDED.data_source_type,
                crawler_config = EXCLUDED.crawler_config,
                parser_config = EXCLUDED.parser_config,
                disease_mapping_rules = EXCLUDED.disease_mapping_rules,
                report_config = EXCLUDED.report_config,
                is_active = true,
                metadata = EXCLUDED.metadata,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "code": "KR",
            "name": profile.name,
            "name_en": profile.name_en,
            "name_local": profile.name_local,
            "language": profile.language,
            "timezone": profile.timezone,
            "data_source_url": bootstrap.get("data_source_url"),
            "data_source_type": bootstrap.get("data_source_type"),
            "crawler_config": json.dumps(bootstrap.get("crawler_config") or {}),
            "parser_config": json.dumps(bootstrap.get("parser_config") or {}),
            "disease_mapping_rules": json.dumps(
                bootstrap.get("disease_mapping_rules") or {}
            ),
            "report_config": json.dumps(bootstrap.get("report_config") or {}),
            "metadata": json.dumps({"origin": "kr_history_import"}),
            "notes": bootstrap.get("notes"),
        },
    )

    await ensure_country_scope_schema(db)
    await ensure_country_scope(
        db,
        scope_code="KR",
        country_code="KR",
        scope_type="canonical",
        language_code=profile.language,
        display_name=profile.name,
        is_default=True,
        is_active=True,
        metadata={"origin": "kr_history_import"},
    )


async def _upsert_standard_diseases_and_diseases(db) -> int:
    with STANDARD_DISEASES_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        await db.execute(
            text(
                """
                INSERT INTO standard_diseases (
                    disease_id, standard_name_en, standard_name_zh, category,
                    icd_10, icd_11, description, source, metadata, is_active,
                    created_at, updated_at
                ) VALUES (
                    :disease_id, :standard_name_en, :standard_name_zh, :category,
                    :icd_10, :icd_11, :description, :source, CAST(:metadata AS json),
                    true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (disease_id) DO UPDATE SET
                    standard_name_en = EXCLUDED.standard_name_en,
                    standard_name_zh = EXCLUDED.standard_name_zh,
                    category = EXCLUDED.category,
                    icd_10 = EXCLUDED.icd_10,
                    icd_11 = EXCLUDED.icd_11,
                    description = EXCLUDED.description,
                    source = EXCLUDED.source,
                    metadata = EXCLUDED.metadata,
                    is_active = true,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "disease_id": _norm(row.get("disease_id")),
                "standard_name_en": _norm(row.get("standard_name_en")),
                "standard_name_zh": _norm(row.get("standard_name_zh")) or None,
                "category": _norm(row.get("category")) or "Other",
                "icd_10": _norm(row.get("icd_10")) or None,
                "icd_11": _norm(row.get("icd_11")) or None,
                "description": _norm(row.get("description")) or None,
                "source": _norm(row.get("source")) or "Manual",
                "metadata": json.dumps({"origin": "standard_diseases_csv"}),
            },
        )

    await db.execute(
        text(
            """
            INSERT INTO diseases (
                name, name_en, category, icd_10, icd_11, description,
                aliases, keywords, metadata, is_active, created_at, updated_at
            )
            SELECT
                disease_id,
                standard_name_en,
                COALESCE(category, 'Other'),
                NULLIF(LEFT(COALESCE(icd_10, ''), 10), ''),
                NULLIF(LEFT(COALESCE(icd_11, ''), 20), ''),
                description,
                '[]'::json,
                '[]'::json,
                json_build_object('standard_name_zh', standard_name_zh),
                is_active,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM standard_diseases
            ON CONFLICT (name) DO UPDATE SET
                name_en = EXCLUDED.name_en,
                category = EXCLUDED.category,
                icd_10 = EXCLUDED.icd_10,
                icd_11 = EXCLUDED.icd_11,
                description = EXCLUDED.description,
                metadata = EXCLUDED.metadata,
                is_active = EXCLUDED.is_active,
                updated_at = CURRENT_TIMESTAMP
            """
        )
    )
    return len(rows)


async def _upsert_kr_mappings(db) -> int:
    await db.execute(text("ALTER TABLE disease_mappings ALTER COLUMN category DROP NOT NULL"))

    with KR_MAPPING_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    inserted = 0
    for row in rows:
        disease_id = _norm(row.get("disease_id"))
        local_name = _norm(row.get("local_name"))
        category = _norm(row.get("category")) or None
        source = _norm(row.get("data_source")) or "Korea KDCA EID"
        metadata = {
            "origin": "kr_mapping_csv",
            "local_code": _norm(row.get("local_code")),
            "notes": _norm(row.get("notes")),
        }

        if local_name:
            await _upsert_one_mapping(
                db,
                disease_id=disease_id,
                local_name=local_name,
                category=category,
                source=source,
                is_primary=True,
                is_alias=False,
                priority=100,
                metadata=metadata,
            )
            inserted += 1

        aliases = _split_aliases(row.get("local_code"), row.get("aliases"))
        for alias in aliases:
            if alias == local_name:
                continue
            await _upsert_one_mapping(
                db,
                disease_id=disease_id,
                local_name=alias,
                category=category,
                source=source,
                is_primary=False,
                is_alias=True,
                priority=50,
                metadata=metadata,
            )
            inserted += 1

    return inserted


async def _upsert_one_mapping(
    db,
    *,
    disease_id: str,
    local_name: str,
    category: str | None,
    source: str,
    is_primary: bool,
    is_alias: bool,
    priority: int,
    metadata: dict,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO disease_mappings (
                disease_id, country_code, local_name, is_primary, is_alias,
                priority, usage_count, confidence_score, category, source,
                metadata, is_active, created_at, updated_at
            ) VALUES (
                :disease_id, 'KR', :local_name, :is_primary, :is_alias,
                :priority, 0, 1.0, :category, :source,
                CAST(:metadata AS json), true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (disease_id, country_code, local_name) DO UPDATE SET
                is_primary = EXCLUDED.is_primary,
                is_alias = EXCLUDED.is_alias,
                priority = GREATEST(disease_mappings.priority, EXCLUDED.priority),
                category = EXCLUDED.category,
                source = EXCLUDED.source,
                metadata = EXCLUDED.metadata,
                is_active = true,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "disease_id": disease_id,
            "local_name": local_name,
            "is_primary": is_primary,
            "is_alias": is_alias,
            "priority": priority,
            "category": category,
            "source": source,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )


async def _db_kr_summary(db) -> dict:
    result = await db.execute(
        text(
            """
            SELECT
                COUNT(*) AS records,
                MIN(dr.time) AS min_time,
                MAX(dr.time) AS max_time,
                COUNT(DISTINCT date_trunc('month', dr.time)) AS months,
                COUNT(DISTINCT dr.disease_id) AS diseases,
                COALESCE(SUM(dr.cases), 0) AS cases
            FROM disease_records dr
            JOIN countries c ON c.id = dr.country_id
            WHERE c.code = 'KR'
            """
        )
    )
    row = result.mappings().one()
    return dict(row)


async def run(args: argparse.Namespace) -> None:
    raw_dir = Path(args.raw_dir)
    output_csv = Path(args.output_csv)
    updater = KRMonthlyUpdater(output_csv=output_csv)
    months = updater.history_months(start_year=args.start_year)

    logger.info("Preparing KR country, disease, and mapping rows...")
    async with get_db() as db:
        await _ensure_kr_country_and_scope(db)
        standard_count = await _upsert_standard_diseases_and_diseases(db)
        mapping_count = await _upsert_kr_mappings(db)
        await db.commit()
    logger.info(
        "KR prerequisites ready | standard_diseases={} mapping_entries={}",
        standard_count,
        mapping_count,
    )
    if args.prepare_only:
        print("\nKR prerequisites prepared")
        print("Historical data fetch skipped (--prepare-only).")
        print(f"Standard diseases upserted: {standard_count}")
        print(f"KR mapping entries upserted: {mapping_count}")
        return

    logger.info(
        "Fetching KR KDCA monthly history | months={} range={}-01..{}-{:02d}",
        len(months),
        args.start_year,
        datetime.now().year,
        datetime.now().month,
    )
    fetched = updater.refresh_source(
        source="kdca_open_api",
        force=True,
        months=months,
        save_raw=not args.no_save_raw,
        raw_dir=raw_dir,
        source_file=Path(args.source_file) if args.source_file else None,
        source_dir=Path(args.source_dir) if args.source_dir else None,
    )
    logger.info(
        "KR source prepared | rows={} latest={} csv={}",
        len(fetched.rows),
        fetched.source_latest_date,
        fetched.source_csv,
    )
    for line in fetched.script_logs:
        logger.info(line)

    async with get_db() as db:
        before = await _db_kr_summary(db)
        db_latest = await updater.get_db_latest_date(db)
        result = await updater.import_rows(
            db,
            fetched.rows,
            db_latest_date=db_latest,
            source_latest_date=fetched.source_latest_date,
            force=True,
        )
        await db.commit()

    async with get_db() as db:
        after = await _db_kr_summary(db)

    print("\nKR history import complete")
    print(f"Source CSV: {fetched.source_csv}")
    print(f"Raw archive: {'disabled' if args.no_save_raw else raw_dir}")
    print(f"Fetched source rows: {len(fetched.rows)}")
    print(f"Upserted DB rows: {result.inserted_or_updated}")
    print(f"Skipped unmapped rows: {result.skipped_unmapped}")
    print(f"Before DB summary: {before}")
    print(f"After DB summary: {after}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=DEFAULT_HISTORY_START_YEAR)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--raw-dir", default=str(ROOT / "data" / "raw" / "kr"))
    parser.add_argument(
        "--source-file",
        help="Optional KDCA dportal/KOSIS CSV, Excel, HTML, or JSON export to import without an API key.",
    )
    parser.add_argument(
        "--source-dir",
        help="Optional directory containing KDCA dportal/KOSIS exports to import without an API key.",
    )
    parser.add_argument("--no-save-raw", action="store_true")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only upsert KR country, standard disease, and mapping rows; skip API fetch.",
    )
    return parser.parse_args(list(argv))


def main() -> None:
    asyncio.run(run(parse_args(sys.argv[1:])))


if __name__ == "__main__":
    main()
