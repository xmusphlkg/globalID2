"""Metadata-only adapters for WHO periodicals (WER) and WPRO surveillance.

Both sources are authoritative WHO publications, but their article-level
redistribution terms are not confirmed for this project yet.  Until that is
settled these adapters record title, week and link only, and never carry body
text, so the site can surface "WHO published WER week X" without re-publishing
any of its content.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event

WRO_SOURCE_IDS = ("who_wer", "who_wpro")

WER_SOURCE_ID = "who_wer"
WER_URL = "https://www.who.int/publications/journals/weekly-epidemiological-record"
#: The WER landing page only lists issue numbers ("WER 101, No. 41") and no
#: publication date, so the adapter reads the dated article records from the
#: same official publications hub the Disease Outbreak News adapter uses.
WER_API_URL = "https://www.who.int/api/hubs/publications"
WER_ITEM_TEMPLATE = "https://www.who.int/publications/i/item/{slug}"
WER_ITEM_MARKER = "who-wer"
WER_SELECT_FIELDS = ["Id", "Title", "PublicationDate", "ItemDefaultUrl", "UrlName"]
#: The hub rejects anything above its documented 100-row cap, so a larger
#: ``limit`` is honoured by paging.
WER_PAGE_SIZE = 100

WPRO_SOURCE_ID = "who_wpro"
WPRO_URL = "https://www.who.int/westernpacific/emergencies/surveillance"
#: The surveillance landing page is a navigation hub whose cards carry no
#: publication date, so the adapter reads the dated topic feeds behind it
#: instead (respiratory viruses, avian influenza, Pacific islands, dengue).
WPRO_TOPIC_URLS: tuple[str, ...] = (
    "https://www.who.int/westernpacific/emergencies/surveillance/respiratory-viruses",
    "https://www.who.int/westernpacific/emergencies/surveillance/avian-influenza",
    "https://www.who.int/westernpacific/emergencies/surveillance/pacific-islands-surveillance",
    "https://www.who.int/westernpacific/emergencies/surveillance/dengue",
)
_WPRO_ITEM = re.compile(r"/publications/(?:m|i)/item/", re.I)
_WPRO_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b", re.I)
_WPRO_KEEP = re.compile(
    r"\b(surveillance|bulletin|update|situation|report|weekly|epidemiological)\b",
    re.I,
)


def _wer_url(item: dict[str, Any]) -> str | None:
    slug = str(item.get("ItemDefaultUrl") or item.get("UrlName") or "").strip().strip("/")
    return WER_ITEM_TEMPLATE.format(slug=slug) if slug else None


def normalize_wer_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map one publications-hub record to a WER event, or ``None`` if unusable."""

    native_id = str(item.get("Id") or item.get("UrlName") or "").strip()
    title = " ".join(str(item.get("Title") or "").split())
    url = _wer_url(item)
    published = parse_date(item.get("PublicationDate"))
    if not native_id or not title or not url or not published:
        return None
    diseases = match_diseases(title)
    countries = match_countries(title)
    # WER carries vaccination policy and elimination reports too; only keep the
    # articles tied to a disease or a jurisdiction.
    if not diseases and not countries:
        return None
    return build_event(
        source_id=WER_SOURCE_ID,
        native_id=native_id,
        title_en=title,
        url=url,
        published=published,
        diseases=diseases,
        countries=countries,
        record_type="surveillance_update",
        keep_summary=False,
        document_ref={"kind": "pdf_page", "page_url": url},
    )


def normalize_wer_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in payload.get("value", []):
        event = normalize_wer_item(item)
        if event:
            events.append(event)
    return events


def _wer_since(since: str | None) -> str | None:
    """Turn a plain ``YYYY-MM-DD`` into a timestamp the hub filter accepts."""

    value = str(since or "").strip()
    if not value:
        return None
    return value if "T" in value else f"{value}T00:00:00Z"


