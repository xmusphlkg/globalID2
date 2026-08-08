"""Small client for anonymous Power BI ``Publish to web`` reports.

Power BI's public report pages do not expose a documented data API.  They do,
however, bootstrap the report model through public endpoints and execute
semantic queries against that model.  This module deliberately discovers the
resource key, cluster, model, data-set and report identifiers on every run;
none of those implementation identifiers are treated as a stable source
contract.

The query response uses Power BI's DSR v2 row compression.  ``decode_dsr_v2``
implements the parts used by public semantic-query responses: dictionary
encoded values, repeated-column bitmaps (``R``), and null bitmaps (``Ø``).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlparse, urlunparse

import requests


_RESOURCE_DESCRIPTOR_RE = re.compile(
    r"resourceDescriptor\s*=\s*JSON\.parse\('\{\\\"k\\\":\\\""
    r"(?P<key>[0-9a-f-]{36})\\\"",
    re.IGNORECASE,
)
_RESOURCE_KEY_FALLBACK_RE = re.compile(
    r"\\?[\"']k\\?[\"']\s*:\s*\\?[\"'](?P<key>[0-9a-f-]{36})",
    re.IGNORECASE,
)
_CLUSTER_RE = re.compile(
    r"(?:var\s+)?resolvedClusterUri\s*=\s*['\"](?P<uri>https://[^'\"]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PowerBIReportContext:
    """Identifiers and schema discovered from one public report page."""

    view_url: str
    resource_key: str
    api_base_url: str
    model_id: int
    dataset_id: str
    report_id: str
    last_refresh: Optional[str]
    schema_fingerprint: str
    models_payload: Mapping[str, Any] = field(repr=False)
    schema_payload: Mapping[str, Any] = field(repr=False)
    landing_html: str = field(repr=False)


@dataclass(frozen=True)
class PowerBIQueryResult:
    """Decoded rows plus the exact public-query request and response."""

    rows: List[Dict[str, Any]]
    request_payload: Mapping[str, Any]
    response_payload: Mapping[str, Any]


def _api_base_from_cluster(cluster_uri: str) -> str:
    """Apply the same cluster-to-APIM transform used by Power BI's web app."""

    parsed = urlparse(cluster_uri)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid Power BI cluster URI: {cluster_uri!r}")
    labels = parsed.hostname.split(".")
    first = labels[0].replace("-redirect", "").replace("global-", "")
    if not first.endswith("-api"):
        first += "-api"
    hostname = ".".join([first, *labels[1:]])
    return urlunparse((parsed.scheme, hostname, "", "", "", "")).rstrip("/")


