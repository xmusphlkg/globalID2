"""Africa CDC adapter (official WordPress REST API).

Africa CDC publishes two kinds of records that matter here:

* ``news-item`` posts: press statements on active outbreaks (for example an
  Ebola response in Kenya).  These carry the highest signal and are dated.
* ``wpdmpro`` downloads: the Epidemic Intelligence Weekly Report.  The tag
  archive page prints no dates, so the REST API is used instead.

Redistribution terms are not confirmed, so both stay metadata-only: title,
date and link, never body text.
"""

from __future__ import annotations

import re
from typing import Any

from .catalogue import match_countries, match_diseases, parse_date
from .client import fetch
from .events import build_event, clean_html_fragment

SOURCE_ID = "africa_cdc"
API_BASE = "https://africacdc.org/wp-json/wp/v2"
NEWS_URL = f"{API_BASE}/news-item"
REPORTS_URL = f"{API_BASE}/wpdmpro"
REPORT_QUERY = "epidemic intelligence weekly report"
FIELDS = "id,date_gmt,modified_gmt,link,title"
PAGE_SIZE = 50


#: Africa CDC posts each French translation as its own record with no language
#: field, so French titles are recognized by their function words and dropped;
#: the English twin carries the same notice.
_FRENCH_MARKERS = re.compile(
    r"\b(le|la|les|des|du|aux|sur|pour|avec|face|et|une|réunit|exprime|renforcer|"
    r"épidémie|riposte|communiqué|santé)\b|\b[dl][’']",
    re.I,
)


def is_french(title: str) -> bool:
    return len(_FRENCH_MARKERS.findall(title)) >= 2


def _title(item: dict[str, Any]) -> str:
    rendered = (item.get("title") or {}).get("rendered") if isinstance(item.get("title"), dict) else item.get("title")
    return clean_html_fragment(str(rendered or ""))


def normalize_item(item: dict[str, Any], *, kind: str) -> dict[str, Any] | None:
    """Map one REST record to a normalized event, or ``None`` if unusable.

    ``kind`` is ``news`` or ``report``.  News items must name a catalogue
    disease, which drops governance and partnership announcements; weekly
    reports are kept even when their title names no disease because each one
    is a continental surveillance summary in its own right.
    """

    native_id = str(item.get("id") or "").strip()
    title = _title(item)
    url = str(item.get("link") or "").strip()
    published = parse_date(item.get("date_gmt"))
    if not native_id or not title or not url.startswith("http") or not published:
        return None
    if is_french(title):
        return None
    diseases = match_diseases(title)
    countries = match_countries(title)
    if kind == "news" and not diseases:
        return None
    modified = parse_date(item.get("modified_gmt"))
    return build_event(
        source_id=SOURCE_ID,
        native_id=f"{kind}-{native_id}",
        title_en=title,
        url=url,
        published=published,
        diseases=diseases,
        countries=countries,
        record_type="outbreak_statement" if kind == "news" else "weekly_threat_report",
        updated_at_source=(modified or published).isoformat(),
        keep_summary=False,
    )


def normalize_payload(rows: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        event = normalize_item(row, kind=kind)
        if event:
            events.append(event)
    return events


def fetch_items(*, since: str | None = None, client: Any | None = None) -> list[dict[str, Any]]:
    """Read recent outbreak statements and weekly reports.

    ``since`` (``YYYY-MM-DD``) is passed to WordPress as ``after`` so only the
    window is transferred.  A failing endpoint is re-raised only when the other
    one also produced nothing.
    """

    base: dict[str, Any] = {"per_page": str(PAGE_SIZE), "_fields": FIELDS, "orderby": "date", "order": "desc"}
    if since:
        base["after"] = f"{since}T00:00:00"
    events: list[dict[str, Any]] = []
    failures: list[BaseException] = []
    for url, kind, extra in (
        (NEWS_URL, "news", {}),
        (REPORTS_URL, "report", {"search": REPORT_QUERY}),
    ):
        try:
            response = fetch(url, params={**base, **extra}, client=client)
            events.extend(normalize_payload(response.json(), kind=kind))
        except Exception as exc:  # noqa: BLE001 - one endpoint offline keeps the other
            failures.append(exc)
    if not events and failures:
        raise failures[0]
    events.sort(key=lambda row: row["published_date"] or "", reverse=True)
    return events
