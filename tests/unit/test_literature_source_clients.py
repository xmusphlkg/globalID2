from datetime import datetime, timezone

import httpx
import pytest

from src.literature.clients.base import ProviderNotConfiguredError
from src.literature.clients.preprints import BiorxivClient
from src.literature.clients.publisher_apis import ElsevierClient, SpringerNatureClient
from src.literature.clients.rss import PublisherRssClient, validate_feed_whitelist
from src.literature.normalization import (
    normalize_biorxiv,
    normalize_elsevier,
    normalize_springer_nature,
)
from src.literature.pipeline import (
    _deduplicate_candidates,
    _hold_preprint_for_review,
    _isolate_optional_source,
    _preserve_optional_checkpoint,
)
from src.literature.types import Classification


SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [
    SpringerNatureClient(api_key="", contact_email="tests@example.org", retries=1),
    ElsevierClient(api_key="", contact_email="tests@example.org", retries=1),
])
async def test_publisher_clients_fail_closed_before_transport_without_credentials(client):
    with pytest.raises(ProviderNotConfiguredError, match="credential"):
        await client.search_recent(query="dengue", since=SINCE, until=UNTIL, max_records=10)


@pytest.mark.asyncio
async def test_springer_pagination_obeys_global_bound_and_omits_abstract():
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["s"])
        count = int(request.url.params["p"])
        starts.append(start)
        records = [
            {
                "identifier": f"doi:10.1000/springer-{index}",
                "title": f"Dengue record {index}",
                "publicationName": "Test Journal",
                "publicationDate": "2026-08-20",
                "abstract": "Publisher abstract must not be retained.",
                "creators": [{"creator": "A. Researcher"}],
            }
            for index in range(start, start + count)
        ]
        return httpx.Response(
            200,
            json={"result": [{"total": "9"}], "records": records},
            request=request,
        )

    client = SpringerNatureClient(
        api_key="test-key",
        contact_email="tests@example.org",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    client.MAX_PAGE_SIZE = 2
    result = await client.search_recent(query="dengue", since=SINCE, until=UNTIL, max_records=3)
    assert len(result.records) == 3
    assert starts == [1, 3]
    assert result.checkpoint["truncated"] is True
    candidate = normalize_springer_nature(result.records[0])
    assert candidate is not None
    assert candidate.abstract_text is None
    assert "abstract" not in candidate.source_payload["springer_nature"]
    resumed = await client.search_recent(
        query="dengue",
        since=UNTIL,
        until=UNTIL,
        max_records=3,
        checkpoint=result.checkpoint,
    )
    assert starts == [1, 3, 4, 6]
    assert resumed.checkpoint["from_date"] == SINCE.date().isoformat()


def test_elsevier_normalization_retains_only_search_metadata():
    candidate = normalize_elsevier({
        "eid": "2-s2.0-123",
        "prism:doi": "10.1000/elsevier",
        "dc:title": "Dengue surveillance",
        "dc:creator": "Researcher, A.",
        "prism:publicationName": "Journal of Surveillance",
        "prism:coverDate": "2026-08-20",
        "openaccess": "1",
        "dc:description": "Proprietary abstract must not be retained.",
        "link": [{"@ref": "scopus", "@href": "https://api.elsevier.com/content/abstract/scopus_id/123"}],
    })
    assert candidate is not None
    assert candidate.abstract_text is None
    assert candidate.open_access_status == "open"
    assert "dc:description" not in candidate.source_payload["elsevier"]


@pytest.mark.asyncio
async def test_preprint_cursor_pagination_is_bounded_and_every_record_is_marked_preprint():
    cursors: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.path.rsplit("/", 1)[-1])
        cursors.append(cursor)
        collection = [
            {
                "doi": f"10.1101/2026.08.27.{cursor + offset}",
                "title": f"Dengue preprint {cursor + offset}",
                "authors": "A. Researcher; B. Scientist",
                "date": "2026-08-27",
                "version": "1",
                "license": "cc_by",
                "abstract": "Public preprint abstract.",
            }
            for offset in range(2)
        ]
        return httpx.Response(
            200,
            json={"messages": [{"total": "5"}], "collection": collection},
            request=request,
        )

    client = BiorxivClient(
        contact_email="tests@example.org",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    client.PAGE_SIZE = 2
    result = await client.fetch_recent(
        since=SINCE,
        until=UNTIL,
        max_records=3,
        servers=("medrxiv",),
    )
    assert len(result.records) == 3
    assert cursors == [0, 2]
    assert result.checkpoint["truncated"] is True
    candidates = [normalize_biorxiv(row) for row in result.records]
    assert all(candidate is not None for candidate in candidates)
    assert all(candidate.peer_review_status == "preprint" for candidate in candidates if candidate)
    assert all(candidate.article_type == "preprint" for candidate in candidates if candidate)
    classification = Classification(publication_status="published")
    assert _hold_preprint_for_review(candidates[0], classification) is True
    assert classification.publication_status == "review"
    resumed = await client.fetch_recent(
        since=UNTIL,
        until=UNTIL,
        max_records=2,
        servers=("medrxiv",),
        checkpoint=result.checkpoint,
    )
    assert cursors == [0, 2, 3]
    assert resumed.checkpoint["truncated"] is False


def test_preprint_registry_marking_survives_higher_priority_provider_deduplication():
    publisher = normalize_elsevier({
        "prism:doi": "10.1101/2026.08.27.42",
        "dc:title": "Dengue surveillance",
        "prism:publicationName": "Search Index",
        "prism:coverDate": "2026-08-27",
    })
    preprint = normalize_biorxiv({
        "server": "medrxiv",
        "doi": "10.1101/2026.08.27.42",
        "title": "Dengue surveillance",
        "authors": "A. Researcher",
        "date": "2026-08-27",
    })
    assert publisher is not None and preprint is not None
    merged, duplicates = _deduplicate_candidates([publisher, preprint])
    assert duplicates == 1
    assert merged[0].peer_review_status == "preprint"
    assert merged[0].article_type == "preprint"


@pytest.mark.asyncio
async def test_optional_provider_failure_is_isolated_and_does_not_expose_exception_detail():
    async def failed():
        raise httpx.ConnectError(
            "secret-key-in-url",
            request=httpx.Request("GET", "https://provider.example/?api_key=secret"),
        )

    async def succeeded():
        return {"records": 2}

    failed_result, failure = await _isolate_optional_source("springer-nature", failed())
    healthy_result, healthy_failure = await _isolate_optional_source("biorxiv-api", succeeded())
    assert failed_result is None
    assert failure == "ConnectError"
    assert "secret" not in failure
    assert healthy_result == {"records": 2}
    assert healthy_failure is None


def test_optional_provider_failure_preserves_last_committed_checkpoint():
    previous = {"strategy": "bounded-offset-v1", "next_start": 51, "truncated": True}
    assert _preserve_optional_checkpoint(None, previous) is previous
    assert _preserve_optional_checkpoint(None, None) is None

    class Result:
        checkpoint = {"strategy": "bounded-offset-v1", "next_start": 76, "truncated": True}

    assert _preserve_optional_checkpoint(Result(), previous) == Result.checkpoint


@pytest.mark.asyncio
async def test_preprint_missing_total_still_checkpoints_partial_page_for_resume():
    cursors: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.path.rsplit("/", 1)[-1])
        cursors.append(cursor)
        rows = [
            {
                "doi": f"10.1101/no-total-{index}",
                "title": f"Preprint {index}",
                "date": "2026-08-27",
            }
            for index in (range(cursor, cursor + 2) if cursor == 0 else range(cursor, cursor + 1))
        ]
        return httpx.Response(200, json={"messages": [{}], "collection": rows}, request=request)

    client = BiorxivClient(
        contact_email="tests@example.org",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    client.PAGE_SIZE = 2
    first = await client.fetch_recent(
        since=SINCE,
        until=UNTIL,
        max_records=1,
        servers=("medrxiv",),
    )
    assert first.checkpoint["truncated"] is True
    assert first.checkpoint["servers"]["medrxiv"]["next_cursor"] == 1

    resumed = await client.fetch_recent(
        since=UNTIL,
        until=UNTIL,
        max_records=10,
        servers=("medrxiv",),
        checkpoint=first.checkpoint,
    )
    assert cursors == [0, 1]
    assert resumed.checkpoint["from_date"] == SINCE.date().isoformat()
    assert resumed.checkpoint["truncated"] is False


def _whitelist(*, enabled: bool = True) -> dict:
    return {
        "schema_version": 1,
        "feeds": [{
            "feed_id": "trusted-feed",
            "publisher": "Trusted Publisher",
            "journal": "Trusted Journal",
            "issn": ["1234-5678"],
            "url": "https://feeds.example/recent.xml",
            "allowed_hosts": ["feeds.example"],
            "enabled": enabled,
        }],
    }


def test_rss_activation_readiness_requires_an_enabled_trusted_https_feed():
    with pytest.raises(ValueError, match="at least one"):
        validate_feed_whitelist(_whitelist(enabled=False))
    report = validate_feed_whitelist(_whitelist())
    assert report == {
        "schema_version": 1,
        "ready_for_probe": True,
        "enabled_feed_count": 1,
        "enabled_feed_ids": ["trusted-feed"],
        "https_only": True,
    }


@pytest.mark.asyncio
async def test_rss_dry_run_probe_returns_health_evidence_without_persisting_state():
    body = b"""<rss><channel><item><title>Dengue Online First</title>
        <guid>https://doi.org/10.1000/rss</guid></item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    report = await client.probe_readiness(whitelist=_whitelist(), now=UNTIL)
    assert report["probe_mode"] == "dry-run"
    assert report["ready"] is True
    assert report["feeds_successful"] == 1
    assert report["sample_records"] == 1
