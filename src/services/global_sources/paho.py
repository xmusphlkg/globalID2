"""PAHO adapter (official IRIS institutional repository, DSpace 7 API).

``www.paho.org`` rejects non-browser clients with 403, including its RSS and
JSON:API endpoints.  PAHO's own IRIS repository (``iris.paho.org``) exposes the
same epidemiological alerts, updates and regional surveillance newsletters
through the documented DSpace search API, so this adapter reads from there.

The search index mixes journal articles and governing-body documents with
surveillance products, so only ``Newsletters`` whose title is an
epidemiological alert/update or a regional surveillance update are kept.
Rights metadata on these items is empty, so records stay metadata-only.
"""

from __future__ import annotations

import re
from typing import Any

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event

SOURCE_ID = "paho"
SEARCH_URL = "https://iris.paho.org/server/api/discover/search/objects"
HANDLE_URL = "https://iris.paho.org/handle/{handle}"
QUERY = "epidemiological"
PAGE_SIZE = 40
MAX_PAGES = 5
_KEEP_TITLE = re.compile(r"\b(epidemiological\s+(alert|update)|regional\s+update|situation\s+report)\b", re.I)


def _first(metadata: dict[str, Any], key: str) -> str:
    values = metadata.get(key) or []
    for value in values:
        text = str((value or {}).get("value") or "").strip()
        if text:
            return text
    return ""


def _all(metadata: dict[str, Any], key: str) -> list[str]:
    return [str((value or {}).get("value") or "").strip() for value in metadata.get(key) or [] if value]


def normalize_object(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map one DSpace search hit to a normalized event, or ``None``."""

    item = ((entry or {}).get("_embedded") or {}).get("indexableObject") or {}
    metadata = item.get("metadata") or {}
    handle = str(item.get("handle") or "").strip()
    title = " ".join(_first(metadata, "dc.title").replace("﻿", "").split())
    published = parse_date(_first(metadata, "dc.date.issued"))
    if not handle or not title or not published:
        return None
    if item.get("withdrawn") or "Newsletters" not in _all(metadata, "dc.type"):
        return None
    languages = {value.lower() for value in _all(metadata, "dc.language.iso")}
    if languages and "en" not in languages:
        return None
    if not _KEEP_TITLE.search(title):
        return None
    # Subjects are MeSH-style headings ("Influenza, Human"); they help the
    # disease match without ever being republished.
    subjects = " ; ".join(_all(metadata, "dc.subject"))
    diseases = match_diseases(f"{title} ; {subjects}")
    countries = match_countries(title)
    return build_event(
        source_id=SOURCE_ID,
        native_id=handle.replace("/", "-"),
        title_en=title[:300],
        url=_first(metadata, "dc.identifier.uri") or HANDLE_URL.format(handle=handle),
        published=published,
        diseases=diseases,
        countries=countries,
        record_type="surveillance_update" if "regional update" in title.lower() else "outbreak_notice",
        updated_at_source=(parse_date(item.get("lastModified")) or published).isoformat(),
        keep_summary=False,
    )


def normalize_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    objects = (((payload or {}).get("_embedded") or {}).get("searchResult") or {}).get("_embedded", {}).get("objects") or []
    events: list[dict[str, Any]] = []
    for entry in objects:
        event = normalize_object(entry)
        if event:
            events.append(event)
    return events


def fetch_items(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    """Page newest-first through IRIS until results fall before ``since``."""

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        params = {
            "query": QUERY,
            "dsoType": "ITEM",
            "sort": "dc.date.issued,DESC",
            "size": str(PAGE_SIZE),
            "page": str(page),
        }
        payload = fetch(SEARCH_URL, params=params, client=client).json()
        batch = normalize_payload(payload)
        for event in batch:
            if event["event_id"] not in seen:
                seen.add(event["event_id"])
                events.append(event)
        objects = (((payload or {}).get("_embedded") or {}).get("searchResult") or {}).get("_embedded", {}).get("objects") or []
        if len(objects) < PAGE_SIZE:
            break
        # Results are sorted by issue date, so once the last raw hit on a page
        # predates the window nothing further can qualify.
        last = objects[-1].get("_embedded", {}).get("indexableObject", {}).get("metadata", {})
        last_date = parse_date(_first(last, "dc.date.issued"))
        if since and last_date and last_date.isoformat() < since:
            break
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events
