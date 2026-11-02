"""Regression coverage against excerpts of the actual authority archives."""

from pathlib import Path

import httpx
import pytest

from src.services.global_sources import collection, reliefweb, who_regional
from src.services.global_sources.catalogue import SOURCE_REGISTRY
from src.services.situation_notices import documents

FIXTURES = Path(__file__).parents[1] / "fixtures" / "global_sources"


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_all_authority_feeds_have_loaders():
    assert set(SOURCE_REGISTRY) == set(collection.SOURCE_LOADERS)
    assert {"paho_alerts", "who_afro", "who_searo", "who_emro", "who_euro", "who_sitreps"} <= SOURCE_REGISTRY.keys()


def test_paho_mirror_includes_missing_measles_and_arbovirus_reports():
    events = reliefweb.parse_listing(fixture("paho_reliefweb"), "paho_alerts")
    assert len(events) == 2
    measles, arbovirus = events
    assert measles["published_date"] == "2026-10-22"  # Uploaded 29 October.
    assert measles["native_id"] == "4236286"
    assert "Measles" in measles["title_en"]
    assert "Dengue and Chikungunya" in arbovirus["title_en"]
    assert arbovirus["published_date"] == "2026-10-20"
    assert measles["document_ref"]["organization"] == "paho"
    assert measles["summary_en"] is None


def test_mirror_checks_author_not_just_search_results():
    page = fixture("paho_reliefweb").replace('/organization/paho', '/organization/unrelated')
    assert reliefweb.parse_listing(page, "paho_alerts") == []


def test_emro_collects_who_regional_situation_and_polio_reports():
    rows = reliefweb.parse_listing(fixture("emro_reports"), "who_emro")
    assert len(rows) == 2
    assert all(row["document_ref"]["organization"] == "who" for row in rows)
    assert "Health emergencies" in rows[0]["title_en"]
    assert "Polio Bulletin" in rows[1]["title_en"]
    assert reliefweb.parse_listing(fixture("paho_reliefweb"), "who_emro") == []


def test_archive_failure_on_later_page_is_not_silently_reported_as_success():
    def handler(request):
        if request.url.params["page"] == "0":
            return httpx.Response(200, text=fixture("paho_reliefweb") + '<a rel="next" href="?page=1">Next</a>')
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            reliefweb.fetch_items(source_id="paho_alerts", client=client)


def test_recent_upload_of_old_report_does_not_stop_pagination():
    page0 = fixture("paho_reliefweb").replace('22 October 2026', '22 October 2024').replace('20 October 2026', '20 October 2024')
    page0 += '<a href="?page=1" rel="next">Next</a>'
    page1 = fixture("paho_reliefweb").replace('4236286', '9999999')
    seen = []

    def handler(request):
        page = int(request.url.params["page"])
        seen.append(page)
        return httpx.Response(200, text=[page0, page1][page])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        events = reliefweb.fetch_items(source_id="paho_alerts", since="2026-10-01", client=client)
    assert seen == [0, 1]
    assert len(events) == 2


def test_archive_schema_change_is_failure_not_healthy_empty_feed():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='<h1>Service unavailable</h1>'))) as client:
        with pytest.raises(ValueError, match="recognizable"):
            reliefweb.fetch_items(source_id="paho_alerts", client=client)
        with pytest.raises(ValueError, match="recognizable"):
            who_regional.fetch_afro(client=client)


def test_afro_keeps_regional_scope_and_original_pdf():
    rows = who_regional.parse_afro(fixture("afro"))
    assert len(rows) == 2
    assert rows[0]["published_date"] == "2026-10-11"
    assert rows[0]["countries"] == []  # Country in hosting URL is not report scope.
    assert rows[0]["document_ref"]["kind"] == "pdf_url"
    assert rows[0]["document_ref"]["url"].endswith('/download')


def test_searo_reads_dates_outside_anchor_and_complete_cover_title():
    rows = who_regional.parse_publications(fixture("searo"), source_id="who_searo", base_url=who_regional.SEARO_URL)
    assert len(rows) == 2
    assert rows[0]["published_date"] == "2026-10-21"
    assert "Reporting period: 5-18 October 2026" in rows[0]["title_en"]
    assert rows[0]["document_ref"]["url"].endswith('/content')
    assert rows[0]["countries"] == []


def test_euro_does_not_duplicate_hidden_and_visible_titles():
    rows = who_regional.parse_publications(fixture("euro_bulletin"), source_id="who_euro", base_url=who_regional.EURO_URL)
    assert rows[0]["title_en"] == "WHO European Region Health Security Bulletin: April–June 2026"
    assert rows[0]["published_date"] == "2026-10-16"


@pytest.mark.parametrize("attachment_type", ["report", "infographic"])
def test_reliefweb_body_download_is_scoped_to_verified_original_article(monkeypatch, attachment_type):
    markup = '''<a href="/other.pdf">Unrelated</a>
    <article class="rw-article--report">
      <div class="rw-entity-meta__tag-value--source"><a href="/organization/paho">PAHO</a></div>
      <div class="rw-attachment rw-attachment--TYPE"><a href="/attachments/original.pdf">Report</a></div>
    </article>'''.replace("TYPE", attachment_type)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b'%PDF-original' if request.url.path.endswith('.pdf') else markup.encode())

    monkeypatch.setattr(documents, '_pdf_document', lambda data: documents.FetchedDocument(documents.PDF, "full report", data))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = documents._reliefweb_report({"page_url": "https://reliefweb.int/report/world/test", "organization": "paho"}, client)
    assert result.data == b'%PDF-original'
    assert seen == ['/report/world/test', '/attachments/original.pdf']


def test_reliefweb_body_rejects_mismatched_author_and_abstract_only():
    markup = '<article class="rw-article--report"><div class="rw-entity-meta__tag-value--source"><a href="/organization/who">WHO</a></div><p>Summary only</p></article>'
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=markup))) as client:
        with pytest.raises(documents.DocumentUnavailable, match="author"):
            documents._reliefweb_report({"page_url": "https://reliefweb.int/report/world/test", "organization": "paho"}, client)
        with pytest.raises(documents.DocumentUnavailable, match="original PDF"):
            documents._reliefweb_report({"page_url": "https://reliefweb.int/report/world/test", "organization": "who"}, client)


def test_who_iris_download_links_are_readable_pdf_references():
    assert documents.pdf_link('<a href="https://iris.who.int/bitstreams/abc/download">PDF</a>', 'https://www.who.int') == 'https://iris.who.int/bitstreams/abc/download'
