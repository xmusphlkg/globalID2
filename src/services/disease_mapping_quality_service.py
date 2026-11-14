"""Deterministic quality checks for country disease mappings.

This module deliberately does not try to infer medical equivalence.  It checks
structural invariants that must hold before a mapping is safe to publish:

* independent source series must not be hidden behind one country/disease key;
* one normalized country label must not resolve to multiple disease IDs; and
* an aggregate series and one of its descendants must not be summed together.

The checks are pure and deterministic so they can be used from tests, CI, or a
command-line audit without an AI provider or a database connection.
"""

from __future__ import annotations

from src.ontology.mapping_quality_inputs import (
    LANGUAGE_ONLY_MAPPING_STEMS as LANGUAGE_ONLY_MAPPING_STEMS,
    MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID as MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID,
    NORMALIZED_NAME_MULTIPLE_IDS as NORMALIZED_NAME_MULTIPLE_IDS,
    AGGREGATE_CHILD_DOUBLE_COUNT_RISK as AGGREGATE_CHILD_DOUBLE_COUNT_RISK,
    RESOLVED_BY_SERIES_REGISTRY as RESOLVED_BY_SERIES_REGISTRY,
    LEGACY_FLAT_PROJECTION_LOSSY as LEGACY_FLAT_PROJECTION_LOSSY,
    SERIES_BACKFILL_PENDING as SERIES_BACKFILL_PENDING,
    MALFORMED_MAPPING_ROW as MALFORMED_MAPPING_ROW,
    CHECK_CODES as CHECK_CODES,
    SEVERITIES as SEVERITIES,
    _FOOTNOTE_RE as _FOOTNOTE_RE,
    _SPACE_RE as _SPACE_RE,
    _TRUE_VALUES as _TRUE_VALUES,
    _COUNTRY_FIELDS as _COUNTRY_FIELDS,
    _DISEASE_ID_FIELDS as _DISEASE_ID_FIELDS,
    _LOCAL_NAME_FIELDS as _LOCAL_NAME_FIELDS,
    _SOURCE_FIELDS as _SOURCE_FIELDS,
    _SERIES_ID_FIELDS as _SERIES_ID_FIELDS,
    _EXPLICIT_SERIES_ID_FIELDS as _EXPLICIT_SERIES_ID_FIELDS,
    _LOCAL_CODE_FIELDS as _LOCAL_CODE_FIELDS,
    _RESOLVABLE_SERIES_STATUSES as _RESOLVABLE_SERIES_STATUSES,
    _BACKFILL_PENDING as _BACKFILL_PENDING,
    normalize_mapping_name as normalize_mapping_name,
    split_mapping_aliases as split_mapping_aliases,
    _first_value as _first_value,
    _country_code as _country_code,
    _disease_id as _disease_id,
    _local_name as _local_name,
    _data_source as _data_source,
    _explicit_series_id as _explicit_series_id,
    _is_alias_row as _is_alias_row,
    _is_registry_category_dimension as _is_registry_category_dimension,
    _location as _location,
    _assignment as _assignment,
    _series_descriptor as _series_descriptor,
    _json_sort_key as _json_sort_key,
    _deduplicate_dicts as _deduplicate_dicts,
    _string_values as _string_values,
    _exact_match_key as _exact_match_key,
    _registry_disease_id as _registry_disease_id,
    _normalized_registry_rows as _normalized_registry_rows,
    _registry_candidates as _registry_candidates,
    _normalized_id_set as _normalized_id_set,
    _coerce_edges as _coerce_edges,
    _descendant_depths as _descendant_depths,
)


import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_DIR = ROOT / "configs" / "mapping"
# ``en.csv`` is the canonical English-name catalogue, not an ISO country
# mapping.  Treating it as country ``EN`` creates false country-level findings.


