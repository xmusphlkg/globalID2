#!/usr/bin/env python3
"""Normalize US CDC NNDSS weekly data into the project's history_merged.csv format.

This script supports two ingestion paths:
1. Local historical CSV export, such as data/raw/us/NNDSS_Weekly_Data_20260317.csv
2. API-based refresh for national TOTAL rows using public CDC Socrata endpoints

Output is written to data/processed/us/history_merged.csv so the existing
full_rebuild_database.py --country us flow can reuse the standard history import.

Notes:
- The current project schema can safely ingest national TOTAL rows because there is
  only one record per disease/week/country.
- State-level rows are intentionally excluded here; they would collide on the current
  disease_records primary key and require a schema extension.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_CSV = ROOT / "data/raw/us/NNDSS_Weekly_Data_20260317.csv"
DEFAULT_OUTPUT_CSV = ROOT / "data/processed/us/history_merged.csv"
DEFAULT_REPORTING_AREA = "TOTAL"
DEFAULT_SOURCE_NAME = "US CDC NNDSS"
DEFAULT_JSON_API_URL = (
    "https://data.cdc.gov/api/v3/views/x9gk-5huc/query.json?query="
    "SELECT%0A"
    "%20%20%60states%60%2C%0A"
    "%20%20%60year%60%2C%0A"
    "%20%20%60week%60%2C%0A"
    "%20%20%60label%60%2C%0A"
    "%20%20%60m1%60%2C%0A"
    "%20%20%60m1_flag%60%2C%0A"
    "%20%20%60m2%60%2C%0A"
    "%20%20%60m2_flag%60%2C%0A"
    "%20%20%60m3%60%2C%0A"
    "%20%20%60m3_flag%60%2C%0A"
    "%20%20%60m4%60%2C%0A"
    "%20%20%60m4_flag%60%2C%0A"
    "%20%20%60location1%60%2C%0A"
    "%20%20%60location2%60%2C%0A"
    "%20%20%60sort_order%60%2C%0A"
    "%20%20%60geocode%60%0A"
    "WHERE%20caseless_one_of(%60states%60%2C%20%22TOTAL%22)%0A"
    "ORDER%20BY%20%60sort_order%60%20ASC%20NULL%20LAST"
)
DEFAULT_CSV_API_URL = (
    "https://data.cdc.gov/resource/x9gk-5huc.csv"
    "?$select=states,year,week,label,m1,m1_flag,m2,m2_flag,m3,m3_flag,m4,m4_flag,location1,location2,sort_order,geocode"
    "&$where=upper(states)='TOTAL'"
    "&$order=sort_order"
)
USER_AGENT = "Mozilla/5.0 (GlobalID NNDSS sync)"

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
    "__source_file",
]


@dataclass(frozen=True)
class ApiCandidate:
    url: str
    kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare US CDC NNDSS weekly data for GlobalID history import.",
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=None,
        help="Optional local raw CSV export. If omitted, the script fetches via API.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help="Normalized history CSV output path.",
    )
    parser.add_argument(
        "--reporting-area",
        default=DEFAULT_REPORTING_AREA,
        help="Reporting area to ingest. Use TOTAL to stay compatible with the current schema.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the output file instead of merging/upserting into the existing one.",
    )
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE_NAME,
        help="Value written into the Source column.",
    )
    return parser.parse_args()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def first_value(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        if key in row:
            value = normalize_text(row.get(key))
            if value:
                return value
    return ""


def parse_numeric(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return ""
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)


def mmwr_week_end_date(year: int, week: int) -> date:
    jan_4 = date(year, 1, 4)
    week_1_start = jan_4 - timedelta(days=(jan_4.weekday() + 1) % 7)
    return week_1_start + timedelta(weeks=week - 1, days=6)


def build_normalized_record(
    row: dict[str, object],
    *,
    reporting_area_filter: str,
    source_name: str,
    update_mode: str,
    source_file: str,
) -> dict[str, str] | None:
    reporting_area = first_value(row, "Reporting Area", "states")
    if reporting_area.upper() != reporting_area_filter.upper():
        return None

    year_text = first_value(row, "Current MMWR Year", "year")
    week_text = first_value(row, "MMWR WEEK", "week")
    label = first_value(row, "Label", "label")
    if not year_text or not week_text or not label:
        return None

    try:
        year = int(float(year_text))
        week = int(float(week_text))
        week_end = mmwr_week_end_date(year, week)
    except ValueError:
        return None

    return {
        "Date": datetime.combine(week_end, time.min).strftime("%Y-%m-%d"),
        "Diseases": label,
        "DiseasesCN": label,
        "Cases": parse_numeric(first_value(row, "Current week", "m1")),
        "Deaths": "",
        "Source": source_name,
        "CountryCode": "US",
        "ReportingArea": reporting_area,
        "MMWRYear": str(year),
        "MMWRWeek": str(week),
        "CurrentWeekFlag": first_value(row, "Current week, flag", "m1_flag"),
        "Previous52WeekMax": parse_numeric(first_value(row, "Previous 52 week Max", "m2")),
        "Previous52WeekMaxFlag": first_value(row, "Previous 52 weeks Max, flag", "m2_flag"),
        "CumulativeYTDCurrentYear": parse_numeric(first_value(row, "Cumulative YTD Current MMWR Year", "m3")),
        "CumulativeYTDCurrentYearFlag": first_value(row, "Cumulative YTD Current MMWR Year, flag", "m3_flag"),
        "CumulativeYTDPreviousYear": parse_numeric(first_value(row, "Cumulative YTD Previous MMWR Year", "m4")),
        "CumulativeYTDPreviousYearFlag": first_value(row, "Cumulative YTD Previous MMWR Year, flag", "m4_flag"),
        "Location1": first_value(row, "LOCATION1", "location1"),
        "Location2": first_value(row, "LOCATION2", "location2"),
        "SortOrder": first_value(row, "sort_order"),
        "Geocode": first_value(row, "geocode"),
        "RawDiseaseLabel": label,
        "IsProvisional": "true",
        "UpdateMode": update_mode,
        "__source_file": source_file,
    }


def read_local_csv(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_api_rows() -> tuple[list[dict[str, object]], str]:
    candidates = [
        ApiCandidate(DEFAULT_JSON_API_URL, "json"),
        ApiCandidate(DEFAULT_CSV_API_URL, "csv"),
    ]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            req = request.Request(candidate.url, headers={"User-Agent": USER_AGENT})
            with request.urlopen(req, timeout=60) as response:
                payload = response.read()

            if candidate.kind == "json":
                data = json.loads(payload.decode("utf-8"))
                rows = extract_rows_from_json(data)
            else:
                text = payload.decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(text)))

            if rows:
                return rows, candidate.url
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Unable to fetch CDC NNDSS rows from configured endpoints: {last_error}")


def extract_rows_from_json(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return [row for row in payload["data"] if isinstance(row, dict)]
        if isinstance(payload.get("results"), list):
            return [row for row in payload["results"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def normalize_rows(
    rows: Iterable[dict[str, object]],
    *,
    reporting_area: str,
    source_name: str,
    update_mode: str,
    source_file: str,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        record = build_normalized_record(
            row,
            reporting_area_filter=reporting_area,
            source_name=source_name,
            update_mode=update_mode,
            source_file=source_file,
        )
        if record is not None:
            normalized.append(record)

    normalized.sort(key=lambda item: (item["Date"], item["Diseases"], item["SortOrder"]))
    return normalized


def merge_existing_rows(output: Path, new_rows: list[dict[str, str]], replace: bool) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {}

    if not replace and output.exists():
        with output.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row.get("Date", ""), row.get("ReportingArea", ""), row.get("RawDiseaseLabel", ""))
                merged[key] = {column: row.get(column, "") for column in OUTPUT_COLUMNS}

    for row in new_rows:
        key = (row["Date"], row["ReportingArea"], row["RawDiseaseLabel"])
        merged[key] = row

    result = list(merged.values())
    result.sort(key=lambda item: (item["Date"], item["Diseases"], item["SortOrder"]))
    return result


def write_output(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()

    if args.input_csv is not None:
        input_csv = args.input_csv
    elif DEFAULT_INPUT_CSV.exists():
        input_csv = DEFAULT_INPUT_CSV
    else:
        input_csv = None

    if input_csv is not None:
        raw_rows = read_local_csv(input_csv)
        source_file = input_csv.name
        update_mode = "historical_csv"
    else:
        raw_rows, source_url = read_api_rows()
        source_file = source_url
        update_mode = "api_sync"

    normalized_rows = normalize_rows(
        raw_rows,
        reporting_area=args.reporting_area,
        source_name=args.source_name,
        update_mode=update_mode,
        source_file=source_file,
    )
    merged_rows = merge_existing_rows(args.output, normalized_rows, args.replace)
    write_output(args.output, merged_rows)

    unique_labels = {row["RawDiseaseLabel"] for row in merged_rows}
    print(f"Wrote {len(merged_rows):,} rows to {args.output}")
    print(f"Distinct labels: {len(unique_labels):,}")
    print(f"Reporting area: {args.reporting_area}")
    print(f"Mode: {update_mode}")


if __name__ == "__main__":
    main()