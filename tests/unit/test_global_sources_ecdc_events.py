"""Offline tests for the ECDC public health events adapter."""

from __future__ import annotations

import httpx

from src.services.global_sources import ecdc_events
from src.services.global_sources.ecdc_events import normalize_rows, parse_listing

EBOLA = "Ebola disease outbreak caused by Bundibugyo virus – Multi-country – 2026"
SHAMONDA = "First detection of Shamonda virus in an immunocompromised patient, France October 2026"


def _card(code: str, day: str, title: str, text: str = "") -> str:
    return f"""
<div class="ecdc-card d-flex">
  <div class="ecdc-badges"><span class="badge">Outbreaks</span><span class="badge">Active</span></div>
  <p class="ecdc-card__title h5"><a href="/en/public-health-events/{code}/{day}">{title}</a></p>
  <time datetime="{day}T12:00:00Z">date</time>
  <div class="ecdc-card__description"><div>{text}</div></div>
</div>
"""


PAGE = (
    _card("2026-eip-00026", "2026-10-30", EBOLA, "DRC has reported a total of 10 274 confirmed cases.")
    + _card("2026-eip-00026", "2026-10-28", EBOLA)
    + _card("2026-eip-00026", "2026-10-21", EBOLA)
    + _card("2026-evd-00036", "2026-10-30", SHAMONDA)
    + '<a href="/en/public-health-events">Public health events</a>'
)


def _by_id(events: list[dict], native_id: str) -> dict:
    return next(event for event in events if event["native_id"] == native_id)


def test_listing_gives_one_row_per_dated_version():
    rows = parse_listing(PAGE)
    assert len(rows) == 4
    assert rows[0]["code"] == "2026-eip-00026"
    assert rows[0]["published"].isoformat() == "2026-10-30"
    assert rows[0]["badges"] == ["Outbreaks", "Active"]


def test_only_the_newest_version_in_a_week_is_kept():
    events = normalize_rows(parse_listing(PAGE))
    assert sorted(event["native_id"] for event in events) == [
        "2026-eip-00026-2026-W43",
        "2026-eip-00026-2026-W44",
        "2026-evd-00036-2026-W44",
    ]
    week44 = _by_id(events, "2026-eip-00026-2026-W44")
    assert week44["published_date"] == "2026-10-30"
    assert week44["url"].endswith("/2026-eip-00026/2026-10-30")
    assert week44["document_ref"] == {"kind": "ecdc_event", "page_url": week44["url"]}
    assert week44["record_type"] == "public_health_event"


def test_a_total_of_cases_is_not_read_as_the_legacy_total_disease():
    ebola = _by_id(normalize_rows(parse_listing(PAGE)), "2026-eip-00026-2026-W44")
    assert [row["name_en"] for row in ebola["diseases"]] == ["Ebola"]


def test_country_comes_from_the_title_only():
    events = normalize_rows(parse_listing(PAGE))
    assert [row["code"] for row in _by_id(events, "2026-evd-00036-2026-W44")["countries"]] == ["FR"]
    assert _by_id(events, "2026-eip-00026-2026-W44")["countries"] == []


def test_crimean_congo_is_not_read_as_the_republic_of_the_congo():
    page = _card("2026-evd-00018", "2026-10-23", "Seasonal surveillance of Crimean-Congo haemorrhagic fever in the EU/EEA")
    event = normalize_rows(parse_listing(page))[0]
    assert [row["name_en"] for row in event["diseases"]] == ["Crimean-Congo hemorrhagic fever"]
    assert event["countries"] == []


def test_fetch_stops_once_a_page_reaches_the_window_start():
    class Client:
        def __init__(self) -> None:
            self.pages: list[str] = []

        def get(self, url, params=None, timeout=None):
            page = str((params or {}).get("page"))
            self.pages.append(page)
            day = {"0": "2026-10-30", "1": "2026-09-01", "2": "2026-08-01"}[page]
            return httpx.Response(
                200,
                text=_card("2026-evd-00016", day, "Seasonal surveillance of dengue"),
                request=httpx.Request("GET", url),
            )

    client = Client()
    events = ecdc_events.fetch_items(since="2026-10-01", client=client)
    assert client.pages == ["0", "1"]
    assert events