def _schema_signature(schema_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a stable, useful schema projection without volatile GUIDs."""

    signatures: List[Dict[str, Any]] = []
    schemas = schema_payload.get("schemas")
    if not isinstance(schemas, list):
        return {"entities": signatures}
    for schema_wrapper in schemas:
        if not isinstance(schema_wrapper, Mapping):
            continue
        schema = schema_wrapper.get("schema")
        if not isinstance(schema, Mapping):
            continue
        entities = schema.get("Entities")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            properties = []
            for prop in entity.get("Properties") or []:
                if not isinstance(prop, Mapping):
                    continue
                properties.append(
                    {
                        "name": str(prop.get("Name") or ""),
                        "data_type": prop.get("DataType"),
                        "kind": "measure" if "Measure" in prop else "column",
                    }
                )
            signatures.append(
                {
                    "name": str(entity.get("Name") or ""),
                    "properties": sorted(properties, key=lambda item: item["name"]),
                }
            )
    return {"entities": sorted(signatures, key=lambda item: item["name"])}


def schema_fingerprint(schema_payload: Mapping[str, Any]) -> str:
    """Hash entity/property semantics so source drift can fail visibly."""

    canonical = json.dumps(
        _schema_signature(schema_payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _dictionary_value(dictionary: object, value: object) -> object:
    if not isinstance(value, int):
        return value
    if isinstance(dictionary, list):
        if 0 <= value < len(dictionary):
            return dictionary[value]
        raise ValueError(f"Power BI dictionary index out of range: {value}")
    if isinstance(dictionary, Mapping):
        if value in dictionary:
            return dictionary[value]
        if str(value) in dictionary:
            return dictionary[str(value)]
        raise ValueError(f"Power BI dictionary index not found: {value}")
    return value


def _rowsets(ds: Mapping[str, Any]) -> Iterable[List[Mapping[str, Any]]]:
    """Yield DSR detail rowsets without interpreting hierarchy metadata."""

    phase_groups = ds.get("PH")
    if isinstance(phase_groups, list):
        for phase in phase_groups:
            if not isinstance(phase, Mapping):
                continue
            for name, rows in phase.items():
                if str(name).startswith("DM") and isinstance(rows, list):
                    yield [row for row in rows if isinstance(row, Mapping)]

    for name, rows in ds.items():
        if str(name).startswith("DM") and isinstance(rows, list):
            yield [row for row in rows if isinstance(row, Mapping)]


def decode_dsr_v2(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Decode flat detail rows from a Power BI DSR v2 query response.

    The function is intentionally strict.  A malformed compressed row or a
    changed response shape raises instead of silently shifting values into the
    wrong columns.
    """

    decoded: List[Dict[str, Any]] = []
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Power BI query response is missing results")

    for result in results:
        if not isinstance(result, Mapping):
            continue
        result_body = result.get("result")
        if not isinstance(result_body, Mapping):
            continue
        if result_body.get("error"):
            raise RuntimeError(f"Power BI semantic query failed: {result_body['error']}")
        data = result_body.get("data")
        if not isinstance(data, Mapping):
            continue

        descriptor_names: Dict[str, str] = {}
        descriptor = data.get("descriptor")
        if isinstance(descriptor, Mapping):
            for selected in descriptor.get("Select") or []:
                if not isinstance(selected, Mapping):
                    continue
                value_name = str(selected.get("Value") or "")
                if value_name:
                    descriptor_names[value_name] = str(
                        selected.get("Name") or value_name
                    )

        dsr = data.get("dsr")
        datasets = dsr.get("DS") if isinstance(dsr, Mapping) else None
        if not isinstance(datasets, list):
            raise ValueError("Power BI query response is missing DSR datasets")

        for dataset in datasets:
            if not isinstance(dataset, Mapping):
                continue
            dictionaries = dataset.get("ValueDicts")
            dictionaries = dictionaries if isinstance(dictionaries, Mapping) else {}
            for compact_rows in _rowsets(dataset):
                schema: Optional[List[Mapping[str, Any]]] = None
                previous: List[Any] = []
                for compact in compact_rows:
                    row_schema = compact.get("S")
                    if isinstance(row_schema, list):
                        schema = [item for item in row_schema if isinstance(item, Mapping)]
                    if not schema:
                        raise ValueError("Power BI DSR rowset has no column schema")

                    repeat_mask = int(compact.get("R") or 0)
                    null_mask = int(compact.get("Ø") or 0)
                    compact_values = compact.get("C")
                    if compact_values is None:
                        compact_values = []
                    if not isinstance(compact_values, list):
                        raise ValueError("Power BI DSR compact values are not a list")

                    value_index = 0
                    values: List[Any] = []
                    for column_index, column in enumerate(schema):
                        bit = 1 << column_index
                        if repeat_mask & bit:
                            if column_index >= len(previous):
                                raise ValueError(
                                    "Power BI DSR row repeats a value before a prior row"
                                )
                            value = previous[column_index]
                        elif null_mask & bit:
                            value = None
                        else:
                            if value_index >= len(compact_values):
                                raise ValueError(
                                    "Power BI DSR row has fewer values than its bitmaps require"
                                )
                            value = compact_values[value_index]
                            value_index += 1

                        dictionary_name = column.get("DN")
                        if dictionary_name and value is not None:
                            value = _dictionary_value(
                                dictionaries.get(str(dictionary_name)), value
                            )
                        values.append(value)

                    if value_index != len(compact_values):
                        raise ValueError(
                            "Power BI DSR row has unconsumed compact values; schema drift suspected"
                        )
                    previous = values
                    decoded.append(
                        {
                            descriptor_names.get(
                                str(column.get("N") or ""),
                                str(column.get("N") or ""),
                            ): value
                            for column, value in zip(schema, values)
                        }
                    )

    return decoded


class PublicPowerBIClient:
    """Discover and query one or more anonymous Power BI reports."""

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        timeout: int = 90,
        user_agent: str = "Mozilla/5.0 (compatible; GlobalID/2.0; Public-PowerBI)",
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.setdefault("User-Agent", user_agent)

    @staticmethod
    def _request_headers(resource_key: str, *, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "ActivityId": str(uuid.uuid4()),
            "RequestId": str(uuid.uuid4()),
            "X-PowerBI-ResourceKey": resource_key,
            "Origin": "https://app.powerbi.com",
            "Referer": "https://app.powerbi.com/",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def discover(self, view_url: str) -> PowerBIReportContext:
        """Resolve a public view URL into its current model and schema."""

        landing = self.session.get(view_url, timeout=self.timeout)
        landing.raise_for_status()
        html = landing.text

        resource_match = _RESOURCE_DESCRIPTOR_RE.search(html)
        if resource_match is None:
            resource_match = _RESOURCE_KEY_FALLBACK_RE.search(html)
        if resource_match is None:
            raise RuntimeError("Unable to discover Power BI resource key from report page")
        resource_key = resource_match.group("key").lower()

        cluster_match = _CLUSTER_RE.search(html)
        if cluster_match is None:
            raise RuntimeError("Unable to discover Power BI cluster from report page")
        api_base_url = _api_base_from_cluster(cluster_match.group("uri"))

        headers = self._request_headers(resource_key)
        models_url = (
            f"{api_base_url}/public/reports/{resource_key}/modelsAndExploration"
            "?preferReadOnlySession=true"
        )
        models_response = self.session.get(
            models_url, headers=headers, timeout=self.timeout
        )
        models_response.raise_for_status()
        models_payload = models_response.json()

        schema_url = f"{api_base_url}/public/reports/{resource_key}/conceptualschema"
        schema_response = self.session.get(
            schema_url,
            headers=self._request_headers(resource_key),
            timeout=self.timeout,
        )
        schema_response.raise_for_status()
        schema_payload = schema_response.json()

        exploration = models_payload.get("exploration")
        report = exploration.get("report") if isinstance(exploration, Mapping) else None
        if not isinstance(report, Mapping):
            raise RuntimeError("Power BI bootstrap did not expose a report")

        models = models_payload.get("models") if isinstance(models_payload, Mapping) else None
        if not isinstance(models, list) or not models:
            raise RuntimeError("Power BI bootstrap did not expose a model")
        report_model_id = report.get("modelId")
        model = next(
            (
                candidate
                for candidate in models
                if isinstance(candidate, Mapping)
                and (
                    report_model_id is None
                    or str(candidate.get("id")) == str(report_model_id)
                )
            ),
            None,
        )
        if not isinstance(model, Mapping):
            raise RuntimeError("Power BI report model was not present in bootstrap models")

        try:
            model_id = int(model["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Power BI bootstrap contains an invalid model id") from exc
        dataset_id = str(model.get("dbName") or "").strip()
        report_id = str(report.get("objectId") or "").strip()
        if not dataset_id or not report_id:
            raise RuntimeError("Power BI bootstrap is missing dataset/report identifiers")

        return PowerBIReportContext(
            view_url=view_url,
            resource_key=resource_key,
            api_base_url=api_base_url,
            model_id=model_id,
            dataset_id=dataset_id,
            report_id=report_id,
            last_refresh=str(model.get("LastRefreshTime") or "").strip() or None,
            schema_fingerprint=schema_fingerprint(schema_payload),
            models_payload=models_payload,
            schema_payload=schema_payload,
            landing_html=html,
        )

    @staticmethod
    def _entity_properties(
        context: PowerBIReportContext, entity_name: str
    ) -> set[str]:
        schemas = context.schema_payload.get("schemas")
        for wrapper in schemas if isinstance(schemas, list) else []:
            if not isinstance(wrapper, Mapping):
                continue
            wrapper_model_id = wrapper.get("modelId")
            if (
                wrapper_model_id is not None
                and str(wrapper_model_id) != str(context.model_id)
            ):
                continue
            schema = wrapper.get("schema")
            entities = schema.get("Entities") if isinstance(schema, Mapping) else None
            for entity in entities if isinstance(entities, list) else []:
                if not isinstance(entity, Mapping) or entity.get("Name") != entity_name:
                    continue
                return {
                    str(prop.get("Name"))
                    for prop in entity.get("Properties") or []
                    if isinstance(prop, Mapping) and prop.get("Name")
                }
        raise RuntimeError(f"Power BI entity not found in current schema: {entity_name}")

    def query_entity_sum(
        self,
        context: PowerBIReportContext,
        *,
        entity: str,
        group_columns: Sequence[str],
        value_column: str,
        row_limit: int = 30_000,
    ) -> PowerBIQueryResult:
        """Group an entity by columns and sum one numeric fact column."""

        properties = self._entity_properties(context, entity)
        expected = {*group_columns, value_column}
        missing = sorted(expected - properties)
        if missing:
            raise RuntimeError(
                f"Power BI schema drift for {entity}: missing {', '.join(missing)}"
            )

        alias = "f"
        selections: List[Dict[str, Any]] = []
        for column in group_columns:
            selections.append(
                {
                    "Column": {
                        "Expression": {"SourceRef": {"Source": alias}},
                        "Property": column,
                    },
                    "Name": f"{entity}.{column}",
                }
            )
        selections.append(
            {
                "Aggregation": {
                    "Expression": {
                        "Column": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": value_column,
                        }
                    },
                    "Function": 0,
                },
                "Name": f"Sum({entity}.{value_column})",
            }
        )

        semantic_query = {
            "Version": 2,
            "From": [{"Name": alias, "Entity": entity, "Type": 0}],
            "Select": selections,
        }
        command = {
            "SemanticQueryDataShapeCommand": {
                "Query": semantic_query,
                "Binding": {
                    "Primary": {
                        "Groupings": [
                            {"Projections": list(range(len(selections)))}
                        ]
                    },
                    "DataReduction": {
                        "DataVolume": 6,
                        "Primary": {"Window": {"Count": int(row_limit)}},
                    },
                    "Version": 1,
                },
                "ExecutionMetricsKind": 1,
            }
        }
        request_payload = {
            "version": "1.0.0",
            "queries": [
                {
                    "Query": {"Commands": [command]},
                    "QueryId": "",
                    "ApplicationContext": {
                        "DatasetId": context.dataset_id,
                        "Sources": [
                            {
                                "ReportId": context.report_id,
                                "VisualId": "globalid-public-extract",
                            }
                        ],
                    },
                }
            ],
            "cancelQueries": [],
            "modelId": context.model_id,
        }

        query_url = f"{context.api_base_url}/public/reports/querydata?synchronous=true"
        response = self.session.post(
            query_url,
            headers=self._request_headers(context.resource_key, json_body=True),
            json=request_payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        response_payload = response.json()
        if not isinstance(response_payload, Mapping):
            raise RuntimeError("Power BI query returned a non-object response")
        if response_payload.get("error"):
            raise RuntimeError(f"Power BI query failed: {response_payload['error']}")
        rows = decode_dsr_v2(response_payload)
        if len(rows) >= int(row_limit):
            raise RuntimeError(
                f"Power BI query reached its {row_limit}-row safety window; "
                "refuse a potentially truncated extract"
            )
        return PowerBIQueryResult(
            rows=rows,
            request_payload=request_payload,
            response_payload=response_payload,
        )


__all__ = [
    "PowerBIQueryResult",
    "PowerBIReportContext",
    "PublicPowerBIClient",
    "decode_dsr_v2",
    "schema_fingerprint",
]
