"""Dated WHO regional bulletins and global emergency situation reports.

Only dedicated publication lists are read. Dates come from report cards, not
the date a crawler visited the page. WHO AFRO is distinct from Africa CDC.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event

AFRO_URL = "https://www.afro.who.int/health-topics/disease-outbreaks/outbreaks-and-other-emergencies-updates"
SEARO_URL = "https://www.who.int/southeastasia/outbreaks-and-emergencies/surveillance-and-alert/sear-epi-bulletins"
EURO_URL = "https://www.who.int/europe/emergencies/who-european-region-health-security-bulletin"
SITREPS_URL = "https://www.who.int/emergencies/situation-reports"
SITREP_TOPICS = (
    SITREPS_URL,
    "https://www.who.int/emergencies/situations/monkeypox-oubreak-2022",
    "https://www.who.int/emergencies/situations/cholera-upsurge",
    "https://www.who.int/emergencies/situations/ebola-outbreak---drc-2026",
)
MAX_PAGES = 60


def _event(source_id: str, title: str, url: str, published, download: str | None = None) -> dict[str, Any]:
    return build_event(
        source_id=source_id, native_id=hashlib.sha256(url.encode()).hexdigest()[:24],
        title_en=title, url=url, published=published,
        diseases=match_diseases(title), countries=match_countries(title),
        record_type="surveillance_update", keep_summary=False,
        document_ref={"kind": "pdf_url", "url": download} if download else {"kind": "pdf_page", "page_url": url},
    )


def parse_publications(html: str, *, source_id: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    events = {}
    # SEARO's book-style cards have dates outside the title anchor; newer cards
    # link to IRIS bitstreams directly. Full titles live in the cover alt text.
    for card in soup.select(".sf-publications-item"):
        anchor = card.select_one(".sf-publications-item__header a[href]")
        stamp = card.select_one(".sf-publications-item__date")
        if not anchor or not stamp:
            continue
        cover = card.select_one("img[alt]")
        title = str(cover.get("alt") or "") if cover else ""
        title = " ".join((title or anchor.get_text(" ", strip=True)).split())
        published = parse_date(stamp.get_text(" ", strip=True))
        url = urljoin(base_url, anchor["href"])
        download = card.select_one("a.download-url[href]")
        if published and title and "/publications/" in urlsplit(url).path:
            events[url] = _event(source_id, title, url, published, urljoin(base_url, download["href"]) if download else None)
    for anchor in soup.select("a.sf-meeting-report-list__item[href]"):
        stamp = anchor.select_one(".timestamp, time")
        heading = anchor.select_one(".full-title") or anchor.select_one("h4, h3")
        if not stamp or not heading:
            continue
        title = heading.get_text(" ", strip=True)
        published = parse_date(stamp.get("datetime") or stamp.get_text(" ", strip=True))
        url = urljoin(base_url, anchor["href"])
        if published and title and "/publications/" in urlsplit(url).path:
            events[url] = _event(source_id, title, url, published)
    return sorted(events.values(), key=lambda row: row["published_date"], reverse=True)


def fetch_publications(*, source_id: str, url: str, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    events = parse_publications(fetch(url, client=client).text, source_id=source_id, base_url=url)
    if not events:
        raise ValueError(f"{source_id}: publication list has no recognizable dated reports")
    return [row for row in events if not since or row["published_date"] >= since]


def parse_afro(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for card in soup.select(".pub-list-body"):
        anchor = card.select_one(".pub-list-title a[href]")
        stamp = card.select_one(".pub-card-date")
        if not anchor or not stamp:
            continue
        published = parse_date(stamp.get_text(" ", strip=True))
        title = anchor.get_text(" ", strip=True)
        if not published or "weekly bulletin" not in title.lower():
            continue
        download = card.select_one("a.pub-btn-download[href]")
        events.append(_event("who_afro", title, urljoin(AFRO_URL, anchor["href"]), published,
                             urljoin(AFRO_URL, download["href"]) if download else None))
    return events


def fetch_afro(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    events = {}
    for page in range(MAX_PAGES):
        response = fetch(AFRO_URL, params={"page": str(page)}, client=client)
        batch = parse_afro(response.text)
        if not batch:
            raise ValueError(f"who_afro: no recognizable bulletin cards on page {page}")
        if all(row["event_id"] in events for row in batch):
            raise ValueError("who_afro: archive repeated a page")
        events.update({row["event_id"]: row for row in batch})
        # Cards are occasionally out of order within a page; only stop when
        # the whole page predates the requested window.
        if since and max(row["published_date"] for row in batch) < since:
            break
        if not BeautifulSoup(response.text, "html.parser").select_one('a[aria-label="Next page"]'):
            break
    else:
        raise ValueError("who_afro: archive pagination limit reached")
    return sorted((row for row in events.values() if not since or row["published_date"] >= since),
                  key=lambda row: row["published_date"], reverse=True)


def fetch_sitreps(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    events = {}
    for url in SITREP_TOPICS:
        for row in fetch_publications(source_id="who_sitreps", url=url, since=since, client=client):
            if re.search(r"situation report|epidemiological update", row["title_en"], re.I):
                events[row["event_id"]] = row
    return sorted(events.values(), key=lambda row: row["published_date"], reverse=True)
