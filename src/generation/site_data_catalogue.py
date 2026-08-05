"""Stable disease catalogue loading and export coverage validation."""

import csv
from pathlib import Path

from src.ontology import DiseaseOntology


def load_standard_diseases(csv_path: Path) -> list[dict]:
    diseases = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            diseases.append(
                {
                    "disease_id": row["disease_id"],
                    "name_en": row["standard_name_en"],
                    "name_zh": row["standard_name_zh"],
                    "category": row["category"],
                    "icd_10": row["icd_10"],
                    "icd_11": row["icd_11"],
                    "description": row.get("description", ""),
                    "slug": row["standard_name_en"]
                    .lower()
                    .replace(" ", "-")
                    .replace("/", "-"),
                }
            )
    return diseases


def enrich_diseases_with_ontology(
    diseases: list[dict], ontology: DiseaseOntology
) -> list[dict]:
    """Attach compact faceted metadata without duplicating the full registry."""

    concept_ids = set(ontology.concept_ids)
    for disease in diseases:
        disease_id = disease["disease_id"]
        if disease_id not in concept_ids:
            continue
        detail = ontology.concept_detail(disease_id)
        disease["ontology"] = {
            "status": detail["status"],
            "rollup_policy": detail["rollup_policy"],
            "facet_tags": detail.get("facet_tags", {}),
            "group_ids": detail.get("group_ids", []),
            "relations": detail.get("relations", {}),
            "source_series_count": len(detail.get("source_series", [])),
            "availability": detail.get("availability", []),
        }
    return diseases


def validate_record_catalogue_coverage(
    records: list[dict],
    catalogue_ids: set[str],
    public_ids: set[str],
) -> None:
    """Fail visibly when facts would otherwise disappear from site exports."""

    observed_ids = {
        str(record.get("disease_id") or "").strip()
        for record in records
        if str(record.get("disease_id") or "").strip()
    }
    unknown_ids = sorted(observed_ids - catalogue_ids)
    if unknown_ids:
        raise RuntimeError(
            "Disease records reference IDs missing from standard_diseases.csv: "
            + ", ".join(unknown_ids)
        )

    unsupported_non_public = sorted(observed_ids - public_ids - {"D999"})
    if unsupported_non_public:
        raise RuntimeError(
            "Disease records still reference deprecated/non-public concepts; "
            "run the disease ontology migration before export: "
            + ", ".join(unsupported_non_public)
        )
