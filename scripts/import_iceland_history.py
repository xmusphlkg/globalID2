#!/usr/bin/env python3
"""Download, parse, audit, and import Iceland's historical Excel releases.

Examples::

    # Fetch all 22 primary files, write CSV/manifest artifacts, no database I/O
    python scripts/import_iceland_history.py --dry-run

    # Re-parse a previously archived immutable raw snapshot
    python scripts/import_iceland_history.py \
        --no-download --raw-manifest data/raw/is/history/raw_manifest.json \
        --skip-db

    # Full atomic legacy projection + source-series import
    python scripts/import_iceland_history.py

The script never promotes unregistered labels.  The processor's quarantine is
written even when database import is skipped or later fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from src.core import get_database, init_app
from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.data.crawlers.is_history import (
    DEFAULT_LANDING_URL,
    DEFAULT_RAW_DIR,
    IcelandHistoryCrawler,
)
from src.data.processors.is_history import (
    DEFAULT_ONTOLOGY_PATH,
    DEFAULT_OUTPUT_DIR,
    HISTORY_SOURCE_ID,
    LEGACY_SOURCE_ID,
    IcelandHistoryProcessor,
)
from src.data.storage.series_observation_store import (
    SeriesObservationQualityPolicy,
    SeriesObservationStore,
)


async def _ensure_country(db) -> None:
    """Upsert the checked-in Iceland profile without touching other countries."""

    profile = get_country_profile("IS")
    bootstrap = get_country_bootstrap_config("IS")
    await db.execute(
        text(
            """
            INSERT INTO countries (
                code, name, name_en, name_local, language, timezone,
                data_source_url, data_source_type, crawler_config, parser_config,
                disease_mapping_rules, report_config, is_active, metadata, notes,
                created_at, updated_at
            ) VALUES (
                'IS', :name, :name_en, :name_local, :language, :timezone,
                :data_source_url, :data_source_type, CAST(:crawler_config AS json),
                CAST(:parser_config AS json), CAST(:disease_mapping_rules AS json),
                CAST(:report_config AS json), true, CAST(:metadata AS json), :notes,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
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
            "metadata": json.dumps({"origin": "iceland_history_import"}),
            "notes": bootstrap.get("notes"),
        },
    )


async def _retire_ineligible_compatibility_rows(db, prepared) -> int:
    """Delete only derived history rows whose series is no longer projectable.

    Source observations remain untouched.  This narrowly reconciles the
    lossy ``disease_records`` layer after an ontology relation is corrected
    (for example H1N1 subtype or multiple non-additive D100 definitions), so a
    stale compatibility row cannot survive a safe re-import.
    """

    reviewed_codes = {
        str(row.get("SourceSeriesCode") or "")
        for row in prepared.series_rows
        if row.get("SourceId") == HISTORY_SOURCE_ID
        and row.get("Measure") == "case_notifications"
        and row.get("SourceSeriesCode")
    }
    eligible_codes = {
        str(row.get("SourceSeriesCode") or "")
        for row in prepared.rows
        if row.get("SourceId") == HISTORY_SOURCE_ID
        and row.get("SourceSeriesCode")
    }
    retired_codes = sorted(reviewed_codes - eligible_codes)
    if not retired_codes:
        return 0
    result = await db.execute(
        text(
            """
            DELETE FROM disease_records
            WHERE country_id = (SELECT id FROM countries WHERE code = 'IS')
              AND COALESCE(metadata::jsonb ->> 'source_kind', '') IN (
                  'registry_annual', 'registry_disease_monthly'
              )
              AND COALESCE(metadata::jsonb ->> 'source_series_code', '') =
                  ANY(:series_codes)
            """
        ),
        {"series_codes": retired_codes},
    )
    return int(result.rowcount or 0)


