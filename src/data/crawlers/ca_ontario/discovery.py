"""Discover Ontario's report visual and validate ephemeral embed credentials."""
from __future__ import annotations

import base64
import copy
import json
import re
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urlparse

from .common import _json_object, _norm_header, _norm_text
from .contracts import EmbedContext, MonthlyVisual
from .powerbi import _build_pbir_visual_query

DEFAULT_PAGE_DISPLAY_NAME = "Monthly Data Table"
DEFAULT_VISUAL_NAME = "8533f6960c0f199b51ae"
YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
TOKEN_RE = re.compile(r"accessToken\s*=\s*[\"']([^\"']+)[\"']")
REPORT_ID_RE = re.compile(r"embedReportId\s*=\s*[\"']([^\"']+)[\"']")


def _decode_routing_payload(token: str) -> dict[str, Any]:
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError("Power BI embed token has no routing payload")
    encoded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Power BI embed token routing payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("Power BI embed token routing payload is not an object")
    return payload


def extract_embed_context(html: str, *, expected_report_id: str) -> EmbedContext:
    """Extract an in-memory Power BI token and validate its routing host."""

    token_match = TOKEN_RE.search(html)
    if token_match is None:
        raise RuntimeError("PHO IDTO Power BI embed token was not found")
    token = token_match.group(1)
    route = _decode_routing_payload(token)

    cluster_url = _norm_text(route.get("clusterUrl")).rstrip("/")
    parsed = urlparse(cluster_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.casefold().endswith(".analysis.windows.net")
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError("PHO IDTO returned an unexpected Power BI cluster host")

    try:
        expires_at = int(route["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("PHO IDTO embed token has no valid expiry") from exc
    if expires_at <= int(time.time()):
        raise RuntimeError("PHO IDTO embed token is already expired")

    report_match = REPORT_ID_RE.search(html)
    report_id = report_match.group(1) if report_match else expected_report_id
    if report_id != expected_report_id:
        raise RuntimeError(
            "PHO IDTO embed report id changed; configuration review is required"
        )
    return EmbedContext(
        token=token,
        cluster_url=cluster_url,
        expires_at=expires_at,
        activity_id=str(uuid.uuid4()),
        report_id=report_id,
    )


def powerbi_headers(context: EmbedContext, *, post: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "ActivityId": context.activity_id,
        "RequestId": str(uuid.uuid4()),
        "Authorization": "EmbedToken " + context.token,
        "X-PowerBI-HostEnv": "Embed for Customers",
    }
    if post:
        headers["Content-Type"] = "application/json"
    return headers


def _visual_title(config: Mapping[str, Any]) -> str:
    try:
        value = config["singleVisual"]["vcObjects"]["title"][0]["properties"][
            "text"
        ]["expr"]["Literal"]["Value"]
    except (KeyError, IndexError, TypeError):
        return ""
    text = _norm_text(value)
    if len(text) >= 2 and text[0] == text[-1] == "'":
        text = text[1:-1]
    return text.replace("''", "'")


def _discover_pbir_monthly_visual(
    metadata: Mapping[str, Any],
    *,
    page_display_name: str,
    preferred_visual_name: str,
) -> tuple[dict[str, Any], str, str, str] | None:
    exploration = metadata.get("exploration")
    if not isinstance(exploration, Mapping):
        return None
    exploration_content = exploration.get("explorationContent")
    if not isinstance(exploration_content, Mapping):
        return None
    document_value = exploration_content.get("explorationDocument")
    if document_value is None:
        return None
    try:
        document = _json_object(document_value, label="PBIR exploration document")
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("PHO IDTO PBIR exploration document is invalid") from exc

    pages_container = document.get("pages")
    pages = pages_container.get("pages") if isinstance(pages_container, Mapping) else None
    if not isinstance(pages, list):
        raise RuntimeError("PHO IDTO PBIR page definitions are missing")
    page_matches = [
        item
        for item in pages
        if isinstance(item, Mapping)
        and isinstance(item.get("content"), Mapping)
        and _norm_text(item["content"].get("displayName")).casefold()
        == page_display_name.casefold()
    ]
    if len(page_matches) != 1:
        raise RuntimeError(
            f"PHO IDTO monthly page discovery returned {len(page_matches)} matches"
        )
    page_wrapper = page_matches[0]
    page = page_wrapper["content"]

    candidates: list[Mapping[str, Any]] = []
    for wrapper in page_wrapper.get("visualContainers") or []:
        if not isinstance(wrapper, Mapping):
            continue
        content = wrapper.get("content")
        definition = content.get("visual") if isinstance(content, Mapping) else None
        if (
            isinstance(definition, Mapping)
            and definition.get("visualType") == "tableEx"
            and isinstance(definition.get("query"), Mapping)
        ):
            candidates.append(content)
    preferred = [
        item
        for item in candidates
        if _norm_text(item.get("name")) == preferred_visual_name
    ]
    selected_pool = preferred or candidates
    if len(selected_pool) != 1:
        raise RuntimeError(
            f"PHO IDTO monthly table discovery returned {len(selected_pool)} matches"
        )
    visual = selected_pool[0]

    # The preliminary reporting year is a source contract, not the crawler's
    # wall-clock year. Recover it from the official current-date page filter and
    # fail closed if PHO changes that definition.
    reporting_years: set[int] = set()
    page_filter_config = page.get("filterConfig")
    page_filters = (
        page_filter_config.get("filters")
        if isinstance(page_filter_config, Mapping)
        else []
    )
    if not isinstance(page_filters, list):
        raise RuntimeError("PHO IDTO PBIR page filter configuration is invalid")
    for item in page_filters:
        if not isinstance(item, Mapping):
            continue
        field = item.get("field")
        column = field.get("Column") if isinstance(field, Mapping) else None
        if not isinstance(column, Mapping) or _norm_header(
            column.get("Property")
        ) != "date":
            continue
        filter_text = json.dumps(item.get("filter") or {}, ensure_ascii=False)
        reporting_years.update(int(value) for value in YEAR_RE.findall(filter_text))
    if len(reporting_years) != 1:
        raise RuntimeError(
            "PHO IDTO PBIR reporting-year filter is missing or ambiguous"
        )
    reporting_year = next(iter(reporting_years))

    report_wrapper = document.get("report")
    report = (
        report_wrapper.get("content")
        if isinstance(report_wrapper, Mapping)
        and isinstance(report_wrapper.get("content"), Mapping)
        else {}
    )
    try:
        query = _build_pbir_visual_query(
            report=report,
            page=page,
            visual=visual,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return (
        query,
        _norm_text(page.get("displayName")),
        _norm_text(visual.get("name")),
        f"{_norm_text(page.get('displayName'))} {reporting_year}",
    )


def discover_monthly_visual(
    metadata: Mapping[str, Any],
    *,
    page_display_name: str = DEFAULT_PAGE_DISPLAY_NAME,
    preferred_visual_name: str = DEFAULT_VISUAL_NAME,
) -> MonthlyVisual:
    """Discover the monthly table by semantic page/type, not array position."""

    models = metadata.get("models")
    if not isinstance(models, list) or len(models) != 1:
        raise RuntimeError("PHO IDTO expected exactly one Power BI model")
    model = models[0]
    if not isinstance(model, dict):
        raise RuntimeError("PHO IDTO Power BI model metadata is invalid")

    page_name = ""
    visual_name = ""
    title = ""
    query: dict[str, Any] | None = None
    sections = (metadata.get("exploration") or {}).get("sections")
    if isinstance(sections, list):
        page_matches = [
            page
            for page in sections
            if _norm_text(page.get("displayName")).casefold()
            == page_display_name.casefold()
        ]
        if len(page_matches) == 1:
            page = page_matches[0]
            candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
            for visual in page.get("visualContainers") or []:
                if not isinstance(visual, dict):
                    continue
                try:
                    config = _json_object(
                        visual.get("config") or {}, label="visual config"
                    )
                except (ValueError, json.JSONDecodeError):
                    continue
                single = config.get("singleVisual") or {}
                if single.get("visualType") != "tableEx" or not visual.get("query"):
                    continue
                candidates.append((visual, config, _visual_title(config)))

            preferred = [
                item
                for item in candidates
                if _norm_text(item[1].get("name")) == preferred_visual_name
            ]
            semantic = [
                item
                for item in candidates
                if "disease" in item[2].casefold()
                and "month" in item[2].casefold()
            ]
            selected_pool = preferred or semantic or candidates
            if len(selected_pool) == 1:
                visual, config, title = selected_pool[0]
                query = _json_object(
                    visual["query"], label="monthly visual query"
                )
                page_name = _norm_text(page.get("displayName"))
                visual_name = _norm_text(config.get("name"))

    if query is None:
        pbir = _discover_pbir_monthly_visual(
            metadata,
            page_display_name=page_display_name,
            preferred_visual_name=preferred_visual_name,
        )
        if pbir is None:
            if not isinstance(sections, list):
                raise RuntimeError("PHO IDTO Power BI page metadata is missing")
            page_count = len(
                [
                    page
                    for page in sections
                    if _norm_text(page.get("displayName")).casefold()
                    == page_display_name.casefold()
                ]
            )
            if page_count != 1:
                raise RuntimeError(
                    f"PHO IDTO monthly page discovery returned {page_count} matches"
                )
            raise RuntimeError("PHO IDTO monthly table discovery returned 0 matches")
        query, page_name, visual_name, title = pbir

    try:
        model_id = int(model["id"])
        dataset_id = _norm_text(model["dbName"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("PHO IDTO model id/dataset id is missing") from exc
    if not dataset_id:
        raise RuntimeError("PHO IDTO dataset id is blank")

    return MonthlyVisual(
        model_id=model_id,
        dataset_id=dataset_id,
        query=query,
        page_name=page_name,
        visual_name=visual_name,
        title=title,
        model_refresh_time=_norm_text(
            model.get("LastRefreshTime") or model.get("lastRefreshTime")
        ),
    )
