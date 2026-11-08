"""A failed journal must not let a healthy journal hide an unread index window."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.literature.clients.crossref import CrossrefClient
from src.literature.health_snapshot import _source_run_outcome

SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)
UNTIL = SINCE + timedelta(days=1)


def _record(doi: str, minute: int) -> dict:
    return {
        "DOI": doi,
        "indexed": {"date-time": (SINCE + timedelta(minutes=minute)).isoformat()},
    }


class _JournalClient(CrossrefClient):
    def __init__(self, records, failures=()):
        super().__init__(mailto="tests@example.org")
        self.records = records
        self.failures = set(failures)
        self.requests = []

    async def get_json(self, _client, path, *, params):
        issn = path.split("/")[2]
        self.requests.append((issn, dict(params)))
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
        if (issn, offset) in self.failures:
            raise httpx.ConnectError("journal unavailable")
        start = datetime.fromisoformat(
            params["filter"].split("from-index-date:", 1)[1].split(",", 1)[0]
        ).replace(tzinfo=timezone.utc)
        records = [
            item for item in self.records[issn]
            if datetime.fromisoformat(item["indexed"]["date-time"]) >= start
        ]
        items = records[offset:offset + int(params["rows"])]
        return {"message": {
            "items": items,
            "next-cursor": str(offset + len(items)),
            "total-results": len(records),
        }}


@pytest.mark.parametrize("failed_page", [0, 2])
async def test_partial_failure_holds_watermark_and_recovery_reads_missing_journal(failed_page):
    records = {
        "1111-1111": [_record(f"10.1000/a{i}", i) for i in range(1, 4)],
        "2222-2222": [_record("10.1000/b", 20)],
    }
    client = _JournalClient(records, [("1111-1111", failed_page)])
    journals = [{"issn": issn} for issn in records]
    result = await client.fetch_incremental(
        journals=journals, since=SINCE, until=UNTIL, max_records=4,
    )

    assert "10.1000/b" in {item["DOI"] for item in result.records}
    assert result.checkpoint["source_errors"] == 1
    assert result.checkpoint["watermark_held_for_errors"] is True
    assert result.checkpoint["truncated"] is True
    assert result.checkpoint["catch_up_required"] is True
    assert result.checkpoint["through_indexed_at"] == SINCE.isoformat()
    assert result.checkpoint["next_from_indexed_at"] == SINCE.isoformat()
    assert result.checkpoint["resume_after"] == {"indexed_at": SINCE.isoformat(), "record_ids": []}
    failed = result.checkpoint["journals"][0]
    assert failed["exhausted"] is False
    assert failed["error"] == "ConnectError"

    client.failures.clear()
    recovered = await client.fetch_incremental(
        journals=journals,
        since=datetime.fromisoformat(result.checkpoint["next_from_indexed_at"]),
        until=UNTIL,
        max_records=10,
        resume_after=result.checkpoint["resume_after"],
    )
    assert {item["DOI"] for item in recovered.records} == {
        item["DOI"] for rows in records.values() for item in rows
    }
    assert recovered.checkpoint["source_errors"] == 0
    assert recovered.checkpoint["truncated"] is False
    assert recovered.checkpoint["through_indexed_at"] == UNTIL.isoformat()


async def test_failure_keeps_previously_committed_same_timestamp_boundary():
    records = {
        "1111-1111": [_record("10.1000/a1", 0), _record("10.1000/a2", 0)],
        "2222-2222": [_record("10.1000/b", 0)],
    }
    client = _JournalClient(records, [("2222-2222", 0)])
    journals = [{"issn": issn} for issn in records]
    boundary = {"indexed_at": SINCE.isoformat(), "record_ids": ["doi:10.1000/a1"]}
    failed = await client.fetch_incremental(
        journals=journals, since=SINCE, until=UNTIL, max_records=10, resume_after=boundary,
    )
    assert [item["DOI"] for item in failed.records] == ["10.1000/a2"]
    assert failed.checkpoint["resume_after"] == boundary

    client.failures.clear()
    recovered = await client.fetch_incremental(
        journals=journals, since=SINCE, until=UNTIL, max_records=10,
        resume_after=failed.checkpoint["resume_after"],
    )
    assert {item["DOI"] for item in recovered.records} == {"10.1000/a2", "10.1000/b"}
    assert recovered.checkpoint["truncated"] is False


async def test_all_journals_failing_does_not_claim_the_window_is_exhausted():
    client = _JournalClient({"a": [], "b": []}, [("a", 0), ("b", 0)])
    result = await client.fetch_incremental(
        journals=[{"issn": "a"}, {"issn": "b"}], since=SINCE, until=UNTIL, max_records=10,
    )
    assert result.records == []
    assert result.checkpoint["source_errors"] == 2
    assert result.checkpoint["truncated"] is True
    assert result.checkpoint["through_indexed_at"] == SINCE.isoformat()


async def test_journal_cursor_refills_keep_the_initial_page_parameters():
    client = _JournalClient({
        "a": [_record(f"10.1000/a{index}", index) for index in range(4)],
        "b": [_record("10.1000/b", 20)],
    })
    result = await client.fetch_incremental(
        journals=[{"issn": "a"}, {"issn": "b"}], since=SINCE, until=UNTIL, max_records=3,
    )
    journal_requests = [params for issn, params in client.requests if issn == "a"]
    assert len(journal_requests) == 2
    assert [params["rows"] for params in journal_requests] == [2, 2]
    assert {
        key: value for key, value in journal_requests[0].items() if key != "cursor"
    } == {
        key: value for key, value in journal_requests[1].items() if key != "cursor"
    }
    assert len(result.records) == 3


async def test_cancellation_drains_all_initial_journal_requests():
    started = asyncio.Event()
    cleaned = set()

    class SlowClient(CrossrefClient):
        async def get_json(self, _client, path, *, params):
            try:
                if path.endswith("/b/works"):
                    started.set()
                await asyncio.Event().wait()
            finally:
                # Include asynchronous cleanup to catch a gather that returns
                # before cancelled sibling coroutines have actually finished.
                await asyncio.sleep(0)
                cleaned.add(path)

    request = asyncio.create_task(SlowClient(mailto="tests@example.org").fetch_incremental(
        journals=[{"issn": "a"}, {"issn": "b"}], since=SINCE, until=UNTIL, max_records=10,
    ))
    await asyncio.wait_for(started.wait(), timeout=2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert cleaned == {"/journals/a/works", "/journals/b/works"}


@pytest.mark.parametrize(("reported_errors", "checkpoint_errors"), [(2, 2), (0, 10)])
def test_crossref_health_reports_both_new_and_legacy_partial_failures(reported_errors, checkpoint_errors):
    outcome = _source_run_outcome(
        "crossref",
        completed_sources={"crossref"},
        counts={
            "crossref_fetched": 100,
            "source_records_seen": 100,
            "source_records_returned": 100,
            "crossref_source_errors": reported_errors,
        },
        checkpoint={"source_errors": checkpoint_errors},
    )
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "provider_errors"
    assert outcome["error_count"] == max(reported_errors, checkpoint_errors)
