#!/usr/bin/env python3
"""Build a conservative US weekly history import set from review buckets."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


DEFAULT_INPUT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_handled.csv"
)
DEFAULT_OUTPUT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_import_ready_strict.csv"
)
DEFAULT_SUMMARY = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_import_ready_strict_summary.json"
)

SAFE_RECOVERED_DISEASE_IDS = {"D021", "D050", "D052", "D053", "D096", "D107", "D128"}
SOURCE_NAME = "US CDC NNDSS"
UPDATE_MODE = "strict_history_import_ready_v1"

OUTPUT_COLUMNS = [
    "Date",
    "Current MMWR Year",
    "MMWR WEEK",
    "Reporting Area",
    "Label",
    "Cases",
    "CanonicalDiseaseId",
    "CanonicalLabel",
    "CanonicalResolution",
    "HandlingBucket",
    "SelectedReason",
    "Source",
    "UpdateMode",
    "RawDiseaseLabel",
    "label_raw",
    "source_file",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a strict US import-ready weekly history CSV.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Handled review CSV.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output import-ready CSV.")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY, help="Output summary JSON.")
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


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{k: normalize_text(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def write_rows(output_csv: Path, rows: Sequence[dict[str, str]]) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_COLUMNS})


def mmwr_week_end_date(year: int, week: int) -> date:
    jan_4 = date(year, 1, 4)
    week_1_start = jan_4 - timedelta(days=(jan_4.weekday() + 1) % 7)
    return week_1_start + timedelta(weeks=week - 1, days=6)


def row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        normalize_text(row.get("Current MMWR Year")),
        normalize_text(row.get("MMWR WEEK")),
        normalize_text(row.get("CanonicalDiseaseId")),
    )


def choose_best_row(rows: Sequence[dict[str, str]]) -> dict[str, str]:
    def rank(row: dict[str, str]) -> tuple[int, int, int, str]:
        label = normalize_text(row.get("Label")).lower()
        return (
            0 if "confirmed" not in label else 1,
            0 if "|" not in label else 1,
            len(label),
            label,
        )

    return sorted(rows, key=rank)[0]


def is_safe_aggregate_total(row: dict[str, str]) -> bool:
    label = normalize_text(row.get("Label")).lower()
    disease_id = normalize_text(row.get("CanonicalDiseaseId"))

    if disease_id == "D100":
        return "all ages" in label and "all serotypes" in label

    if disease_id == "D106":
        return (
            "all ages" in label
            and "probable" not in label
            and "streptococcus pneumoniae" not in label
            and "drug resistant" not in label
        )

    if disease_id == "D110":
        return ("all serogroups" in label or "all groups" in label) and "unknown" not in label

    return False


def build_output_row(row: dict[str, str], *, cases: int, selected_reason: str) -> dict[str, str]:
    year = int(normalize_text(row.get("Current MMWR Year")))
    week = int(normalize_text(row.get("MMWR WEEK")))
    report_day = mmwr_week_end_date(year, week)
    return {
        "Date": datetime.combine(report_day, time.min).strftime("%Y-%m-%d"),
        "Current MMWR Year": str(year),
        "MMWR WEEK": str(week),
        "Reporting Area": normalize_text(row.get("Reporting Area")),
        "Label": normalize_text(row.get("Label")),
        "Cases": str(max(0, cases)),
        "CanonicalDiseaseId": normalize_text(row.get("CanonicalDiseaseId")),
        "CanonicalLabel": normalize_text(row.get("CanonicalLabel")),
        "CanonicalResolution": normalize_text(row.get("CanonicalResolution")),
        "HandlingBucket": normalize_text(row.get("HandlingBucket")),
        "SelectedReason": selected_reason,
        "Source": SOURCE_NAME,
        "UpdateMode": UPDATE_MODE,
        "RawDiseaseLabel": normalize_text(row.get("Label")),
        "label_raw": normalize_text(row.get("label_raw")),
        "source_file": normalize_text(row.get("source_file")),
    }


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)

    selected_rows: list[dict[str, str]] = []
    selected_keys: set[tuple[str, str, str]] = set()
    skipped_reasons: Counter[str] = Counter()
    selected_by_bucket: Counter[str] = Counter()

    clean_rows = [row for row in rows if normalize_text(row.get("HandlingBucket")) == "clean_direct"]
    for row in clean_rows:
        cases = parse_int(row.get("metric_value"))
        if cases is None:
            skipped_reasons["clean_direct_blank_or_invalid_cases"] += 1
            continue
        key = row_key(row)
        selected_keys.add(key)
        selected_rows.append(build_output_row(row, cases=cases, selected_reason="clean_direct_numeric"))
        selected_by_bucket["clean_direct"] += 1

    aggregate_candidates: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if normalize_text(row.get("HandlingBucket")) != "aggregate_total_variant":
            continue
        if not is_safe_aggregate_total(row):
            skipped_reasons["aggregate_total_out_of_policy"] += 1
            continue
        cases = parse_int(row.get("metric_value"))
        if cases is None:
            skipped_reasons["aggregate_total_blank_or_invalid_cases"] += 1
            continue
        key = row_key(row)
        aggregate_candidates[key].append(row)

    for key, group in aggregate_candidates.items():
        if key in selected_keys:
            skipped_reasons["aggregate_total_overlap_with_selected"] += len(group)
            continue
        values = {parse_int(row.get("metric_value")) for row in group}
        values.discard(None)
        if not values:
            skipped_reasons["aggregate_total_group_without_numeric_cases"] += len(group)
            continue
        if len(values) > 1:
            skipped_reasons["aggregate_total_conflicting_case_values"] += len(group)
            continue
        chosen = choose_best_row([row for row in group if parse_int(row.get("metric_value")) is not None])
        selected_keys.add(key)
        selected_rows.append(
            build_output_row(
                chosen,
                cases=parse_int(chosen.get("metric_value")) or 0,
                selected_reason="aggregate_total_safe_allowlist",
            )
        )
        selected_by_bucket["aggregate_total_variant"] += 1
        skipped_reasons["aggregate_total_duplicate_variants_dropped"] += max(0, len(group) - 1)

    recovered_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if normalize_text(row.get("HandlingBucket")) != "recovered_specific":
            continue
        if normalize_text(row.get("CanonicalDiseaseId")) not in SAFE_RECOVERED_DISEASE_IDS:
            skipped_reasons["recovered_specific_out_of_policy"] += 1
            continue
        recovered_groups[row_key(row)].append(row)

    for key, group in recovered_groups.items():
        if key in selected_keys:
            skipped_reasons["recovered_specific_overlap_with_selected"] += len(group)
            continue

        nonblank = [row for row in group if parse_int(row.get("metric_value")) is not None]
        distinct_values = {parse_int(row.get("metric_value")) for row in nonblank}
        distinct_values.discard(None)

        if not nonblank:
            skipped_reasons["recovered_specific_all_blank"] += len(group)
            continue
        if len(distinct_values) > 1:
            skipped_reasons["recovered_specific_conflicting_case_values"] += len(group)
            continue

        chosen = choose_best_row(nonblank)
        selected_keys.add(key)
        selected_rows.append(
            build_output_row(
                chosen,
                cases=parse_int(chosen.get("metric_value")) or 0,
                selected_reason="recovered_specific_gap_fill",
            )
        )
        selected_by_bucket["recovered_specific"] += 1
        skipped_reasons["recovered_specific_duplicate_variants_dropped"] += max(0, len(group) - 1)

    selected_rows.sort(
        key=lambda row: (
            row["Date"],
            row["CanonicalDiseaseId"],
            row["Label"],
        )
    )
    write_rows(args.output, selected_rows)

    dates = [row["Date"] for row in selected_rows]
    summary = {
        "input_csv": str(args.input),
        "output_csv": str(args.output),
        "input_rows": len(rows),
        "selected_rows": len(selected_rows),
        "selected_unique_keys": len({(row["Date"], row["CanonicalDiseaseId"]) for row in selected_rows}),
        "selected_unique_diseases": len({row["CanonicalDiseaseId"] for row in selected_rows}),
        "selected_by_bucket": dict(selected_by_bucket),
        "skipped_reasons": dict(skipped_reasons.most_common()),
        "date_range": {
            "min": min(dates) if dates else "",
            "max": max(dates) if dates else "",
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Input rows: {len(rows)}")
    print(f"Output rows: {len(selected_rows)}")
    print(f"Output CSV: {args.output}")
    print(f"Summary JSON: {args.summary}")
    print("")
    print("Selected by bucket:")
    for bucket, count in selected_by_bucket.items():
        print(f"  {bucket}: {count}")
    print("")
    print("Skipped reasons:")
    for reason, count in skipped_reasons.most_common():
        print(f"  {reason}: {count}")


if __name__ == "__main__":
    main()
