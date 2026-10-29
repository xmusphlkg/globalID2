"""WHO Disease Outbreak News adapter (official OData hub API).

The DON hub is the single highest-signal cross-border outbreak source: each item
is an agency-verified notice with a stable permalink.  Records keep metadata
plus a truncated, attributed extract; every entry links back to WHO.
"""

from __future__ import annotations

from typing import Any

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event

SOURCE_ID = "who_don"
API_URL = "https://www.who.int/api/hubs/diseaseoutbreaknews"
ITEM_TEMPLATE = "https://www.who.int/emergencies/disease-outbreak-news/item/{slug}"
SELECT_FIELDS = [
    "Id",
    "DonId",
    "UrlName",
    "ItemDefaultUrl",
    "Title",
    "OverrideTitle",
    "Summary",
    "PublicationDate",
    "PublicationDateAndTime",
    "LastModified",
    "FormattedDate",
]


def _item_url(item: dict[str, Any]) -> str | None:
    for key in ("UrlName", "DonId"):
        slug = str(item.get(key) or "").strip().strip("/")
        if slug:
            return ITEM_TEMPLATE.format(slug=slug)
    fallback = str(item.get("ItemDefaultUrl") or "").strip()
    if not fallback:
        return None
    if fallback.startswith("/") and "/disease-outbreak-news/item/" not in fallback:
        fallback = f"/emergencies/disease-outbreak-news/item/{fallback.lstrip('/')}"
    return f"https://www.who.int{fallback}" if fallback.startswith("/") else fallback


def normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map one hub payload item to a normalized event, or ``None`` if unusable."""

    native_id = str(item.get("DonId") or item.get("UrlName") or item.get("Id") or "").strip()
    title = str(item.get("OverrideTitle") or item.get("Title") or "").strip()
    url = _item_url(item)
    published = parse_date(item.get("PublicationDate") or item.get("PublicationDateAndTime"))
    if not native_id or not title or not url or not published:
        return None
    # Headline matches are only hints: every DON item is an outbreak report and
    # is read in full later, so an unmatched headline is not a reason to drop it.
    diseases = match_diseases(title)
    countries = match_countries(title)
    slug = str(item.get("UrlName") or item.get("DonId") or "").strip().strip("/")
    return build_event(
        source_id=SOURCE_ID,
        native_id=native_id,
        title_en=title,
        url=url,
        published=published,
        diseases=diseases,
        countries=countries,
        record_type="outbreak_notice",
        summary_en=str(item.get("Summary") or ""),
        updated_at_source=(parse_date(item.get("LastModified")) or published).isoformat(),
        keep_summary=True,
        document_ref={"kind": "who_don", "slug": slug},
    )


def normalize_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in payload.get("value", []):
        event = normalize_item(item)
        if event:
            events.append(event)
    return events


def fetch_items(
    *,
    limit: int = 200,
    since: str | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent DON items from the official hub API.

    ``since`` filters server-side on ``PublicationDateAndTime`` so a scheduled
    run only transfers genuinely new notices.
    """

    # The hub rejects anything above its documented 100-row cap, so honour a
    # larger ``limit`` by paging instead of asking for an oversized page.
    page_size = 100
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    skip = 0
    target = max(1, min(int(limit), 500))
    while len(events) < target:
        top = min(page_size, target - len(events))
        params: dict[str, Any] = {
            "$select": ",".join(SELECT_FIELDS),
            "$orderby": "PublicationDateAndTime desc",
            "$top": str(top),
        }
        if since:
            params["$filter"] = f"PublicationDateAndTime ge {since}"
        if skip:
            params["$skip"] = str(skip)
        response = fetch(API_URL, params=params, client=client)
        page = normalize_payload(response.json())
        new = [event for event in page if event["native_id"] not in seen_ids]
        for event in new:
            seen_ids.add(event["native_id"])
        events.extend(new)
        skip += page_size
        # A short (or empty) page means the source is exhausted; requesting more
        # would only repeat the same rows.
        if len(page) < top or not page:
            break
    return events[:target]
