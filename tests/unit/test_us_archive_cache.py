"""Archive caching must preserve revisions, retries and source evidence."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.ingestion import us_archive_cache as cache
from src.services.ingestion.countries import us


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)


def crawler_for(rows):
    return SimpleNamespace(get=Mock(return_value=SimpleNamespace(content=json.dumps(rows).encode())))


def fetch(crawler, **kwargs):
    options = {"resource": {"id": "fixture", "updatedAt": "2026-11-16"}, "offset": 0, "page_size": 50000}
    options.update(kwargs)
    return cache.fetch_archive_page(crawler, **options)


def test_identical_revision_reuses_verified_bytes_and_keeps_source_url():
    crawler = crawler_for([{"reporting_area": "CALIFORNIA"}])
    first = fetch(crawler)
    second = fetch(crawler)
    assert first[:2] == second[:2]
    assert first[2] is False and second[2] is True
    assert first[1].startswith("https://data.cdc.gov/resource/fixture.json?")
    crawler.get.assert_called_once()


@pytest.mark.parametrize("change", ["revision", "offset", "size", "force", "corruption", "expired"])
def test_changed_or_untrusted_cache_requires_download(change, monkeypatch):
    crawler = crawler_for([{"n": 1}])
    fetch(crawler)
    options = {}
    if change == "revision":
        options["resource"] = {"id": "fixture", "updatedAt": "new revision"}
    elif change == "offset":
        options["offset"] = 50000
    elif change == "size":
        options["page_size"] = 10000
    elif change == "force":
        options["force"] = True
    elif change == "corruption":
        next(cache.CACHE_DIR.rglob("page.json")).write_bytes(b"corrupt")
    elif change == "expired":
        receipt = next(cache.CACHE_DIR.rglob("receipt.json"))
        data = json.loads(receipt.read_bytes())
        monkeypatch.setattr(cache.time, "time", lambda: data["fetched_at"] + cache.REVALIDATE_SECONDS)
    assert fetch(crawler, **options)[2] is False
    assert crawler.get.call_count == 2


def test_missing_revision_never_reuses_cache():
    crawler = crawler_for([])
    for _ in range(2):
        assert fetch(crawler, resource={"id": "fixture"})[2] is False
    assert crawler.get.call_count == 2


def test_concurrent_tasks_download_unchanged_page_once():
    crawler = crawler_for([{"n": 1}])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: fetch(crawler), range(4)))
    assert sum(not result[2] for result in results) == 1
    crawler.get.assert_called_once()


def test_failed_or_invalid_response_is_not_cached():
    crawler = crawler_for({"error": "unavailable"})
    with pytest.raises(ValueError, match="JSON array"):
        fetch(crawler)
    crawler.get.return_value.content = b'[{"n":1}]'
    assert fetch(crawler)[2] is False
    assert crawler.get.call_count == 2


async def test_archive_retry_reuses_completed_page_and_captures_it_again(monkeypatch):
    resource = {"id": "fixture", "updatedAt": "revision1", "description": "NNDSS - Table I - 2020. fixture",
                "columns_name": ["Disease, Current week"]}
    calls = []
    failed = False
    def get(url, **kwargs):
        nonlocal failed
        calls.append((url, kwargs))
        if url == us.SOCRATA_CATALOG_URL:
            payload = {"results": [{"resource": resource}]}
        elif url.endswith("fixture.json"):
            offset = kwargs["params"]["$offset"]
            if offset == 1 and not failed:
                failed = True
                raise ConnectionError("interrupted second page")
            payload = [{"n": 1}] if offset == 0 else []
        else:
            return SimpleNamespace(content=b"year,week\n", text="year,week\n", url=url)
        content = json.dumps(payload).encode()
        return SimpleNamespace(content=content, text=content.decode(), url=url, json=lambda: payload)
    crawler = SimpleNamespace(get=get, session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: crawler)
    monkeypatch.setattr(us, "SOCRATA_PAGE_SIZE", 1)
    class Writer:
        def __init__(self): self.artifacts = []
        async def write(self, payload, **metadata): self.artifacts.append((payload, metadata))
    with pytest.raises(ConnectionError):
        await us.collect(Writer(), source="nndss_api", archive_years=(2020,))
    retry = Writer()
    await us.collect(retry, source="nndss_api", archive_years=(2020,))
    table_calls = [kwargs["params"]["$offset"] for url, kwargs in calls if url.endswith("fixture.json")]
    assert table_calls == [0, 1, 1]
    pages = [meta for payload, meta in retry.artifacts if "resource" in meta.get("metadata", {})]
    assert [page["metadata"]["cache_hit"] for page in pages] == [True, False]
