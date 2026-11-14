"""Ontario PBIR query projection and DM0 response decoding."""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .common import _norm_header, _norm_text
from .contracts import MonthlyVisual


def _transform_query_tree(value: Any, transform: Any) -> Any:
    """Deep-copy a Power BI query fragment while transforming each object."""

    if isinstance(value, Mapping):
        transformed = {
            str(key): _transform_query_tree(item, transform)
            for key, item in value.items()
        }
        return transform(transformed)
    if isinstance(value, list):
        return [_transform_query_tree(item, transform) for item in value]
    return copy.deepcopy(value)


def _build_pbir_visual_query(
    *,
    report: Mapping[str, Any],
    page: Mapping[str, Any],
    visual: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a Fabric PBIR visual definition into a read-only query.

    Power BI's newer PBIR response no longer includes the legacy executable
    ``visualContainer.query``.  It retains the same source contract as
    declarative projections and report/page/visual filters, so build the
    equivalent SemanticQueryDataShape command without hard-coding PHO values.
    """

    visual_definition = visual.get("visual")
    if not isinstance(visual_definition, Mapping):
        raise ValueError("PHO IDTO PBIR table visual definition is invalid")
    query_state = (visual_definition.get("query") or {}).get("queryState")
    if not isinstance(query_state, Mapping):
        raise ValueError("PHO IDTO PBIR table query state is missing")
    values = query_state.get("Values")
    projections = values.get("projections") if isinstance(values, Mapping) else None
    if not isinstance(projections, list) or not projections:
        raise ValueError("PHO IDTO PBIR table projections are missing")

    entities: list[str] = []

    def alias_for(entity: object) -> str:
        name = _norm_text(entity)
        if not name:
            raise ValueError("PHO IDTO PBIR query contains a blank entity")
        if name not in entities:
            entities.append(name)
        return f"s{entities.index(name)}"

    def projection_transform(node: dict[str, Any]) -> dict[str, Any]:
        source_ref = node.get("SourceRef")
        if isinstance(source_ref, Mapping) and source_ref.get("Entity"):
            node["SourceRef"] = {"Source": alias_for(source_ref["Entity"])}
        return node

    selections: list[dict[str, Any]] = []
    semantic_names: set[str] = set()
    for projection in projections:
        if not isinstance(projection, Mapping):
            raise ValueError("PHO IDTO PBIR projection is not an object")
        field = projection.get("field")
        if not isinstance(field, Mapping) or len(field) != 1:
            raise ValueError("PHO IDTO PBIR projection field is invalid")
        field_kind = next(iter(field))
        if field_kind not in {"Column", "Measure"}:
            raise ValueError(
                f"PHO IDTO PBIR projection type is unsupported: {field_kind}"
            )
        query_ref = _norm_text(projection.get("queryRef"))
        if not query_ref or query_ref in semantic_names:
            raise ValueError("PHO IDTO PBIR projection names are invalid")
        semantic_names.add(query_ref)
        transformed = _transform_query_tree(field, projection_transform)
        selection = {
            field_kind: transformed[field_kind],
            "Name": query_ref,
            "NativeReferenceName": _norm_text(
                projection.get("nativeQueryRef")
                or projection.get("displayName")
                or query_ref
            ),
        }
        selections.append(selection)

    where: list[dict[str, Any]] = []
    filter_configs = (
        report.get("filterConfig"),
        page.get("filterConfig"),
        visual.get("filterConfig"),
    )
    for filter_config in filter_configs:
        filters = (
            filter_config.get("filters")
            if isinstance(filter_config, Mapping)
            else []
        )
        if not isinstance(filters, list):
            raise ValueError("PHO IDTO PBIR filter configuration is invalid")
        for item in filters:
            if not isinstance(item, Mapping):
                raise ValueError("PHO IDTO PBIR filter is not an object")
            query_filter = item.get("filter")
            # Power BI includes empty Advanced filter placeholders for visible
            # measures. They carry no condition and must not become queries.
            if query_filter is None:
                continue
            if not isinstance(query_filter, Mapping):
                raise ValueError("PHO IDTO PBIR filter query is invalid")
            filter_from = query_filter.get("From") or []
            filter_where = query_filter.get("Where") or []
            if not isinstance(filter_from, list) or not isinstance(
                filter_where, list
            ):
                raise ValueError("PHO IDTO PBIR filter query shape is invalid")
            local_aliases: dict[str, str] = {}
            for source in filter_from:
                if not isinstance(source, Mapping):
                    raise ValueError("PHO IDTO PBIR filter source is invalid")
                local_name = _norm_text(source.get("Name"))
                entity = _norm_text(source.get("Entity"))
                if not local_name or not entity or local_name in local_aliases:
                    raise ValueError("PHO IDTO PBIR filter source is ambiguous")
                local_aliases[local_name] = alias_for(entity)

            def filter_transform(node: dict[str, Any]) -> dict[str, Any]:
                source_ref = node.get("SourceRef")
                if not isinstance(source_ref, Mapping):
                    return node
                if source_ref.get("Entity"):
                    node["SourceRef"] = {
                        "Source": alias_for(source_ref["Entity"])
                    }
                    return node
                local_name = _norm_text(source_ref.get("Source"))
                if local_name:
                    if local_name not in local_aliases:
                        raise ValueError(
                            "PHO IDTO PBIR filter references an unknown source"
                        )
                    node["SourceRef"] = {"Source": local_aliases[local_name]}
                return node

            transformed_where = _transform_query_tree(
                filter_where, filter_transform
            )
            if not isinstance(transformed_where, list):
                raise ValueError("PHO IDTO PBIR transformed filter is invalid")
            where.extend(transformed_where)

    semantic_query: dict[str, Any] = {
        "Version": 2,
        "From": [
            {"Name": alias_for(entity), "Entity": entity, "Type": 0}
            for entity in entities
        ],
        "Select": selections,
    }
    if where:
        semantic_query["Where"] = where
    return {
        "Commands": [
            {
                "SemanticQueryDataShapeCommand": {
                    "Query": semantic_query,
                    "Binding": {
                        "Primary": {
                            "Groupings": [
                                {"Projections": list(range(len(selections)))}
                            ]
                        },
                        "DataReduction": {
                            "DataVolume": 3,
                            "Primary": {"Window": {"Count": 500}},
                        },
                        "Version": 1,
                    },
                    "ExecutionMetricsKind": 1,
                }
            }
        ]
    }


def build_query_payload(visual: MonthlyVisual, *, report_id: str) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "queries": [
            {
                "Query": visual.query,
                "ApplicationContext": {
                    "DatasetId": visual.dataset_id,
                    "Sources": [{"ReportId": report_id}],
                },
            }
        ],
        "cancelQueries": [],
        "modelId": visual.model_id,
    }


def decode_powerbi_dm0(response: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Decode the C/R/Ø compression used by the current IDTO table response."""

    try:
        results = response["results"]
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError("PHO IDTO query must return exactly one result")
        data = results[0]["result"]["data"]
        datasets = data["dsr"]["DS"]
        if not isinstance(datasets, list) or len(datasets) != 1:
            raise ValueError("PHO IDTO query must return exactly one dataset")
        dataset = datasets[0]
        phase_groups = dataset["PH"]
        if not isinstance(phase_groups, list) or len(phase_groups) != 1:
            raise ValueError("PHO IDTO query must return exactly one phase group")
        phase_group = phase_groups[0]
        dm0 = phase_group["DM0"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("PHO IDTO query response has no expected DSR/DM0 table") from exc
    if not isinstance(data, Mapping) or not isinstance(dataset, Mapping):
        raise ValueError("PHO IDTO query response has invalid DSR containers")
    if dataset.get("IC") is False:
        raise ValueError("PHO IDTO query returned an incomplete reduced dataset")
    unsupported_continuation_keys = {
        "continuation",
        "continuationtoken",
        "continuationtokens",
        "datareduction",
        "reductioncontinuation",
    }
    for container in (data, data.get("dsr", {}), dataset, phase_group):
        if not isinstance(container, Mapping):
            raise ValueError("PHO IDTO query response has invalid DSR containers")
        normalized_keys = {_norm_header(key).replace(" ", "") for key in container}
        if normalized_keys.intersection(unsupported_continuation_keys):
            raise ValueError("PHO IDTO query returned a paged or data-reduced table")
    alternate_matrices = {
        key
        for key in phase_group
        if re.fullmatch(r"DM\d+", str(key)) and key != "DM0"
    }
    if alternate_matrices:
        raise ValueError("PHO IDTO query returned multiple data matrices")
    if not isinstance(dm0, list) or not dm0:
        raise ValueError("PHO IDTO query returned an empty monthly table")
    if dataset.get("ValueDicts"):
        raise ValueError(
            "PHO IDTO introduced unsupported Power BI value-dictionary encoding"
        )

    schema_rows = [
        index
        for index, row in enumerate(dm0)
        if isinstance(row, dict) and "S" in row
    ]
    if schema_rows != [0]:
        raise ValueError("PHO IDTO DM0 schema must appear exactly once on row 0")
    schema_row = dm0[0]
    schema = schema_row.get("S")
    if not isinstance(schema, list) or not schema:
        raise ValueError("PHO IDTO DM0 schema is empty")
    physical = [_norm_text(item.get("N")) for item in schema]
    if any(not name for name in physical) or len(set(physical)) != len(physical):
        raise ValueError("PHO IDTO DM0 physical column names are invalid")

    previous: list[Any] = [None] * len(physical)
    decoded: list[dict[str, Any]] = []
    for row_number, encoded in enumerate(dm0):
        if not isinstance(encoded, dict):
            raise ValueError(f"PHO IDTO DM0 row {row_number} is not an object")
        try:
            repeat_mask = int(encoded.get("R", 0))
            null_mask = int(encoded.get("Ø", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"PHO IDTO DM0 row {row_number} has an invalid mask") from exc
        if repeat_mask < 0 or null_mask < 0:
            raise ValueError(f"PHO IDTO DM0 row {row_number} has a negative mask")
        if (repeat_mask | null_mask) >> len(physical):
            raise ValueError(
                f"PHO IDTO DM0 row {row_number} mask exceeds the schema width"
            )
        if repeat_mask & null_mask:
            raise ValueError(f"PHO IDTO DM0 row {row_number} has overlapping masks")

        compact = encoded.get("C", [])
        if not isinstance(compact, list):
            raise ValueError(f"PHO IDTO DM0 row {row_number} has invalid compact data")
        position = 0
        values: list[Any] = []
        for column_number in range(len(physical)):
            bit = 1 << column_number
            if repeat_mask & bit:
                if row_number == 0:
                    raise ValueError("PHO IDTO first DM0 row cannot repeat values")
                value = previous[column_number]
            elif null_mask & bit:
                value = None
            else:
                if position >= len(compact):
                    raise ValueError(f"PHO IDTO DM0 row {row_number} compact data underflow")
                value = compact[position]
                position += 1
            values.append(value)
        if position != len(compact):
            raise ValueError(f"PHO IDTO DM0 row {row_number} compact data overflow")
        previous = values
        decoded.append(dict(zip(physical, values)))

    try:
        selections = data["descriptor"]["Select"]
    except (KeyError, TypeError) as exc:
        raise ValueError("PHO IDTO semantic column descriptor is missing") from exc
    if not isinstance(selections, list):
        raise ValueError("PHO IDTO semantic column descriptor is invalid")
    name_map: dict[str, str] = {}
    semantic_names: set[str] = set()
    for selection in selections:
        if not isinstance(selection, dict):
            continue
        value_name = _norm_text(selection.get("Value"))
        semantic_name = _norm_text(selection.get("Name"))
        if value_name and semantic_name:
            if value_name in name_map or semantic_name in semantic_names:
                raise ValueError("PHO IDTO semantic column descriptor is ambiguous")
            name_map[value_name] = semantic_name
            semantic_names.add(semantic_name)
        dynamic_name = _norm_text(
            ((selection.get("DynamicFormat") or {}).get("Format"))
        )
        if dynamic_name and semantic_name:
            dynamic_semantic = semantic_name + ".__dynamic_format__"
            if dynamic_name in name_map or dynamic_semantic in semantic_names:
                raise ValueError("PHO IDTO dynamic-format descriptor is ambiguous")
            name_map[dynamic_name] = dynamic_semantic
            semantic_names.add(dynamic_semantic)

    semantic_rows = [
        {name_map.get(column, column): value for column, value in row.items()}
        for row in decoded
    ]
    return _norm_text(data.get("timestamp")), semantic_rows
