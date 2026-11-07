from datetime import datetime, timedelta, timezone

import pytest

from src.literature.clients.search_page import SearchPage
from src.literature.controlled_discovery import (
    CHECKPOINT_SCHEMA,
    build_controlled_query_batches,
    fetch_controlled_discovery,
    select_controlled_query_batches,
)

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
BATCHES = build_controlled_query_batches([], {})


class Pages:
    def __init__(self, pages=()):
        self.pages = list(pages)
        self.calls = []

    async def search_works_page(self, **kwargs):
        self.calls.append(kwargs)
        result = self.pages.pop(0) if self.pages else SearchPage([])
        if isinstance(result, Exception):
            raise result
        return result

    search_recent_page = search_works_page


async def collect(
    client, *, checkpoint=None, since=START, until=None, batches=BATCHES, **overrides
):
    arguments = {
        "crossref": client,
        "europe_pmc": None,
        "batches": batches,
        "checkpoint": checkpoint,
        "since": since,
        "until": until or START + timedelta(days=2),
        "max_queries": 1,
        "records_per_query": 2,
        "max_records": 2,
    }
    arguments.update(overrides)
    return await fetch_controlled_discovery(**arguments)


def provider_state(result, query=0, provider="crossref"):
    return result.checkpoint["query_states"][BATCHES[query].query_id]["providers"][
        provider
    ]


async def test_unselected_queries_keep_original_start_when_core_watermark_advances():
    client = Pages()
    first = await collect(client)
    second = await collect(
        client,
        checkpoint=first.checkpoint,
        since=START + timedelta(days=8),
        until=START + timedelta(days=9),
    )
    assert client.calls[1]["since"] == START
    assert second.checkpoint["selected_query_ids"] == [BATCHES[1].query_id]
    assert (
        provider_state(second, query=0)["completed_through"]
        == (START + timedelta(days=2)).isoformat()
    )


async def test_failed_query_retries_exact_window_and_other_provider_can_progress():
    failing = Pages([RuntimeError("temporary"), SearchPage([])])
    good = Pages()
    first = await collect(good, europe_pmc=failing, max_records=4)
    second = await collect(
        good,
        europe_pmc=failing,
        max_records=4,
        checkpoint=first.checkpoint,
        since=START + timedelta(days=9),
        until=START + timedelta(days=10),
    )
    assert failing.calls[1] == failing.calls[0]
    assert good.calls[1]["since"] > good.calls[0]["since"]
    assert not second.checkpoint["query_errors"]


async def test_full_page_resumes_fixed_window_before_advancing_watermark():
    client = Pages(
        [SearchPage([{"DOI": "1"}, {"DOI": "2"}], "next"), SearchPage([{"DOI": "3"}])]
    )
    first = await collect(client, batches=BATCHES[:1])
    assert "completed_through" not in provider_state(first)
    second = await collect(
        client,
        batches=BATCHES[:1],
        checkpoint=first.checkpoint,
        since=START + timedelta(days=9),
        until=START + timedelta(days=10),
    )
    assert client.calls[1] == {**client.calls[0], "cursor": "next"}
    assert [row["DOI"] for row in first.crossref_records + second.crossref_records] == [
        "1",
        "2",
        "3",
    ]
    assert (
        provider_state(second)["completed_through"]
        == (START + timedelta(days=2)).isoformat()
    )
    assert (
        provider_state(second)["next_from"] == (START + timedelta(days=3)).isoformat()
    )


async def test_smaller_budget_restarts_page_without_changing_cursor_parameters():
    client = Pages([SearchPage([{}, {}], "next"), SearchPage([{}], "new")])
    first = await collect(client, batches=BATCHES[:1])
    await collect(
        client, batches=BATCHES[:1], checkpoint=first.checkpoint, max_records=1
    )
    assert client.calls[1]["cursor"] is None
    assert client.calls[1]["max_records"] == 1
    assert client.calls[1]["since"] == client.calls[0]["since"]


async def test_pubmed_oversize_window_splits_then_advances_past_finished_day():
    pubmed = Pages([SearchPage([], split_required=True), SearchPage([])])
    first = await collect(Pages(), batches=BATCHES[:1], pubmed=pubmed, max_records=4)
    assert (
        provider_state(first, provider="pubmed")["window_until"]
        == (START + timedelta(days=1)).isoformat()
    )
    second = await collect(
        Pages(),
        batches=BATCHES[:1],
        pubmed=pubmed,
        max_records=4,
        checkpoint=first.checkpoint,
        until=START + timedelta(days=3),
    )
    assert (
        provider_state(second, provider="pubmed")["next_from"]
        == (START + timedelta(days=2)).isoformat()
    )


async def test_pubmed_single_day_overflow_is_visible_without_advancing():
    result = await collect(
        Pages(),
        batches=BATCHES[:1],
        pubmed=Pages([SearchPage([], split_required=True)]),
        max_records=4,
        until=START,
    )
    pending = provider_state(result, provider="pubmed")
    assert "completed_through" not in pending
    assert pending["window_from"] == START.isoformat()
    assert "10000" in result.checkpoint["query_errors"][0]["error"]


