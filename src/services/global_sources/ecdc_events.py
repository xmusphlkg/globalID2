"""ECDC public health events adapter.

ECDC keeps one page per tracked event (an outbreak, a seasonal surveillance
series, a risk assessment in production) and republishes it under a new dated URL
``/public-health-events/{event-code}/{update-date}`` every time the figures move.
There is no feed, so the adapter reads the public listing, which can be sorted by
publication date and paged 50 cards at a time.

An outbreak such as Ebola is updated almost daily and every version repeats the
whole page, so reading each one would only pay for the same text again.  The
adapter keeps the newest version of each event per ISO week, which matches the
weekly grain of the CDTR; a later version in the same week replaces the earlier
one under the same notice and sends it back through the pipeline.

ECDC publishes its web content under CC BY 4.0, so the card abstract may be
carried next to the metadata with attribution.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .catalogue import iso_week, match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event, clean_html_fragment

SOURCE_ID = "ecdc_events"
LIST_URL = "https://www.ecdc.europa.eu/en/public-health-events"
SITE_URL = "https://www.ecdc.europa.eu"
#: The listing refuses anything above 50 per page (100 returns an empty page).
PAGE_SIZE = 50
#: Fifty cards a page and a few dozen updates a week: twenty pages is well past a year.
MAX_PAGES = 20
#: ``D999`` is the legacy China "Total" aggregate; its name matches "a total of N cases"
#: in every outbreak abstract, so it must never be read as a disease here.
_NON_DISEASE_IDS = {"D999"}
_EVENT_HREF = re.compile(r"/public-health-events/([^/?#]+)/(\d{4}-\d{2}-\d{2})/?$", re.IGNORECASE)


def parse_listing(html: str) -> list[dict[str, Any]]:
    """Read one listing page into one raw row per dated event version."""

    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.ecdc-card"):
        anchor = card.select_one(".ecdc-card__title a[href]")
        if anchor is None:
            continue
        match = _EVENT_HREF.search(str(anchor.get("href") or "").split("?")[0])
        if not match:
            continue
        url = urljoin(SITE_URL, f"/en/public-health-events/{match.group(1)}/{match.group(2)}")
        title = " ".join(anchor.get_text(" ", strip=True).split())
        stamp = card.find("time")
        published = parse_date(stamp.get("datetime") if stamp is not None else None) or parse_date(match.group(2))
        if url in seen or not title or published is None:
            continue
        seen.add(url)
        description = card.select_one(".ecdc-card__description")
        rows.append(
            {
                "code": match.group(1).lower(),
                "url": url,
                "title": title,
                "published": published,
                "badges": [badge.get_text(" ", strip=True) for badge in card.select(".ecdc-badges .badge")],
                "description": clean_html_fragment(description.get_text(" ") if description is not None else ""),
            }
        )
    return rows


def latest_per_week(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest version of each event in each ISO week."""

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["code"], iso_week(row["published"])["iso_week_key"])
        current = best.get(key)
        if current is None or row["published"] > current["published"]:
            best[key] = row
    return list(best.values())


def _without_disease_names(title: str, diseases: list[dict[str, Any]]) -> str:
    """Title with the matched disease names removed, so "Crimean-Congo" is not read as Congo."""

    text = title
    for disease in diseases:
        for alias in sorted(disease.get("aliases") or [], key=len, reverse=True):
            text = re.sub(re.escape(alias), " ", text, flags=re.IGNORECASE)
    return text


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in latest_per_week(rows):
        week_key = iso_week(row["published"])["iso_week_key"]
        # A page names its disease in the title or the abstract; the country is
        # only taken from the title, since an abstract lists every affected place.
        diseases = [
            disease
            for disease in match_diseases(f"{row['title']}. {row['description']}")
            if disease["disease_id"] not in _NON_DISEASE_IDS
        ]
        events.append(
            build_event(
                source_id=SOURCE_ID,
                native_id=f"{row['code']}-{week_key}",
                title_en=row["title"],
                url=row["url"],
                published=row["published"],
                diseases=diseases,
                countries=match_countries(_without_disease_names(row["title"], diseases)),
                record_type="public_health_event",
                summary_en=row["description"],
                updated_at_source=row["published"].isoformat(),
                keep_summary=True,
                document_ref={"kind": "ecdc_event", "page_url": row["url"]},
            )
        )
    return events


def fetch_items(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    """Event versions from the newest listing page back to ``since`` (``YYYY-MM-DD``)."""

    since_value = str(since)[:10] if since else None
    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        params = {
            "items_per_page": str(PAGE_SIZE),
            "sort_bef_combine": "publication_date_DESC",
            "page": str(page),
        }
        try:
            response = fetch(LIST_URL, params=params, client=client)
        except Exception:
            if page == 0:
                raise
            break
        batch = parse_listing(response.text)
        if not batch:
            break
        rows.extend(batch)
        if since_value and min(row["published"] for row in batch).isoformat() < since_value:
            break
    events = normalize_rows(rows)
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events
