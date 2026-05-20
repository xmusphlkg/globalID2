#!/usr/bin/env python3
"""Audit standard disease and mapping CSVs for likely duplicate concepts."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_STANDARD = ROOT / "configs" / "standard_diseases.csv"
DEFAULT_MAPPING_DIR = ROOT / "configs" / "mapping"

FOOTNOTE_RE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+")
PUNCT_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "associated",
    "by",
    "caused",
    "disease",
    "diseases",
    "fever",
    "infection",
    "infections",
    "of",
    "other",
    "syndrome",
    "the",
    "unspecified",
    "virus",
}


def clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = FOOTNOTE_RE.sub("", text)
    text = text.replace("e. coli", "escherichia coli")
    text = text.replace("e coli", "escherichia coli")
    text = text.replace("vero toxin", "shiga toxin")
    text = text.replace("verotoxin", "shiga toxin")
    text = re.sub(r"\b(vtec|ehec)\b", "stec", text)
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_set(value: object) -> set[str]:
    return {token for token in clean(value).split() if token and token not in STOPWORDS}


def split_aliases(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def is_deprecated(row: dict[str, str]) -> bool:
    haystack = " ".join(
        str(row.get(key, "") or "")
        for key in ("standard_name_en", "description", "source")
    ).lower()
    return "deprecated duplicate" in haystack or "do not use" in haystack


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def standard_duplicate_findings(rows: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    high: list[str] = []
    medium: list[str] = []
    active = [row for row in rows if not is_deprecated(row)]

    for field, label in (
        ("standard_name_zh", "Chinese name"),
        ("standard_name_en", "English name"),
    ):
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in active:
            raw = str(row.get(field, "") or "").strip()
            key = raw if field == "standard_name_zh" else clean(raw)
            if key:
                grouped[key].append(row)
        for key, matches in sorted(grouped.items()):
            ids = sorted({row["disease_id"] for row in matches})
            if len(ids) > 1:
                names = "; ".join(
                    f"{row['disease_id']}={row.get('standard_name_en', '')}"
                    for row in matches
                )
                high.append(f"Duplicate {label} `{key}`: {names}")

    for index, left in enumerate(active):
        left_name = left.get("standard_name_en", "")
        left_tokens = token_set(left_name)
        if len(left_tokens) < 2:
            continue
        for right in active[index + 1 :]:
            right_name = right.get("standard_name_en", "")
            right_tokens = token_set(right_name)
            if len(right_tokens) < 2:
                continue
            if left.get("category") and right.get("category") and left.get("category") != right.get("category"):
                continue
            overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
            ratio = SequenceMatcher(None, clean(left_name), clean(right_name)).ratio()
            if overlap >= 0.82 or ratio >= 0.92:
                medium.append(
                    f"{left['disease_id']} `{left_name}` <> "
                    f"{right['disease_id']} `{right_name}` "
                    f"(token_overlap={overlap:.2f}, text_similarity={ratio:.2f})"
                )

    return high, medium


def is_code_like(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z]\d+[a-z0-9.]*", value)) or bool(re.fullmatch(r"\d+[a-z0-9.]*", value))


def mapping_conflict_findings(mapping_dir: Path) -> list[str]:
    terms: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(mapping_dir.glob("*.csv")):
        for row in read_csv(path):
            disease_id = str(row.get("disease_id", "") or "").strip()
            if not disease_id:
                continue
            candidates = [
                row.get("local_name", ""),
                row.get("local_code", ""),
                *split_aliases(row.get("aliases", "")),
            ]
            for term in candidates:
                key = clean(term)
                if len(key) < 3 or key in {"total", "unknown"}:
                    continue
                if is_code_like(key):
                    continue
                terms[key].append((disease_id, path.name, str(term).strip()))

    findings: list[str] = []
    for key, matches in sorted(terms.items()):
        ids = sorted({disease_id for disease_id, _, _ in matches})
        if len(ids) <= 1:
            continue
        samples = "; ".join(
            f"{disease_id}@{filename}=`{term}`"
            for disease_id, filename, term in matches[:6]
        )
        findings.append(f"Mapping term `{key}` maps to multiple disease IDs {ids}: {samples}")
    return findings


def print_ai_review(result: dict[str, object]) -> None:
    print("## AI review")
    route = result.get("model_route")
    if isinstance(route, dict):
        print(f"Model: {route.get('provider_key', 'unknown')} / {route.get('model_name', 'unknown')}")
    else:
        print("Model: unknown")
    summary = result.get("summary")
    if isinstance(summary, dict):
        print(
            "Summary: "
            f"merge={summary.get('merge', 0)}, "
            f"keep_separate={summary.get('keep_separate', 0)}, "
            f"add_standard_disease={summary.get('add_standard_disease', 0)}, "
            f"needs_human_review={summary.get('needs_human_review', 0)}"
        )

    recommendations = result.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            finding = str(item.get("finding") or "").strip()
            decision = str(item.get("decision") or "needs_human_review").strip()
            confidence = str(item.get("confidence") or "unknown").strip()
            canonical = str(item.get("canonical_id") or "").strip()
            rationale = str(item.get("rationale_zh") or item.get("rationale_en") or "").strip()
            suffix = f" canonical={canonical}" if canonical and canonical != "null" else ""
            print(f"- {decision} ({confidence}){suffix}: {finding}")
            if rationale:
                print(f"  rationale: {rationale}")
    else:
        print("- No recommendations returned")

    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", type=Path, default=DEFAULT_STANDARD)
    parser.add_argument("--mapping-dir", type=Path, default=DEFAULT_MAPPING_DIR)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--fail-on-high", action="store_true")
    parser.add_argument("--ai-review", action="store_true", help="Ask the dashboard model center to classify review candidates.")
    parser.add_argument("--ai-model", default="", help="Deprecated; AI routing now uses the model center.")
    parser.add_argument("--ai-base-url", default="", help="Deprecated; AI routing now uses the model center.")
    parser.add_argument("--ai-output", type=Path, default=None, help="Optional JSON file for the AI review result.")
    parser.add_argument("--ai-limit", type=int, default=40, help="Maximum candidate findings sent to the AI model; use 0 for all.")
    parser.add_argument("--ai-timeout", type=float, default=120.0, help="Deprecated; AI routing now uses the model center.")
    args = parser.parse_args()

    rows = read_csv(args.standard)
    high, medium = standard_duplicate_findings(rows)
    mapping_review = mapping_conflict_findings(args.mapping_dir)

    print("# Disease duplicate audit")
    print()
    print(f"Standard catalogue: {args.standard}")
    print(f"Mapping directory: {args.mapping_dir}")
    print()

    print(f"## High confidence standard-duplicate findings ({len(high)})")
    if high:
        for item in high[: args.limit]:
            print(f"- {item}")
        if len(high) > args.limit:
            print(f"- ... {len(high) - args.limit} more")
    else:
        print("- None")
    print()

    print(f"## Mapping-term review candidates ({len(mapping_review)})")
    if mapping_review:
        for item in mapping_review[: args.limit]:
            print(f"- {item}")
        if len(mapping_review) > args.limit:
            print(f"- ... {len(mapping_review) - args.limit} more")
    else:
        print("- None")
    print()

    print(f"## Similar-name review candidates ({len(medium)})")
    if medium:
        for item in medium[: args.limit]:
            print(f"- {item}")
        if len(medium) > args.limit:
            print(f"- ... {len(medium) - args.limit} more")
    else:
        print("- None")

    if args.ai_review:
        print()
        try:
            from src.services.disease_duplicate_audit_service import DiseaseDuplicateAuditService

            service = DiseaseDuplicateAuditService(
                standard_path=args.standard,
                mapping_dir=args.mapping_dir,
            )
            service_audit = service.run_local_audit(include_new_disease_candidates=True)
            result = asyncio.run(service.run_ai_review(service_audit, max_candidates=args.ai_limit))
        except Exception as exc:
            print("## AI review")
            print(f"- Failed: {exc}")
            return 2
        print_ai_review(result)
        if args.ai_output:
            args.ai_output.parent.mkdir(parents=True, exist_ok=True)
            args.ai_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"\nAI review JSON written to {args.ai_output}")

    if args.fail_on_high and high:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
