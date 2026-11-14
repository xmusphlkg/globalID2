"""Pure normalization, source descriptors and hierarchy inputs for mapping audits."""
from __future__ import annotations
import json
import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any

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