class DiseaseMappingQualityService:
    """Run deterministic structural checks over disease mapping records."""

    def __init__(self, mapping_dir: Path = DEFAULT_MAPPING_DIR) -> None:
        self.mapping_dir = Path(mapping_dir)

    def load_mapping_rows(self) -> list[dict[str, Any]]:
        """Load all country mapping CSVs and attach source-line evidence."""

        if not self.mapping_dir.is_dir():
            raise FileNotFoundError(f"mapping directory does not exist: {self.mapping_dir}")

        rows: list[dict[str, Any]] = []
        for path in sorted(self.mapping_dir.glob("*.csv")):
            if path.stem.casefold() in LANGUAGE_ONLY_MAPPING_STEMS:
                continue
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                for row_number, raw_row in enumerate(reader, start=2):
                    row: dict[str, Any] = dict(raw_row)
                    if not str(row.get("country_code", "") or "").strip():
                        row["country_code"] = path.stem.upper()
                    row["_source_path"] = str(path)
                    row["_row_number"] = row_number
                    rows.append(row)
        return rows

    def run_audit(
        self,
        mapping_rows: Iterable[Mapping[str, Any]] | None = None,
        *,
        source_series_rows: Iterable[Mapping[str, Any]] | None = None,
        series_registry_rows: Iterable[Mapping[str, Any]] | None = None,
        hierarchy: object | None = None,
        aggregate_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Audit mappings and return a stable, JSON-serializable report.

        ``mapping_rows`` define names and aliases.  ``source_series_rows`` may
        provide a more precise inventory of independently reported series; if
        omitted, each primary mapping CSV row is treated as a source series.
        ``series_registry_rows`` is the independent ontology registry used to
        prove that a flat-key collision is protected in the canonical fact
        layer.  It does not replace the source-series inventory.

        ``hierarchy`` is optional.  When absent, the aggregate/child check is
        explicitly reported as skipped.  ``aggregate_ids`` can restrict which
        parent nodes represent additive totals; otherwise every hierarchy parent
        is considered an aggregate for this conservative risk check.
        """

        loaded_rows = self.load_mapping_rows() if mapping_rows is None else list(mapping_rows)
        rows = [dict(row) for row in loaded_rows]
        if source_series_rows is None:
            series_rows = rows
            series_input = "mapping_primary_rows"
        else:
            series_rows = [dict(row) for row in source_series_rows]
            series_input = "source_series_rows"
        registry_rows = (
            None
            if series_registry_rows is None
            else _normalized_registry_rows(series_registry_rows)
        )

        findings: list[dict[str, Any]] = []
        findings.extend(self._malformed_mapping_findings(rows))
        series_findings, collision_coverage = self._multiple_series_findings(
            series_rows,
            registry_rows=registry_rows,
        )
        findings.extend(series_findings)
        findings.extend(self._ambiguous_name_findings(rows))

        explicit_mapping_total = 0
        explicit_mapping_bound = 0
        for row in rows:
            if _is_alias_row(row) or not _explicit_series_id(row):
                continue
            country = _country_code(row)
            disease_id = _disease_id(row)
            if not country or not disease_id:
                continue
            explicit_mapping_total += 1
            if registry_rows is None:
                continue
            _, candidates = _registry_candidates(
                row,
                country=country,
                disease_id=disease_id,
                registry_rows=registry_rows,
            )
            if len(candidates) == 1:
                explicit_mapping_bound += 1

        registry_coverage = {
            "status": "skipped" if registry_rows is None else "completed",
            "registry_series_count": 0 if registry_rows is None else len(registry_rows),
            "collision_identities_total": collision_coverage["total"],
            "collision_identities_bound": collision_coverage["bound"],
            "mapping_rows_explicit_series_id": explicit_mapping_total,
            "mapping_rows_explicit_bound": explicit_mapping_bound,
        }
        if registry_rows is None:
            registry_coverage["reason"] = "ontology series registry was not provided"

        hierarchy_status: dict[str, Any]
        if hierarchy is None:
            hierarchy_status = {
                "status": "skipped",
                "finding_count": 0,
                "reason": "ontology hierarchy was not provided",
            }
        else:
            adjacency, configured_aggregates = _coerce_edges(hierarchy)
            if aggregate_ids is not None:
                selected_aggregates = _normalized_id_set(aggregate_ids)
            elif configured_aggregates is not None:
                selected_aggregates = configured_aggregates
            else:
                selected_aggregates = set(adjacency)
            hierarchy_findings = self._aggregate_child_findings(
                series_rows,
                adjacency,
                selected_aggregates,
            )
            findings.extend(hierarchy_findings)
            hierarchy_status = {
                "status": "completed",
                "finding_count": len(hierarchy_findings),
                "aggregate_node_count": len(selected_aggregates),
                "edge_count": sum(len(children) for children in adjacency.values()),
            }

        severity_rank = {severity: index for index, severity in enumerate(SEVERITIES)}
        findings.sort(
            key=lambda finding: (
                severity_rank.get(str(finding.get("severity")), len(SEVERITIES)),
                str(finding.get("code", "")),
                _json_sort_key(finding.get("evidence", {})),
            )
        )
        code_counts = Counter(str(finding["code"]) for finding in findings)
        severity_counts = Counter(str(finding["severity"]) for finding in findings)

        registry_check_status = "skipped" if registry_rows is None else "completed"
        checks = {
            MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID: {
                "status": "completed",
                "finding_count": code_counts[MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID],
                "series_input": series_input,
            },
            NORMALIZED_NAME_MULTIPLE_IDS: {
                "status": "completed",
                "finding_count": code_counts[NORMALIZED_NAME_MULTIPLE_IDS],
            },
            AGGREGATE_CHILD_DOUBLE_COUNT_RISK: hierarchy_status,
            MALFORMED_MAPPING_ROW: {
                "status": "completed",
                "finding_count": code_counts[MALFORMED_MAPPING_ROW],
            },
            RESOLVED_BY_SERIES_REGISTRY: {
                "status": registry_check_status,
                "finding_count": code_counts[RESOLVED_BY_SERIES_REGISTRY],
            },
            LEGACY_FLAT_PROJECTION_LOSSY: {
                "status": registry_check_status,
                "finding_count": code_counts[LEGACY_FLAT_PROJECTION_LOSSY],
            },
            SERIES_BACKFILL_PENDING: {
                "status": registry_check_status,
                "finding_count": code_counts[SERIES_BACKFILL_PENDING],
            },
        }
        return {
            "summary": {
                "mapping_row_count": len(rows),
                "source_series_row_count": len(series_rows),
                "finding_count": len(findings),
                "by_severity": {
                    severity: severity_counts[severity] for severity in SEVERITIES
                },
                "by_code": {code: code_counts[code] for code in CHECK_CODES},
                "registry_coverage": registry_coverage,
            },
            "checks": checks,
            "findings": findings,
        }

    @staticmethod
    def _multiple_series_findings(
        rows: Iterable[Mapping[str, Any]],
        *,
        registry_rows: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        grouped: dict[
            tuple[str, str],
            dict[tuple[str, str, str], list[tuple[dict[str, Any], dict[str, Any]]]],
        ] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            parsed = _series_descriptor(row)
            if parsed is None:
                continue
            identity, descriptor = parsed
            grouped[(_country_code(row), _disease_id(row))][identity].append(
                (dict(row), descriptor)
            )

        findings: list[dict[str, Any]] = []
        collision_identity_total = 0
        collision_identity_bound = 0
        for (country, disease_id), series_by_identity in sorted(grouped.items()):
            if len(series_by_identity) <= 1:
                continue
            collision_identity_total += len(series_by_identity)
            descriptors: list[dict[str, Any]] = []
            for identity in sorted(series_by_identity):
                descriptors.extend(
                    _deduplicate_dicts(
                        descriptor
                        for _, descriptor in series_by_identity[identity]
                    )
                )

            evidence: dict[str, Any] = {
                "country_code": country,
                "disease_id": disease_id,
                "source_series_count": len(series_by_identity),
                "source_series": descriptors,
            }
            resolved = False
            pending_series_ids: list[str] = []
            if registry_rows is not None:
                coverage_identities: list[dict[str, Any]] = []
                unique_bound_ids: list[str] = []
                for identity in sorted(series_by_identity):
                    entries = series_by_identity[identity]
                    candidate_sets: list[set[str]] = []
                    candidate_by_id: dict[str, dict[str, Any]] = {}
                    match_methods: set[str] = set()
                    for row, _ in entries:
                        method, candidates = _registry_candidates(
                            row,
                            country=country,
                            disease_id=disease_id,
                            registry_rows=registry_rows,
                        )
                        match_methods.add(method)
                        ids = {item["series_id"] for item in candidates}
                        candidate_sets.append(ids)
                        candidate_by_id.update(
                            {item["series_id"]: item for item in candidates}
                        )
                    candidate_ids = (
                        set.intersection(*candidate_sets) if candidate_sets else set()
                    )
                    if len(candidate_ids) == 1:
                        collision_identity_bound += 1
                        bound_id = next(iter(candidate_ids))
                        unique_bound_ids.append(bound_id)
                        if _BACKFILL_PENDING in candidate_by_id[bound_id][
                            "availability_statuses"
                        ]:
                            pending_series_ids.append(bound_id)
                        match_status = "bound"
                    elif candidate_ids:
                        match_status = "ambiguous"
                    else:
                        match_status = "unbound"
                    coverage_identities.append(
                        {
                            "match_status": match_status,
                            "match_methods": sorted(match_methods),
                            "candidate_series_ids": sorted(candidate_ids),
                            "source_series": _deduplicate_dicts(
                                descriptor for _, descriptor in entries
                            ),
                        }
                    )

                resolved = (
                    len(unique_bound_ids) == len(series_by_identity)
                    and len(set(unique_bound_ids)) == len(series_by_identity)
                )
                evidence["registry_coverage"] = {
                    "collision_identity_count": len(series_by_identity),
                    "bound_identity_count": sum(
                        item["match_status"] == "bound"
                        for item in coverage_identities
                    ),
                    "distinct_bound_series_count": len(set(unique_bound_ids)),
                    "all_identities_uniquely_bound_to_distinct_series": resolved,
                    "identities": coverage_identities,
                }

            if resolved:
                findings.extend(
                    [
                        {
                            "severity": "info",
                            "code": RESOLVED_BY_SERIES_REGISTRY,
                            "message": (
                                "Every colliding flat mapping identity is uniquely "
                                "bound to a distinct active or historical registry series."
                            ),
                            "evidence": evidence,
                        },
                        {
                            "severity": "warning",
                            "code": LEGACY_FLAT_PROJECTION_LOSSY,
                            "message": (
                                "The canonical series registry protects these observations, "
                                "but the legacy country/disease flat projection remains lossy."
                            ),
                            "evidence": evidence,
                        },
                    ]
                )
                if pending_series_ids:
                    pending_evidence = dict(evidence)
                    pending_evidence["pending_series_ids"] = sorted(
                        set(pending_series_ids)
                    )
                    findings.append(
                        {
                            "severity": "warning",
                            "code": SERIES_BACKFILL_PENDING,
                            "message": (
                                "At least one protected registry series is available "
                                "upstream but still awaits ingestion or backfill."
                            ),
                            "evidence": pending_evidence,
                        }
                    )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID,
                        "message": (
                            "Multiple independent source series resolve to one disease ID "
                            "within a country and can overwrite or collapse each other."
                        ),
                        "evidence": evidence,
                    }
                )
        return findings, {
            "total": collision_identity_total,
            "bound": collision_identity_bound,
        }

    @staticmethod
    def _malformed_mapping_findings(
        rows: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for row in rows:
            if None not in row:
                continue
            extra_values = _string_values(row.get(None))
            evidence: dict[str, Any] = {"extra_values": extra_values}
            country = _country_code(row)
            if country:
                evidence["country_code"] = country
            location = _location(row)
            if location:
                evidence["location"] = location
            findings.append(
                {
                    "severity": "error",
                    "code": MALFORMED_MAPPING_ROW,
                    "message": (
                        "The CSV row has more values than header columns; DictReader "
                        "stored the overflow under a null column name."
                    ),
                    "evidence": evidence,
                }
            )
        return findings

    @staticmethod
    def _ambiguous_name_findings(
        rows: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            country = _country_code(row)
            disease_id = _disease_id(row)
            if not country or not disease_id:
                continue

            primary_name = _local_name(row)
            candidates: list[tuple[str, str]] = []
            if primary_name:
                candidates.append(("local_name", primary_name))
            candidates.extend(("alias", alias) for alias in split_mapping_aliases(row.get("aliases")))
            for name_field, raw_name in candidates:
                normalized = normalize_mapping_name(raw_name)
                if normalized:
                    grouped[(country, normalized)].append(
                        _assignment(row, raw_name=raw_name, name_field=name_field)
                    )

        findings: list[dict[str, Any]] = []
        for (country, normalized_name), raw_assignments in sorted(grouped.items()):
            assignments = _deduplicate_dicts(raw_assignments)
            disease_ids = sorted({item["disease_id"] for item in assignments})
            if len(disease_ids) <= 1:
                continue
            findings.append(
                {
                    "severity": "error",
                    "code": NORMALIZED_NAME_MULTIPLE_IDS,
                    "message": (
                        "The same normalized source name resolves to multiple disease IDs "
                        "within one country."
                    ),
                    "evidence": {
                        "country_code": country,
                        "normalized_name": normalized_name,
                        "disease_ids": disease_ids,
                        "assignments": assignments,
                    },
                }
            )
        return findings

    @staticmethod
    def _aggregate_child_findings(
        rows: Iterable[Mapping[str, Any]],
        adjacency: Mapping[str, set[str]],
        aggregate_ids: set[str],
    ) -> list[dict[str, Any]]:
        series_by_country_disease: dict[
            tuple[str, str], dict[tuple[str, str], list[dict[str, Any]]]
        ] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            parsed = _series_descriptor(row)
            if parsed is None:
                continue
            identity, descriptor = parsed
            key = (_country_code(row), _disease_id(row))
            series_by_country_disease[key][identity].append(descriptor)

        country_diseases: dict[str, set[str]] = defaultdict(set)
        for country, disease_id in series_by_country_disease:
            country_diseases[country].add(disease_id)

        def descriptors(country: str, disease_id: str) -> list[dict[str, Any]]:
            values: list[dict[str, Any]] = []
            for identity in sorted(series_by_country_disease[(country, disease_id)]):
                values.extend(
                    _deduplicate_dicts(
                        series_by_country_disease[(country, disease_id)][identity]
                    )
                )
            return values

        findings: list[dict[str, Any]] = []
        for country, present_ids in sorted(country_diseases.items()):
            for parent in sorted(aggregate_ids):
                if parent not in present_ids:
                    continue
                depths = _descendant_depths(adjacency, parent)
                present_descendants = sorted(set(depths).intersection(present_ids))
                if not present_descendants:
                    continue
                findings.append(
                    {
                        "severity": "warning",
                        "code": AGGREGATE_CHILD_DOUBLE_COUNT_RISK,
                        "message": (
                            "An aggregate series and descendant series coexist in one "
                            "country; additive summaries can double count them."
                        ),
                        "evidence": {
                            "country_code": country,
                            "parent_disease_id": parent,
                            "parent_source_series": descriptors(country, parent),
                            "descendants": [
                                {
                                    "disease_id": child,
                                    "relation_depth": depths[child],
                                    "source_series": descriptors(country, child),
                                }
                                for child in present_descendants
                            ],
                        },
                    }
                )
        return findings


__all__ = [
    "AGGREGATE_CHILD_DOUBLE_COUNT_RISK",
    "CHECK_CODES",
    "DiseaseMappingQualityService",
    "LEGACY_FLAT_PROJECTION_LOSSY",
    "MALFORMED_MAPPING_ROW",
    "MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID",
    "NORMALIZED_NAME_MULTIPLE_IDS",
    "RESOLVED_BY_SERIES_REGISTRY",
    "SERIES_BACKFILL_PENDING",
    "normalize_mapping_name",
    "split_mapping_aliases",
]
