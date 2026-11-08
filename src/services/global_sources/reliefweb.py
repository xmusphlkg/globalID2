"""Authority-authored reports distributed through UN OCHA ReliefWeb.

The public HTML archive and attached original PDFs require no API credential.
Verify the author on every card: a search match alone is not provenance. PAHO's
IRIS collection is mostly respiratory newsletters, not its alerts archive.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event

LIST_URL = "https://reliefweb.int/updates"
MAX_PAGES = 60
SOURCES = {
    "paho_alerts": ('source.exact:"Pan American Health Organization"', "paho"),
    "who_emro": ('source:"World Health Organization" AND title:"Eastern Mediterranean"', "who"),
}
_REPORT = re.compile(
    r"epidemiological\s+(?:alert|update)|(?:regional\s+)?situation\s+report|"
    r"epidemiologic(?:al)?\s+bulletin|polio\s+bulletin|"
    r"(?:alerta|actualizaci[oó]n)\s+epidemiol[oó]gica|informe\s+de\s+situaci[oó]n",
    re.I,
)
_TITLE_DATE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]+\s+20\d{2})\s*[).\]]*$")


def parse_listing(html: str, source_id: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    organization = SOURCES[source_id][1]
    events = []
    for card in soup.select("article.rw-river-article--report"):
        authors = card.select('[class*="tag-value--source"] a[href]')
        if not any(urlsplit(a["href"]).path.rstrip("/") == f"/organization/{organization}" for a in authors):
            continue
        heading = card.select_one(".rw-river-article__title")
        anchor = heading.select_one("a[href]") if heading else None
        stamp = card.select_one('[class*="tag-value--published"] time[datetime]')
        if anchor is None or stamp is None:
            continue
        title = anchor.get_text(" ", strip=True)
        if not _REPORT.search(title):
            continue
        if source_id == "who_emro" and "eastern mediterranean" not in title.lower():
            continue
        # Repository upload dates can lag the report; prefer an explicit report
        # date at the end of its title, never a year/month guessed into a day.
        title_date = _TITLE_DATE.search(title)
        published = parse_date(title_date[1]) if title_date else parse_date(stamp["datetime"])
        native_id = str(card.get("data-id") or "")
        url = urljoin(LIST_URL, anchor["href"])
        if not published or not native_id.isdigit() or urlsplit(url).hostname != "reliefweb.int":
            continue
        event = build_event(
            source_id=source_id, native_id=native_id, title_en=title, url=url,
            published=published, diseases=match_diseases(title), countries=match_countries(title),
            record_type="outbreak_notice", keep_summary=False,
            document_ref={"kind": "reliefweb_report", "page_url": url, "organization": organization},
        )
        event["language"] = str(heading.get("lang") or "en")[:10]
        events.append(event)
    return events


def fetch_items(*, source_id: str, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    seen_pages: set[tuple[str, ...]] = set()
    for page in range(MAX_PAGES):
        response = fetch(LIST_URL, params={"search": SOURCES[source_id][0], "page": str(page)}, client=client)
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("article.rw-river-article--report")
        if not cards:
            raise ValueError(f"{source_id}: report archive has no recognizable cards (page {page})")
        ids = tuple(str(card.get("data-id")) for card in cards)
        if ids in seen_pages:
            raise ValueError(f"{source_id}: archive repeated a page before reaching the requested window")
        seen_pages.add(ids)
        for event in parse_listing(response.text, source_id):
            if not since or event["published_date"] >= since:
                events[event["event_id"]] = event
        # The archive is sorted by upload time, not original publication time.
        # A recently uploaded old report must not stop pagination prematurely.
        posted = [parse_date(t.get("datetime")) for card in cards for t in card.select('[class*="tag-value--posted"] time')]
        if since and len(posted) == len(cards) and all(day and day.isoformat() < since for day in posted):
            break
        if not soup.select_one('a[rel="next"], .pager__item--next a'):
            break
    else:
        raise ValueError(f"{source_id}: archive exceeded {MAX_PAGES} pages; requested window is incomplete")
    return sorted(events.values(), key=lambda row: row["published_date"], reverse=True)
