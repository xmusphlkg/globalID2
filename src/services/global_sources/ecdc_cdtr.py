"""ECDC Communicable Disease Threats Report adapter.

The RSS feed only lists the ten most recent issues.  Older issues come from the
public CDTR archive, which pages about fifteen reports at a time and dates each
card.  ECDC publishes CDTR as CC BY 4.0, so the issue-level summary that the
feed already exposes may be carried next to the metadata with attribution.
The PDF tables stay behind the ECDC link.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .catalogue import match_diseases, parse_date
from .client import fetch
from .events import build_event, clean_html_fragment

SOURCE_ID = "ecdc_cdtr"
RSS_URL = "https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed"
ARCHIVE_URL = "https://www.ecdc.europa.eu/en/publications-and-data/monitoring/weekly-threats-reports"
#: About fifteen cards per page; twenty pages covers well past three years.
MAX_ARCHIVE_PAGES = 20
_CDTR_HREF = re.compile(r"/publications-data/communicable-disease-threats-report-", re.I)
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


def _card_published(anchor: Any) -> date | None:
    """Date on the archive card, which sits beside the title rather than inside the link."""

    node = anchor
    for _ in range(6):
        node = getattr(node, "parent", None)
        if node is None:
            return None
        stamp = node.find("time")
        if stamp is None:
            continue
        published = parse_date(stamp.get("datetime") or stamp.get_text(" ", strip=True))
        if published is not None:
            return published
    return None


def normalize_archive(html: str) -> list[dict[str, Any]]:
    """Read one archive page.  A listing title names the week, not the diseases.

    The PDF is read later, so an issue is kept even when the card names no disease.
    """

    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if not _CDTR_HREF.search(href):
            continue
        url = urljoin("https://www.ecdc.europa.eu", href.split("?")[0])
        if url in seen:
            continue
        published = _card_published(anchor)
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or published is None:
            continue
        seen.add(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        events.append(
            build_event(
                source_id=SOURCE_ID,
                native_id=slug,
                title_en=title,
                url=url,
                published=published,
                diseases=_extract_diseases(title, ""),
                countries=[],
                record_type="weekly_threat_report",
                keep_summary=True,
                document_ref={"kind": "pdf_page", "page_url": url},
            )
        )
    return events


def _next_archive_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("a", rel=lambda value: value and "next" in (value if isinstance(value, (list, tuple)) else [value]))
    href = str(link.get("href") or "").strip() if link is not None else ""
    if not href:
        return None
    nxt = urljoin(current_url, href)
    return None if nxt == current_url else nxt


def fetch_items(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    """Recent issues from RSS, then the archive back to ``since`` (``YYYY-MM-DD``).

    RSS wins when both list the same issue, because the feed carries the disease summary.
    """

    events = normalize_items(fetch(RSS_URL, client=client).text)
    seen = {event["native_id"] for event in events}
    url: str | None = ARCHIVE_URL
    since_value = str(since)[:10] if since else None
    for _ in range(MAX_ARCHIVE_PAGES):
        if not url:
            break
        try:
            response = fetch(url, client=client)
        except Exception:  # noqa: BLE001 - keep the issues already read
            break
        batch = normalize_archive(response.text)
        if not batch:
            break
        for event in batch:
            if event["native_id"] in seen:
                continue
            seen.add(event["native_id"])
            events.append(event)
        oldest = min(event["published_date"] for event in batch)
        if since_value and oldest < since_value:
            break
        url = _next_archive_page(response.text, str(response.url))
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events
