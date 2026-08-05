"""Configuration-driven disease ontology registry.

The registry deliberately separates stable disease concepts from source-specific
surveillance series.  Facet and concept relationships are semantic: the loader
requires an explicit ``no_auto_rollup`` policy and never infers an aggregation
rule from a taxonomy edge.

Only the Python standard library is used so the registry can be loaded by
crawlers, audits, command-line tools, and application startup code alike.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_ONTOLOGY_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "disease_ontology.json"
)
NO_AUTO_ROLLUP = "no_auto_rollup"

_REFERENCE_KINDS = frozenset({"concept", "group"})
_CONCEPT_STATUSES = frozenset({"active", "deprecated", "reserved"})
_SERIES_STATUSES = frozenset({"active", "historical", "planned", "deprecated"})
_AVAILABILITY_STATUSES = frozenset(
    {
        "available",
        "upstream_available_ingestion_pending",
        "not_reported_by_source",
        "planned",
        "not_assessed",
        "parser_blocked",
        "mapping_missing",
    }
)
_MAPPING_RELATIONS = frozenset(
    {"exact", "narrower", "broader", "related", "aggregate", "ambiguous", "unmapped"}
)
_COMPARABILITY_VALUES = frozenset(
    {"direct", "conditional", "not_comparable", "unknown"}
)
_AGGREGATION_POLICIES = frozenset(
    {"none", "direct_only", "reported_aggregate", "sum_disjoint", "non_additive"}
)
_MISSING_VALUE_POLICIES = frozenset(
    {
        "missing_is_unknown",
        "explicit_zero_only",
        "silence_means_zero",
        "suppressed",
        "not_applicable",
    }
)


class OntologyValidationError(ValueError):
    """Raised when an ontology document is structurally or semantically invalid."""


class DiseaseOntology:
    """Validated, immutable-by-copy view of a disease ontology JSON document."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        if not isinstance(document, Mapping):
            raise OntologyValidationError("ontology root must be a JSON object")

        self._document: dict[str, Any] = copy.deepcopy(dict(document))
        self._validate_and_index()

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_ONTOLOGY_PATH) -> DiseaseOntology:
        """Load and validate an ontology from a UTF-8 JSON file."""

        resolved_path = Path(path)
        try:
            with resolved_path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except json.JSONDecodeError as exc:
            raise OntologyValidationError(
                f"invalid ontology JSON at {resolved_path}: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno})"
            ) from exc
        return cls(document)

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> DiseaseOntology:
        """Build a validated registry from an in-memory JSON-compatible mapping."""

        return cls(document)

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the source document."""

        return copy.deepcopy(self._document)

    @property
    def concept_ids(self) -> tuple[str, ...]:
        """Concept identifiers in registry order."""

        return tuple(self._concepts)

    @property
    def group_ids(self) -> tuple[str, ...]:
        """Group identifiers in registry order."""

        return tuple(self._groups)

    @property
    def series_ids(self) -> tuple[str, ...]:
        """Source-series identifiers in registry order."""

        return tuple(self._series)

    def concept_detail(self, concept_id: str) -> dict[str, Any]:
        """Return one concept with resolved facets, groups, relations, and series."""

        concept = self._get(self._concepts, concept_id, "concept")
        result = copy.deepcopy(concept)
        result["facet_details"] = self._resolved_facet_tags(
            concept.get("facet_tags", {})
        )
        result["group_ids"] = [
            group_id
            for group_id, group in self._groups.items()
            if concept_id in group["concept_ids"]
        ]
        result["relations"] = self._relations_for("concept", concept_id)
        result["source_series"] = [
            self._series_detail(series)
            for series in self._series.values()
            if series.get("concept_id") == concept_id
        ]
        result["availability"] = self._availability_for("concept", concept_id)
        return result

    def facet_tree(
        self, facet_id: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Return a nested facet DAG.

        A tag with multiple parents appears under each parent.  This is an
        intentional DAG projection, not a lossy conversion to a single-parent
        taxonomy.
        """

        if facet_id is None:
            return [self._facet_tree(item_id) for item_id in self._facets]
        return self._facet_tree(facet_id)

    def group_detail(self, group_id: str) -> dict[str, Any]:
        """Return one group with its direct concepts, groups, and source metadata."""

        group = self._get(self._groups, group_id, "group")
        result = copy.deepcopy(group)
        result["concepts"] = [
            self._concept_summary(self._concepts[concept_id])
            for concept_id in group["concept_ids"]
        ]
        result["subgroups"] = [
            self._group_summary(self._groups[subgroup_id])
            for subgroup_id in group["subgroup_ids"]
        ]
        result["parent_group_ids"] = [
            parent_id
            for parent_id, parent in self._groups.items()
            if group_id in parent["subgroup_ids"]
        ]
        result["relations"] = self._relations_for("group", group_id)
        result["source_series"] = [
            self._series_detail(series)
            for series in self._series.values()
            if series.get("group_id") == group_id
        ]
        result["availability"] = self._availability_for("group", group_id)
        return result

    def series_lookup(
        self,
        series_id: str | None = None,
        *,
        source_id: str | None = None,
        country_code: str | None = None,
        local_code: str | None = None,
        local_label: str | None = None,
        concept_id: str | None = None,
        group_id: str | None = None,
        status: str | None = None,
        availability_status: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Resolve a series by ID or search source series with exact filters.

        Codes and labels are matched case-insensitively.  Passing ``series_id``
        returns one object (or raises ``KeyError``); filter mode returns a list.
        """

        if series_id is not None:
            filters = (
                source_id,
                country_code,
                local_code,
                local_label,
                concept_id,
                group_id,
                status,
                availability_status,
            )
            if any(value is not None for value in filters):
                raise TypeError("series_id cannot be combined with series filters")
            return self._series_detail(self._get(self._series, series_id, "series"))

        normalized_code = _casefold(local_code)
        normalized_label = _casefold(local_label)
        normalized_country = country_code.upper() if country_code else None
        matches: list[dict[str, Any]] = []
        for series in self._series.values():
            source = self._sources[series["source_id"]]
            if source_id is not None and series["source_id"] != source_id:
                continue
            if (
                normalized_country is not None
                and source["country_code"] != normalized_country
            ):
                continue
            if normalized_code is not None and normalized_code not in {
                value.casefold() for value in series["local_codes"]
            }:
                continue
            if normalized_label is not None and normalized_label not in {
                value.casefold() for value in series["local_labels"]
            }:
                continue
            if concept_id is not None and series.get("concept_id") != concept_id:
                continue
            if group_id is not None and series.get("group_id") != group_id:
                continue
            if status is not None and series["status"] != status:
                continue
            if availability_status is not None and not any(
                item["status"] == availability_status
                for item in self._availability_by_series.get(series["id"], ())
            ):
                continue
            matches.append(self._series_detail(series))
        return matches

    def availability_lookup(
        self,
        *,
        source_id: str | None = None,
        country_code: str | None = None,
        concept_id: str | None = None,
        group_id: str | None = None,
        series_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search availability assertions independently of series presence."""

        normalized_country = country_code.upper() if country_code else None
        matches: list[dict[str, Any]] = []
        for item in self._availability.values():
            source = self._sources[item["source_id"]]
            target = item["target_ref"]
            if source_id is not None and item["source_id"] != source_id:
                continue
            if (
                normalized_country is not None
                and source["country_code"] != normalized_country
            ):
                continue
            if concept_id is not None and target != {
                "kind": "concept",
                "id": concept_id,
            }:
                continue
            if group_id is not None and target != {"kind": "group", "id": group_id}:
                continue
            if series_id is not None and item.get("series_id") != series_id:
                continue
            if status is not None and item["status"] != status:
                continue
            detail = copy.deepcopy(item)
            detail["source"] = copy.deepcopy(source)
            matches.append(detail)
        return matches

    def mapping_quality_hierarchy(self) -> dict[str, Any]:
        """Export the shape accepted by ``DiseaseMappingQualityService``.

        Hierarchical relations in the registry point from a narrower concept to
        its broader concept.  The quality audit expects parent-to-child edges,
        so this export reverses that direction.  Group relations are excluded
        because the audit currently accepts D-code edges only.
        """

        edges = []
        for relation in self._relations.values():
            source = relation["from_ref"]
            target = relation["to_ref"]
            if not relation["hierarchical"]:
                continue
            if source["kind"] != "concept" or target["kind"] != "concept":
                continue
            edges.append(
                {
                    "parent_id": target["id"],
                    "child_id": source["id"],
                    "relation_type": relation["type"],
                }
            )

        aggregate_ids = [
            concept_id
            for concept_id, concept in self._concepts.items()
            if "surveillance_scope.aggregate"
            in self._tag_ancestors_for_concept(concept)
        ]
        return {"edges": edges, "aggregate_ids": aggregate_ids}

    def mapping_quality_series_registry(self) -> list[dict[str, Any]]:
        """Export source-series rows consumed by the mapping quality audit.

        The export intentionally contains only stable matching and operational
        fields.  Each row has exactly one canonical target, represented as
        ``disease_id`` for a concept or ``group_id`` for an aggregate group.
        Availability is flattened to a deterministic status set so the audit
        can identify upstream series whose ingestion or backfill is pending.
        """

        rows: list[dict[str, Any]] = []
        for series in self._series.values():
            target = self._series_target(series, series["id"])
            row: dict[str, Any] = {
                "country_code": self._sources[series["source_id"]]["country_code"],
                "source_id": series["source_id"],
                "series_id": series["id"],
                "local_codes": copy.deepcopy(series["local_codes"]),
                "local_labels": copy.deepcopy(series["local_labels"]),
                "status": series["status"],
                "availability_statuses": sorted(
                    {
                        item["status"]
                        for item in self._availability_by_series.get(series["id"], ())
                    }
                ),
            }
            row["disease_id" if target["kind"] == "concept" else "group_id"] = (
                target["id"]
            )
            rows.append(row)
        return sorted(
            rows,
            key=lambda row: (
                row["country_code"],
                row["source_id"],
                row["series_id"],
            ),
        )

    def _validate_and_index(self) -> None:
        document = self._document
        if document.get("schema_version") != 1:
            raise OntologyValidationError("schema_version must be 1")
        _required_string(document, "registry_id", "ontology")
        if document.get("default_rollup_policy") != NO_AUTO_ROLLUP:
            raise OntologyValidationError(
                "default_rollup_policy must be 'no_auto_rollup'"
            )

        facets = _required_list(document, "facets", "ontology")
        concepts = _required_list(document, "concepts", "ontology")
        groups = _required_list(document, "groups", "ontology")
        relations = _required_list(document, "relations", "ontology")
        sources = _required_list(document, "sources", "ontology")
        source_series = _required_list(document, "source_series", "ontology")
        availability = _required_list(document, "availability", "ontology")

        self._facets = _indexed(facets, "facets")
        self._concepts = _indexed(concepts, "concepts")
        self._groups = _indexed(groups, "groups")
        self._relations = _indexed(relations, "relations")
        self._sources = _indexed(sources, "sources")
        self._series = _indexed(source_series, "source_series")
        self._availability = _indexed(availability, "availability")

        self._validate_facets()
        self._validate_concepts()
        self._validate_groups()
        self._validate_relations()
        self._validate_sources()
        self._validate_series()
        self._validate_availability()

    def _validate_facets(self) -> None:
        tags: dict[str, dict[str, Any]] = {}
        for facet_id, facet in self._facets.items():
            _labels(facet, f"facet {facet_id}")
            facet_tags = _required_list(facet, "tags", f"facet {facet_id}")
            for position, tag_value in enumerate(facet_tags):
                path = f"facet {facet_id}.tags[{position}]"
                tag = _mapping(tag_value, path)
                tag_id = _required_string(tag, "id", path)
                if tag_id in tags:
                    raise OntologyValidationError(f"duplicate tag id: {tag_id}")
                if tag.get("facet_id") != facet_id:
                    raise OntologyValidationError(
                        f"tag {tag_id} facet_id must be {facet_id!r}"
                    )
                _labels(tag, f"tag {tag_id}")
                _string_list(tag, "parent_ids", f"tag {tag_id}")
                tags[tag_id] = tag

        self._tags = tags
        for tag_id, tag in tags.items():
            for parent_id in tag["parent_ids"]:
                parent = tags.get(parent_id)
                if parent is None:
                    raise OntologyValidationError(
                        f"tag {tag_id} references unknown parent tag {parent_id}"
                    )
                if parent["facet_id"] != tag["facet_id"]:
                    raise OntologyValidationError(
                        f"tag {tag_id} parent {parent_id} belongs to facet "
                        f"{parent['facet_id']!r}, not {tag['facet_id']!r}"
                    )
        _assert_acyclic(
            tags,
            {tag_id: tag["parent_ids"] for tag_id, tag in tags.items()},
            "facet tag graph",
        )

    def _validate_concepts(self) -> None:
        for concept_id, concept in self._concepts.items():
            _labels(concept, f"concept {concept_id}")
            status = _required_string(concept, "status", f"concept {concept_id}")
            if status not in _CONCEPT_STATUSES:
                raise OntologyValidationError(
                    f"concept {concept_id} has invalid status {status!r}"
                )
            self._validate_rollup(concept, f"concept {concept_id}")
            self._validate_facet_assignments(
                concept.get("facet_tags"), f"concept {concept_id}"
            )

    def _validate_groups(self) -> None:
        for group_id, group in self._groups.items():
            _labels(group, f"group {group_id}")
            self._validate_rollup(group, f"group {group_id}")
            concept_ids = _string_list(group, "concept_ids", f"group {group_id}")
            subgroup_ids = _string_list(group, "subgroup_ids", f"group {group_id}")
            for concept_id in concept_ids:
                if concept_id not in self._concepts:
                    raise OntologyValidationError(
                        f"group {group_id} references unknown concept {concept_id}"
                    )
            for subgroup_id in subgroup_ids:
                if subgroup_id not in self._groups:
                    raise OntologyValidationError(
                        f"group {group_id} references unknown subgroup {subgroup_id}"
                    )

        _assert_acyclic(
            self._groups,
            {
                group_id: group["subgroup_ids"]
                for group_id, group in self._groups.items()
            },
            "group graph",
        )

    def _validate_relations(self) -> None:
        hierarchical_edges: dict[str, list[str]] = defaultdict(list)
        for relation_id, relation in self._relations.items():
            _required_string(relation, "type", f"relation {relation_id}")
            self._validate_rollup(relation, f"relation {relation_id}")
            if relation.get("rollup") is not False:
                raise OntologyValidationError(
                    f"relation {relation_id}.rollup must be false"
                )
            if not isinstance(relation.get("hierarchical"), bool):
                raise OntologyValidationError(
                    f"relation {relation_id}.hierarchical must be a boolean"
                )
            source = self._validate_reference(
                relation.get("from_ref"), f"relation {relation_id}.from_ref"
            )
            target = self._validate_reference(
                relation.get("to_ref"), f"relation {relation_id}.to_ref"
            )
            if source == target:
                raise OntologyValidationError(
                    f"relation {relation_id} cannot reference itself"
                )
            if relation["hierarchical"]:
                source_key = _reference_key(source)
                hierarchical_edges[source_key].append(_reference_key(target))

        relation_nodes = {
            _reference_key({"kind": kind, "id": entity_id})
            for kind, index in (("concept", self._concepts), ("group", self._groups))
            for entity_id in index
        }
        _assert_acyclic(
            relation_nodes,
            hierarchical_edges,
            "hierarchical relation graph",
        )

    def _validate_sources(self) -> None:
        for source_id, source in self._sources.items():
            country = _required_string(source, "country_code", f"source {source_id}")
            if len(country) != 2 or country != country.upper():
                raise OntologyValidationError(
                    f"source {source_id}.country_code must be a two-letter uppercase code"
                )
            _labels(source, f"source {source_id}")
            legacy_sources = source.get("legacy_data_sources", [])
            if legacy_sources:
                _string_list(source, "legacy_data_sources", f"source {source_id}")
                if any(not str(value).strip() for value in legacy_sources):
                    raise OntologyValidationError(
                        f"source {source_id}.legacy_data_sources cannot contain blanks"
                    )

    def _validate_series(self) -> None:
        code_targets: dict[tuple[str, str], dict[str, str]] = {}
        for series_id, series in self._series.items():
            source_id = _required_string(series, "source_id", f"series {series_id}")
            if source_id not in self._sources:
                raise OntologyValidationError(
                    f"series {series_id} references unknown source {source_id}"
                )
            target = self._series_target(series, series_id)
            self._validate_reference(target, f"series {series_id} target")
            _string_list(series, "local_codes", f"series {series_id}")
            _string_list(series, "local_labels", f"series {series_id}")
            if not series["local_codes"] and not series["local_labels"]:
                raise OntologyValidationError(
                    f"series {series_id} needs a local code or label"
                )
            _required_string(series, "frequency", f"series {series_id}")
            _required_string(series, "measure", f"series {series_id}")
            status = _required_string(series, "status", f"series {series_id}")
            if status not in _SERIES_STATUSES:
                raise OntologyValidationError(
                    f"series {series_id} has invalid status {status!r}"
                )
            _validate_optional_date_range(series, f"series {series_id}")
            self._validate_rollup(series, f"series {series_id}")
            self._validate_facet_assignments(
                series.get("facet_tags", {}), f"series {series_id}"
            )
            self._validate_optional_enum(
                series,
                "mapping_relation",
                _MAPPING_RELATIONS,
                f"series {series_id}",
            )
            self._validate_optional_enum(
                series,
                "comparability",
                _COMPARABILITY_VALUES,
                f"series {series_id}",
            )
            self._validate_optional_enum(
                series,
                "aggregation_policy",
                _AGGREGATION_POLICIES,
                f"series {series_id}",
            )
            self._validate_optional_enum(
                series,
                "missing_value_policy",
                _MISSING_VALUE_POLICIES,
                f"series {series_id}",
            )
            for optional_text in (
                "definition_version",
                "reporting_basis",
                "time_basis",
                "unit",
                "comparability_set",
            ):
                value = series.get(optional_text)
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise OntologyValidationError(
                        f"series {series_id}.{optional_text} must be a non-empty string"
                    )

            # A source event code can legitimately have separate confirmed and
            # probable components, but it must not silently point at different
            # canonical concepts.  That mistake previously assigned the HBV
            # code 10105 to chronic HCV as well.
            for local_code in series["local_codes"]:
                key = (source_id, local_code.casefold())
                previous_target = code_targets.setdefault(key, target)
                if previous_target != target:
                    raise OntologyValidationError(
                        f"source code {local_code!r} in {source_id} maps to "
                        f"multiple targets: {previous_target} and {target}"
                    )

    def _validate_availability(self) -> None:
        by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for availability_id, item in self._availability.items():
            source_id = _required_string(
                item, "source_id", f"availability {availability_id}"
            )
            if source_id not in self._sources:
                raise OntologyValidationError(
                    f"availability {availability_id} references unknown source {source_id}"
                )
            target = self._validate_reference(
                item.get("target_ref"),
                f"availability {availability_id}.target_ref",
            )
            status = _required_string(item, "status", f"availability {availability_id}")
            if status not in _AVAILABILITY_STATUSES:
                raise OntologyValidationError(
                    f"availability {availability_id} has invalid status {status!r}"
                )
            _validate_optional_date_range(item, f"availability {availability_id}")
            series_id = item.get("series_id")
            if series_id is None:
                if status in {"available", "upstream_available_ingestion_pending"}:
                    raise OntologyValidationError(
                        f"availability {availability_id} status {status!r} "
                        "requires series_id"
                    )
                continue
            if not isinstance(series_id, str) or not series_id:
                raise OntologyValidationError(
                    f"availability {availability_id}.series_id must be a non-empty string"
                )
            if status == "not_reported_by_source":
                raise OntologyValidationError(
                    f"availability {availability_id} status 'not_reported_by_source' "
                    "must not reference a series"
                )
            series = self._series.get(series_id)
            if series is None:
                raise OntologyValidationError(
                    f"availability {availability_id} references unknown series {series_id}"
                )
            if series["source_id"] != source_id:
                raise OntologyValidationError(
                    f"availability {availability_id} source does not match series {series_id}"
                )
            if self._series_target(series, series_id) != target:
                raise OntologyValidationError(
                    f"availability {availability_id} target does not match series {series_id}"
                )
            by_series[series_id].append(item)

        missing = [
            series_id for series_id in self._series if series_id not in by_series
        ]
        if missing:
            raise OntologyValidationError(
                "source series without availability assertion: " + ", ".join(missing)
            )
        self._availability_by_series = by_series

    def _validate_facet_assignments(self, value: Any, path: str) -> None:
        assignments = _mapping(value, f"{path}.facet_tags")
        for facet_id, tag_ids_value in assignments.items():
            if facet_id not in self._facets:
                raise OntologyValidationError(
                    f"{path} references unknown facet {facet_id}"
                )
            tag_ids = _string_sequence(tag_ids_value, f"{path}.facet_tags.{facet_id}")
            for tag_id in tag_ids:
                tag = self._tags.get(tag_id)
                if tag is None:
                    raise OntologyValidationError(
                        f"{path} references unknown tag {tag_id}"
                    )
                if tag["facet_id"] != facet_id:
                    raise OntologyValidationError(
                        f"{path} assigns tag {tag_id} under facet {facet_id}, "
                        f"but the tag belongs to {tag['facet_id']}"
                    )

    def _validate_reference(self, value: Any, path: str) -> dict[str, str]:
        reference = _mapping(value, path)
        kind = _required_string(reference, "kind", path)
        entity_id = _required_string(reference, "id", path)
        if kind not in _REFERENCE_KINDS:
            raise OntologyValidationError(
                f"{path}.kind must be one of {sorted(_REFERENCE_KINDS)}"
            )
        index = self._concepts if kind == "concept" else self._groups
        if entity_id not in index:
            raise OntologyValidationError(
                f"{path} references unknown {kind} {entity_id}"
            )
        return {"kind": kind, "id": entity_id}

    @staticmethod
    def _validate_rollup(entity: Mapping[str, Any], path: str) -> None:
        if entity.get("rollup_policy") != NO_AUTO_ROLLUP:
            raise OntologyValidationError(
                f"{path}.rollup_policy must be 'no_auto_rollup'"
            )

    @staticmethod
    def _validate_optional_enum(
        entity: Mapping[str, Any],
        key: str,
        allowed: frozenset[str],
        path: str,
    ) -> None:
        value = entity.get(key)
        if value is not None and value not in allowed:
            raise OntologyValidationError(
                f"{path}.{key} must be one of {sorted(allowed)}"
            )

    @staticmethod
    def _series_target(series: Mapping[str, Any], series_id: str) -> dict[str, str]:
        concept_id = series.get("concept_id")
        group_id = series.get("group_id")
        if (concept_id is None) == (group_id is None):
            raise OntologyValidationError(
                f"series {series_id} must define exactly one of concept_id or group_id"
            )
        if concept_id is not None:
            if not isinstance(concept_id, str) or not concept_id:
                raise OntologyValidationError(
                    f"series {series_id}.concept_id must be a non-empty string"
                )
            return {"kind": "concept", "id": concept_id}
        if not isinstance(group_id, str) or not group_id:
            raise OntologyValidationError(
                f"series {series_id}.group_id must be a non-empty string"
            )
        return {"kind": "group", "id": group_id}

    def _facet_tree(self, facet_id: str) -> dict[str, Any]:
        facet = self._get(self._facets, facet_id, "facet")
        children: dict[str, list[str]] = defaultdict(list)
        tag_order = [tag["id"] for tag in facet["tags"]]
        for tag in facet["tags"]:
            for parent_id in tag["parent_ids"]:
                children[parent_id].append(tag["id"])

        def build(tag_id: str, path: tuple[str, ...]) -> dict[str, Any]:
            # Validation already guarantees a DAG. The path guard protects this
            # read API even if internals are accidentally changed during debugging.
            if tag_id in path:
                raise OntologyValidationError(
                    "cycle encountered while rendering facet tree: "
                    + " -> ".join((*path, tag_id))
                )
            node = copy.deepcopy(self._tags[tag_id])
            node["children"] = [
                build(child_id, (*path, tag_id)) for child_id in children[tag_id]
            ]
            return node

        result = {
            key: copy.deepcopy(value) for key, value in facet.items() if key != "tags"
        }
        roots = [tag_id for tag_id in tag_order if not self._tags[tag_id]["parent_ids"]]
        result["roots"] = [build(tag_id, ()) for tag_id in roots]
        result["tag_count"] = len(tag_order)
        return result

    def _resolved_facet_tags(
        self, assignments: Mapping[str, Sequence[str]]
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            facet_id: [copy.deepcopy(self._tags[tag_id]) for tag_id in tag_ids]
            for facet_id, tag_ids in assignments.items()
        }

    def _relations_for(
        self, kind: str, entity_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        reference = {"kind": kind, "id": entity_id}
        return {
            "outgoing": [
                copy.deepcopy(relation)
                for relation in self._relations.values()
                if relation["from_ref"] == reference
            ],
            "incoming": [
                copy.deepcopy(relation)
                for relation in self._relations.values()
                if relation["to_ref"] == reference
            ],
        }

    def _availability_for(self, kind: str, entity_id: str) -> list[dict[str, Any]]:
        reference = {"kind": kind, "id": entity_id}
        return [
            copy.deepcopy(item)
            for item in self._availability.values()
            if item["target_ref"] == reference
        ]

    def _series_detail(self, series: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(series))
        source = self._sources[series["source_id"]]
        target_ref = self._series_target(series, series["id"])
        target_index = (
            self._concepts if target_ref["kind"] == "concept" else self._groups
        )
        target = target_index[target_ref["id"]]
        result["source"] = copy.deepcopy(source)
        result["target_ref"] = target_ref
        result["target"] = (
            self._concept_summary(target)
            if target_ref["kind"] == "concept"
            else self._group_summary(target)
        )
        result["facet_details"] = self._resolved_facet_tags(
            series.get("facet_tags", {})
        )
        result["availability"] = [
            copy.deepcopy(item)
            for item in self._availability_by_series.get(series["id"], ())
        ]
        return result

    def _tag_ancestors_for_concept(self, concept: Mapping[str, Any]) -> set[str]:
        result: set[str] = set()
        pending = [
            tag_id
            for tag_ids in concept.get("facet_tags", {}).values()
            for tag_id in tag_ids
        ]
        while pending:
            tag_id = pending.pop()
            if tag_id in result:
                continue
            result.add(tag_id)
            pending.extend(self._tags[tag_id]["parent_ids"])
        return result

    @staticmethod
    def _concept_summary(concept: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(concept[key])
            for key in ("id", "status", "labels", "rollup_policy")
        }

    @staticmethod
    def _group_summary(group: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(group[key]) for key in ("id", "labels", "rollup_policy")
        }

    @staticmethod
    def _get(index: Mapping[str, Any], entity_id: str, kind: str) -> Any:
        try:
            return index[entity_id]
        except KeyError as exc:
            raise KeyError(f"unknown {kind} id: {entity_id}") from exc


def load_disease_ontology(
    path: str | Path | None = None,
) -> DiseaseOntology:
    """Load the default registry or a caller-supplied JSON registry path."""

    return DiseaseOntology.from_file(DEFAULT_ONTOLOGY_PATH if path is None else path)


def _indexed(values: list[Any], collection: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for position, value in enumerate(values):
        path = f"{collection}[{position}]"
        item = _mapping(value, path)
        entity_id = _required_string(item, "id", path)
        if entity_id in result:
            raise OntologyValidationError(f"duplicate id {entity_id!r} in {collection}")
        result[entity_id] = item
    return result


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OntologyValidationError(f"{path} must be an object")
    return dict(value)


def _required_list(container: Mapping[str, Any], key: str, path: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise OntologyValidationError(f"{path}.{key} must be an array")
    return value


def _required_string(container: Mapping[str, Any], key: str, path: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OntologyValidationError(f"{path}.{key} must be a non-empty string")
    return value


def _string_list(container: Mapping[str, Any], key: str, path: str) -> list[str]:
    return _string_sequence(container.get(key), f"{path}.{key}")


def _string_sequence(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise OntologyValidationError(f"{path} must be an array")
    if any(not isinstance(item, str) or not item for item in value):
        raise OntologyValidationError(f"{path} must contain only non-empty strings")
    if len(value) != len(set(value)):
        raise OntologyValidationError(f"{path} must not contain duplicates")
    return value


def _validate_optional_date_range(container: Mapping[str, Any], path: str) -> None:
    parsed: dict[str, date | None] = {}
    for key in ("valid_from", "valid_to"):
        value = container.get(key)
        if value is None:
            parsed[key] = None
            continue
        if not isinstance(value, str):
            raise OntologyValidationError(f"{path}.{key} must be an ISO date string")
        try:
            parsed[key] = date.fromisoformat(value)
        except ValueError as exc:
            raise OntologyValidationError(
                f"{path}.{key} must be an ISO date string"
            ) from exc
    if (
        parsed["valid_from"] is not None
        and parsed["valid_to"] is not None
        and parsed["valid_to"] < parsed["valid_from"]
    ):
        raise OntologyValidationError(f"{path} has an invalid validity range")


def _labels(container: Mapping[str, Any], path: str) -> dict[str, Any]:
    labels = _mapping(container.get("labels"), f"{path}.labels")
    _required_string(labels, "en", f"{path}.labels")
    return labels


def _assert_acyclic(
    nodes: Mapping[str, Any] | set[str],
    edges: Mapping[str, Sequence[str]],
    graph_name: str,
) -> None:
    states: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state = states.get(node, 0)
        if state == 2:
            return
        if state == 1:
            start = stack.index(node)
            cycle = stack[start:] + [node]
            raise OntologyValidationError(
                f"cycle in {graph_name}: " + " -> ".join(cycle)
            )
        states[node] = 1
        stack.append(node)
        for neighbor in edges.get(node, ()):
            visit(neighbor)
        stack.pop()
        states[node] = 2

    for node in nodes:
        visit(node)


def _reference_key(reference: Mapping[str, str]) -> str:
    return f"{reference['kind']}:{reference['id']}"


def _casefold(value: str | None) -> str | None:
    return value.casefold() if value is not None else None


__all__ = [
    "DEFAULT_ONTOLOGY_PATH",
    "NO_AUTO_ROLLUP",
    "DiseaseOntology",
    "OntologyValidationError",
    "load_disease_ontology",
]
