#!/usr/bin/env python3
"""Run deterministic structural checks over country disease mappings."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing the services package initializes the application logger.  Keep that
# initialization chatter on stderr so stdout remains valid machine-readable JSON.
with redirect_stdout(sys.stderr):
    from src.ontology import load_disease_ontology  # noqa: E402
    from src.services.disease_mapping_quality_service import (  # noqa: E402
        DEFAULT_MAPPING_DIR,
        DiseaseMappingQualityService,
        normalize_mapping_name,
    )


SEMANTIC_GOLDEN_CASE_MISMATCH = "SEMANTIC_GOLDEN_CASE_MISMATCH"

# These identities are deliberately source-scoped.  The same English label can
# legitimately mean a different concept in another country or source, so a
# global alias-only guard would create false positives.  Adding a high-risk
# correction here turns the reviewed source identity into a regression test.
SEMANTIC_GOLDEN_CASES: tuple[dict[str, str], ...] = (
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Australian bat lyssavirus infection", "expected_disease_id": "D214"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Paratyphoid", "expected_disease_id": "D234"},
    {"country_code": "BR", "data_source": "Brazil DATASUS SINAN", "local_code": "MENI", "local_name": "Meningitis", "expected_disease_id": "D235"},
    {"country_code": "CH", "data_source": "Switzerland FOPH IDD", "local_code": "typhoidParatyphoidFever", "local_name": "Typhoid and paratyphoid fever", "expected_disease_id": "D026"},
    {"country_code": "CN", "data_source": "China CDC", "local_code": "Typhoid", "local_name": "伤寒和副伤寒", "expected_disease_id": "D026"},
    {"country_code": "HK", "data_source": "Hong Kong, China CHP Notifiable Infectious Diseases", "local_code": "", "local_name": "Paratyphoid fever", "expected_disease_id": "D234"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Paratyphoid fever", "expected_disease_id": "D234"},
    {"country_code": "KR", "data_source": "Korea KDCA EID", "local_code": "", "local_name": "파라티푸스", "expected_disease_id": "D234"},
    {"country_code": "NZ", "data_source": "NZ Monthly Notifiable", "local_code": "", "local_name": "Paratyphoid fever", "expected_disease_id": "D234"},
    {"country_code": "NZ", "data_source": "NZ Monthly Notifiable", "local_code": "", "local_name": "Viral Haemorrhagic Fever", "expected_disease_id": "D127"},
    {"country_code": "TW", "data_source": "Taiwan", "local_code": "002a", "local_name": "副傷寒", "expected_disease_id": "D234"},
    {"country_code": "TW", "data_source": "Taiwan", "local_code": "004", "local_name": "桿菌性痢疾", "expected_disease_id": "D105"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "Salmonella Paratyphi infection", "local_name": "Salmonella Paratyphi infection", "expected_disease_id": "D234"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "CPO", "local_name": "Carbapenemase-Producing Organisms (CPO), Total", "expected_disease_id": "D133"},
    # Additional reviewed target changes in the same ontology migration.  They
    # are guarded here even when their historical facts require component-wise
    # reconstruction rather than a flat disease_id update.
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Hepatitis C (newly acquired)", "expected_disease_id": "D210"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Rotavirus", "expected_disease_id": "D199"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Rubella congenital", "expected_disease_id": "D168"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Syphilis < 2 years", "expected_disease_id": "D232"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Syphilis > 2 years or unspecified duration", "expected_disease_id": "D233"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Syphilis congenital", "expected_disease_id": "D167"},
    {"country_code": "AU", "data_source": "AU NINDSS Monthly", "local_code": "", "local_name": "Varicella zoster (unspecified)", "expected_disease_id": "D213"},
    {"country_code": "BR", "data_source": "Brazil DATASUS SINAN", "local_code": "HIVE", "local_name": "Child exposed to HIV", "expected_disease_id": "D230"},
    {"country_code": "BR", "data_source": "Brazil DATASUS SINAN", "local_code": "HIVG", "local_name": "HIV infection in pregnancy", "expected_disease_id": "D231"},
    {"country_code": "BR", "data_source": "Brazil DATASUS SINAN", "local_code": "NTRA", "local_name": "Trachoma survey positive cases", "expected_disease_id": "D200"},
    {"country_code": "CN", "data_source": "China CDC", "local_code": "Unspecified Hepatitis", "local_name": "未分型肝炎", "expected_disease_id": "D071"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Group A streptococcal pharyngitis", "expected_disease_id": "D224"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Infectious gastroenteritis (Rotavirus)", "expected_disease_id": "D199"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Amoebic dysentery", "expected_disease_id": "D165"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Bacterial dysentery", "expected_disease_id": "D105"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Carbapenem-resistant Enterobacterales infection", "expected_disease_id": "D227"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Hantavirus pulmonary syndrome", "expected_disease_id": "D164"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Congenital rubella syndrome", "expected_disease_id": "D168"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Herpes B (Macacine herpesvirus 1) infection", "expected_disease_id": "D171"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Glanders", "expected_disease_id": "D221"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Japanese spotted fever", "expected_disease_id": "D222"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Rocky Mountain spotted fever", "expected_disease_id": "D223"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Severe invasive group A streptococcal infection", "expected_disease_id": "D225"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Typhus (Rickettsial)", "expected_disease_id": "D183"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Crimean-Congo hemorrhagic fever", "expected_disease_id": "D203"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Marburg hemorrhagic fever", "expected_disease_id": "D174"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Omsk hemorrhagic fever", "expected_disease_id": "D226"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "South American hemorrhagic fever", "expected_disease_id": "D204"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Rift Valley fever", "expected_disease_id": "D173"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Eastern equine encephalitis", "expected_disease_id": "D218"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Western equine encephalitis", "expected_disease_id": "D219"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Venezuelan equine encephalitis", "expected_disease_id": "D220"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Tick-borne encephalitis", "expected_disease_id": "D205"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Kyasanur Forest disease", "expected_disease_id": "D217"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Hendra virus infection", "expected_disease_id": "D215"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Nipah virus infection", "expected_disease_id": "D175"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Lyssavirus infection (excluding rabies)", "expected_disease_id": "D214"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Multidrug-resistant Acinetobacter infection", "expected_disease_id": "D228"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Acute flaccid paralysis (excluding acute poliomyelitis)", "expected_disease_id": "D178"},
    {"country_code": "JP", "data_source": "JP NIID Weekly", "local_code": "", "local_name": "Acute encephalitis (excluding Japanese and West Nile encephalitis)", "expected_disease_id": "D216"},
    {"country_code": "KR", "data_source": "Korea KDCA EID", "local_code": "", "local_name": "카바페넴내성장내세균목(CRE) 감염증", "expected_disease_id": "D227"},
    {"country_code": "TW", "data_source": "Taiwan", "local_code": "0705", "local_name": "急性病毒性Ｃ型肝炎", "expected_disease_id": "D210"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10100", "local_name": "Hepatitis B, acute, Confirmed", "expected_disease_id": "D068"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10100", "local_name": "Hepatitis B, acute, Probable", "expected_disease_id": "D068"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10105", "local_name": "Hepatitis B, chronic, Confirmed", "expected_disease_id": "D208"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10105", "local_name": "Hepatitis B, chronic, Probable", "expected_disease_id": "D208"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10104", "local_name": "Hepatitis B, perinatal, Confirmed", "expected_disease_id": "D209"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10101", "local_name": "Hepatitis C, acute, Confirmed", "expected_disease_id": "D210"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10101", "local_name": "Hepatitis C, acute, Probable", "expected_disease_id": "D210"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10106", "local_name": "Hepatitis C, chronic, Confirmed", "expected_disease_id": "D211"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "10106", "local_name": "Hepatitis C, chronic, Probable", "expected_disease_id": "D211"},
    {"country_code": "US", "data_source": "US CDC NNDSS", "local_code": "50248", "local_name": "Hepatitis C, perinatal, Confirmed", "expected_disease_id": "D212"},
)


def audit_semantic_golden_cases(
    mapping_dir: Path,
    expectations: tuple[dict[str, str], ...] = SEMANTIC_GOLDEN_CASES,
) -> list[dict[str, Any]]:
    """Return source-scoped concept mismatches for reviewed high-risk rows."""

    rows_by_country: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(mapping_dir.glob("*.csv")):
        country_code = path.stem.upper()
        if country_code == "EN":
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows_by_country[country_code] = [
                {
                    **{key: (value or "").strip() for key, value in raw.items() if key},
                    "_location": {"path": str(path), "row": line_number},
                }
                for line_number, raw in enumerate(csv.DictReader(handle), start=2)
            ]

    findings: list[dict[str, Any]] = []
    identity_fields = ("data_source", "local_code", "local_name")
    for expected in expectations:
        country_code = expected["country_code"].upper()
        candidates = [
            row
            for row in rows_by_country.get(country_code, [])
            if all(
                normalize_mapping_name(row.get(field, ""))
                == normalize_mapping_name(expected[field])
                for field in identity_fields
            )
        ]
        actual_ids = sorted({row.get("disease_id", "") for row in candidates})
        expected_id = expected["expected_disease_id"]
        if len(candidates) == 1 and actual_ids == [expected_id]:
            continue

        evidence: dict[str, Any] = {
            "identity": {
                "country_code": country_code,
                **{field: expected[field] for field in identity_fields},
            },
            "expected_disease_id": expected_id,
            "actual_disease_ids": actual_ids,
            "match_count": len(candidates),
            "matches": [
                {
                    "disease_id": row.get("disease_id", ""),
                    "location": row["_location"],
                }
                for row in candidates
            ],
        }
        if not candidates:
            evidence["expected_path"] = str(
                mapping_dir / f"{country_code.lower()}.csv"
            )
        findings.append(
            {
                "code": SEMANTIC_GOLDEN_CASE_MISMATCH,
                "severity": "error",
                "message": (
                    "Reviewed source identity is missing, duplicated, or mapped "
                    "to a different disease concept."
                ),
                "evidence": evidence,
            }
        )
    return findings


def _attach_semantic_golden_check(
    report: dict[str, Any],
    mapping_dir: Path,
) -> None:
    """Attach production golden-case results to the structural audit report."""

    is_default_inventory = mapping_dir.resolve() == DEFAULT_MAPPING_DIR.resolve()
    findings = (
        audit_semantic_golden_cases(mapping_dir) if is_default_inventory else []
    )
    report["checks"][SEMANTIC_GOLDEN_CASE_MISMATCH] = {
        "status": "completed" if is_default_inventory else "skipped",
        "finding_count": len(findings),
        "expectation_count": len(SEMANTIC_GOLDEN_CASES) if is_default_inventory else 0,
        **(
            {}
            if is_default_inventory
            else {"reason": "semantic golden cases apply to the production mapping inventory"}
        ),
    }
    report["findings"].extend(findings)
    report["summary"]["by_code"][SEMANTIC_GOLDEN_CASE_MISMATCH] = len(findings)
    report["summary"]["by_severity"]["error"] += len(findings)
    report["summary"]["finding_count"] += len(findings)


def _load_hierarchy(path: Path | None) -> Any | None:
    if path is None:
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit country disease mappings for structural data-loss risks."
    )
    parser.add_argument(
        "--mapping-dir",
        type=Path,
        default=DEFAULT_MAPPING_DIR,
        help="directory containing country mapping CSV files",
    )
    parser.add_argument(
        "--hierarchy",
        type=Path,
        help=(
            "optional JSON hierarchy ({parent: [children]} or "
            "{edges: [...], aggregate_ids: [...]})"
        ),
    )
    parser.add_argument(
        "--no-ontology",
        action="store_true",
        help=(
            "skip default ontology aggregate/child checks and series-registry "
            "collision resolution"
        ),
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="exit with status 1 when the report contains an error finding",
    )
    args = parser.parse_args()

    ontology = None if args.no_ontology else load_disease_ontology()
    hierarchy = _load_hierarchy(args.hierarchy)
    if hierarchy is None and ontology is not None:
        hierarchy = ontology.mapping_quality_hierarchy()
    series_registry = (
        None if ontology is None else ontology.mapping_quality_series_registry()
    )
    report = DiseaseMappingQualityService(mapping_dir=args.mapping_dir).run_audit(
        hierarchy=hierarchy,
        series_registry_rows=series_registry,
    )
    _attach_semantic_golden_check(report, args.mapping_dir)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if args.fail_on_error and report["summary"]["by_severity"]["error"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
