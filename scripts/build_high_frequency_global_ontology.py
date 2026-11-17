#!/usr/bin/env python3
"""Build isolated ontology/review patches from actual locally organized source facts."""
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.global_high_frequency_review import FLUNET_TARGETS, CA_TARGETS, SPF_TARGETS


def main():
    receipts = {}
    for path in sorted((ROOT / "data/raw/frequency-expansion").glob("**/artifacts_manifest.json")):
        receipt = json.loads(path.read_text())
        if receipt.get("status") == "organized" and receipt.get("source_scope") in {
            "who_flunet_weekly", "ca_phac_respiratory_weekly", "spf_odisse_weekly"
        }:
            receipts[receipt["country_code"], receipt["source_scope"]] = receipt
    sources, series, availability, mappings = {}, [], [], []
    summary = {}
    for (country, scope), receipt in sorted(receipts.items()):
        identities = {}
        with (ROOT / receipt["output_csv"]).open(newline="") as stream:
            for row in csv.DictReader(stream):
                key = row["SourceDiseaseCode"], row["DefinitionVersion"]
                if key not in identities:
                    identities[key] = row
        source_id = receipt["source_id"]
        source_name = next(iter(identities.values()))["Source"]
        if country != "MY" or scope != "who_flunet_weekly":
            sources[source_id] = {"id": source_id, "country_code": country,
                                  "labels": {"en": source_name}, "legacy_data_sources": [source_name]}
        country_summary = summary.setdefault(country, {})
        country_summary[scope] = {"source_id": source_id, "categories": len(identities),
                                 "observations": receipt["observations"], "numeric_observations": receipt["numeric_observations"],
                                 "latest": receipt["latest"]}
        for (local_code, version), row in sorted(identities.items()):
            dimensions = json.loads(row["Dimensions"])
            if scope == "who_flunet_weekly":
                target = FLUNET_TARGETS.get(local_code)
            elif scope == "ca_phac_respiratory_weekly":
                target = CA_TARGETS.get(row["SourceVirus"])
            else:
                target = SPF_TARGETS.get(row["SourceDataset"])
            relation, comparable = ("narrower", "conditional") if target else ("unmapped", "not_comparable")
            digest = hashlib.sha256(f"{source_id}|{local_code}|{version}".encode()).hexdigest()[:20]
            series_id = f"SER_HF_{country}_{digest}"
            notes = (
                "Source-native weekly measurement; keep source population, origin/age/metric dimensions, "
                "calendar, explicit zero/missing and original units. No patient-case projection, "
                "no total/subtype/origin/age sum; no conversion from tests or rates into cases. "
                "SPF hospital field denominator says emergency passages while narrative says admissions; "
                "both are retained without recalculation. " + row["SourceURL"]
            )
            mapping = {"country_code": country, "disease_id": target or "", "local_name": row["RawDiseaseLabel"],
                       "local_code": local_code, "category": "Reviewed typed high-frequency source category",
                       "aliases": "", "notes": notes, "data_source": "", "source_id": source_id,
                       "series_id": "", "mapping_scope": "source_category_dimension",
                       "target_kind": "concept" if target else "group", "target_code": target or "G_UNMAPPED_SOURCE_CATEGORIES",
                       "mapping_relation": relation, "comparability": comparable,
                       "projection_policy": "canonical" if target else "no_projection",
                       "aggregation_policy": "non_additive", "definition_version": version}
            mappings.append(mapping)
            # Malaysia already has holding series with this identity. Reviewed
            # mappings attach its existing facts; static replacement would duplicate them.
            if country == "MY" and scope == "who_flunet_weekly":
                continue
            spec = {"id": series_id, "source_id": source_id,
                    "concept_id" if target else "group_id": target or "G_UNMAPPED_SOURCE_CATEGORIES",
                    "local_codes": [local_code], "local_labels": [row["RawDiseaseLabel"]],
                    "geography_key": row["GeographyKey"], "frequency": row["Frequency"],
                    "measure": row["Measure"], "reporting_basis": row["ReportingBasis"],
                    "time_basis": row["TimeBasis"], "unit": row["Unit"],
                    "definition_version": version, "mapping_relation": relation,
                    "comparability": comparable, "aggregation_policy": "non_additive",
                    "projection_policy": "source_observations_only", "missing_value_policy": "missing_is_unknown",
                    "case_definition": notes, "facet_tags": {"population": ["population.all"]},
                    "status": "active", "rollup_policy": "no_auto_rollup"}
            series.append(spec)
            availability.append({"id": f"AV_HF_{country}_{digest}", "source_id": source_id,
                                 "target_ref": {"kind": "concept" if target else "group", "id": target or "G_UNMAPPED_SOURCE_CATEGORIES"},
                                 "series_id": series_id, "status": "upstream_available_ingestion_pending",
                                 "reason_code": "actual_full_source_capture_and_offline_organization_verified",
                                 "missing_value_policy": "missing_is_unknown"})
    patch = {"schema_version": 1, "sources": list(sources.values()), "source_series": series,
             "availability": availability, "summary": summary,
             "application_note": "Merge by stable IDs. MY static series intentionally omitted; attach reviewed mappings to existing holding facts. Availability remains ingestion_pending until formal store acceptance."}
    (ROOT / "configs/high_frequency_global_ontology.json").write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "configs/onboarding/high_frequency_global_mappings.json").write_text(json.dumps({
        "schema_version": 1, "reviewed_on": "2026-11-17", "rows": mappings,
        "application_note": "Merge into reviewed country CSVs through existing release service; do not blindly overwrite prior reviewed identities. MY former unmapped decisions require an explicit new mapping release."}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sources": len(sources), "series": len(series), "availability": len(availability),
                      "reviewed_mappings": len(mappings), "country_scopes": len(receipts)}, indent=2))


if __name__ == "__main__":
    main()
