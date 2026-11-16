"""Official BES capture, organization and validated model-center fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import requests

from src.data.crawlers.co_bes import (
    ARCHIVE_URL,
    BESContractError,
    BESCover,
    BESReport,
    sharepoint_list_url,
)
from src.data.crawlers.co_bes_cache import BESPDFCache
from src.services.ingestion.countries import co_bes

PDF_URL = "https://www.ins.gov.co/buscador-eventos/BES-2025-01.pdf"
PDF = b"%PDF-1.7\nexample\n%%EOF\n"
HTML = (b'<html><a href="/buscador-eventos/BES-2025-01.pdf">BES</a>'
        b'<a href="/buscador-eventos/BES-2025-01.pdf">duplicate</a></html>')
GUID = "46c5247c-687b-46e8-abe8-6e38e644ceb9"
FOLDER = "/buscador-eventos/BoletinEpidemiologico"
SHAREPOINT_HTML = (f'<html><script>ctx.listName = "{{{GUID.upper()}}}";'
                   f'ctx.listUrlDir = "{FOLDER}";</script></html>').encode()


def _list_item(year, week, item_id):
    name = f"{year}_Boletin_epidemiologico_semana_{week}.pdf"
    return {"Id": item_id, "A_x00f1_o": str(year), "Consecutivo": week,
            "File": {"Name": name, "ServerRelativeUrl": f"{FOLDER}/{name}"}}


class Response:
    def __init__(self, url, payload, *, length=None, content_type="application/pdf", error=None):
        self.url = url
        self.payload = payload
        self.headers = {"Content-Type": content_type,
                        "Content-Length": str(len(payload) if length is None else length)}
        self.error = error

    def iter_content(self, chunk_size):
        if self.error:
            raise self.error
        yield self.payload

    def close(self):
        pass


class Writer:
    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        artifact = SimpleNamespace(
            payload=payload, source_url=source_url, kind=kind, metadata_=metadata,
            sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload))
        self.artifacts.append(artifact)
        return artifact


def transport_type(*, pdf=None, html=HTML):
    class Transport:
        def get(self, url, **kwargs):
            if url == ARCHIVE_URL:
                return Response(url, html, content_type="text/html")
            assert url == PDF_URL
            return pdf or Response(url, PDF)

        def close(self):
            pass

    return Transport


def test_cache_rejects_truncated_pdf_and_bad_length(tmp_path):
    for response in (Response(PDF_URL, PDF[:-5]), Response(PDF_URL, PDF, length=999)):
        with pytest.raises(BESContractError):
            BESPDFCache(tmp_path).get(transport_type(pdf=response)(), PDF_URL)
    assert not list(tmp_path.glob("*.json"))


def test_cache_verifies_receipt_and_force_refetches(tmp_path):
    cache = BESPDFCache(tmp_path)
    first, receipt = cache.get(transport_type()(), PDF_URL)
    assert first == PDF and receipt["cache_status"] == "downloaded"
    _, hit = cache.get(transport_type(pdf=Response(PDF_URL, b"bad"))(), PDF_URL)
    assert hit["cache_status"] == "hit_verified"
    _, forced = cache.get(transport_type()(), PDF_URL, force=True)
    assert forced["cache_status"] == "downloaded"
    (tmp_path / (receipt["sha256"] + ".pdf")).write_bytes(b"broken")
    recovered, repaired = cache.get(transport_type()(), PDF_URL)
    assert recovered == PDF and repaired["cache_status"] == "downloaded"
    assert next((tmp_path / "quarantine").glob("*/*/payload.pdf")).read_bytes() == b"broken"


@pytest.mark.parametrize("force", [False, True])
def test_cache_recovers_invalid_receipt_through_original_transport(tmp_path, force):
    cache = BESPDFCache(tmp_path)
    cache.get(transport_type()(), PDF_URL)
    receipt_path = tmp_path / (hashlib.sha256(PDF_URL.encode()).hexdigest() + ".json")
    receipt_path.write_bytes(b'{"sha256": "incomplete')

    class RecoveryTransport:
        calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            assert url == PDF_URL and kwargs == {"stream": True}
            return Response(url, PDF)

    transport = RecoveryTransport()
    payload, receipt = cache.get(transport, PDF_URL, force=force)
    assert payload == PDF and receipt["cache_status"] == "downloaded"
    assert transport.calls == 1
    assert next((tmp_path / "quarantine").glob("*/*/receipt.json")).read_bytes() == (
        b'{"sha256": "incomplete'
    )
    assert json.loads(receipt_path.read_bytes())["sha256"] == hashlib.sha256(PDF).hexdigest()


def test_cache_recovery_network_failure_never_returns_bad_bytes(tmp_path):
    cache = BESPDFCache(tmp_path)
    _, receipt = cache.get(transport_type()(), PDF_URL)
    (tmp_path / (receipt["sha256"] + ".pdf")).write_bytes(b"broken")

    class FailedTransport:
        def get(self, url, **kwargs):
            raise requests.ConnectionError("unavailable")

    with pytest.raises(requests.ConnectionError):
        cache.get(FailedTransport(), PDF_URL)
    assert not list(tmp_path.glob("*.json"))
    assert next((tmp_path / "quarantine").glob("*/*/payload.pdf")).read_bytes() == b"broken"

    payload, receipt = cache.get(transport_type()(), PDF_URL)
    assert payload == PDF and receipt["cache_status"] == "downloaded"


def test_verified_cache_makes_zero_requests(tmp_path):
    cache = BESPDFCache(tmp_path)
    cache.get(transport_type()(), PDF_URL)

    class NoRequests:
        def get(self, *args, **kwargs):
            pytest.fail("Verified fresh cache must not request the source")

    payload, receipt = cache.get(NoRequests(), PDF_URL)
    assert payload == PDF and receipt["cache_status"] == "hit_verified"


@pytest.mark.parametrize("digest", ["../outside", "a" * 63, "A" * 64, "g" * 64])
def test_cache_rejects_receipt_digest_paths_before_reading(tmp_path, digest):
    cache = BESPDFCache(tmp_path / "cache")
    cache.get(transport_type()(), PDF_URL)
    receipt_path = cache.directory / (hashlib.sha256(PDF_URL.encode()).hexdigest() + ".json")
    receipt = json.loads(receipt_path.read_bytes())
    receipt["sha256"] = digest
    receipt_path.write_text(json.dumps(receipt))
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private unrelated data")

    payload, repaired = cache.get(transport_type()(), PDF_URL)

    assert payload == PDF and repaired["cache_status"] == "downloaded"
    assert outside.read_bytes() == b"private unrelated data"
    assert not list((cache.directory / "quarantine").glob("*/*/payload.pdf"))


def test_cache_blob_symlink_is_quarantined_without_reading_target(tmp_path):
    cache = BESPDFCache(tmp_path / "cache")
    _, receipt = cache.get(transport_type()(), PDF_URL)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(PDF)
    blob = cache.directory / (receipt["sha256"] + ".pdf")
    blob.unlink()
    blob.symlink_to(outside)

    payload, repaired = cache.get(transport_type()(), PDF_URL)

    assert payload == PDF and repaired["cache_status"] == "downloaded"
    assert not blob.is_symlink()
    assert next((cache.directory / "quarantine").glob("*/*/payload.pdf")).is_symlink()
    assert outside.read_bytes() == PDF


def test_collection_deduplicates_and_audits_stream_failure(tmp_path):
    failure = requests.ConnectionError("https://proxy:secret@proxy.example")
    writer = Writer()
    asyncio.run(co_bes.collect(
        writer, cache_directory=tmp_path,
        transport_type=transport_type(pdf=Response(PDF_URL, PDF, error=failure))))
    plan = json.loads(writer.artifacts[-1].payload)
    assert len(plan["reports"]) == 1
    assert len(plan["failed_downloads"]) == 1
    assert "secret" not in json.dumps(plan)
    assert not any(item.kind == "cda_pdf" for item in writer.artifacts)


def test_collection_rejects_incomplete_archive(tmp_path):
    writer = Writer()
    with pytest.raises(BESContractError, match="Complete"):
        asyncio.run(co_bes.collect(
            writer, cache_directory=tmp_path,
            transport_type=transport_type(html=HTML[:-7])))
    assert not writer.artifacts


def test_collection_rejects_archive_length_mismatch(tmp_path):
    class BadArchive(transport_type()):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            if url == ARCHIVE_URL:
                response.headers["Content-Length"] = str(len(HTML) + 1)
            return response

    with pytest.raises(BESContractError, match="Content-Length mismatch"):
        asyncio.run(co_bes.collect(
            Writer(), cache_directory=tmp_path, transport_type=BadArchive))


def test_link_year_is_only_a_download_hint(tmp_path):
    html = b"""<html>
      <a href="/buscador-eventos/BES-2024-52.pdf">older</a>
      <a href="/buscador-eventos/BES-current.pdf">no year in link</a>
    </html>"""

    class Transport:
        def get(self, url, **kwargs):
            if url == ARCHIVE_URL:
                return Response(url, html, content_type="text/html")
            assert url.endswith("BES-current.pdf")
            return Response(url, PDF)

        def close(self):
            pass

    writer = Writer()
    asyncio.run(co_bes.collect(writer, cache_directory=tmp_path, transport_type=Transport))
    plan = json.loads(writer.artifacts[-1].payload)
    assert len(plan["deferred"]) == 1
    assert plan["deferred"][0]["status"] == "deferred_outside_link_hint"
    assert len(plan["captured"]) == 1


def test_collection_captures_paginated_sharepoint_year_items(tmp_path, monkeypatch):
    first = sharepoint_list_url(GUID, 2025, 2026)
    second = first + "&%24skiptoken=Paged%3DTRUE"
    pages = {
        first: {"d": {"results": [_list_item(2025, 53, 1)], "__next": second}},
        second: {"d": {"results": [_list_item(2026, 44, 2)]}},
    }

    class Transport:
        def get(self, url, **kwargs):
            if url == ARCHIVE_URL:
                return Response(url, SHAREPOINT_HTML, content_type="text/html")
            assert kwargs["headers"]["Accept"] == "application/json;odata=verbose"
            return Response(url, json.dumps(pages[url]).encode(), content_type="application/json")

        def close(self):
            pass

    def fake_download(report, directory, force, transport_type):
        return PDF, {"final_url": report.pdf_url, "sha256": hashlib.sha256(PDF).hexdigest(),
                     "byte_size": len(PDF), "cache_status": "downloaded"}

    monkeypatch.setattr(co_bes, "_download_pdf", fake_download)
    writer = Writer()
    asyncio.run(co_bes.collect(writer, end_year=2026, cache_directory=tmp_path,
                               transport_type=Transport))
    plan = json.loads(writer.artifacts[-1].payload)
    assert plan["discovery_method"] == "sharepoint_year_filtered_items"
    assert len(plan["listing_pages"]) == 2
    assert [report["listed_year"] for report in plan["reports"]] == [2025, 2026]
    assert len(plan["captured"]) == 2
    assert len([item for item in writer.artifacts
                if item.metadata_.get("endpoint") == "sharepoint_items"]) == 2


def test_collection_rejects_sharepoint_pagination_filter_change(tmp_path):
    first = sharepoint_list_url(GUID, 2025, 2026)
    changed = sharepoint_list_url(GUID, 2025, 2025) + "&%24skiptoken=Paged%3DTRUE"
    page = {"d": {"results": [_list_item(2025, 1, 1)], "__next": changed}}

    class Transport:
        def get(self, url, **kwargs):
            if url == ARCHIVE_URL:
                return Response(url, SHAREPOINT_HTML, content_type="text/html")
            assert url == first
            return Response(url, json.dumps(page).encode(), content_type="application/json")

        def close(self):
            pass

    with pytest.raises(BESContractError, match="year filter"):
        asyncio.run(co_bes.collect(Writer(), end_year=2026, cache_directory=tmp_path,
                                   transport_type=Transport))


def test_collection_limits_pdf_download_concurrency(tmp_path):
    links = [f"https://www.ins.gov.co/buscador-eventos/BES-2025-{i:02}.pdf"
             for i in range(1, 7)]
    html = ("<html>" + "".join(f'<a href="{url}">BES</a>' for url in links)
            + "</html>").encode()
    lock = threading.Lock()
    active = 0
    maximum = 0

    class SlowResponse(Response):
        def iter_content(self, chunk_size):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            yield self.payload

    class Transport:
        def get(self, url, **kwargs):
            if url == ARCHIVE_URL:
                return Response(url, html, content_type="text/html")
            return SlowResponse(url, PDF)

        def close(self):
            pass

    writer = Writer()
    asyncio.run(co_bes.collect(
        writer, cache_directory=tmp_path, transport_type=Transport, download_workers=2))
    plan = json.loads(writer.artifacts[-1].payload)
    assert len(plan["captured"]) == len(links)
    assert maximum == 2


def test_organize_rejects_capture_without_parsed_numeric_rows(tmp_path, monkeypatch):
    writer = Writer()
    asyncio.run(co_bes.collect(writer, cache_directory=tmp_path, transport_type=transport_type()))
    monkeypatch.setattr(co_bes, "read_artifact", lambda artifact: artifact.payload)
    updater = SimpleNamespace(normalize_source=lambda source: source)
    with pytest.raises(BESContractError, match="No INS BES dashboard cumulative observations"):
        co_bes.organize(writer.artifacts, updater=updater, parse_workers=1)


def test_validated_ai_cache_reuses_canonical_cell_normalizer(tmp_path, monkeypatch):
    report = BESReport(PDF_URL, ARCHIVE_URL, "BES-2025-01.pdf", 2025)
    digest = hashlib.sha256(PDF).hexdigest()
    cache = tmp_path / f"{digest}-{co_bes.AI_CACHE_VERSION}.json"
    document = {
        "pdf_sha256": digest, "prompt_version": co_bes.AI_CACHE_VERSION,
        "validated": True, "coverage_scope": "source_native_cumulative_dashboard_cells",
        "report": {"pdf_url": report.pdf_url, "listed_year": 2025},
        "cover": {"year": 2025, "week": 1, "period_end": "2025-01-04"},
        "pages": [31], "partial_report": False,
        "cells": [{"pdf_sha256": digest, "page_1_based": 31,
                   "event_group_position_1_based": 1,
                   "event_label_raw": "Dengue", "case_definition_raw": "Confirmados",
                   "geography_label_raw": "Total nacional",
                   "accumulated_header_raw": "Acumulado 2025",
                   "accumulated_value_raw": "1,234",
                   "extraction_method": "model_center_native_pdf"}],
    }
    cache.write_text(json.dumps(document))
    monkeypatch.setattr(co_bes, "inspect_pdf", lambda payload: BESCover(2025, 1, date(2025, 1, 4)))
    rows, audit, partial, pages = co_bes._load_validated_ai_rows(report, PDF, digest, str(tmp_path))
    assert rows[0]["Cases"] == "1234"
    assert rows[0]["ExtractionMethod"] == "model_center_native_pdf"
    assert audit["extraction_method"] == "model_center_pdf_vision"
    assert not partial and pages == {31}
    document["cells"][0]["accumulated_value_raw"] = "4,95"
    cache.write_text(json.dumps(document))
    unresolved, unreadable_audit, _, _ = co_bes._load_validated_ai_rows(
        report, PDF, digest, str(tmp_path))
    assert unresolved[0]["MissingValueStatus"] == "source_unreadable"
    assert unresolved[0]["SourceAccumulatedValueRaw"] == "4,95"
    assert unreadable_audit["unparsed_cells"] == 1
    document["pages"] = [31, 32]
    cache.write_text(json.dumps(document))
    with pytest.raises(BESContractError, match="validated cells"):
        co_bes._load_validated_ai_rows(report, PDF, digest, str(tmp_path))
    document["pages"] = [31]
    document["pdf_sha256"] = "b" * 64
    cache.write_text(json.dumps(document))
    with pytest.raises(BESContractError, match="identity changed"):
        co_bes._load_validated_ai_rows(report, PDF, digest, str(tmp_path))


def test_ai_partial_report_replaces_only_verified_page():
    def row(page, identity):
        return {"SourceTablePage": page, "SourceRecordID": identity,
                "RawDiseaseLabel": identity, "MissingValueStatus": ""}

    original = [row(31, "old"), row(32, "other")]
    audit = {"gaps": [{"page": 31, "reason": "cumulative_count_unreadable"}],
             "table_pages": [31, 32], "source_events": ["old", "other"]}
    replacement = [row(31, "new")]
    rows, merged = co_bes._merge_ai_pages(
        original, audit, (replacement, {"ai_cache_path": "cache.json", "gaps": []}, True, {31}))
    assert {item["SourceRecordID"] for item in rows} == {"new", "other"}
    assert merged["gaps"] == [] and merged["unparsed_cells"] == 0
    assert merged["ai_replaced_pages"] == [31]
    _, unresolved = co_bes._merge_ai_pages(
        original, audit, (replacement, {"ai_cache_path": "cache.json",
                                    "gaps": [{"page": 31, "reason": "cumulative_count_unreadable"}]},
                          True, {31}))
    assert len(unresolved["gaps"]) == 1 and unresolved["unparsed_cells"] == 1
    audit["gaps"][0]["source_cell_retained"] = True
    kept, unchanged = co_bes._merge_ai_pages(
        original, audit, (replacement, {"ai_cache_path": "cache.json", "gaps": []}, True, {31}))
    assert kept == original and unchanged is audit


def test_async_organizer_uses_model_center_only_for_failed_report(monkeypatch):
    from src.services.ingestion import co_bes_ai

    report = BESReport(PDF_URL, ARCHIVE_URL, "BES-2025-01.pdf", 2025)
    artifact = SimpleNamespace(metadata_={"endpoint": "pdf", "report": report.__dict__},
                               relative_path="data/raw/co/sample.pdf")
    first = SimpleNamespace(rows=[], source_audit={
        "failed_reports": [{"url": PDF_URL}], "parser_gaps": []})
    updated = SimpleNamespace(rows=[{"Cases": "1"}], source_audit={})
    calls = iter([first, updated])
    monkeypatch.setattr(co_bes, "organize", lambda *args, **kwargs: next(calls))
    monkeypatch.setattr(co_bes, "_candidate_annex_pages", lambda path: [31])
    extract = AsyncMock(return_value={"validated": True})
    monkeypatch.setattr(co_bes_ai, "extract_report", extract)
    result = asyncio.run(co_bes.organize_async([artifact], updater=object()))
    assert result is updated
    assert result.source_audit["ai_failures"] == []
    assert extract.call_args.kwargs["partial_report"] is False
    assert extract.call_args.args[2] == [31]


async def test_crawl_service_bes_handler_uses_process_true(monkeypatch):
    from src.data.processors.co_bes import ColombiaINSBESUpdater
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import co_bes_refresh, runner

    assert co_bes_refresh.collect is co_bes.collect
    assert co_bes_refresh.organize is co_bes.organize
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    updater = ColombiaINSBESUpdater()
    await CrawlService()._execute_co_bes_surveillance(
        task=SimpleNamespace(input_data={"start_year": 2025, "end_year": 2026}),
        updater=updater, source="co_ins_bes_weekly", force=False,
        process=True, save_raw=True, fill_missing=False)
    arguments = run.call_args.kwargs
    assert arguments["connector"] is co_bes_refresh
    assert arguments["process"] is True
    assert arguments["updater"] is updater
    assert updater.public_release_enabled is False
    assert arguments["connector_options"] == {
        "start_year": 2025, "end_year": 2026,
        "download_workers": 8, "parse_workers": 4, "force": False,
    }


async def test_crawl_service_bes_defaults_to_two_bogota_calendar_years(monkeypatch):
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import runner

    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    await CrawlService()._execute_co_bes_surveillance(
        task=SimpleNamespace(input_data={}), updater=object(),
        source="co_ins_bes_weekly", force=False, process=True,
        save_raw=True, fill_missing=False)
    year = datetime.now(ZoneInfo("America/Bogota")).year
    assert run.call_args.kwargs["connector_options"]["start_year"] == max(2025, year - 1)
    assert run.call_args.kwargs["connector_options"]["end_year"] == year
