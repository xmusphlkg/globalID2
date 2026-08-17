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
LANGUAGE_ONLY_MAPPING_STEMS = {"en"}

MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID = "MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID"
NORMALIZED_NAME_MULTIPLE_IDS = "NORMALIZED_NAME_MULTIPLE_IDS"
AGGREGATE_CHILD_DOUBLE_COUNT_RISK = "AGGREGATE_CHILD_DOUBLE_COUNT_RISK"
RESOLVED_BY_SERIES_REGISTRY = "RESOLVED_BY_SERIES_REGISTRY"
LEGACY_FLAT_PROJECTION_LOSSY = "LEGACY_FLAT_PROJECTION_LOSSY"
SERIES_BACKFILL_PENDING = "SERIES_BACKFILL_PENDING"
MALFORMED_MAPPING_ROW = "MALFORMED_MAPPING_ROW"

CHECK_CODES = tuple(
    sorted(
        {
            MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID,
            NORMALIZED_NAME_MULTIPLE_IDS,
            AGGREGATE_CHILD_DOUBLE_COUNT_RISK,
            RESOLVED_BY_SERIES_REGISTRY,
            LEGACY_FLAT_PROJECTION_LOSSY,
            SERIES_BACKFILL_PENDING,
            MALFORMED_MAPPING_ROW,
        }
    )
)
SEVERITIES = ("error", "warning", "info")

_FOOTNOTE_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SPACE_RE = re.compile(r"\s+")
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}

_COUNTRY_FIELDS = ("country_code", "country_id", "country")
_DISEASE_ID_FIELDS = ("disease_id", "standard_disease_id", "target_disease_id")
_LOCAL_NAME_FIELDS = (
    "local_name",
    "disease_name",
    "source_name",
    "condition_name",
    "name",
)
_SOURCE_FIELDS = ("data_source", "source", "dataset", "source_dataset")
_SERIES_ID_FIELDS = ("series_id", "source_series_id", "local_code", "source_code")
_EXPLICIT_SERIES_ID_FIELDS = ("series_id", "source_series_id")
_LOCAL_CODE_FIELDS = ("local_code", "source_code")
_RESOLVABLE_SERIES_STATUSES = {"active", "historical"}
_BACKFILL_PENDING = "upstream_available_ingestion_pending"


def normalize_mapping_name(value: object) -> str:
    """Return a Unicode-aware comparison key for a source disease label."""

    raw = _FOOTNOTE_RE.sub("", str(value or ""))
    text = unicodedata.normalize("NFKC", raw).casefold().strip()
    # Preserve letters, numbers, and combining marks from every writing system.
    # Punctuation is only a separator, so ``Hepatitis-B`` and ``Hepatitis B``
    # resolve to the same audit key.
    text = "".join(
        char if unicodedata.category(char)[:1] in {"L", "M", "N"} else " "
        for char in text
    )
    return _SPACE_RE.sub(" ", text).strip()


