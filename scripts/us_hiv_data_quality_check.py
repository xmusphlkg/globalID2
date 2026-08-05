#!/usr/bin/env python3
"""Deterministic checks for US CDC NHSS annual HIV/AIDS history rows."""

from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/history/us/history_merged.csv"
DEFAULT_SUMMARY = ROOT / "data/history/us/hiv_quality_summary.json"
NHSS_SOURCE_NAME = "US CDC NHSS"
HIV_LABEL = "HIV diagnoses among persons aged 13 years and older"
AIDS_LABEL = "AIDS classifications"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate US NHSS annual history rows.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--expected-latest-year",
        type=int,
        default=date.today().year - 2,
        help="Minimum expected latest HIV diagnosis year (default: current year - 2).",
    )
    return parser.parse_args()


def check_history(path: Path, expected_latest_year: int) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if (row.get("Source") or "").strip() == NHSS_SOURCE_NAME
        ]

    errors: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    series: dict[str, dict[int, int]] = {HIV_LABEL: {}, AIDS_LABEL: {}}

    for row in rows:
        label = (row.get("RawDiseaseLabel") or row.get("Diseases") or "").strip()
        date_text = (row.get("Date") or "").strip()
        key = (date_text, label)
        if key in seen:
            errors.append(f"duplicate annual observation: {date_text} / {label}")
        seen.add(key)

        if label not in series:
            errors.append(f"unexpected NHSS series: {label}")
            continue
        if not date_text.endswith("-12-31"):
            errors.append(f"annual observation is not dated December 31: {date_text} / {label}")
            continue
        try:
            year = int(date_text[:4])
            cases = int(float((row.get("Cases") or "").replace(",", "")))
        except ValueError:
            errors.append(f"invalid year/case count: {date_text} / {label}")
            continue
        if cases < 0:
            errors.append(f"negative case count: {date_text} / {label}")
        if (row.get("Deaths") or "").strip():
            errors.append(f"death count must be missing for diagnosis-only NHSS row: {date_text} / {label}")
        if not (row.get("__source_file") or "").strip():
            errors.append(f"missing source provenance: {date_text} / {label}")
        series[label][year] = cases

    hiv_years = sorted(series[HIV_LABEL])
    aids_years = sorted(series[AIDS_LABEL])
    if not hiv_years:
        errors.append("HIV diagnosis series is missing")
    else:
        missing_hiv = sorted(set(range(hiv_years[0], hiv_years[-1] + 1)) - set(hiv_years))
        if missing_hiv:
            errors.append(f"HIV diagnosis years are not continuous: {missing_hiv}")
        if hiv_years[-1] < expected_latest_year:
            errors.append(
                f"latest HIV year {hiv_years[-1]} is older than expected {expected_latest_year}"
            )
    if not aids_years:
        errors.append("AIDS classification series is missing")
    else:
        missing_aids = sorted(set(range(aids_years[0], aids_years[-1] + 1)) - set(aids_years))
        if missing_aids:
            errors.append(f"AIDS classification years are not continuous: {missing_aids}")

    for year in sorted(set(hiv_years) & set(aids_years)):
        if series[AIDS_LABEL][year] > series[HIV_LABEL][year]:
            errors.append(f"AIDS classifications exceed all-stage HIV diagnoses in {year}")

    if hiv_years and max(hiv_years) - max(aids_years or hiv_years) > 2:
        warnings.append("AIDS classifications end more than two years before HIV diagnoses")

    return {
        "input_csv": str(path),
        "status": "pass" if not errors else "fail",
        "nhss_rows": len(rows),
        "series": {
            label: {
                "rows": len(values),
                "min_year": min(values) if values else None,
                "max_year": max(values) if values else None,
            }
            for label, values in series.items()
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    args = parse_args()
    summary = check_history(args.input, args.expected_latest_year)
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
