#!/usr/bin/env python3
"""Safely rebuild Brazil SINAN NTRA monthly history.

NTRA is a trachoma survey aggregate: ``NU_CASOPOS`` is the number of positive
cases and ``NU_CASOEXA`` is the number of persons examined.  It must never be
reconstructed by counting DBF records.  This command delegates source parsing
to the existing BR crawler, limits the request to NTRA, writes only to a
temporary or explicitly selected CSV, and is a database dry run by default.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.data.crawlers.br import (  # noqa: E402
    DEFAULT_HISTORY_START_YEAR,
    DEFAULT_SOURCE_NAME,
    SINAN_DISEASE_PREFIXES,
    BrazilSINANCrawler,
)
from src.data.processors.br import (  # noqa: E402
    DEFAULT_OUTPUT_CSV,
    BRMonthlyUpdater,
)
from src.data.storage import SeriesObservationStore  # noqa: E402


COUNTRY_CODE = "BR"
SOURCE_ID = "SRC_BR_SINAN"
NTRA_PREFIX = "NTRA"
NTRA_LABEL = SINAN_DISEASE_PREFIXES[NTRA_PREFIX]
NATIONAL_GEOGRAPHY = "country:BR:national"


def build_parser() -> argparse.ArgumentParser:
    current_year = date.today().year
    parser = argparse.ArgumentParser(
        description="Rebuild BR SINAN NTRA history without touching the full BR CSV."
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=DEFAULT_HISTORY_START_YEAR,
    )
    parser.add_argument("--end-year", type=int, default=current_year)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional dedicated NTRA CSV. The default is a temporary file.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upsert legacy and source-series facts. The default is DB dry-run.",
    )
    return parser


def months_for_year_range(
    start_year: int,
    end_year: int,
    *,
    today: date | None = None,
) -> list[tuple[int, int]]:
    upper = today or date.today()
    if start_year < 1900 or end_year < 1900:
        raise ValueError("--start-year and --end-year must be at least 1900")
    if end_year < start_year:
        raise ValueError("--end-year must be greater than or equal to --start-year")
    if end_year > upper.year:
        raise ValueError("--end-year must not be in the future")

    months: list[tuple[int, int]] = []
    for year in range(start_year, end_year + 1):
        last_month = upper.month if year == upper.year else 12
        months.extend((year, month) for month in range(1, last_month + 1))
    return months


def _nonnegative_integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if (
        not number.is_finite()
        or number < 0
        or number != number.to_integral_value()
    ):
        return None
    return int(number)


def _split_values(row: dict[str, Any], plural: str, singular: str) -> list[str]:
    values: list[str] = []
    for field in (plural, singular):
        for token in str(row.get(field) or "").split("|"):
            normalized = token.strip()
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def normalize_ntra_rows(
    rows: list[dict[str, Any]],
    *,
    requested_months: set[tuple[int, int]],
) -> tuple[list[dict[str, str]], Counter[str]]:
    """Validate and deterministically coalesce crawler output by month."""

    skipped: Counter[str] = Counter()
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        if str(row.get("DiseaseCode") or "").strip().upper() != NTRA_PREFIX:
            skipped["non_ntra"] += 1
            continue
        try:
            report_date = date.fromisoformat(str(row.get("Date") or "").strip())
        except ValueError:
            skipped["invalid_row"] += 1
            continue
        month_key = (report_date.year, report_date.month)
        if month_key not in requested_months:
            skipped["out_of_range"] += 1
            continue
        cases = _nonnegative_integer(row.get("Cases"))
        persons_examined = _nonnegative_integer(row.get("PersonsExamined"))
        if cases is None or persons_examined is None:
            skipped["invalid_row"] += 1
            continue

        if month_key in grouped:
            skipped["duplicate_rows_coalesced"] += 1
        bucket = grouped.setdefault(
            month_key,
            {
                "Cases": 0,
                "PersonsExamined": 0,
                "DatasetStatuses": [],
                "SourceFiles": [],
                "SourceURLs": [],
            },
        )
        bucket["Cases"] += cases
        bucket["PersonsExamined"] += persons_examined
        for value in _split_values(row, "DatasetStatus", "DatasetStatus"):
            if value not in bucket["DatasetStatuses"]:
                bucket["DatasetStatuses"].append(value)
        for value in _split_values(row, "SourceFiles", "SourceFile"):
            if value not in bucket["SourceFiles"]:
                bucket["SourceFiles"].append(value)
        for value in _split_values(row, "SourceURLs", "SourceURL"):
            if value not in bucket["SourceURLs"]:
                bucket["SourceURLs"].append(value)

    normalized: list[dict[str, str]] = []
    for (year, month), bucket in sorted(grouped.items()):
        normalized.append(
            {
                "Date": date(year, month, 1).isoformat(),
                "RawDiseaseLabel": NTRA_LABEL,
                "DiseaseCode": NTRA_PREFIX,
                "Year": str(year),
                "Month": str(month),
                "Cases": str(bucket["Cases"]),
                "PersonsExamined": str(bucket["PersonsExamined"]),
                "DatasetStatus": "|".join(sorted(bucket["DatasetStatuses"])),
                "SourceFiles": "|".join(bucket["SourceFiles"]),
                "SourceURLs": "|".join(bucket["SourceURLs"]),
                "Source": DEFAULT_SOURCE_NAME,
            }
        )
    return normalized, skipped


def write_ntra_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Atomically write a dedicated, deterministic NTRA extract."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "",
        "Disease",
        "DiseaseCode",
        "Year",
        "Month",
        "Date",
        "Cases",
        "PersonsExamined",
        "DatasetStatus",
        "SourceFiles",
        "SourceURLs",
        "Source",
    ]
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for index, row in enumerate(rows, start=1):
                writer.writerow(
                    {
                        "": str(index),
                        "Disease": row["RawDiseaseLabel"],
                        "DiseaseCode": row["DiseaseCode"],
                        "Year": row["Year"],
                        "Month": row["Month"],
                        "Date": row["Date"],
                        "Cases": row["Cases"],
                        "PersonsExamined": row["PersonsExamined"],
                        "DatasetStatus": row["DatasetStatus"],
                        "SourceFiles": row["SourceFiles"],
                        "SourceURLs": row["SourceURLs"],
                        "Source": row["Source"],
                    }
                )
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _assert_safe_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(DEFAULT_OUTPUT_CSV).resolve():
        raise ValueError(
            "Refusing to overwrite the full BR current CSV; choose a dedicated "
            "NTRA output path."
        )
    return resolved


async def _run_with_output(
    args: argparse.Namespace,
    *,
    output_path: Path,
    output_persisted: bool,
) -> dict[str, Any]:
    months = months_for_year_range(args.start_year, args.end_year)
    requested_months = set(months)
    crawler = BrazilSINANCrawler(save_raw=False)
    updater = BRMonthlyUpdater(output_csv=output_path)
    fetched = updater.refresh_source(
        source=NTRA_PREFIX,
        force=True,
        months=months,
        save_raw=False,
        load_csv_fallback=False,
        write_csv=False,
        crawler=crawler,
    )
    rows, normalization_skips = normalize_ntra_rows(
        list(fetched.rows),
        requested_months=requested_months,
    )
    write_ntra_csv(output_path, rows)

    store = SeriesObservationStore()
    built = store.build_observations(
        rows,
        COUNTRY_CODE,
        source_id=SOURCE_ID,
        geography_key=NATIONAL_GEOGRAPHY,
    )
    positive_cases = sum(int(row["Cases"]) for row in rows)
    persons_examined = sum(int(row["PersonsExamined"]) for row in rows)
    source_latest_date = max(
        (date.fromisoformat(row["Date"]) for row in rows),
        default=None,
    )

    legacy_upserts = 0
    series_upserts = 0
    legacy_unmapped = 0
    registry_not_synced = 0
    apply_blocked = False
    preflight_skips = (
        built.skipped_unmatched + built.skipped_ambiguous + built.skipped_invalid
    )
    if args.apply and rows:
        if preflight_skips:
            apply_blocked = True
        else:
            async with get_db() as db:
                saved = await store.save_rows(
                    db,
                    rows,
                    COUNTRY_CODE,
                    source_id=SOURCE_ID,
                    geography_key=NATIONAL_GEOGRAPHY,
                )
                series_upserts = saved.upserted
                registry_not_synced = saved.skipped_registry_not_synced
                if (
                    not registry_not_synced
                    and series_upserts == len(built.observations)
                ):
                    imported = await updater.import_rows(
                        db,
                        rows,
                        db_latest_date=None,
                        source_latest_date=source_latest_date,
                        force=True,
                    )
                    legacy_upserts = imported.inserted_or_updated
                    legacy_unmapped = imported.skipped_unmapped
                else:
                    apply_blocked = True

    skip_counts = {
        "non_ntra": normalization_skips["non_ntra"],
        "out_of_range": normalization_skips["out_of_range"],
        "invalid_row": normalization_skips["invalid_row"],
        "duplicate_rows_coalesced": normalization_skips[
            "duplicate_rows_coalesced"
        ],
        "series_unmatched": built.skipped_unmatched,
        "series_ambiguous": built.skipped_ambiguous,
        "series_invalid": built.skipped_invalid,
        "registry_not_synced": registry_not_synced,
        "legacy_unmapped": legacy_unmapped,
    }
    return {
        "mode": "apply" if args.apply else "dry_run",
        "start_year": args.start_year,
        "end_year": args.end_year,
        "requested_months": len(months),
        "output": str(output_path),
        "output_persisted": output_persisted,
        "rows": len(rows),
        "positive_cases": positive_cases,
        "persons_examined": persons_examined,
        "legacy_upserts": legacy_upserts,
        "series_upserts": series_upserts,
        "skipped": sum(skip_counts.values()),
        "skip_counts": skip_counts,
        "apply_blocked": apply_blocked,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    # Validate the date range before any crawler can read a cache or network.
    months_for_year_range(args.start_year, args.end_year)
    if args.output is not None:
        return await _run_with_output(
            args,
            output_path=_assert_safe_output(Path(args.output)),
            output_persisted=True,
        )

    with tempfile.TemporaryDirectory(prefix="globalid_br_ntra_history_") as tmp_dir:
        return await _run_with_output(
            args,
            output_path=Path(tmp_dir) / "br_ntra_history.csv",
            output_persisted=False,
        )


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
