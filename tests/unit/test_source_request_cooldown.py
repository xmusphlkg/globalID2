"""Repeated refreshes defer known failures without disguising missing data."""
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers import base, request_cooldown
from src.data.crawlers.nz import DHB_TOTAL_CSV_MIRRORS, NewZealandPHFCrawler
from src.data.crawlers.request_cooldown import (
    RequestFailureCooldown,
    SourceRequestDeferred,
)
from src.data.crawlers.th import ThailandDOE506Crawler
from src.services.ingestion.countries import th


def test_cooldown_survives_new_instance_and_expires_with_recovery_probe(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(request_cooldown.time, "time", lambda: clock[0])
    cache = RequestFailureCooldown(tmp_path, ttl_seconds=900)
    cache.record("https://official.example/file", reason="http_404")
    second = RequestFailureCooldown(tmp_path, ttl_seconds=900)
    with pytest.raises(SourceRequestDeferred) as error:
        second.check("https://official.example/file")
    assert error.value.retry_at == 1900
    second.check("https://official.example/changed-file")
    clock[0] = 1900
    second.check("https://official.example/file")
    second.clear("https://official.example/file")
    assert list(tmp_path.iterdir()) == []


def test_invalid_cache_and_clock_rollback_do_not_block_source_access(tmp_path, monkeypatch):
    monkeypatch.setattr(request_cooldown.time, "time", lambda: 1000)
    cache = RequestFailureCooldown(tmp_path, ttl_seconds=900)
    path = cache._path("https://official.example/file")
    for value in ["broken-json", "null", '{"retry_at": "nan"}', json.dumps({
            "url": "https://official.example/file", "retry_at": 10000, "reason": "http_404"})]:
        path.write_text(value)
        cache.check("https://official.example/file")


def test_thailand_missing_report_deferred_but_force_and_expiry_probe_recover(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "get_config", lambda: SimpleNamespace(cache_dir=tmp_path))
    clock = [1000.0]
    monkeypatch.setattr(request_cooldown.time, "time", lambda: clock[0])
    calls = []
    available = [False]
    url = "http://doe1.moph.go.th/surdata/y68/mcd_Influenza_68.rtf"

    def transport(self, target, **kwargs):
        calls.append(target)
        if target.endswith(".rtf") and not available[0]:
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError("Not Found", response=response)
        return SimpleNamespace(content=b"actual source bytes")

    monkeypatch.setattr(base.BaseCrawler, "get", transport)
    first = ThailandDOE506Crawler()
    with pytest.raises(requests.HTTPError):
        first.get(url)
    second = ThailandDOE506Crawler()
    with pytest.raises(SourceRequestDeferred):
        second.get(url)
    second.get(th.INDEX_URL)
    assert calls == [url, th.INDEX_URL]
    # An explicit forced collection probes immediately, then normal attempts
    # can still recheck after the finite negative-cache window.
    second.force_refresh = True
    with pytest.raises(requests.HTTPError):
        second.get(url)
    available[0] = True
    second.force_refresh = False
    clock[0] += 24 * 3600
    assert second.get(url).content == b"actual source bytes"
    assert second.get(url).content == b"actual source bytes"
    assert len(calls) == 5


async def test_thailand_deferred_report_stays_in_partial_collection_audit(tmp_path, monkeypatch):
    fixtures = Path(__file__).parents[1] / "fixtures/ingestion/TH"
    audit_by_code = {item["code"]: item for item in json.loads((fixtures / "audit_2023.json").read_text())}

    def fixture(name):
        with zipfile.ZipFile(fixtures / "reports_2023.zip") as archive:
            return archive.read(name)

    monkeypatch.setattr(base, "get_config", lambda: SimpleNamespace(cache_dir=tmp_path))
    calls = []

    def transport(self, url, **kwargs):
        calls.append(url)
        if url == th.INDEX_URL:
            content = fixture("index.html")
        elif "dcontent=old" in url:
            content = fixture(url.split("ds=")[1].split("&")[0] + "_history.html")
        elif url == audit_by_code["15"]["url"]:
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError("Not Found", response=response)
        elif url == audit_by_code["71"]["url"]:
            content = fixture("71.rtf")
        else:
            pytest.fail(f"unexpected request {url}")
        return SimpleNamespace(content=content)

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, payload, **kwargs):
            self.artifacts.append(SimpleNamespace(payload=payload, kind=kwargs["kind"]))

    monkeypatch.setattr(base.BaseCrawler, "get", transport)
    for attempt in range(2):
        writer = Writer()
        await th.collect(writer, disease_codes=["15", "71"], start_year=2023, end_year=2023)
        audit = json.loads(writer.artifacts[-1].payload)
        assert len(audit["expected"]) == 2
        assert audit["collection_errors"][0]["disease_code"] == "15"
        assert len([item for item in writer.artifacts if item.kind == "monthly_rtf"]) == 1
        if attempt:
            assert "deferred" in audit["collection_errors"][0]["error"]
    assert calls.count(audit_by_code["15"]["url"]) == 1


def test_nz_mirror_switch_is_bounded_and_next_instance_skips_timed_out_host(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "get_config", lambda: SimpleNamespace(cache_dir=tmp_path))
    calls = []

    def transport(self, url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        if url == DHB_TOTAL_CSV_MIRRORS[0]:
            raise requests.ConnectTimeout("canonical host unavailable")
        return SimpleNamespace(content=b"actual CSV bytes")

    monkeypatch.setattr(base.BaseCrawler, "get", transport)
    for _ in range(2):
        crawler = NewZealandPHFCrawler()
        for url in DHB_TOTAL_CSV_MIRRORS:
            assert crawler.session.get_adapter(url).max_retries.total == 0
        monkeypatch.setattr(crawler, "parse_dhb_annual_csv", lambda payload, **kwargs: (payload, kwargs["source_url"]))
        assert crawler.fetch_dhb_annual_rows() == (b"actual CSV bytes", DHB_TOTAL_CSV_MIRRORS[1])
    assert calls == [(DHB_TOTAL_CSV_MIRRORS[0], (10, 30)),
                     (DHB_TOTAL_CSV_MIRRORS[1], (10, 30)),
                     (DHB_TOTAL_CSV_MIRRORS[1], (10, 30))]


def test_base_request_timeout_override_still_captures_source_bytes(tmp_path, monkeypatch):
    from src.services.ingestion import capture

    monkeypatch.setattr(base, "get_config", lambda: SimpleNamespace(cache_dir=tmp_path))
    monkeypatch.setattr(base.time, "sleep", lambda _: None)
    captured = []
    monkeypatch.setattr(capture, "assert_source_access", lambda: None)
    monkeypatch.setattr(capture, "capture_sync", lambda payload, *args, **kwargs: captured.append(payload) or payload)
    crawler = ThailandDOE506Crawler()
    sent = []

    def transport(url, **kwargs):
        sent.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = b"source bytes"
        return response

    monkeypatch.setattr(crawler.session, "get", transport)
    crawler.get(th.INDEX_URL, timeout=(5, 10))
    crawler.get(th.INDEX_URL)
    assert sent == [{"timeout": (5, 10)}, {"timeout": 40}]
    assert captured == [b"source bytes", b"source bytes"]
