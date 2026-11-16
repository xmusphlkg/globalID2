#!/usr/bin/env python3
"""Read-only replay of annual mapping contracts against retained source CSVs.

This validates semantic mapping coverage, not database import, public snapshot
generation or deployment. It never opens a write database connection.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.remaining_country_sources import SPF_TERRITORIES
from src.data.crawlers.who_annual import COUNTRY_CONTRACTS
from src.services.annual_source_mapping_review import spf_mapping, who_mapping
from src.services.disease_mapping_config import load_reviewed_source_category_mappings


def verify_country(country: str, *, data_root: Path = ROOT / "data/current", as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    who = country in COUNTRY_CONTRACTS
    source_id = f"SRC_{country}_WHO_GHO" if who else f"SRC_{country}_SPF_ODISSE"
    scope = "who_gho_annual" if who else "spf_odisse_annual"
    path = data_root / country.lower() / f"{scope}.csv"
    configured = {
        (item.source_code, item.definition_version): item
        for item in load_reviewed_source_category_mappings()
        if item.source_id == source_id
    }
    payload = path.read_bytes()
    seen, observed, counts = set(), set(), Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            identity = (row["SourceDiseaseCode"], row["DefinitionVersion"])
            mapping = configured.get(identity)
            if mapping is None:
                raise ValueError(f"{path}:{row_number}: source category has no reviewed mapping")
            observed.add(identity)
            rule = (who_mapping if who else spf_mapping)(identity[0], row["RawDiseaseLabel"], identity[1])
            if (row["Measure"], row["Frequency"], row["Unit"], row["ReportingBasis"], row["GeographyKey"]) != (
                rule["metric_type"], "annual", "count", rule["reporting_basis"], f"country:{country}:national"
            ):
                raise ValueError(f"{path}:{row_number}: source measure/grain/geography contract changed")
            dimensions = json.loads(row["Dimensions"])
            if rule["total_dimensions"] is not None and dimensions != rule["total_dimensions"]:
                raise ValueError(f"{path}:{row_number}: national total dimensions changed")
            year = int(row["Year"])
            if not year < as_of.year or row["PeriodStart"] != f"{year}-01-01" or row["PeriodEnd"] != f"{year}-12-31":
                raise ValueError(f"{path}:{row_number}: annual closed-period contract changed")
            if not who and row["SourceDiseaseCode"] == "tuberculosis":
                expected = "notification_department" if year <= 2018 else "residence_department"
                if row["ReportingBasis"] != expected:
                    raise ValueError(f"{path}:{row_number}: TB 2019 geographical definition break changed")
            source_identity = (*identity, row["PeriodStart"], row["GeographyKey"], json.dumps(dimensions, sort_keys=True))
            if source_identity in seen:
                raise ValueError(f"{path}:{row_number}: duplicate source observation")
            seen.add(source_identity)
            counts["observations"] += 1
            counts[mapping.projection_policy + "_observations"] += 1
            status = row["MissingValueStatus"]
            if status not in {"reported", "source_missing", "source_suppressed"}:
                raise ValueError(f"{path}:{row_number}: missing-value contract changed")
            if status == "reported":
                if not row["Cases"].isdigit():
                    raise ValueError(f"{path}:{row_number}: reported count is not a nonnegative integer")
                counts["numeric_observations"] += 1
            else:
                counts[status + "_observations"] += 1
            if row["Measure"] == "death_notifications":
                counts["death_observations"] += 1
    return {
        "country_code": country, "source_id": source_id, "source_csv": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(), "configured_categories": len(configured),
        "observed_categories": len(observed), "configured_without_observations": len(set(configured) - observed),
        **dict(counts), "raw_facts_rewritten": False, "database_verified": False,
        "public_snapshot_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    countries = sorted(set(COUNTRY_CONTRACTS) | set(SPF_TERRITORIES))
    results = [verify_country(code) for code in countries]
    report = {
        "status": "passed", "verification": "retained_source_csv_semantic_mapping_replay",
        "countries": results, "total_observations": sum(item["observations"] for item in results),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
