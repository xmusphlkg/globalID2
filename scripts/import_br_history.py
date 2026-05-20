#!/usr/bin/env python3
"""Historical backfill helper for Brazil SINAN / DATASUS.

Uses the existing BRMonthlyUpdater pipeline and runs year-by-year to avoid a
single very long request. This is especially useful for full-history imports.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import sys
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import get_database, init_app
from src.data.processors import BRMonthlyUpdater


def _year_months(year: int) -> list[tuple[int, int]]:
    now = datetime.now()
    if year < now.year:
        return [(year, m) for m in range(1, 13)]
    return [(year, m) for m in range(1, now.month + 1)]


async def import_year(year: int, updater: BRMonthlyUpdater, save_raw: bool) -> None:
    months = _year_months(year)
    print(f"[BR] year={year:04d} months={len(months)} -> fetching")

    fetched = updater.refresh_source(
        source="sinan_datasus",
        force=False,
        months=months,
        save_raw=save_raw,
        raw_dir=Path("data/raw/br"),
    )
    print(
        f"[BR] year={year:04d} rows={len(fetched.rows)} "
        f"latest={fetched.source_latest_date}"
    )

    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        import_result = await updater.import_rows(
            db,
            fetched.rows,
            db_latest_date=db_latest,
            source_latest_date=fetched.source_latest_date,
            force=False,
        )
        await db.commit()

    print(
        f"[BR] year={year:04d} upserted={import_result.inserted_or_updated} "
        f"skipped={import_result.skipped_unmapped}"
    )


async def run(start_year: int, end_year: int, save_raw: bool, dry_run: bool) -> int:
    await init_app()
    updater = BRMonthlyUpdater()
    years: Iterable[int] = range(start_year, end_year + 1)

    print(f"[BR] starting history import from {start_year} to {end_year} (save_raw={save_raw}, dry_run={dry_run})")
    for year in years:
        if dry_run:
            print(f"[BR] DRY RUN year={year:04d} months={len(_year_months(year))}")
            continue
        try:
            await import_year(year, updater, save_raw=save_raw)
        except Exception as exc:
            print(f"[BR] year={year:04d} failed: {type(exc).__name__}: {exc}")
            return 1
    print("[BR] history import done.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    now = datetime.now().year
    parser = argparse.ArgumentParser(description="Backfill Brazil SINAN history year-by-year.")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=now)
    parser.add_argument("--save-raw", action="store_true", default=False, help="Keep downloaded .dbc files in data/raw/br")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Only print plan, skip import")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.start_year < 1900 or args.end_year < 1900:
        raise SystemExit("start-year/end-year must be >= 1900")
    if args.end_year < args.start_year:
        raise SystemExit("end-year must be >= start-year")

    rc = asyncio.run(run(args.start_year, args.end_year, save_raw=args.save_raw, dry_run=args.dry_run))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