async def _import_database(processor: IcelandHistoryProcessor, prepared) -> None:
    history_rows = [
        row for row in prepared.series_rows if row.get("SourceId") == HISTORY_SOURCE_ID
    ]
    legacy_rows = [
        row for row in prepared.series_rows if row.get("SourceId") == LEGACY_SOURCE_ID
    ]
    if not history_rows:
        raise RuntimeError(
            "No registered Iceland historical notification rows survived the "
            "ontology gate; sync/add SRC_IS_DOH_HISTORY source_series first"
        )

    policy = SeriesObservationQualityPolicy(
        mode="quarantine",
        registry_coverage="required",
        history_lookback_days=0,
    )
    store = SeriesObservationStore()
    async with get_database() as db:
        await _ensure_country(db)
        retired_projection_rows = await _retire_ineligible_compatibility_rows(
            db, prepared
        )
        projection = await processor.import_rows(db, prepared.rows)
        history_result = await store.save_rows(
            db,
            history_rows,
            "IS",
            source_id=HISTORY_SOURCE_ID,
            geography_key="country:IS:national",
            quality_policy=policy,
        )
        legacy_result = None
        if legacy_rows:
            legacy_result = await store.save_rows(
                db,
                legacy_rows,
                "IS",
                source_id=LEGACY_SOURCE_ID,
                geography_key="country:IS:national",
                quality_policy=policy,
            )
        await db.commit()

    print(
        "[IS] database projection_upserted="
        f"{projection.inserted_or_updated} projection_skipped={projection.skipped_unmapped} "
        f"projection_retired={retired_projection_rows} "
        "projection_skipped_due_current="
        f"{projection.skipped_current_precedence} "
        f"history_series_upserted={history_result.upserted} "
        f"legacy_series_upserted={legacy_result.upserted if legacy_result else 0}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Iceland Directorate of Health historical Excel data."
    )
    parser.add_argument("--landing-url", default=DEFAULT_LANDING_URL)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--raw-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY_PATH)
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Parse --raw-manifest (or RAW_DIR/raw_manifest.json) without network I/O.",
    )
    parser.add_argument(
        "--include-validation-workbook",
        action="store_true",
        help="Also archive the duplicate 2011-2015 annual workbook; it is not imported.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Write normalized artifacts but do not connect to the database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Equivalent to --skip-db; download/parse/audit still run.",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    if args.no_download:
        raw_manifest = args.raw_manifest or args.raw_dir / "raw_manifest.json"
        if not raw_manifest.exists():
            raise FileNotFoundError(
                f"--no-download requires an existing raw manifest: {raw_manifest}"
            )
    else:
        crawler = IcelandHistoryCrawler(
            landing_url=args.landing_url,
            raw_dir=args.raw_dir,
        )
        downloaded = crawler.download_history(
            include_validation=args.include_validation_workbook
        )
        raw_manifest = downloaded.manifest_path
        print(
            f"[IS] downloaded files={len(downloaded.raw_files)} "
            f"manifest={downloaded.manifest_path}"
        )

    processor = IcelandHistoryProcessor(ontology_path=args.ontology)
    prepared = processor.prepare_manifest(raw_manifest)
    outputs = processor.write_outputs(prepared, args.output_dir)
    counts = prepared.manifest["counts"]
    quarantine = prepared.manifest["quarantine"]
    print(
        "[IS] prepared "
        f"projection_rows={counts['projection_rows']} "
        f"series_rows={counts['series_rows']} "
        f"quarantine_rows={counts['quarantine_rows']}"
    )
    print(
        "[IS] quarantine_by_reason="
        + json.dumps(quarantine["by_reason"], ensure_ascii=False, sort_keys=True)
    )
    print(
        "[IS] outputs="
        + json.dumps({key: str(value) for key, value in outputs.items()}, sort_keys=True)
    )

    if args.skip_db or args.dry_run:
        print("[IS] database import skipped")
        return 0
    await init_app()
    await _import_database(processor, prepared)
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(f"[IS] failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(result)


if __name__ == "__main__":
    main()