def split_mapping_aliases(value: object) -> list[str]:
    """Split the pipe-delimited alias format used by mapping CSV files."""

    if isinstance(value, (list, tuple, set)):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def _first_value(row: Mapping[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = str(row.get(field, "") or "").strip()
        if value:
            return value
    return ""


def _country_code(row: Mapping[str, Any]) -> str:
    return _first_value(row, _COUNTRY_FIELDS).upper()


def _disease_id(row: Mapping[str, Any]) -> str:
    return _first_value(row, _DISEASE_ID_FIELDS).upper()


def _local_name(row: Mapping[str, Any]) -> str:
    return _first_value(row, _LOCAL_NAME_FIELDS)


def _data_source(row: Mapping[str, Any]) -> str:
    return _first_value(row, _SOURCE_FIELDS)


def _explicit_series_id(row: Mapping[str, Any]) -> str:
    return _first_value(row, _EXPLICIT_SERIES_ID_FIELDS)


def _is_alias_row(row: Mapping[str, Any]) -> bool:
    value = row.get("is_alias", False)
    if isinstance(value, bool):
        return value
    if str(value or "").strip().casefold() in _TRUE_VALUES:
        return True
    return str(row.get("record_type", "") or "").strip().casefold() == "alias"


def _is_registry_category_dimension(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("mapping_scope", "") or "").strip().casefold()
        == "source_category_dimension"
    )


def _location(row: Mapping[str, Any]) -> dict[str, Any] | None:
    path = str(row.get("_source_path", row.get("source_path", "")) or "").strip()
    raw_line = row.get("_row_number", row.get("row_number"))
    location: dict[str, Any] = {}
    if path:
        location["path"] = path
    if raw_line not in (None, ""):
        try:
            location["row"] = int(raw_line)
        except (TypeError, ValueError):
            location["row"] = str(raw_line)
    return location or None


def _assignment(
    row: Mapping[str, Any],
    *,
    raw_name: str,
    name_field: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "disease_id": _disease_id(row),
        "name_field": name_field,
        "raw_name": raw_name,
    }
    source = _data_source(row)
    if source:
        result["data_source"] = source
    location = _location(row)
    if location:
        result["location"] = location
    return result


def _series_descriptor(
    row: Mapping[str, Any],
) -> tuple[tuple[str, str, str], dict[str, Any]] | None:
    country = _country_code(row)
    disease_id = _disease_id(row)
    if (
        not country
        or not disease_id
        or _is_alias_row(row)
        or _is_registry_category_dimension(row)
    ):
        return None

    source = _data_source(row)
    explicit_series_id = _explicit_series_id(row)
    series_value = ""
    series_field = ""
    for field in _SERIES_ID_FIELDS:
        candidate = str(row.get(field, "") or "").strip()
        if candidate:
            series_value = candidate
            series_field = field
            break
    local_name = _local_name(row)
    if not series_value:
        series_value = local_name
        series_field = "local_name"
    normalized_series = normalize_mapping_name(series_value)
    if not normalized_series:
        return None

    # Explicit series IDs are stable identities.  Multiple mapping rows may be
    # aliases for the same registered series and must not become fake,
    # independent series merely because their labels differ.
    if explicit_series_id:
        identity = ("explicit_series_id", explicit_series_id.casefold(), "")
    # Some surveillance feeds reuse one condition code for separately reported
    # case-status components (for example confirmed and probable).  The source
    # label/qualifier is therefore part of the series identity even when a
    # local code is present; otherwise those components disappear from this
    # structural audit before reaching the legacy fact-key collision check.
    else:
        identity = (
            normalize_mapping_name(source),
            normalized_series,
            normalize_mapping_name(local_name),
        )
    descriptor: dict[str, Any] = {
        "series_key": series_value,
        "series_key_field": series_field,
    }
    if local_name:
        descriptor["local_name"] = local_name
    if source:
        descriptor["data_source"] = source
    location = _location(row)
    if location:
        descriptor["location"] = location
    return identity, descriptor


def _json_sort_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _deduplicate_dicts(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {_json_sort_key(value): value for value in values}
    return [unique[key] for key in sorted(unique)]


def _string_values(value: object) -> list[str]:
    """Coerce a registry scalar or sequence to non-empty strings."""

    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values = [value]
    else:
        try:
            values = list(value)  # type: ignore[arg-type]
        except TypeError:
            values = [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _exact_match_key(value: object) -> str:
    """Return a case-insensitive key without erasing meaningful punctuation."""

    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _registry_disease_id(row: Mapping[str, Any]) -> str:
    disease_id = _disease_id(row)
    if disease_id:
        return disease_id
    target_ref = row.get("target_ref")
    if isinstance(target_ref, Mapping) and target_ref.get("kind") == "concept":
        return str(target_ref.get("id", "") or "").strip().upper()
    return ""


def _normalized_registry_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        series_id = _explicit_series_id(row)
        if not series_id:
            continue
        availability_statuses = _string_values(row.get("availability_statuses"))
        if not availability_statuses:
            availability_statuses = _string_values(row.get("availability_status"))
        normalized.append(
            {
                "country_code": _country_code(row),
                "source_id": str(row.get("source_id", "") or "").strip(),
                "series_id": series_id,
                "disease_id": _registry_disease_id(row),
                "group_id": str(row.get("group_id", "") or "").strip(),
                "local_codes": _string_values(row.get("local_codes")),
                "local_labels": _string_values(row.get("local_labels")),
                "status": str(row.get("status", "") or "").strip().casefold(),
                "availability_statuses": sorted(set(availability_statuses)),
            }
        )
    return sorted(normalized, key=_json_sort_key)


def _registry_candidates(
    row: Mapping[str, Any],
    *,
    country: str,
    disease_id: str,
    registry_rows: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Return the registry match method and eligible exact candidates."""

    candidates = [
        item
        for item in registry_rows
        if item["country_code"] == country
        and item["disease_id"] == disease_id
        and item["status"] in _RESOLVABLE_SERIES_STATUSES
    ]
    explicit_source_id = str(row.get("source_id", "") or "").strip()
    if explicit_source_id:
        candidates = [
            item for item in candidates if item["source_id"] == explicit_source_id
        ]

    explicit_series_id = _explicit_series_id(row)
    if explicit_series_id:
        key = explicit_series_id.casefold()
        return "explicit_series_id", [
            item for item in candidates if item["series_id"].casefold() == key
        ]

    local_code = _first_value(row, _LOCAL_CODE_FIELDS)
    local_label = _local_name(row)
    if not local_code and not local_label:
        return "country_target_code_label", []

    if local_code:
        code_key = _exact_match_key(local_code)
        candidates = [
            item
            for item in candidates
            if code_key in {_exact_match_key(value) for value in item["local_codes"]}
        ]
    if local_label:
        label_key = _exact_match_key(local_label)
        candidates = [
            item
            for item in candidates
            if label_key
            in {_exact_match_key(value) for value in item["local_labels"]}
        ]
    return "country_target_code_label", candidates


def _normalized_id_set(values: object) -> set[str]:
    if isinstance(values, (str, bytes)):
        values = [values]
    return {
        str(item or "").strip().upper()
        for item in values or []  # type: ignore[union-attr]
        if str(item or "").strip()
    }


def _coerce_edges(hierarchy: object) -> tuple[dict[str, set[str]], set[str] | None]:
    """Convert supported hierarchy formats into parent-to-child adjacency.

    Supported inputs are ``{parent: [children]}``, an iterable of
    ``(parent, child)`` pairs, or ``{"edges": [...], "aggregate_ids": [...]}``.
    In the first two forms every parent is treated as an aggregate node.
    """

    aggregate_ids: set[str] | None = None
    raw_edges: object = hierarchy
    if isinstance(hierarchy, Mapping) and "edges" in hierarchy:
        raw_edges = hierarchy.get("edges", [])
        if "aggregate_ids" in hierarchy:
            aggregate_ids = _normalized_id_set(hierarchy.get("aggregate_ids", []))

    edges: list[tuple[str, str]] = []
    if isinstance(raw_edges, Mapping):
        for parent, children in raw_edges.items():
            if isinstance(children, str):
                children = [children]
            for child in children or []:
                edges.append((str(parent), str(child)))
    else:
        if isinstance(raw_edges, (str, bytes)):
            raise ValueError("hierarchy must not be a string")
        for item in raw_edges or []:  # type: ignore[union-attr]
            if isinstance(item, Mapping):
                parent = _first_value(item, ("parent_id", "parent", "parent_disease_id"))
                child = _first_value(item, ("child_id", "child", "child_disease_id"))
            else:
                try:
                    parent, child = item
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid hierarchy edge: {item!r}") from exc
            edges.append((str(parent), str(child)))

    adjacency: dict[str, set[str]] = defaultdict(set)
    for raw_parent, raw_child in edges:
        parent = raw_parent.strip().upper()
        child = raw_child.strip().upper()
        if parent and child and parent != child:
            adjacency[parent].add(child)
    return dict(adjacency), aggregate_ids


def _descendant_depths(adjacency: Mapping[str, set[str]], parent: str) -> dict[str, int]:
    depths: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque(
        (child, 1) for child in sorted(adjacency.get(parent, set()))
    )
    while queue:
        node, depth = queue.popleft()
        if node == parent:
            continue
        previous = depths.get(node)
        if previous is not None and previous <= depth:
            continue
        depths[node] = depth
        for child in sorted(adjacency.get(node, set())):
            queue.append((child, depth + 1))
    return depths


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
