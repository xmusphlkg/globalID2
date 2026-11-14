"""Decode AU runtime hints and ephemeral report configuration without network I/O."""
from __future__ import annotations

import base64
import gzip
import html as html_lib
import io
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .queries import _norm_text


def _extract_runtime_hints_from_post_data(post_data: Optional[str]) -> Dict[str, Any]:
    if not post_data:
        return {}
    try:
        payload = json.loads(post_data)
    except Exception:
        return {}

    queries = payload.get("queries", [])
    for query_entry in queries:
        commands = (
            query_entry.get("Query", {})
            .get("Commands", [])
        )
        for command in commands:
            semantic = command.get("SemanticQueryDataShapeCommand", {})
            query = semantic.get("Query", {})
            from_items = query.get("From", [])
            entities = {
                _norm_text(item.get("Name")): _norm_text(item.get("Entity"))
                for item in from_items
                if isinstance(item, dict)
            }
            location_alias = next(
                (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART LOCATION_DIM"),
                "",
            )
            fact_alias = next(
                (
                    name
                    for name, entity in entities.items()
                    if entity == "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT"
                ),
                "",
            )
            disease_alias = next(
                (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART DISEASE_DIM"),
                "",
            )
            case_alias = next(
                (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART CASE_DIM"),
                "",
            )
            if not location_alias or not fact_alias:
                continue

            state_seen = False
            measure_property = ""
            for select_item in query.get("Select", []):
                column = select_item.get("Column", {})
                column_expr = column.get("Expression", {}).get("SourceRef", {})
                if (
                    _norm_text(column_expr.get("Source")) == location_alias
                    and _norm_text(column.get("Property")) == "STATE"
                ):
                    state_seen = True
                measure = select_item.get("Measure", {})
                measure_expr = measure.get("Expression", {}).get("SourceRef", {})
                if _norm_text(measure_expr.get("Source")) == fact_alias:
                    measure_property = _norm_text(measure.get("Property"))

            if state_seen and measure_property:
                score = 0
                if measure_property == "Count_Notification":
                    score += 10
                if measure_property.lower() == "count_notification":
                    score += 5
                if not any("HierarchyLevel" in item for item in query.get("Select", [])):
                    score += 3
                return {
                    "location": {
                        "fact_alias": fact_alias,
                        "location_alias": location_alias,
                        "disease_alias": disease_alias or "d11",
                        "case_alias": case_alias or "d3",
                        "state_property": "STATE",
                        "measure_property": measure_property,
                        "_score": score,
                    }
                }
    return {}


def _extract_config_from_html(html_content: str, *, derive_api_url, decode_embed_config, logger) -> Optional[Dict[str, Any]]:
    access_token = ""
    api_url = ""
    report_id = ""
    dataset_id = ""
    model_id: Optional[int] = None

    match = re.search(r"var\s+pbiAppConfig\s*=\s*({.*?});", html_content, re.S)
    if match:
        try:
            app_cfg = json.loads(match.group(1))
            access_token = _norm_text(app_cfg.get("accessToken"))
            report_id = _norm_text(app_cfg.get("reportId"))
            api_url = derive_api_url(_norm_text(app_cfg.get("embedUrl")))
        except json.JSONDecodeError:
            logger.debug("[AU-NINDSS] Failed to decode pbiAppConfig JSON from HTML")

    if not access_token:
        match = re.search(r'embedconfig="([^"]+)"', html_content, re.I)
        if match:
            try:
                cfg = decode_embed_config(match.group(1))
                embed_token = cfg.get("EmbedToken", {}) if isinstance(cfg, dict) else {}
                access_token = _norm_text(embed_token.get("token"))
                report_id = report_id or _norm_text(cfg.get("Id") or cfg.get("id"))
                api_url = api_url or derive_api_url(
                    _norm_text(cfg.get("EmbedUrl"))
                )
            except Exception as exc:
                logger.debug(f"[AU-NINDSS] Failed to decode embedconfig | error={exc}")

    if not access_token:
        match = (
            re.search(r'"accessToken"\s*:\s*"([^"]+)"', html_content)
            or re.search(r"'accessToken'\s*:\s*'([^']+)'", html_content)
        )
        if match:
            access_token = _norm_text(match.group(1))

    if not access_token:
        return None

    # The static portal HTML currently exposes an embed token (often
    # starting with `H4s...`) that is not accepted by the QES public/query
    # endpoint. Only accept HTML extraction when it looks like the older
    # JWT-style access token used by the direct API path.
    if not access_token.startswith("ey"):
        logger.warning(
            f"[AU-NINDSS] Ignoring HTML-extracted token with unsupported format "
            f"prefix={access_token[:5]!r}"
        )
        return None

    return {
        "accessToken": access_token,
        "apiUrl": api_url,
        "reportId": report_id,
        "datasetId": dataset_id,
        "modelId": model_id,
    }


def _decode_embed_config(raw_embed_config: str) -> Dict[str, Any]:
    encoded = html_lib.unescape(raw_embed_config).strip()
    padding = (-len(encoded)) % 4
    if padding:
        encoded += "=" * padding
    try:
        decoded = base64.b64decode(encoded)
    except Exception:
        decoded = base64.urlsafe_b64decode(encoded)

    if decoded.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(decoded)) as gz:
            decoded = gz.read()

    return json.loads(decoded.decode("utf-8"))


def _derive_api_url_from_embed_url(embed_url: str, *, capacity_id: str, logger) -> str:
    if not embed_url:
        return ""
    try:
        parsed = urlparse(embed_url)
        config_param = parse_qs(parsed.query).get("config", [])
        if not config_param:
            return ""
        cfg_raw = unquote(config_param[0])
        cfg = json.loads(cfg_raw)
        cluster_url = _norm_text(cfg.get("clusterUrl"))
        if cluster_url:
            parsed_cluster = urlparse(cluster_url)
            if parsed_cluster.scheme and parsed_cluster.netloc:
                return (
                    f"{parsed_cluster.scheme}://{parsed_cluster.netloc}"
                    f"/webapi/capacities/{capacity_id}"
                    "/workloads/QES/QueryExecutionService/automatic/public/query"
                )
    except Exception as exc:
        logger.debug(f"[AU-NINDSS] Failed to derive apiUrl from embed URL | error={exc}")
    return ""