async def test_legacy_checkpoint_migrates_with_explicit_unknown_historical_coverage():
    _, checkpoint = select_controlled_query_batches(BATCHES, None, max_queries=1)
    checkpoint["schema_version"] = "literature_controlled_discovery.v1"
    result = await collect(Pages(), checkpoint=checkpoint)
    assert result.checkpoint["schema_version"] == CHECKPOINT_SCHEMA
    assert result.checkpoint["legacy_coverage_unknown"]
    assert result.checkpoint["selected_query_ids"] == [BATCHES[1].query_id]


async def test_retry_queue_does_not_drop_unselected_failures_or_starve_rotation():
    first = await collect(
        Pages([RuntimeError("first"), RuntimeError("second")]),
        max_queries=2,
        max_records=4,
    )
    second = await collect(Pages([RuntimeError("again")]), checkpoint=first.checkpoint)
    assert len(second.checkpoint["retry_query_ids"]) == 2
    third = await collect(Pages(), checkpoint=second.checkpoint)
    assert third.checkpoint["retry_only_last_run"] is False


@pytest.mark.parametrize("provider", ["crossref", "europe_pmc"])
async def test_provider_page_carries_cursor_and_parameters_across_calls(
    monkeypatch, provider
):
    from src.literature.clients.crossref import CrossrefClient
    from src.literature.clients.europe_pmc import EuropePmcClient

    client = (
        CrossrefClient(mailto="test@example.org")
        if provider == "crossref"
        else EuropePmcClient()
    )
    requests = []

    async def get_json(http, url, *, params):
        requests.append(params)
        if provider == "crossref":
            return {
                "message": {
                    "items": [{"DOI": "1"}, {"DOI": "2"}] if len(requests) == 1 else [],
                    "next-cursor": "page2",
                }
            }
        return {
            "resultList": {
                "result": [{"id": "1"}, {"id": "2"}] if len(requests) == 1 else []
            },
            "nextCursorMark": "page2",
        }

    monkeypatch.setattr(client, "get_json", get_json)
    method = (
        client.search_works_page
        if provider == "crossref"
        else client.search_recent_page
    )
    arguments = {
        "query": "dengue",
        "since": START,
        "until": START + timedelta(days=2),
        "max_records": 2,
    }
    first = await method(**arguments)
    second = await method(**arguments, cursor=first.next_cursor)
    token = "cursor" if provider == "crossref" else "cursorMark"
    assert first.next_cursor == "page2"
    assert second.next_cursor is None
    assert requests[1] == {**requests[0], token: "page2"}


@pytest.mark.parametrize("provider", ["crossref", "europe_pmc"])
async def test_malformed_provider_page_cannot_advance_checkpoint(monkeypatch, provider):
    from src.literature.clients.crossref import CrossrefClient
    from src.literature.clients.europe_pmc import EuropePmcClient

    client = (
        CrossrefClient(mailto="test@example.org")
        if provider == "crossref"
        else EuropePmcClient()
    )

    async def get_json(*args, **kwargs):
        return {"error": "invalid query"}

    monkeypatch.setattr(client, "get_json", get_json)
    method = (
        client.search_works_page
        if provider == "crossref"
        else client.search_recent_page
    )
    with pytest.raises(ValueError, match="Invalid"):
        await method(query="dengue", since=START, until=START, max_records=2)


async def test_pubmed_page_uses_bounded_offset_and_requires_complete_summaries():
    import httpx

    from src.literature.clients.pubmed import PubMedClient

    offsets = []
    missing = False

    def handler(request):
        if request.url.path.endswith("esearch.fcgi"):
            offset = int(request.url.params["retstart"])
            offsets.append(offset)
            ids = [str(i) for i in range(offset, min(offset + 2, 3))]
            return httpx.Response(
                200, json={"esearchresult": {"count": "3", "idlist": ids}}
            )
        ids = request.url.params["id"].split(",")
        return httpx.Response(
            200,
            json={
                "result": {
                    "uids": ids,
                    **({} if missing else {i: {"uid": i} for i in ids}),
                }
            },
        )

    client = PubMedClient(
        contact_email="test@example.org",
        min_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    )
    arguments = {"query": "dengue", "since": START, "until": START, "max_records": 2}
    first = await client.search_recent_page(**arguments)
    second = await client.search_recent_page(**arguments, cursor=first.next_cursor)
    assert offsets == [0, 2]
    assert first.next_cursor == 2
    assert second.next_cursor is None
    assert [row["uid"] for row in first.records + second.records] == ["0", "1", "2"]
    missing = True
    with pytest.raises(ValueError, match="incomplete"):
        await client.search_recent_page(**arguments)


