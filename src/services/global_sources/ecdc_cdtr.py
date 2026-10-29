"""ECDC Communicable Disease Threats Report adapter (official RSS feed).

ECDC publishes CDTR as CC BY 4.0, so the issue-level summary that the feed
already exposes may be carried next to the metadata with attribution.  The PDF
tables stay behind the ECDC link: Phase 1 does not re-publish them.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

from .catalogue import match_diseases, parse_date
from .client import fetch
from .events import build_event, clean_html_fragment

SOURCE_ID = "ecdc_cdtr"
RSS_URL = "https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed"
# The issue summary names diseases ("updates on ... Ebola, mpox, dengue") but
# routinely refers to EU/EEA as a whole, which is not a country.  Phase 1
# therefore attaches CDTR issues to diseases only.
_TOPIC_SPLIT = re.compile(r",| and ")


def _text(node: ElementTree.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return str(node.text).strip()


def normalize_items(xml_text: str) -> list[dict[str, Any]]:
    """Parse the CDTR RSS document into normalized weekly issue records."""

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    events: list[dict[str, Any]] = []
    for node in root.iter("item"):
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        guid = _text(node.find("guid")) or link
        published = parse_date(_text(node.find("pubDate")))
        if not title or not link or not published:
            continue
        issues = _extract_diseases(title, clean_html_fragment(_text(node.find("description"))))
        if not issues:
            continue
        events.append(
            build_event(
                source_id=SOURCE_ID,
                native_id=guid.rstrip("/").rsplit("/", 1)[-1] or str(published),
                title_en=title,
                url=link,
                published=published,
                diseases=issues,
                countries=[],
                record_type="weekly_threat_report",
                summary_en=clean_html_fragment(_text(node.find("description"))),
                keep_summary=True,
                document_ref={"kind": "pdf_page", "page_url": link},
            )
        )
    return events


def _extract_diseases(title: str, description: str) -> list[dict[str, Any]]:
    diseases: dict[str, dict[str, Any]] = {}
    for row in match_diseases(f"{title}. {description}"):
        diseases.setdefault(row["disease_id"], row)
    if diseases:
        return list(diseases.values())
    # Fall back to the explicit "updates on a, b and c" clause in the summary.
    match = re.search(r"includes updates on (.+?)\.$", description)
    if not match:
        return []
    for phrase in _TOPIC_SPLIT.split(match.group(1)):
        for row in match_diseases(phrase.strip()):
            diseases.setdefault(row["disease_id"], row)
    return list(diseases.values())


def fetch_items(*, client: Any | None = None) -> list[dict[str, Any]]:
    response = fetch(RSS_URL, client=client)
    return normalize_items(response.text)
