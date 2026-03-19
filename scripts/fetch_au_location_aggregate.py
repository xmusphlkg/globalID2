#!/usr/bin/env python3
"""Fetch/prepare AU data by aggregating location-level rows into national totals.

Australia NINDSS does not provide a direct national API row in this pipeline.
This script:
1) optionally runs the external collector in ID_AU/ScriptGetData
2) reads location-level CSV files (state rows)
3) aggregates monthly totals per disease to national rows
4) writes data/current/au/australia_national_data.csv
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/current/au/australia_national_data.csv"
DEFAULT_AU_SCRIPT_ROOT = Path("/home/likangguo/globalID/ID_AU/ScriptGetData")
DEFAULT_LOCATION_ROOT = Path("/home/likangguo/globalID/ID_AU/Data/GetData/location")

OUTPUT_COLUMNS = [
    "",
    "Disease",
    "Group",
    "Year",
    "Month",
    "Date",
    "Cases",
    "DiseaseFull",
    "Population",
    "Incidence",
]

SKIP_GROUPS = {"AUS", "UNKNOWN", "TOTAL", "ALL"}


@dataclass(frozen=True)
class AggregateKey:
    disease: str
    year: int
    month: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate AU location-level disease CSVs to national monthly data.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output CSV path")
    parser.add_argument(
        "--script-root",
        type=Path,
        default=DEFAULT_AU_SCRIPT_ROOT,
        help="Path to /ID_AU/ScriptGetData",
    )
    parser.add_argument(
        "--location-root",
        type=Path,
        default=DEFAULT_LOCATION_ROOT,
        help="Path to location-level yearly CSV folders",
    )
    parser.add_argument(
        "--python-exec",
        default=sys.executable,
        help="Python executable used to run external AU collector",
    )
    parser.add_argument(
        "--run-external",
        action="store_true",
        help="Run external ID_AU collector before aggregation",
    )
    parser.add_argument("--start-year", type=int, default=2000, help="External fetch start year")
    parser.add_argument("--end-year", type=int, default=datetime.now().year, help="External fetch end year")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing output instead of merging by (Disease, Date)",
    )
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


def parse_month(month_text: str) -> int | None:
    text = norm_text(month_text)
    if not text:
        return None
    numeric = parse_int(text)
    if numeric is not None and 1 <= numeric <= 12:
        return numeric

    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(text, fmt).month
        except ValueError:
            continue
    return None


def month_name(month: int) -> str:
    return datetime(2000, month, 1).strftime("%B")


def run_external_fetch(script_root: Path, python_exec: str, start_year: int, end_year: int) -> None:
    getdata_py = script_root / "GetData.py"
    if not getdata_py.exists():
        raise FileNotFoundError(f"GetData.py not found: {getdata_py}")

    cmd = [
        python_exec,
        str(getdata_py),
        "--groups",
        "location",
        "--disease",
        "all",
        "--start-year",
        str(start_year),
        "--end-year",
        str(end_year),
    ]
    proc = subprocess.run(cmd, cwd=str(script_root), check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"External AU collector failed with exit code {proc.returncode}")


def collect_location_rows(location_root: Path) -> dict[AggregateKey, int]:
    totals: dict[AggregateKey, int] = defaultdict(int)

    if not location_root.exists():
        raise FileNotFoundError(f"Location folder not found: {location_root}")

    csv_files = sorted(location_root.rglob("*.csv"))
    if not csv_files:
        return totals

    for file_path in csv_files:
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                disease = norm_text(row.get("Disease")) or file_path.parent.name
                year = parse_int(row.get("year"))
                month = parse_month(norm_text(row.get("month")))
                cases = parse_int(row.get("cases"))
                group = norm_text(row.get("group")).upper()

                if not disease or year is None or month is None or cases is None:
                    continue
                if group in SKIP_GROUPS:
                    continue

                key = AggregateKey(disease=disease, year=year, month=month)
                totals[key] += max(0, cases)

    return totals


def load_existing(output_file: Path) -> dict[tuple[str, str], dict[str, str]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    if not output_file.exists():
        return merged

    with output_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            disease = norm_text(row.get("Disease"))
            date_text = norm_text(row.get("Date"))
            if not disease or not date_text:
                continue
            merged[(disease, date_text)] = {col: norm_text(row.get(col, "")) for col in OUTPUT_COLUMNS}

    return merged


def write_output(
    output_file: Path,
    totals: dict[AggregateKey, int],
    *,
    replace: bool,
) -> int:
    merged = {} if replace else load_existing(output_file)

    for key, cases in totals.items():
        date_text = f"{key.year:04d}-{key.month:02d}-01"
        merged[(key.disease, date_text)] = {
            "": "",
            "Disease": key.disease,
            "Group": "location_aggregated",
            "Year": str(key.year),
            "Month": str(key.month),
            "Date": date_text,
            "Cases": str(cases),
            "DiseaseFull": key.disease,
            "Population": "",
            "Incidence": "",
        }

    rows = list(merged.values())
    rows.sort(key=lambda r: (r["Date"], r["Disease"]))

    for idx, row in enumerate(rows, start=1):
        row[""] = str(idx)
        month_int = parse_int(row.get("Month"))
        if month_int is not None:
            row["Month"] = str(month_int)
            row["Date"] = f"{parse_int(row.get('Year')):04d}-{month_int:02d}-01"
            # Keep a human-readable month label in DiseaseFull untouched.

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> None:
    args = parse_args()

    if args.run_external:
        print("Running external AU location fetch pipeline...")
        run_external_fetch(
            script_root=args.script_root,
            python_exec=args.python_exec,
            start_year=args.start_year,
            end_year=args.end_year,
        )

    print(f"Aggregating location rows from: {args.location_root}")
    totals = collect_location_rows(args.location_root)
    print(f"Aggregated disease-month rows: {len(totals):,}")

    written = write_output(args.output, totals, replace=args.replace)
    print(f"Wrote AU national CSV: {args.output} ({written:,} rows)")


if __name__ == "__main__":
    main()