async def test_crossref_cursor_request_omits_forbidden_publication_sort(monkeypatch):
    from src.literature.clients.crossref import CrossrefClient

    async def get_json(http, url, *, params):
        assert params["cursor"] == "*"
        assert "sort" not in params
        assert "order" not in params
        return {"message": {"items": []}}

    client = CrossrefClient(mailto="test@example.org")
    monkeypatch.setattr(client, "get_json", get_json)
    await client.search_works_page(
        query="dengue", since=START, until=START, max_records=1
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"count": "0"},
        {"idlist": []},
        {"count": "bad", "idlist": []},
        {"count": "0", "idlist": [], "errorlist": {"fieldsnotfound": ["INVALID"]}},
        {"count": "3", "idlist": ["1", "2", "3"]},
        {"count": "1", "idlist": ["not-an-id"]},
    ],
)
async def test_pubmed_invalid_or_oversized_search_response_fails_closed(result):
    import httpx

    from src.literature.clients.pubmed import PubMedClient

    def handler(request):
        assert request.url.path.endswith("esearch.fcgi")
        return httpx.Response(200, json={"esearchresult": result})

    client = PubMedClient(
        contact_email="test@example.org",
        min_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="Invalid PubMed"):
        await client.search_recent_page(
            query="dengue", since=START, until=START, max_records=2
        )


async def test_page_adapter_overreturn_does_not_skip_unpersisted_records():
    result = await collect(
        Pages([SearchPage([{}, {}, {}], "next")]), batches=BATCHES[:1]
    )
    assert result.crossref_records == []
    assert provider_state(result)["cursor"] is None
    assert "completed_through" not in provider_state(result)
    assert result.checkpoint["query_errors"]


async def test_larger_budget_preserves_existing_page_size_and_cursor():
    client = Pages([SearchPage([{}, {}], "next"), SearchPage([])])
    first = await collect(client, batches=BATCHES[:1])
    await collect(
        client,
        batches=BATCHES[:1],
        checkpoint=first.checkpoint,
        records_per_query=10,
        max_records=10,
    )
    assert client.calls[1]["max_records"] == 2
    assert client.calls[1]["cursor"] == "next"


async def test_query_edit_resets_only_changed_query_state():
    from dataclasses import replace

    first = await collect(Pages(), max_queries=2, max_records=4)
    changed = [replace(BATCHES[0], crossref_query="different query"), BATCHES[1]]
    result = await collect(
        Pages(), batches=changed, checkpoint=first.checkpoint, max_queries=0
    )
    states = result.checkpoint["query_states"]
    assert states[BATCHES[0].query_id]["providers"] == {}
    assert (
        states[BATCHES[1].query_id]
        == first.checkpoint["query_states"][BATCHES[1].query_id]
    )


async def test_unknown_checkpoint_schema_does_not_reuse_provider_cursors():
    first = await collect(Pages([SearchPage([{}, {}], "cursor")]), batches=BATCHES[:1])
    first.checkpoint["schema_version"] = "unsupported"
    client = Pages()
    await collect(
        client,
        batches=BATCHES[:1],
        checkpoint=first.checkpoint,
        since=START + timedelta(days=5),
        until=START + timedelta(days=6),
    )
    assert client.calls[0]["since"] == START + timedelta(days=5)
    assert client.calls[0]["cursor"] is None


@pytest.mark.parametrize("provider", ["crossref", "europe_pmc"])
async def test_missing_cursor_terminal_contract_is_provider_specific(
    monkeypatch, provider
):
    from src.literature.clients.crossref import CrossrefClient
    from src.literature.clients.europe_pmc import EuropePmcClient

    client = (
        CrossrefClient(mailto="test@example.org")
        if provider == "crossref"
        else EuropePmcClient()
    )

    async def get_json(*args, **kwargs):
        return (
            {"message": {"items": [{}]}}
            if provider == "crossref"
            else {"resultList": {"result": [{}]}}
        )

    monkeypatch.setattr(client, "get_json", get_json)
    method = (
        client.search_works_page
        if provider == "crossref"
        else client.search_recent_page
    )
    if provider == "crossref":
        result = await method(query="dengue", since=START, until=START, max_records=1)
        assert result.next_cursor is None
    else:
        with pytest.raises(ValueError, match="cursor"):
            await method(query="dengue", since=START, until=START, max_records=1)


async def test_explicit_cursor_rejection_replays_same_window_without_advancing():
    import httpx

    request = httpx.Request("GET", "https://example.org/works")
    response = httpx.Response(400, request=request, text="Invalid cursor")
    error = httpx.HTTPStatusError("Invalid cursor", request=request, response=response)
    client = Pages([SearchPage([{}, {}], "old"), error, SearchPage([])])
    first = await collect(client, batches=BATCHES[:1])
    second = await collect(
        client,
        batches=BATCHES[:1],
        checkpoint=first.checkpoint,
        since=START + timedelta(days=4),
        until=START + timedelta(days=5),
    )
    assert provider_state(second)["cursor"] is None
    assert "completed_through" not in provider_state(second)
    await collect(
        client,
        batches=BATCHES[:1],
        checkpoint=second.checkpoint,
        since=START + timedelta(days=4),
        until=START + timedelta(days=5),
    )
    assert client.calls[2] == client.calls[0]
