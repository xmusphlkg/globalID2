"""Offline tests for fetching and caching official report bodies."""

from __future__ import annotations

import httpx
import pytest

from src.services.situation_notices import documents as docs
from src.services.situation_notices.documents import (
    DocumentUnavailable,
    FetchedDocument,
    fetch_document,
    html_to_text,
    load_original,
    pdf_link,
    store_original,
    text_hash,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_html_to_text_keeps_paragraphs_and_turns_table_rows_into_pipe_lines():
    markup = (
        "<p>First&nbsp;paragraph.</p><p>Second<br>line</p>"
        "<table><tr><th>Country</th><th>Cases</th></tr><tr><td>Kenya</td><td>9</td></tr></table>"
    )
    lines = html_to_text(markup).splitlines()
    assert lines[0] == "First paragraph."
    assert "Second" in lines and "line" in lines
    assert "Country | Cases" in lines
    assert "Kenya | 9" in lines


def test_html_to_text_of_empty_input_is_empty():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""


def test_pdf_link_picks_the_first_pdf_anchor_and_resolves_it():
    page = '<a href="/about">x</a><a href="/sites/files/cdtr-week-42.PDF">pdf</a><a href="/b.pdf">b</a>'
    assert pdf_link(page, "https://www.ecdc.europa.eu/en/publications") == "https://www.ecdc.europa.eu/sites/files/cdtr-week-42.PDF"
    assert pdf_link("<a href='/x'>x</a>", "https://a.example/") is None


def test_who_don_handler_joins_sections_in_reading_order():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["filter"] = request.url.params["$filter"]
        return httpx.Response(
            200,
            json={"value": [{"Summary": "<p>Sum.</p>", "Overview": "<p>Over.</p>", "Advice": "<p>Adv.</p>", "Response": ""}]},
        )

    with _client(handler) as client:
        document = docs._who_don({"kind": "who_don", "slug": "2026-DON622"}, client)
    assert seen["filter"] == "UrlName eq '2026-DON622'"
    assert document.media_type == docs.HTML
    assert document.text == "## Summary\nSum.\n\n## Overview\nOver.\n\n## Advice\nAdv."


def test_who_don_handler_rejects_unsafe_slugs_before_any_request():
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    with _client(handler) as client:
        with pytest.raises(DocumentUnavailable):
            docs._who_don({"slug": "x' or 1 eq 1 or UrlName eq 'y"}, client)
        with pytest.raises(DocumentUnavailable):
            docs._who_don({}, client)


def test_who_don_handler_reports_missing_and_empty_items_as_final():
    with _client(lambda request: httpx.Response(200, json={"value": []})) as client:
        with pytest.raises(DocumentUnavailable):
            docs._who_don({"slug": "gone"}, client)
    with _client(lambda request: httpx.Response(200, json={"value": [{"Summary": "", "Overview": "  "}]})) as client:
        with pytest.raises(DocumentUnavailable):
            docs._who_don({"slug": "empty"}, client)


def test_wp_rest_handler_requires_https_and_prefixes_the_title():
    payload = {"title": {"rendered": "Statement"}, "content": {"rendered": "<p>Body text.</p>"}}
    with _client(lambda request: httpx.Response(200, json=payload)) as client:
        document = docs._wp_rest({"endpoint": "https://africacdc.org/wp-json/wp/v2/news-item", "id": 7}, client)
        assert document.text == "Statement\n\nBody text."
        with pytest.raises(DocumentUnavailable):
            docs._wp_rest({"endpoint": "http://africacdc.org/x", "id": 7}, client)


def test_pdf_documents_must_be_pdfs_with_text():
    with pytest.raises(DocumentUnavailable):
        docs._pdf_document(b"<html>not a pdf</html>")


def test_fetch_document_without_a_known_kind_is_unavailable():
    for ref in (None, {}, {"kind": "carrier_pigeon"}):
        with pytest.raises(DocumentUnavailable):
            fetch_document(ref)


def test_fetch_document_caps_text_but_keeps_the_original_bytes(monkeypatch):
    big = FetchedDocument(docs.PDF, "x" * (docs.MAX_TEXT_CHARS + 500), b"%PDF-data", 3)
    monkeypatch.setitem(docs._HANDLERS, "fake", lambda ref, client: big)
    document = fetch_document({"kind": "fake"})
    assert len(document.text) == docs.MAX_TEXT_CHARS
    assert document.data == b"%PDF-data" and document.page_count == 3


def test_text_hash_is_stable_sha256():
    assert text_hash("a") == text_hash("a") != text_hash("b")
    assert len(text_hash("a")) == 64


def test_original_cache_round_trips_and_names_do_not_leak_the_notice_id(tmp_path):
    name = store_original("who_don:2026-DON622", b"%PDF-1", directory=tmp_path)
    assert name.endswith(".pdf") and "DON622" not in name
    assert load_original(name, directory=tmp_path) == b"%PDF-1"
    assert store_original("who_don:2026-DON622", b"%PDF-2", directory=tmp_path) == name
    assert load_original(name, directory=tmp_path) == b"%PDF-2"


@pytest.mark.parametrize("name", ["../secret.pdf", "a/b.pdf", "..\\x.pdf", "", None])
def test_load_original_rejects_path_traversal_and_blank_names(tmp_path, name):
    (tmp_path.parent / "secret.pdf").write_bytes(b"secret")
    assert load_original(name, directory=tmp_path) is None


def test_load_original_of_a_missing_file_is_none(tmp_path):
    assert load_original("nope.pdf", directory=tmp_path) is None