def fetch_wer(
    *,
    limit: int = 100,
    since: str | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Fetch dated Weekly Epidemiological Record articles from the hub API.

    ``since`` filters server-side on ``PublicationDate`` so a scheduled run only
    transfers genuinely new issues.
    """

    target = max(1, min(int(limit), 500))
    since_value = _wer_since(since)
    conditions = [f"contains(ItemDefaultUrl,'{WER_ITEM_MARKER}')"]
    if since_value:
        conditions.append(f"PublicationDate gt {since_value}")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    skip = 0
    while len(events) < target:
        top = min(WER_PAGE_SIZE, target - len(events))
        params: dict[str, Any] = {
            "$select": ",".join(WER_SELECT_FIELDS),
            "$orderby": "PublicationDate desc",
            "$filter": " and ".join(conditions),
            "$top": str(top),
        }
        if skip:
            params["$skip"] = str(skip)
        response = fetch(WER_API_URL, params=params, client=client)
        page = normalize_wer_payload(response.json())
        new = [event for event in page if event["native_id"] not in seen]
        for event in new:
            seen.add(event["native_id"])
        events.extend(new)
        skip += WER_PAGE_SIZE
        # A short (or empty) page means the source is exhausted.
        if len(page) < top or not page:
            break
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events[:target]


def _wpro_date(*candidates: str | None) -> date | None:
    """Read ``22 October 2026`` from whichever markup fragment carries it.

    The list markup prints the date in a ``.timestamp`` span, repeats it in the
    ``aria-label`` and finally encodes it in the slug, so each candidate is
    tried in order; month names there are lower-cased, hence the manual
    ``%d %b %Y`` parse instead of :func:`~.catalogue.parse_date`.
    """

    for candidate in candidates:
        if not candidate:
            continue
        match = _WPRO_DATE.search(" ".join(str(candidate).split()))
        if not match:
            continue
        # ``parse_date`` needs the month in title case, which is what the
        # lower-cased site markup lacks.
        candidate = f"{match.group(1)} {match.group(2)[:3].capitalize()} {match.group(3)}"
        parsed = parse_date(candidate)
        if parsed is not None:
            return parsed
    return None


def _wpro_title(anchor: Any) -> str:
    label = " ".join(str(anchor.get("aria-label") or "").split())
    if not label:
        label = " ".join(anchor.get_text(" ", strip=True).split())
        # The card repeats its own headline; keep one copy when that happened.
        half = len(label) // 2
        if half > 20 and label[:half].strip() == label[half:].strip():
            label = label[:half]
    return " ".join(_WPRO_DATE.sub("", label, count=1).split()).strip(" -–—:,")


def _normalize_wpro(soup: BeautifulSoup) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if not _WPRO_ITEM.search(href):
            continue
        url = urljoin("https://www.who.int", href)
        if url in seen:
            continue
        stamp = anchor.select_one(".timestamp, time")
        published = _wpro_date(
            stamp.get_text(" ", strip=True) if stamp is not None else None,
            anchor.get("aria-label"),
            " ".join(anchor.get_text(" ", strip=True).split()),
            href.replace("-", " "),
        )
        if published is None:
            # An item without a date cannot join a week-indexed timeline.
            continue
        title = _wpro_title(anchor)
        if len(title) < 12:
            continue
        diseases = match_diseases(title)
        countries = match_countries(title)
        if not diseases and not _WPRO_KEEP.search(title):
            continue
        seen.add(url)
        events.append(
            build_event(
                source_id=WPRO_SOURCE_ID,
                native_id=url.rstrip("/").rsplit("/", 1)[-1],
                title_en=title[:300],
                url=url,
                published=published,
                diseases=diseases,
                countries=countries,
                record_type="surveillance_update",
                keep_summary=False,
                document_ref={"kind": "pdf_page", "page_url": url},
            )
        )
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events[:20]


def normalize_wpro(html: str) -> list[dict[str, Any]]:
    return _normalize_wpro(BeautifulSoup(html, "html.parser"))


def fetch_wpro(*, client: Any | None = None) -> list[dict[str, Any]]:
    """Read every WPRO surveillance topic feed and merge their dated items.

    One unreachable topic must not sink the whole source, so failures are
    remembered and only re-raised when *no* topic answered.
    """

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    failures: list[BaseException] = []
    for url in WPRO_TOPIC_URLS:
        try:
            response = fetch(url, client=client)
        except Exception as exc:  # noqa: BLE001 - one topic offline keeps the rest
            failures.append(exc)
            continue
        for event in _normalize_wpro(BeautifulSoup(response.text, "html.parser")):
            if event["event_id"] in seen:
                continue
            seen.add(event["event_id"])
            events.append(event)
    if not events and failures:
        raise failures[0]
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events[:40]
