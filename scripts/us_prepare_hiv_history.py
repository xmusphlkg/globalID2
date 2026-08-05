#!/usr/bin/env python3
"""Merge CDC NHSS annual HIV/AIDS history into the US history dataset.

Two official CDC publication channels are combined:

* AtlasPlus historical extract: national HIV diagnoses and AIDS
  classifications for older years.
* Current NHSS release workbook: revised national HIV diagnoses for the years
  retained in the current release.

The current workbook wins for overlapping HIV years. AIDS classifications and
all-stage HIV diagnoses remain separate disease concepts and are never summed.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
import io
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.us import (  # noqa: E402
    NHSS_HISTORIC_ATLAS_URL,
    NHSS_HIV_LABEL,
    NHSS_SOURCE_NAME,
    USNHSSHIVCrawler,
)
from src.core.database import get_db  # noqa: E402
from src.core.disease_mutation_lock import (  # noqa: E402
    acquire_disease_data_mutation_lock,
)
from src.data.processors.us import USWeeklyUpdater  # noqa: E402
from src.data.storage import SeriesObservationStore  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data/history/us/history_merged.csv"
AIDS_LABEL = "AIDS classifications"

OUTPUT_COLUMNS = [
    "Date",
    "Diseases",
    "DiseasesCN",
    "Cases",
    "Deaths",
    "Source",
    "CountryCode",
    "ReportingArea",
    "MMWRYear",
    "MMWRWeek",
    "CurrentWeekFlag",
    "Previous52WeekMax",
    "Previous52WeekMaxFlag",
    "CumulativeYTDCurrentYear",
    "CumulativeYTDCurrentYearFlag",
    "CumulativeYTDPreviousYear",
    "CumulativeYTDPreviousYearFlag",
    "Location1",
    "Location2",
    "SortOrder",
    "Geocode",
    "RawDiseaseLabel",
    "IsProvisional",
    "UpdateMode",
    "Frequency",
    "Measure",
    "PopulationScope",
    "SurveillanceYear",
    "__source_file",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and merge US CDC NHSS annual HIV/AIDS history."
    )
    parser.add_argument(
        "--atlas-csv",
        type=Path,
        help="Optional local AtlasPlus historical extract (otherwise downloaded).",
    )
    parser.add_argument(
        "--current-xlsx",
        type=Path,
        help="Optional local current NHSS release workbook (otherwise discovered/downloaded).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--import-db",
        action="store_true",
        help=(
            "Non-destructively upsert prepared NHSS rows into the legacy projection "
            "and lossless source-series observations."
        ),
    )
    return parser.parse_args()


def _history_row(
    *,
    year: int,
    label: str,
    cases: int,
    source_file: str,
    update_mode: str,
    provisional: bool,
) -> dict[str, str]:
    row = {column: "" for column in OUTPUT_COLUMNS}
    row.update(
        {
            "Date": f"{year}-12-31",
            "Diseases": label,
            "DiseasesCN": label,
            "Cases": str(cases),
            "Deaths": "",
            "Source": NHSS_SOURCE_NAME,
            "CountryCode": "US",
            "ReportingArea": "TOTAL",
            "RawDiseaseLabel": label,
            "IsProvisional": "true" if provisional else "false",
            "UpdateMode": update_mode,
            "Frequency": "annual",
            "Measure": (
                "hiv_diagnoses" if label == NHSS_HIV_LABEL else "aids_classifications"
            ),
            "PopulationScope": "persons_age_13_plus",
            "SurveillanceYear": str(year),
            "__source_file": source_file,
        }
    )
    return row


def parse_atlas_history(text: str) -> list[dict[str, str]]:
    """Extract only national, unstratified HIV and AIDS observations."""

    source = io.StringIO(text)
    reader = csv.reader(source)
    header: list[str] | None = None
    for raw_header in reader:
        if len(raw_header) >= 2 and raw_header[0] == "Indicator" and raw_header[1] == "Year":
            header = raw_header
            break
    if header is None:
        raise RuntimeError("AtlasPlus historical extract data header was not found")

    rows: list[dict[str, str]] = []
    for values in reader:
        row = dict(zip(header, values))
        indicator = (row.get("Indicator") or "").strip()
        if indicator not in {"HIV diagnoses", "AIDS classifications"}:
            continue
        if (row.get("Geography") or "").strip() != "US":
            continue
        if (row.get("Age Group") or "").strip() != "Ages 13 years and older":
            continue
        if (row.get("Race/Ethnicity") or "").strip() != "All races/ethnicities":
            continue
        if (row.get("Sex") or "").strip() != "All gender identities":
            continue
        if (row.get("Transmission Category") or "").strip() != "All transmission categories":
            continue

        try:
            year = int((row.get("Year") or "").strip())
            cases = int(float((row.get("Cases") or "").replace(",", "")))
        except ValueError:
            continue
        label = NHSS_HIV_LABEL if indicator == "HIV diagnoses" else AIDS_LABEL
        rows.append(
            _history_row(
                year=year,
                label=label,
                cases=cases,
                source_file=NHSS_HISTORIC_ATLAS_URL,
                update_mode="historical_atlas_extract",
                provisional=False,
            )
        )

    if not rows:
        raise RuntimeError("No national HIV/AIDS rows found in AtlasPlus historical extract")
    return rows


def parse_current_history(payload: bytes, source_url: str) -> list[dict[str, str]]:
    parsed = USNHSSHIVCrawler.parse_current_workbook(payload, source_url=source_url)
    result: list[dict[str, str]] = []
    for row in parsed:
        year = int(str(row["SurveillanceYear"]))
        result.append(
            _history_row(
                year=year,
                label=NHSS_HIV_LABEL,
                cases=int(str(row["Cases"])),
                source_file=source_url,
                update_mode="current_release_xlsx",
                provisional=str(row.get("IsProvisional", "")).lower() == "true",
            )
        )
    return result


def merge_history(
    output: Path,
    atlas_rows: list[dict[str, str]],
    current_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    if output.exists():
        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw_row in csv.DictReader(handle):
                if (raw_row.get("Source") or "").strip() == NHSS_SOURCE_NAME:
                    continue
                row = {column: raw_row.get(column, "") for column in OUTPUT_COLUMNS}
                if row["Source"] == "US CDC NNDSS":
                    row["Frequency"] = row["Frequency"] or "weekly"
                    row["Measure"] = row["Measure"] or "case_notifications"
                    row["PopulationScope"] = row["PopulationScope"] or "all"
                    row["SurveillanceYear"] = (
                        row["SurveillanceYear"] or row["Date"][:4]
                    )
                key = (row["Date"], row["ReportingArea"], row["RawDiseaseLabel"])
                merged[key] = row

    # Atlas supplies the older baseline; current release revisions overwrite
    # overlapping HIV years while leaving the independent AIDS series intact.
    for row in [*atlas_rows, *current_rows]:
        key = (row["Date"], row["ReportingArea"], row["RawDiseaseLabel"])
        merged[key] = row

    result = list(merged.values())
    result.sort(
        key=lambda row: (
            row.get("Date", ""),
            row.get("Diseases", ""),
            row.get("SortOrder", ""),
        )
    )
    return result


def write_history(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


async def import_db(rows: list[dict[str, str]]) -> tuple[int, int]:
    prepared: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        item["Frequency"] = "annual"
        item["PopulationScope"] = "persons_age_13_plus"
        item["Measure"] = (
            "hiv_diagnoses" if item["RawDiseaseLabel"] == NHSS_HIV_LABEL else "aids_classifications"
        )
        item["SurveillanceYear"] = item["Date"][:4]
        prepared.append(item)

    updater = USWeeklyUpdater()
    async with get_db() as db:
        await acquire_disease_data_mutation_lock(db)
        db_latest = await updater.get_db_latest_date(db)
        result = await updater.import_rows(
            db,
            prepared,
            db_latest_date=db_latest,
            source_latest_date=max(
                (
                    datetime.strptime(date_text, "%Y-%m-%d").date()
                    for date_text in (item.get("Date") for item in prepared)
                    if date_text
                ),
                default=None,
            ),
            force=True,
        )
        series_result = await SeriesObservationStore().save_rows(
            db,
            prepared,
            "US",
            source_id="SRC_US_NHSS",
        )
    return result.inserted_or_updated, series_result.upserted


def _download_text(url: str) -> str:
    response = requests.get(
        url,
        timeout=120,
        headers={"User-Agent": "Mozilla/5.0 (GlobalID NHSS sync)"},
    )
    response.raise_for_status()
    return response.content.decode("utf-8-sig")


def main() -> None:
    args = parse_args()

    if args.atlas_csv:
        atlas_text = args.atlas_csv.read_text(encoding="utf-8-sig")
    else:
        atlas_text = _download_text(NHSS_HISTORIC_ATLAS_URL)
    atlas_rows = parse_atlas_history(atlas_text)

    if args.current_xlsx:
        current_payload = args.current_xlsx.read_bytes()
        current_url = args.current_xlsx.name
    else:
        current_payload, current_url = USNHSSHIVCrawler().fetch_current_workbook()
    current_rows = parse_current_history(current_payload, current_url)

    merged = merge_history(args.output, atlas_rows, current_rows)
    write_history(args.output, merged)

    nhss_rows = [row for row in merged if row["Source"] == NHSS_SOURCE_NAME]
    by_label: dict[str, list[int]] = {}
    for row in nhss_rows:
        by_label.setdefault(row["RawDiseaseLabel"], []).append(int(row["Date"][:4]))
    print(f"Wrote {len(merged):,} rows to {args.output}")
    print(f"NHSS rows: {len(nhss_rows):,}")
    for label, years in sorted(by_label.items()):
        print(f"  {label}: {min(years)}-{max(years)} ({len(years)} rows)")
    if args.import_db:
        legacy_imported, series_imported = asyncio.run(import_db(nhss_rows))
        print(f"Legacy projection rows upserted: {legacy_imported}")
        print(f"Source-series rows upserted: {series_imported}")


if __name__ == "__main__":
    main()
