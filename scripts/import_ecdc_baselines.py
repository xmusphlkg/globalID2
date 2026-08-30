#!/usr/bin/env python3
"""Validate and atomically import prepared ECDC annual country baselines."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRIES  # noqa: E402
from src.data.crawlers.ecdc import CONTRACT_VERSION  # noqa: E402
from src.data.processors.ecdc import ECDCAnnualUpdater  # noqa: E402
from src.generation.site_data_database import ensure_standard_country_rows  # noqa: E402
from src.services.crawl_service import CrawlService  # noqa: E402


START_YEAR = 1990


def prepared_country_codes() -> tuple[str, ...]:
    prepared: list[str] = []
    for code in ECDC_BASELINE_COUNTRIES:
        mapping_paths = (
            ROOT / "configs/mapping" / f"{code.casefold()}.csv",
            ROOT / "configs/mapping/reviewed" / f"{code.casefold()}.csv",
        )
        current_path = (
            ROOT / "data/current" / code.casefold()
            / f"{code.casefold()}_ecdc_atlas_annual.csv"
        )
        if (
            any(
                mapping_path.exists()
                and f"SRC_{code}_ECDC_ATLAS"
                in mapping_path.read_text(encoding="utf-8-sig")
                for mapping_path in mapping_paths
            )
            and current_path.exists()
        ):
            prepared.append(code)
    return tuple(prepared)


def _validate_rows(updater: ECDCAnnualUpdater) -> list[dict[str, str]]:
    rows = updater._load_rows()
    current_year = datetime.now(timezone.utc).year
    if not rows:
        raise ValueError(f"{updater.country_code}: prepared CSV is empty")
    for row in rows:
        if row.get("ReportingArea") != updater.country_code:
            raise ValueError(f"{updater.country_code}: foreign reporting area")
        if row.get("GeographyKey") != updater.series_geography_key:
            raise ValueError(f"{updater.country_code}: invalid geography key")
        if row.get("Frequency") != "annual" or row.get("Unit") != "count":
            raise ValueError(f"{updater.country_code}: invalid annual count grain")
        if row.get("Dimensions") != "{}":
            raise ValueError(f"{updater.country_code}: analytic dimensions must be empty")
        if row.get("MissingValuePolicy") != "missing_is_unknown":
            raise ValueError(f"{updater.country_code}: invalid missing-value policy")
        if row.get("SourceContract") != CONTRACT_VERSION:
            raise ValueError(f"{updater.country_code}: source contract drift")
        if int(row["Year"]) >= current_year:
            raise ValueError(f"{updater.country_code}: in-progress annual row present")
        value = float(row["Cases"])
        if value < 0 or not value.is_integer():
            raise ValueError(f"{updater.country_code}: invalid count")
    return rows


async def run(*, apply: bool, countries: tuple[str, ...]) -> None:
    prepared: list[tuple[ECDCAnnualUpdater, list[dict[str, str]]]] = []
    for code in countries:
        updater = ECDCAnnualUpdater(code)
        rows = _validate_rows(updater)
        prepared.append((updater, rows))
        print(f"Plan: {code} validated_rows={len(rows)}")
    if not apply:
        return

    results: list[tuple[str, int, int]] = []
    async with get_database() as db:
        await ensure_standard_country_rows(db, countries)
        for updater, rows in prepared:
            db_latest = await updater.get_db_latest_date(db)
            deleted = await updater.delete_authoritative_window(
                db, start_year=START_YEAR
            )
            source_latest = max(
                datetime.strptime(row["Date"], "%Y-%m-%d").date()
                for row in rows
            )
            outcome = await CrawlService._import_rows_with_series(
                db,
                updater,
                rows,
                db_latest_date=db_latest,
                source_latest_date=source_latest,
                force=True,
            )
            results.append((
                updater.country_code,
                deleted,
                int(outcome.inserted_or_updated),
            ))
        await db.commit()
    for code, deleted, upserted in results:
        print(f"Applied: {code} replaced={deleted} upserted={upserted}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--countries",
        help="Comma-separated prepared country codes; defaults to every prepared ECDC file.",
    )
    args = parser.parse_args()
    prepared = prepared_country_codes()
    if args.countries:
        requested = tuple(
            code.strip().upper() for code in args.countries.split(",") if code.strip()
        )
        unknown = sorted(set(requested) - set(prepared))
        if unknown:
            raise SystemExit(f"Countries are not fully prepared: {', '.join(unknown)}")
        countries = requested
    else:
        countries = prepared
    if not countries:
        raise SystemExit("No prepared ECDC baseline files were found")
    asyncio.run(run(apply=args.apply, countries=countries))


if __name__ == "__main__":
    main()
