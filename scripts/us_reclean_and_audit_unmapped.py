#!/usr/bin/env python3
"""Re-clean US NNDSS weekly history output and audit unmapped labels.

This script is intentionally conservative:
- it reads the already-normalized national current-week export
- removes exact duplicate rows
- audits label-to-mapping coverage against configs/mapping/us.csv
- writes an aggregated unmapped report with candidate reasons

The goal is to make unmapped labels explainable before we change any
import or mapping behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path("/home/likangguo/globalID/ID_US/US_NID/GetData/output/nndss_2000_2022_national_current_week_cases.csv")
DEFAULT_MAPPING = ROOT / "configs/mapping/us.csv"
DEFAULT_RECLEANED = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_cases_recleaned.csv"
)
DEFAULT_UNMAPPED_REPORT = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_unmapped_report.csv"
)
DEFAULT_CANONICALIZED = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_cases_canonicalized.csv"
)
DEFAULT_SUMMARY_JSON = Path(
    "/home/likangguo/globalID/ID_US/US_NID/GetData/output/"
    "nndss_2000_2022_national_current_week_unmapped_summary.json"
)

COMMON_DETAIL_TOKENS = {
    "acute",
    "chronic",
    "confirmed",
    "probable",
    "congenital",
    "imported",
    "indigenous",
    "neuroinvasive",
    "non neuroinvasive",
    "non-neuroinvasive",
    "all ages",
    "age <5 years",
    "age < 5 years",
    "age <5",
    "age < 5",
}


@dataclass(frozen=True)
class MappingOption:
    disease_id: str
    canonical_name: str
    matched_name: str
    source_kind: str


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def normalize_exact_key(value: str) -> str:
    return normalize_text(value).lower()


def normalize_simple_key(value: str) -> str:
    text = normalize_text(value).lower()
    text = text.replace("&", " and ")
    text = text.replace("/", " and ")
    text = text.replace("e. coli", "escherichia coli")
    text = text.replace("h. influenzae", "haemophilus influenzae")
    text = text.replace("stec", "shiga toxin producing escherichia coli")
    text = re.sub(r"\band\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_mapping(mapping_csv: Path) -> tuple[Dict[str, List[MappingOption]], Dict[str, List[MappingOption]]]:
    exact_lookup: Dict[str, List[MappingOption]] = defaultdict(list)
    simple_lookup: Dict[str, List[MappingOption]] = defaultdict(list)

    with mapping_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            disease_id = normalize_text(row.get("disease_id"))
            local_name = normalize_text(row.get("local_name"))
            if not disease_id or not local_name:
                continue

            raw_names: List[tuple[str, str]] = [(local_name, "local_name")]
            aliases = normalize_text(row.get("aliases"))
            if aliases:
                raw_names.extend((normalize_text(alias), "alias") for alias in aliases.split("|"))

            for name, source_kind in raw_names:
                if not name:
                    continue
                option = MappingOption(
                    disease_id=disease_id,
                    canonical_name=local_name,
                    matched_name=name,
                    source_kind=source_kind,
                )
                exact_lookup[normalize_exact_key(name)].append(option)
                simple_lookup[normalize_simple_key(name)].append(option)

    return dict(exact_lookup), dict(simple_lookup)


def unique_options(options: Iterable[MappingOption]) -> List[MappingOption]:
    seen: set[tuple[str, str]] = set()
    result: List[MappingOption] = []
    for option in options:
        key = (option.disease_id, option.canonical_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(option)
    return result


def lookup_unique(exact_lookup: Dict[str, List[MappingOption]], simple_lookup: Dict[str, List[MappingOption]], value: str) -> tuple[str, List[MappingOption]]:
    exact_key = normalize_exact_key(value)
    if exact_key and exact_key in exact_lookup:
        options = unique_options(exact_lookup[exact_key])
        if len({option.disease_id for option in options}) == 1:
            return "exact", options
        return "exact_ambiguous", options

    simple_key = normalize_simple_key(value)
    if simple_key and simple_key in simple_lookup:
        options = unique_options(simple_lookup[simple_key])
        if len({option.disease_id for option in options}) == 1:
            return "simple", options
        return "simple_ambiguous", options

    return "", []


def add_candidate(candidates: List[str], seen: set[str], value: str) -> None:
    value = normalize_text(value)
    if not value:
        return
    lowered = value.lower()
    if lowered in seen:
        return
    seen.add(lowered)
    candidates.append(value)


def strip_common_suffixes(value: str) -> List[str]:
    results: List[str] = []
    current = normalize_text(value)
    seen: set[str] = set()
    add_candidate(results, seen, current)

    working = current
    while "," in working:
        working = normalize_text(working.rsplit(",", 1)[0])
        add_candidate(results, seen, working)

    if "(" in current:
        add_candidate(results, seen, current.split("(", 1)[0])

    patterns = [
        r"\bvirus infections?\b",
        r"\binfections?\b",
        r"\bdisease\b",
        r"\bprobable\b",
        r"\bconfirmed\b",
    ]
    for pattern in patterns:
        stripped = normalize_text(re.sub(pattern, "", current, flags=re.IGNORECASE))
        add_candidate(results, seen, stripped)

    return results


def base_candidates(label: str, family: str, detail: str) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    family_norm = normalize_text(family)
    detail_norm = normalize_text(detail)

    if family_norm.lower().startswith("hepatitis") and detail_norm:
        detail_match = re.search(r"\b([ABC])\b", detail_norm, flags=re.IGNORECASE)
        detail_letter = detail_match.group(1).upper() if detail_match else ""
        if detail_letter in {"A", "B", "C"}:
            add_candidate(candidates, seen, f"Hepatitis {detail_letter}")

    for value in strip_common_suffixes(family):
        add_candidate(candidates, seen, value)

    for value in strip_common_suffixes(label):
        add_candidate(candidates, seen, value)

    for raw_value in (label, family):
        raw_norm = normalize_text(raw_value)
        if not raw_norm:
            continue
        singularized = raw_norm.replace("diseases", "disease")
        add_candidate(candidates, seen, singularized)

    if family_norm.lower().startswith("west nile virus disease"):
        add_candidate(candidates, seen, "West Nile virus")
    if family_norm.lower().startswith("dengue virus infection") or family_norm.lower().startswith("dengue virus infections"):
        add_candidate(candidates, seen, "Dengue")
    if family_norm.lower() == "chlamydia":
        add_candidate(candidates, seen, "Chlamydia trachomatis infection")
    if family_norm.lower().startswith("varicella"):
        add_candidate(candidates, seen, "Varicella")
    if family_norm.lower().startswith("rabies"):
        add_candidate(candidates, seen, "Rabies")
    if family_norm.lower().startswith("rubella"):
        add_candidate(candidates, seen, "Rubella")
    if family_norm.lower().startswith("measles"):
        add_candidate(candidates, seen, "Measles")
    if family_norm.lower().startswith("syphilis"):
        add_candidate(candidates, seen, "Syphilis")
    if family_norm.lower().startswith("haemophilus influenzae"):
        add_candidate(candidates, seen, "Haemophilus influenzae")
    if family_norm.lower().startswith("vibriosis"):
        add_candidate(candidates, seen, "Vibriosis")
    if family_norm.lower().startswith("meningococcal diseases") or family_norm.lower().startswith("meningococcal disease"):
        add_candidate(candidates, seen, "Meningococcal disease")
    if "toxic shock syndrome" in family_norm.lower():
        add_candidate(candidates, seen, "Toxic shock syndrome")
    if family_norm.lower().startswith("streptococcus pneumoniae"):
        add_candidate(candidates, seen, "Invasive pneumococcal disease")
    if family_norm.lower().startswith("invasive pneumococcal disease"):
        add_candidate(candidates, seen, "Invasive pneumococcal disease")
    if family_norm.lower().startswith("poliovirus infection"):
        add_candidate(candidates, seen, "Poliomyelitis")
    if family_norm.lower().endswith("virus disease"):
        virus_base = normalize_text(re.sub(r"\bvirus disease\b", "", family_norm, flags=re.IGNORECASE))
        add_candidate(candidates, seen, f"{virus_base} virus")
        add_candidate(candidates, seen, virus_base)
    if family_norm.lower().endswith("virus"):
        add_candidate(candidates, seen, re.sub(r"\bvirus\b", "", family_norm, flags=re.IGNORECASE).strip())

    return candidates


def reason_has_parser_artifact(label: str, family: str, detail: str) -> bool:
    raw = " ".join([normalize_text(label), normalize_text(family), normalize_text(detail)]).lower()
    return (
        "unnamed:" in raw
        or "continued" in raw
        or "level_0" in raw
        or "level_1" in raw
    )


@dataclass(frozen=True)
class CanonicalResolution:
    canonical_label: str
    disease_id: str
    resolution_kind: str
    matched_name: str


def lookup_canonical_resolution(
    exact_lookup: Dict[str, List[MappingOption]],
    simple_lookup: Dict[str, List[MappingOption]],
    value: str,
    *,
    resolution_kind: str,
) -> CanonicalResolution | None:
    match_kind, options = lookup_unique(exact_lookup, simple_lookup, value)
    if match_kind in {"exact", "simple"} and len(options) == 1:
        option = options[0]
        return CanonicalResolution(
            canonical_label=option.canonical_name,
            disease_id=option.disease_id,
            resolution_kind=f"{resolution_kind}:{match_kind}",
            matched_name=option.matched_name,
        )
    return None


def resolve_detail_mapping(
    detail: str,
    *,
    exact_lookup: Dict[str, List[MappingOption]],
    simple_lookup: Dict[str, List[MappingOption]],
    resolution_kind: str,
) -> CanonicalResolution | None:
    for candidate in base_candidates(detail, detail, ""):
        resolved = lookup_canonical_resolution(
            exact_lookup,
            simple_lookup,
            candidate,
            resolution_kind=resolution_kind,
        )
        if resolved is not None:
            return resolved
    return None


def resolve_canonical_mapping(
    label: str,
    family: str,
    detail: str,
    *,
    exact_lookup: Dict[str, List[MappingOption]],
    simple_lookup: Dict[str, List[MappingOption]],
) -> CanonicalResolution | None:
    label_resolution = lookup_canonical_resolution(
        exact_lookup,
        simple_lookup,
        label,
        resolution_kind="label",
    )
    if label_resolution is not None:
        return label_resolution

    if reason_has_parser_artifact(label, family, detail):
        artifact_detail_resolution = resolve_detail_mapping(
            detail,
            exact_lookup=exact_lookup,
            simple_lookup=simple_lookup,
            resolution_kind="artifact_cleanup",
        )
        if artifact_detail_resolution is not None:
            return artifact_detail_resolution

        for value in (family.replace("(continued)", "").strip(), label.replace("(continued)", "").strip()):
            resolved = lookup_canonical_resolution(
                exact_lookup,
                simple_lookup,
                value,
                resolution_kind="artifact_cleanup",
            )
            if resolved is not None:
                return resolved

    family_resolution = lookup_canonical_resolution(
        exact_lookup,
        simple_lookup,
        family,
        resolution_kind="family",
    )
    detail_resolution = resolve_detail_mapping(
        detail,
        exact_lookup=exact_lookup,
        simple_lookup=simple_lookup,
        resolution_kind="detail",
    )
    if detail_resolution is not None and (
        family_resolution is None or family_resolution.disease_id != detail_resolution.disease_id
    ):
        return detail_resolution
    if family_resolution is not None:
        return family_resolution

    for candidate in base_candidates(label, family, detail):
        resolved = lookup_canonical_resolution(
            exact_lookup,
            simple_lookup,
            candidate,
            resolution_kind="candidate",
        )
        if resolved is not None:
            return resolved

    return None


def semantic_unmapped_reason(detail: str, all_blank: bool) -> str:
    detail_norm = normalize_simple_key(detail)
    if detail_norm in {normalize_simple_key(token) for token in COMMON_DETAIL_TOKENS}:
        return "subtype_or_status_variant"
    if all_blank:
        return "no_current_week_value"
    return "missing_mapping"


def dedupe_rows(rows: Sequence[dict[str, str]], fieldnames: Sequence[str]) -> tuple[List[dict[str, str]], int]:
    seen: set[tuple[str, ...]] = set()
    deduped: List[dict[str, str]] = []
    duplicates_removed = 0

    for row in rows:
        key = tuple(normalize_text(row.get(field, "")) for field in fieldnames)
        if key in seen:
            duplicates_removed += 1
            continue
        seen.add(key)
        deduped.append(row)

    return deduped, duplicates_removed


def load_rows(input_csv: Path) -> tuple[List[str], List[dict[str, str]]]:
    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {input_csv}")
        rows = [{field: normalize_text(value) for field, value in row.items()} for row in reader]
        return reader.fieldnames, rows


def write_rows(output_csv: Path, fieldnames: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_unmapped_report(
    rows: Sequence[dict[str, str]],
    exact_lookup: Dict[str, List[MappingOption]],
    simple_lookup: Dict[str, List[MappingOption]],
) -> tuple[List[dict[str, str]], dict[str, object]]:
    grouped: Dict[str, List[dict[str, str]]] = defaultdict(list)
    mapped_direct_rows = 0
    mapped_direct_labels: set[str] = set()

    for row in rows:
        label = normalize_text(row.get("Label"))
        grouped[label].append(row)
        match_kind, match_options = lookup_unique(exact_lookup, simple_lookup, label)
        if match_kind in {"exact", "simple"} and match_options:
            mapped_direct_rows += 1
            mapped_direct_labels.add(label)

    report_rows: List[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()

    for label, label_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        match_kind, match_options = lookup_unique(exact_lookup, simple_lookup, label)
        if match_kind in {"exact", "simple"} and match_options:
            continue

        sample = label_rows[0]
        family = normalize_text(sample.get("disease_family"))
        detail = normalize_text(sample.get("disease_detail"))
        blank_rows = sum(1 for row in label_rows if not normalize_text(row.get("metric_value")))
        all_blank = blank_rows == len(label_rows)

        reason = ""
        candidate_kind = ""
        candidate_options: List[MappingOption] = []

        family_match_kind, family_match_options = lookup_unique(exact_lookup, simple_lookup, family)
        detail_resolution = resolve_detail_mapping(
            detail,
            exact_lookup=exact_lookup,
            simple_lookup=simple_lookup,
            resolution_kind="detail",
        )
        if family_match_kind in {"exact", "simple"} and family_match_options:
            family_ids = {option.disease_id for option in family_match_options}
            if detail_resolution is not None and detail_resolution.disease_id not in family_ids:
                reason = "detail_maps_to_different_disease_than_family_needs_review"
                candidate_kind = detail_resolution.resolution_kind
                candidate_options = [
                    MappingOption(
                        disease_id=detail_resolution.disease_id,
                        canonical_name=detail_resolution.canonical_label,
                        matched_name=detail_resolution.matched_name,
                        source_kind="detail_resolution",
                    )
                ]
            else:
                reason = "family_maps_detail_needs_alias_or_collapse" if detail else "family_maps_label_variant"
                candidate_kind = f"{family_match_kind}_family"
                candidate_options = family_match_options
        elif detail_resolution is not None:
            reason = "detail_maps_more_specific_disease"
            candidate_kind = detail_resolution.resolution_kind
            candidate_options = [
                MappingOption(
                    disease_id=detail_resolution.disease_id,
                    canonical_name=detail_resolution.canonical_label,
                    matched_name=detail_resolution.matched_name,
                    source_kind="detail_resolution",
                )
            ]
        else:
            for candidate in base_candidates(label, family, detail):
                base_kind, base_options = lookup_unique(exact_lookup, simple_lookup, candidate)
                if base_kind in {"exact", "simple"} and base_options:
                    reason = "base_disease_maps_but_current_label_is_subtype_or_variant"
                    candidate_kind = f"{base_kind}_base_candidate"
                    candidate_options = base_options
                    break

        if not reason and reason_has_parser_artifact(label, family, detail):
            reason = "parser_artifact"

        if not reason:
            reason = semantic_unmapped_reason(detail, all_blank)

        candidate_id = ""
        candidate_names = ""
        if candidate_options:
            candidate_id = "|".join(sorted({option.disease_id for option in candidate_options}))
            candidate_names = " | ".join(sorted({option.canonical_name for option in candidate_options}))

        sample_raw_labels = " | ".join(
            sorted({normalize_text(row.get("label_raw")) for row in label_rows if normalize_text(row.get("label_raw"))})[:3]
        )
        sample_files = " | ".join(
            sorted({normalize_text(row.get("source_file")) for row in label_rows if normalize_text(row.get("source_file"))})[:3]
        )
        years = sorted({normalize_text(row.get("Current MMWR Year")) for row in label_rows if normalize_text(row.get("Current MMWR Year"))})

        report_rows.append(
            {
                "label": label,
                "disease_family": family,
                "disease_detail": detail,
                "rows": str(len(label_rows)),
                "weeks": str(len({(row.get('Current MMWR Year', ''), row.get('MMWR WEEK', '')) for row in label_rows})),
                "first_year": years[0] if years else "",
                "last_year": years[-1] if years else "",
                "blank_value_rows": str(blank_rows),
                "non_blank_value_rows": str(len(label_rows) - blank_rows),
                "all_values_blank": "true" if all_blank else "false",
                "reason": reason,
                "candidate_match_kind": candidate_kind,
                "candidate_disease_id": candidate_id,
                "candidate_mapping_name": candidate_names,
                "sample_raw_labels": sample_raw_labels,
                "sample_source_files": sample_files,
            }
        )
        reason_counts[reason] += len(label_rows)

    summary = {
        "unique_labels": len(grouped),
        "mapped_direct_labels": len(mapped_direct_labels),
        "mapped_direct_rows": mapped_direct_rows,
        "unmapped_unique_labels": len(report_rows),
        "unmapped_rows": sum(int(row["rows"]) for row in report_rows),
        "reason_counts": dict(reason_counts.most_common()),
    }

    return report_rows, summary


def build_canonicalized_rows(
    rows: Sequence[dict[str, str]],
    exact_lookup: Dict[str, List[MappingOption]],
    simple_lookup: Dict[str, List[MappingOption]],
) -> tuple[List[dict[str, str]], dict[str, int]]:
    canonicalized_rows: List[dict[str, str]] = []
    resolved_rows = 0
    unresolved_rows = 0
    resolved_labels: set[tuple[str, str]] = set()
    unresolved_labels: set[str] = set()
    resolution_counts: Counter[str] = Counter()

    for row in rows:
        label = normalize_text(row.get("Label"))
        family = normalize_text(row.get("disease_family"))
        detail = normalize_text(row.get("disease_detail"))

        resolution = resolve_canonical_mapping(
            label,
            family,
            detail,
            exact_lookup=exact_lookup,
            simple_lookup=simple_lookup,
        )

        enriched = dict(row)
        enriched["CanonicalLabel"] = resolution.canonical_label if resolution else ""
        enriched["CanonicalDiseaseId"] = resolution.disease_id if resolution else ""
        enriched["CanonicalResolution"] = resolution.resolution_kind if resolution else ""
        enriched["CanonicalMatchedName"] = resolution.matched_name if resolution else ""
        canonicalized_rows.append(enriched)

        if resolution is not None:
            resolved_rows += 1
            resolved_labels.add((label, resolution.canonical_label))
            resolution_counts[resolution.resolution_kind] += 1
        else:
            unresolved_rows += 1
            unresolved_labels.add(label)

    stats = {
        "canonicalized_rows": resolved_rows,
        "remaining_unresolved_rows": unresolved_rows,
        "canonicalized_unique_labels": len(resolved_labels),
        "remaining_unresolved_labels": len(unresolved_labels),
        "resolution_counts": dict(resolution_counts.most_common()),
    }
    return canonicalized_rows, stats


def print_console_summary(report_rows: Sequence[dict[str, str]], summary: dict[str, object], print_limit: int) -> None:
    print("")
    print("=== Re-clean Summary ===")
    print(f"Unique labels: {summary['unique_labels']}")
    print(f"Mapped directly: {summary['mapped_direct_labels']} labels / {summary['mapped_direct_rows']} rows")
    print(f"Unmapped: {summary['unmapped_unique_labels']} labels / {summary['unmapped_rows']} rows")
    print("")
    print("=== Unmapped Reasons (by rows) ===")
    for reason, count in summary["reason_counts"].items():
        print(f"{reason}: {count}")
    print("")
    print(f"=== Top Unmapped Labels (top {print_limit}) ===")
    for row in report_rows[:print_limit]:
        print(
            f"{row['rows']:>5} rows | {row['reason']:<48} | "
            f"{row['label']}"
        )
        if row["candidate_disease_id"] or row["candidate_mapping_name"]:
            print(
                f"      candidate={row['candidate_disease_id']} | "
                f"{row['candidate_mapping_name']}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-clean US NNDSS current-week output and audit unmapped mappings.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input current-week CSV.")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING, help="US mapping CSV.")
    parser.add_argument("--output-clean", type=Path, default=DEFAULT_RECLEANED, help="Deduped output CSV.")
    parser.add_argument("--output-canonical", type=Path, default=DEFAULT_CANONICALIZED, help="Canonicalized output CSV.")
    parser.add_argument("--output-unmapped", type=Path, default=DEFAULT_UNMAPPED_REPORT, help="Aggregated unmapped report CSV.")
    parser.add_argument("--output-summary", type=Path, default=DEFAULT_SUMMARY_JSON, help="Summary JSON path.")
    parser.add_argument("--print-limit", type=int, default=80, help="How many unmapped labels to print.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    fieldnames, rows = load_rows(args.input)
    deduped_rows, duplicates_removed = dedupe_rows(rows, fieldnames)
    write_rows(args.output_clean, fieldnames, deduped_rows)

    exact_lookup, simple_lookup = load_mapping(args.mapping)
    canonicalized_rows, canonical_stats = build_canonicalized_rows(deduped_rows, exact_lookup, simple_lookup)
    canonical_fieldnames = list(fieldnames) + [
        "CanonicalLabel",
        "CanonicalDiseaseId",
        "CanonicalResolution",
        "CanonicalMatchedName",
    ]
    write_rows(args.output_canonical, canonical_fieldnames, canonicalized_rows)
    report_rows, report_summary = build_unmapped_report(deduped_rows, exact_lookup, simple_lookup)

    report_fieldnames = [
        "label",
        "disease_family",
        "disease_detail",
        "rows",
        "weeks",
        "first_year",
        "last_year",
        "blank_value_rows",
        "non_blank_value_rows",
        "all_values_blank",
        "reason",
        "candidate_match_kind",
        "candidate_disease_id",
        "candidate_mapping_name",
        "sample_raw_labels",
        "sample_source_files",
    ]
    write_rows(args.output_unmapped, report_fieldnames, report_rows)

    full_summary = {
        "input_csv": str(args.input),
        "mapping_csv": str(args.mapping),
        "input_rows": len(rows),
        "deduped_rows": len(deduped_rows),
        "duplicates_removed": duplicates_removed,
        "recleaned_csv": str(args.output_clean),
        "canonicalized_csv": str(args.output_canonical),
        "unmapped_report_csv": str(args.output_unmapped),
        **canonical_stats,
        **report_summary,
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(json.dumps(full_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Input rows: {len(rows)}")
    print(f"Deduped rows: {len(deduped_rows)}")
    print(f"Duplicates removed: {duplicates_removed}")
    print(f"Recleaned CSV: {args.output_clean}")
    print(f"Canonicalized CSV: {args.output_canonical}")
    print(f"Unmapped report: {args.output_unmapped}")
    print(f"Summary JSON: {args.output_summary}")
    print("")
    print("=== Canonicalization Summary ===")
    print(f"Canonicalized rows: {canonical_stats['canonicalized_rows']}")
    print(f"Remaining unresolved rows: {canonical_stats['remaining_unresolved_rows']}")
    print(f"Canonicalized unique labels: {canonical_stats['canonicalized_unique_labels']}")
    print(f"Remaining unresolved labels: {canonical_stats['remaining_unresolved_labels']}")
    print(f"Resolution kinds: {canonical_stats['resolution_counts']}")
    print_console_summary(report_rows, report_summary, args.print_limit)


if __name__ == "__main__":
    main()
