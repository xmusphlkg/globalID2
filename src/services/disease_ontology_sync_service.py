"""Build deterministic database seed rows from the disease ontology registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.ontology import DiseaseOntology


@dataclass(frozen=True)
class DiseaseOntologySyncPayload:
    """Database-shaped rows derived from one validated registry version."""

    taxonomy_nodes: list[dict[str, Any]]
    taxonomy_edges: list[dict[str, Any]]
    concept_assignments: list[dict[str, Any]]
    concept_relations: list[dict[str, Any]]
    surveillance_series: list[dict[str, Any]]
    source_availability: list[dict[str, Any]]

    def summary(self) -> dict[str, int]:
        return {
            "taxonomy_nodes": len(self.taxonomy_nodes),
            "taxonomy_edges": len(self.taxonomy_edges),
            "concept_assignments": len(self.concept_assignments),
            "concept_relations": len(self.concept_relations),
            "surveillance_series": len(self.surveillance_series),
            "source_availability": len(self.source_availability),
        }


def build_disease_ontology_sync_payload(
    ontology: DiseaseOntology,
) -> DiseaseOntologySyncPayload:
    """Translate validated JSON semantics into ORM table-shaped dictionaries."""

    document = ontology.to_dict()
    registry_id = document["registry_id"]
    schema_version = str(document["schema_version"])
    release_version = str(document.get("release_version") or schema_version)
    registry_definition_version = f"{registry_id}:{release_version}"
    sources = {source["id"]: source for source in document["sources"]}
    series_by_id = {series["id"]: series for series in document["source_series"]}
    availability_by_series: dict[str, list[dict[str, Any]]] = {}
    for item in document["availability"]:
        series_code = item.get("series_id")
        if series_code:
            availability_by_series.setdefault(series_code, []).append(item)

    taxonomy_nodes: list[dict[str, Any]] = []
    taxonomy_edges: list[dict[str, Any]] = []
    for facet in document["facets"]:
        for sort_order, tag in enumerate(facet["tags"]):
            taxonomy_nodes.append(
                {
                    "node_code": tag["id"],
                    "taxonomy_code": registry_id,
                    "facet": facet["id"],
                    "node_type": "facet_value",
                    "label_en": tag["labels"]["en"],
                    "label_zh": tag["labels"].get("zh"),
                    "description": tag.get("description"),
                    "sort_order": sort_order,
                    "is_active": True,
                    "metadata": {
                        "schema_version": document["schema_version"],
                        "rollup_policy": document["default_rollup_policy"],
                    },
                }
            )
            for parent_code in tag["parent_ids"]:
                taxonomy_edges.append(
                    {
                        "parent_node_code": parent_code,
                        "child_node_code": tag["id"],
                        "relation_type": "broader_narrower",
                        "aggregation_policy": "none",
                        "sort_order": sort_order,
                        "is_active": True,
                        "metadata": {
                            "schema_version": document["schema_version"],
                            "rollup_policy": document["default_rollup_policy"],
                        },
                    }
                )

    concept_assignments: list[dict[str, Any]] = []
    for concept in document["concepts"]:
        for facet_id, tag_ids in concept["facet_tags"].items():
            for tag_id in tag_ids:
                concept_assignments.append(
                    {
                        "disease_id": concept["id"],
                        "node_code": tag_id,
                        "mapping_relation": "exact",
                        "is_primary": True,
                        "confidence_score": 1.0,
                        "assertion_status": (
                            "deprecated"
                            if concept["status"] == "deprecated"
                            else "approved"
                        ),
                        "asserted_by": registry_id,
                        "source_name": registry_id,
                        "metadata": {
                            "facet": facet_id,
                            "concept_status": concept["status"],
                            "rollup_policy": concept["rollup_policy"],
                        },
                    }
                )

    concept_relations: list[dict[str, Any]] = []
    for relation in document["relations"]:
        source = relation["from_ref"]
        target = relation["to_ref"]
        if source["kind"] != "concept" or target["kind"] != "concept":
            continue
        concept_relations.append(
            {
                "subject_disease_id": source["id"],
                "object_disease_id": target["id"],
                "relation_type": relation["type"],
                "comparability": "not_comparable",
                "aggregation_policy": "non_additive",
                "is_hierarchical": relation["hierarchical"],
                "confidence_score": 1.0,
                "assertion_status": "approved",
                "asserted_by": registry_id,
                "source_name": registry_id,
                "metadata": {
                    "relation_id": relation["id"],
                    "rollup": relation["rollup"],
                    "rollup_policy": relation["rollup_policy"],
                },
            }
        )

    surveillance_series: list[dict[str, Any]] = []
    for series in document["source_series"]:
        source = sources[series["source_id"]]
        assertions = availability_by_series.get(series["id"], [])
        availability_status = _series_availability_status(
            assertions, series["status"]
        )
        target_group_code = series.get("group_id")
        disease_id = series.get("concept_id")
        source_label = next(
            iter(series["local_labels"] or series["local_codes"]), series["id"]
        )
        surveillance_series.append(
            {
                "series_code": series["id"],
                "disease_id": disease_id,
                "target_group_code": target_group_code,
                "country_code": source["country_code"],
                "scope_code": source["country_code"],
                "source_system": series["source_id"],
                # The stable ontology series ID is the true composite source
                # identity; source condition codes alone can be reused across
                # Confirmed/Probable components.
                "source_series_code": series["id"],
                "source_label": source_label,
                # Registry releases and source case-definition versions are
                # different identities.  Preserve an explicit source version
                # when supplied and use the registry release only as a
                # traceable fallback for legacy declarations.
                "definition_version": series.get(
                    "definition_version", registry_definition_version
                ),
                "case_definition": series.get("case_definition"),
                "case_definition_uri": series.get("case_definition_uri"),
                "metric_type": series["measure"],
                "reporting_basis": series.get(
                    "reporting_basis", _infer_reporting_basis(series)
                ),
                "temporal_granularity": series["frequency"],
                "unit": series.get("unit", "count"),
                "mapping_relation": series.get(
                    "mapping_relation",
                    "aggregate" if disease_id == "D006" or target_group_code else "exact",
                ),
                # Cross-source comparability must be asserted, never inferred
                # merely because two series point at the same concept.
                "comparability": series.get(
                    "comparability",
                    "not_comparable" if target_group_code else "unknown",
                ),
                "aggregation_policy": series.get(
                    "aggregation_policy", "non_additive"
                ),
                "availability_status": availability_status,
                "missing_value_policy": series.get(
                    "missing_value_policy", "missing_is_unknown"
                ),
                "valid_from": _optional_date(series.get("valid_from")),
                "valid_to": _optional_date(series.get("valid_to")),
                "is_active": series["status"] == "active",
                "metadata": {
                    "source_labels": source["labels"],
                    "local_codes": series["local_codes"],
                    "local_labels": series["local_labels"],
                    "facet_tags": series.get("facet_tags", {}),
                    "source_status": series["status"],
                    "availability": assertions,
                    "rollup_policy": series["rollup_policy"],
                    "release_version": release_version,
                    "definition_version_source": (
                        "source"
                        if series.get("definition_version")
                        else "registry_fallback"
                    ),
                    **{
                        key: series[key]
                        for key in (
                            "definition_effective_from",
                            "definition_effective_to",
                            "observation_date_coverage",
                            "comparability_break",
                            "metric_notes",
                            "time_basis",
                            "comparability_set",
                            "projection_policy",
                            "projection_priority",
                        )
                        if key in series
                    },
                },
            }
        )

    source_availability: list[dict[str, Any]] = []
    for item in document["availability"]:
        source = sources[item["source_id"]]
        linked_series = series_by_id.get(item.get("series_id"))
        source_availability.append(
            {
                "availability_code": item["id"],
                "source_system": item["source_id"],
                "country_code": source["country_code"],
                "target_kind": item["target_ref"]["kind"],
                "target_code": item["target_ref"]["id"],
                "series_code": item.get("series_id"),
                "status": item["status"],
                "reason_code": item.get("reason_code"),
                "notes": item.get("notes"),
                "missing_value_policy": "missing_is_unknown",
                "valid_from": _optional_date(item.get("valid_from")),
                "valid_to": _optional_date(item.get("valid_to")),
                "is_active": linked_series is None
                or linked_series["status"] == "active",
                "metadata": {
                    "source_labels": source["labels"],
                    "schema_version": document["schema_version"],
                    "release_version": release_version,
                },
            }
        )

    return DiseaseOntologySyncPayload(
        taxonomy_nodes=taxonomy_nodes,
        taxonomy_edges=taxonomy_edges,
        concept_assignments=concept_assignments,
        concept_relations=concept_relations,
        surveillance_series=surveillance_series,
        source_availability=source_availability,
    )


def _series_availability_status(
    assertions: list[dict[str, Any]], source_status: str
) -> str:
    if source_status == "historical":
        return "historical"
    if source_status == "deprecated":
        return "discontinued"
    statuses = {item["status"] for item in assertions}
    if statuses & {"available", "upstream_available_ingestion_pending"}:
        return "active"
    if "not_reported_by_source" in statuses:
        return "not_available"
    if statuses == {"planned"}:
        return "unknown"
    return "unknown"


def _infer_reporting_basis(series: dict[str, Any]) -> str:
    """Conservative fallback for older registry rows.

    Explicit metadata always wins.  The inference is deliberately limited to
    measure names that encode their basis; every other row retains the legacy
    notification fallback and is marked as such in registry metadata.
    """

    measure = str(series.get("measure") or "").casefold()
    if "sentinel" in measure:
        return "sentinel_surveillance"
    if "survey" in measure or "screening" in measure:
        return "survey_or_screening"
    if "diagnos" in measure or "classification" in measure:
        return "diagnosis_or_classification"
    return "notification"


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


__all__ = [
    "DiseaseOntologySyncPayload",
    "build_disease_ontology_sync_payload",
]
