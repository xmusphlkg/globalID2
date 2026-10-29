"""Offline tests for the WHO periodical adapters (WER and WPRO)."""

from __future__ import annotations

from src.services.global_sources.who_periodicals import (
    WER_API_URL,
    WPRO_TOPIC_URLS,
    normalize_wer_item,
    normalize_wer_payload,
    normalize_wpro,
)

WPRO_ITEM = (
    '<a aria-label="22 October 2026 Respiratory Viruses Surveillance Bulletin: '
    'Epidemiological Week 41, 2026" class="sf-meeting-report-list__item" '
    'href="https://www.who.int/westernpacific/publications/m/item/'
    'respiratory-viruses-surveillance-bulletin--epidemiological-week-41-2026">'
    '<div><span class="timestamp">22 October 2026</span>'
    "Respiratory Viruses Surveillance Bulletin: Epidemiological Week 41, 2026"
    "Respiratory Viruses Surveillance Bulletin: Epidemiological Week 41, 2026</div></a>"
)

AVIAN_ITEM = (
    '<a aria-label="16 October 2026 Avian Influenza Weekly Update # 1066: 16 October 2026" '
    'href="https://www.who.int/westernpacific/publications/m/item/'
    'avian-influenza-weekly-update---1066--16-october-2026">'
    '<span class="timestamp">16 October 2026</span>'
    "Avian Influenza Weekly Update # 1066: 16 October 2026</a>"
)

UNRELATED_LINK = (
    '<a href="https://www.who.int/westernpacific/publications/m/item/some-report">'
    '<span class="timestamp">1 October 2026</span>About the regional office</a>'
)

UNRELATED_NAV = (
    '<a href="https://www.who.int/westernpacific/health-topics/dengue">'
    "Health topics navigation entry for the western pacific region</a>"
)


def test_wpro_topics_are_dated_feeds_behind_the_landing_page():
    assert len(WPRO_TOPIC_URLS) >= 2
    assert all(url.startswith("https://www.who.int/westernpacific/") for url in WPRO_TOPIC_URLS)


def test_wpro_reads_the_date_and_single_title_copy():
    rows = normalize_wpro(WPRO_ITEM)
    assert len(rows) == 1
    row = rows[0]
    assert row["published_date"] == "2026-10-22"
    assert row["iso_week_key"] == "2026-W43"
    assert row["title_en"] == "Respiratory Viruses Surveillance Bulletin: Epidemiological Week 41, 2026"
    assert row["summary_en"] is None, "redistribution terms are unconfirmed, so no body text is carried"
    assert row["source_id"] == "who_wpro"


def test_wpro_maps_agency_headlines_onto_gids_diseases():
    rows = normalize_wpro(AVIAN_ITEM)
    assert [d["disease_id"] for d in rows[0]["diseases"]] == ["D038"]


def test_wpro_skips_items_without_a_date_or_a_surveillance_headline():
    assert normalize_wpro(UNRELATED_LINK) == []
    assert normalize_wpro(UNRELATED_NAV) == []


def test_wpro_reads_the_date_from_the_slug_when_markup_has_no_stamp():
    rows = normalize_wpro(
        '<a aria-label="Avian Influenza Weekly Update # 1064: 2 October 2026" href='
        '"https://www.who.int/westernpacific/publications/m/item/'
        'avian-influenza-weekly-update---1064--2-october-2026">'
        "Avian Influenza Weekly Update # 1064: 2 October 2026</a>"
    )
    assert len(rows) == 1
    assert rows[0]["published_date"] == "2026-10-02"


WER_ARTICLE = {
    "Id": "0ada2dcc-2d15-46fd-84dd-d82b296670ec",
    "Title": "Global dengue update",
    "PublicationDate": "2026-10-13T20:10:00Z",
    "ItemDefaultUrl": "/who-wer10140-285-289",
    "UrlName": "who-wer10140-285-289",
}


def test_wer_reads_dated_articles_from_the_publications_hub():
    row = normalize_wer_item(WER_ARTICLE)
    assert row is not None
    assert row["source_id"] == "who_wer"
    assert row["published_date"] == "2026-10-13"
    assert row["iso_week_key"] == "2026-W42"
    assert row["url"] == "https://www.who.int/publications/i/item/who-wer10140-285-289"
    assert row["title_en"] == "Global dengue update"
    assert [d["disease_id"] for d in row["diseases"]] == ["D021"]
    assert row["summary_en"] is None, "redistribution terms are unconfirmed, so no body text is carried"
    assert WER_API_URL.startswith("https://www.who.int/api/hubs/publications")


def test_wer_drops_records_without_a_date_or_link():
    no_date = {key: value for key, value in WER_ARTICLE.items() if key != "PublicationDate"}
    no_link = {key: value for key, value in WER_ARTICLE.items() if key not in ("ItemDefaultUrl", "UrlName")}
    assert normalize_wer_item(no_date) is None
    assert normalize_wer_item(no_link) is None
    assert normalize_wer_item({**WER_ARTICLE, "Title": "Meetings of the advisory committee"}) is None


def test_wer_payload_keeps_only_usable_records():
    rows = normalize_wer_payload({"value": [WER_ARTICLE, {"Id": "x"}]})
    assert len(rows) == 1
    assert rows[0]["url"].endswith("who-wer10140-285-289")


def test_wer_document_ref_carries_the_article_page_range():
    from src.services.global_sources.who_periodicals import _wer_pages

    assert _wer_pages("https://www.who.int/publications/i/item/who-wer10139-261-268") == {"pages": [261, 268]}
    assert _wer_pages("https://www.who.int/publications/i/item/who-wer10139") == {}
    assert _wer_pages("https://x/who-wer-900-100") == {}
