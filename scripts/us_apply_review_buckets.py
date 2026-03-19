#!/usr/bin/env python3
"""Split US review-report rows into handling buckets.

This script does not change disease mappings. It operationalizes the
review report so downstream work can treat different classes of rows
separately:

- clean_direct: no review flags
- recovered_specific: parser/detail recovery mapped to a specific disease
- aggregate_total_variant: aggregate total-style labels that should stay
  separate from partial-scope rows
- subtype_or_status_variant: subtype/status/travel/clinical variants that
  should not be merged into the main disease-total import
- partial_scope_or_out_of_scope: age/serogroup/animal/perinatal/non-HPS
  rows that are unsafe for direct disease-total import
- policy_fallback: generic fallback mappings that still need human review
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


DEFAULT_CANONICALIZED = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_cases_canonicalized.csv"
)
DEFAULT_REVIEW_REPORT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_mapping_review_report.csv"
)
DEFAULT_OUTPUT_MASTER = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_handled.csv"
)
DEFAULT_OUTPUT_LABEL_SUMMARY = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_handling_label_summary.csv"
)
DEFAULT_OUTPUT_SUMMARY = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_handling_summary.json"
)

BUCKET_ORDER = [
    "clean_direct",
    "recovered_specific",
    "aggregate_total_variant",
    "subtype_or_status_variant",
    "partial_scope_or_out_of_scope",
    "policy_fallback",
]

BUCKET_RECOMMENDATIONS = {
    "clean_direct": "keep_as_main_series",
    "recovered_specific": "keep_separate_recovered_series",
    "aggregate_total_variant": "keep_separate_aggregate_series",
    "subtype_or_status_variant": "keep_separate_variant_series",
    "partial_scope_or_out_of_scope": "quarantine_from_disease_total_import",
    "policy_fallback": "manual_review_before_import",
}

BUCKET_NOTES = {
    "clean_direct": "No review flags for this label.",
    "recovered_specific": "Recovered from parser/detail cleanup to a specific disease.",
    "aggregate_total_variant": "Aggregate total-style label kept separate from partial-scope and subtype rows.",
    "subtype_or_status_variant": "Subtype/status/clinical-travel variant kept separate from the main series.",
    "partial_scope_or_out_of_scope": "Partial-scope or out-of-scope label not suitable for direct disease-total import.",
    "policy_fallback": "Mapped through a generic fallback disease bucket and still needs human review.",
}

PARTIAL_SCOPE_PATTERNS = [
    re.compile(r"\banimal\b", re.IGNORECASE),
    re.compile(r"non-hantavirus pulmonary syndrome", re.IGNORECASE),
    re.compile(r"\bperinatal\b", re.IGNORECASE),
    re.compile(r"age\s*<", re.IGNORECASE),
    re.compile(r"\bage <\s*5", re.IGNORECASE),
    re.compile(r"\bserogroup b\b", re.IGNORECASE),
    re.compile(r"\bserogroups acwy\b", re.IGNORECASE),
    re.compile(r"\bother serogroups\b", re.IGNORECASE),
    re.compile(r"\bunknown serogroup\b", re.IGNORECASE),
    re.compile(r"\bserotype b\b", re.IGNORECASE),
    re.compile(r"\bnon-b serotype\b", re.IGNORECASE),
    re.compile(r"\bnontypeable\b", re.IGNORECASE),
    re.compile(r"\bunknown serotype\b", re.IGNORECASE),
]

AGGREGATE_TOTAL_PATTERNS = [
    re.compile(r"\ball ages\b", re.IGNORECASE),
    re.compile(r"\ball serogroups\b", re.IGNORECASE),
    re.compile(r"\ball groups\b", re.IGNORECASE),
    re.compile(r"\ball ages,\s*all serotypes\b", re.IGNORECASE),
    re.compile(r"\ball serotypes\b", re.IGNORECASE),
]


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def load_rows(csv_path: Path) -> tuple[List[str], List[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")
        rows = [{field: normalize_text(value) for field, value in row.items()} for row in reader]
        return reader.fieldnames, rows


def write_rows(output_csv: Path, fieldnames: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def load_review_lookup(review_rows: Sequence[dict[str, str]]) -> Dict[str, dict[str, object]]:
    lookup: Dict[str, dict[str, object]] = {}
    for row in review_rows:
        label = normalize_text(row.get("label"))
        reasons = [reason for reason in normalize_text(row.get("risk_reasons")).split("|") if reason]
        lookup[label] = {
            "risk_reasons": reasons,
            "canonical_disease_id": normalize_text(row.get("canonical_disease_id")),
            "canonical_label": normalize_text(row.get("canonical_label")),
            "canonical_resolution": normalize_text(row.get("canonical_resolution")),
        }
    return lookup


def matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify_bucket(row: dict[str, str], risk_reasons: set[str]) -> tuple[str, str]:
    label = normalize_text(row.get("Label"))
    text = " ".join(
        [
            label,
            normalize_text(row.get("disease_family")),
            normalize_text(row.get("disease_detail")),
        ]
    ).lower()

    if not risk_reasons:
        return "clean_direct", "Label is outside the review report."

    if "policy_fallback_mapping" in risk_reasons:
        return "policy_fallback", "Uses a generic fallback disease bucket."

    if matches_any(PARTIAL_SCOPE_PATTERNS, text) or {
        "animal_scope_variant",
        "non_hps_variant",
        "perinatal_variant",
    } & risk_reasons:
        return "partial_scope_or_out_of_scope", "Label is partial-scope or outside the target disease-total series."

    if matches_any(AGGREGATE_TOTAL_PATTERNS, text) or {
        "age_scope_variant",
        "serogroup_or_serotype_variant",
    } & risk_reasons:
        return "aggregate_total_variant", "Aggregate total-style series kept separate for scope control."

    if "detail_or_artifact_override" in risk_reasons:
        return "recovered_specific", "Recovered from parser/detail cleanup to a specific disease."

    return "subtype_or_status_variant", "Subtype, status, travel, or clinical variant kept separate."


def bucket_output_path(master_output: Path, bucket: str) -> Path:
    return master_output.with_name(f"{master_output.stem}_{bucket}{master_output.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply handling buckets to the US review report.")
    parser.add_argument("--canonicalized", type=Path, default=DEFAULT_CANONICALIZED, help="Canonicalized row-level CSV.")
    parser.add_argument("--review-report", type=Path, default=DEFAULT_REVIEW_REPORT, help="Review report CSV.")
    parser.add_argument("--output-master", type=Path, default=DEFAULT_OUTPUT_MASTER, help="Annotated master CSV.")
    parser.add_argument("--output-label-summary", type=Path, default=DEFAULT_OUTPUT_LABEL_SUMMARY, help="Per-label handling summary CSV.")
    parser.add_argument("--output-summary", type=Path, default=DEFAULT_OUTPUT_SUMMARY, help="Handling summary JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    canonical_fieldnames, canonical_rows = load_rows(args.canonicalized)
    _, review_rows = load_rows(args.review_report)
    review_lookup = load_review_lookup(review_rows)

    bucketed_rows: Dict[str, List[dict[str, str]]] = defaultdict(list)
    label_summary: Dict[str, dict[str, object]] = {}
    bucket_row_counts: Counter[str] = Counter()
    bucket_label_counts: Counter[str] = Counter()

    master_rows: List[dict[str, str]] = []
    master_fieldnames = list(canonical_fieldnames) + [
        "ReviewRiskReasons",
        "HandlingBucket",
        "HandlingRecommendation",
        "HandlingNote",
    ]

    for row in canonical_rows:
        label = normalize_text(row.get("Label"))
        review_meta = review_lookup.get(label, {})
        risk_reasons = set(review_meta.get("risk_reasons", []))
        bucket, detail_note = classify_bucket(row, risk_reasons)
        annotated = dict(row)
        annotated["ReviewRiskReasons"] = "|".join(sorted(risk_reasons))
        annotated["HandlingBucket"] = bucket
        annotated["HandlingRecommendation"] = BUCKET_RECOMMENDATIONS[bucket]
        annotated["HandlingNote"] = detail_note or BUCKET_NOTES[bucket]
        master_rows.append(annotated)
        bucketed_rows[bucket].append(annotated)
        bucket_row_counts[bucket] += 1

        summary_entry = label_summary.setdefault(
            label,
            {
                "label": label,
                "rows": 0,
                "canonical_disease_id": normalize_text(row.get("CanonicalDiseaseId")),
                "canonical_label": normalize_text(row.get("CanonicalLabel")),
                "canonical_resolution": normalize_text(row.get("CanonicalResolution")),
                "review_risk_reasons": "|".join(sorted(risk_reasons)),
                "handling_bucket": bucket,
                "handling_recommendation": BUCKET_RECOMMENDATIONS[bucket],
                "handling_note": detail_note or BUCKET_NOTES[bucket],
            },
        )
        summary_entry["rows"] += 1

    for bucket in BUCKET_ORDER:
        bucket_label_counts[bucket] = len({row["Label"] for row in bucketed_rows[bucket]})

    write_rows(args.output_master, master_fieldnames, master_rows)
    for bucket in BUCKET_ORDER:
        write_rows(bucket_output_path(args.output_master, bucket), master_fieldnames, bucketed_rows[bucket])

    label_summary_rows = sorted(
        (
            {
                "label": value["label"],
                "rows": str(value["rows"]),
                "canonical_disease_id": value["canonical_disease_id"],
                "canonical_label": value["canonical_label"],
                "canonical_resolution": value["canonical_resolution"],
                "review_risk_reasons": value["review_risk_reasons"],
                "handling_bucket": value["handling_bucket"],
                "handling_recommendation": value["handling_recommendation"],
                "handling_note": value["handling_note"],
            }
            for value in label_summary.values()
        ),
        key=lambda row: (-int(row["rows"]), row["label"]),
    )
    write_rows(
        args.output_label_summary,
        [
            "label",
            "rows",
            "canonical_disease_id",
            "canonical_label",
            "canonical_resolution",
            "review_risk_reasons",
            "handling_bucket",
            "handling_recommendation",
            "handling_note",
        ],
        label_summary_rows,
    )

    summary = {
        "canonicalized_csv": str(args.canonicalized),
        "review_report_csv": str(args.review_report),
        "output_master_csv": str(args.output_master),
        "output_label_summary_csv": str(args.output_label_summary),
        "bucket_csvs": {bucket: str(bucket_output_path(args.output_master, bucket)) for bucket in BUCKET_ORDER},
        "total_rows": len(master_rows),
        "total_unique_labels": len(label_summary_rows),
        "bucket_row_counts": {bucket: bucket_row_counts[bucket] for bucket in BUCKET_ORDER},
        "bucket_unique_label_counts": {bucket: bucket_label_counts[bucket] for bucket in BUCKET_ORDER},
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Master CSV: {args.output_master}")
    print(f"Label summary CSV: {args.output_label_summary}")
    print(f"Summary JSON: {args.output_summary}")
    print("")
    print("=== Bucket Counts ===")
    for bucket in BUCKET_ORDER:
        print(
            f"{bucket}: "
            f"{bucket_row_counts[bucket]} rows / "
            f"{bucket_label_counts[bucket]} labels / "
            f"{bucket_output_path(args.output_master, bucket)}"
        )


if __name__ == "__main__":
    main()
