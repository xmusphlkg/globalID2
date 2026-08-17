"""Crossref incremental metadata client using index dates and cursor paging."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
from typing import Any

import httpx

from .base import LiteratureHttpClient


@dataclass(frozen=True, slots=True)
class CrossrefIncrementalResult:
    """Bounded Crossref records plus enough checkpoint data to resume safely."""

    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


@dataclass(slots=True)
class _JournalStream:
    """Small look-ahead buffer for one journal in the global index-date merge."""

    journal: dict[str, str]
    cursor: str = "*"
    buffer: deque[dict[str, Any]] = field(default_factory=deque)
    seen_record_ids: set[str] = field(default_factory=set)
    total_results: int = 0
    raw_records_seen: int = 0
    records_prefetched: int = 0
    records_examined: int = 0
    records_returned: int = 0
    skipped_resume_records: int = 0
    duplicate_records: int = 0
    pages_fetched: int = 0
    exhausted: bool = False


def _crossref_datetime(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _indexed_at(item: dict[str, Any]) -> datetime | None:
    value = (item.get("indexed") or {}).get("date-time")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stable_record_id(item: dict[str, Any]) -> str:
    doi = str(item.get("DOI") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    identity = {
        "title": item.get("title"),
        "published": item.get("published"),
        "indexed": item.get("indexed"),
        "type": item.get("type"),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    return f"fallback:{hashlib.sha256(encoded).hexdigest()}"


def _record_sort_key(item: dict[str, Any]) -> tuple[datetime, str]:
    return (
        _indexed_at(item) or datetime.min.replace(tzinfo=timezone.utc),
        _stable_record_id(item),
    )


def _resume_boundary(resume_after: dict[str, Any] | None) -> tuple[datetime | None, frozenset[str]]:
    if not isinstance(resume_after, dict):
        return None, frozenset()
    value = resume_after.get("indexed_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    record_ids = frozenset(str(value) for value in resume_after.get("record_ids") or [] if value)
    return parsed, record_ids


def _was_consumed_at_boundary(
    item: dict[str, Any],
    *,
    indexed_at: datetime | None,
    record_ids: frozenset[str],
) -> bool:
    if indexed_at is None:
        return False
    item_indexed_at = _indexed_at(item)
    if item_indexed_at is None:
        return False
    return item_indexed_at < indexed_at or (
        item_indexed_at == indexed_at and _stable_record_id(item) in record_ids
    )


class CrossrefClient(LiteratureHttpClient):
    BASE_URL = "https://api.crossref.org"

    def __init__(self, *, mailto: str, timeout_seconds: float = 30.0, retries: int = 3) -> None:
        contact = mailto.strip() or "research-radar@globalinfectiousdisease.com"
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.mailto = contact

    async def fetch_incremental(
        self,
        *,
        journals: list[dict[str, str]],
        since: datetime,
        until: datetime,
        max_records: int,
        concurrency: int = 4,
        resume_after: dict[str, Any] | None = None,
    ) -> CrossrefIncrementalResult:
        """Return the globally earliest bounded records across all journals.

        A journal endpoint is an independently sorted stream.  Keep only a
        proportional page of look-ahead from each stream and perform a k-way
        merge, instead of reading ``max_records`` from every journal and then
        discarding almost all of them.  At any point every non-exhausted stream
        has a head in the heap, so advancing the global timestamp remains safe.
        """
        semaphore = asyncio.Semaphore(max(1, concurrency))
        journal_count = max(1, len(journals))
        page_size = max(1, min(100, math.ceil(max_records / journal_count)))
        resume_indexed_at, resume_record_ids = _resume_boundary(resume_after)
        streams = [_JournalStream(journal=journal) for journal in journals]

        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:
            async def fill(stream: _JournalStream) -> None:
                async with semaphore:
                    await self._fill_journal_stream(
                        client,
                        stream=stream,
                        since=since,
                        until=until,
                        rows=page_size,
                        resume_indexed_at=resume_indexed_at,
                        resume_record_ids=resume_record_ids,
                    )

            await asyncio.gather(*(fill(stream) for stream in streams))
            heap: list[tuple[datetime, str, int, dict[str, Any]]] = []
            for index, stream in enumerate(streams):
                if stream.buffer:
                    item = stream.buffer.popleft()
                    indexed_at, stable_id = _record_sort_key(item)
                    heapq.heappush(heap, (indexed_at, stable_id, index, item))

            records: list[dict[str, Any]] = []
            returned_ids: set[str] = set()
            while heap and len(records) < max_records:
                _indexed, stable_id, stream_index, item = heapq.heappop(heap)
                stream = streams[stream_index]
                stream.records_examined += 1
                if stable_id not in returned_ids:
                    returned_ids.add(stable_id)
                    records.append(item)
                    stream.records_returned += 1
                else:
                    stream.duplicate_records += 1

                if len(records) >= max_records:
                    break
                if not stream.buffer and not stream.exhausted:
                    active_streams = sum(
                        int(bool(other.buffer) or not other.exhausted)
                        for other in streams
                    )
                    refill_rows = max(
                        1,
                        min(
                            page_size,
                            math.ceil((max_records - len(records)) / max(1, active_streams)),
                        ),
                    )
                    async with semaphore:
                        await self._fill_journal_stream(
                            client,
                            stream=stream,
                            since=since,
                            until=until,
                            rows=refill_rows,
                            resume_indexed_at=resume_indexed_at,
                            resume_record_ids=resume_record_ids,
                        )
                if stream.buffer:
                    next_item = stream.buffer.popleft()
                    next_indexed_at, next_stable_id = _record_sort_key(next_item)
                    heapq.heappush(
                        heap,
                        (next_indexed_at, next_stable_id, stream_index, next_item),
                    )

        truncated = bool(heap) or any(stream.buffer or not stream.exhausted for stream in streams)
        through = max((value for item in records if (value := _indexed_at(item)) is not None), default=None)
        through_value = through if through and truncated else until
        boundary_record_ids: list[str] = []
        if truncated and through is not None:
            consumed = set(resume_record_ids) if resume_indexed_at == through else set()
            consumed.update(
                _stable_record_id(item)
                for item in records
                if _indexed_at(item) == through
            )
            boundary_record_ids = sorted(consumed)
        next_resume_after = (
            {
                "indexed_at": through.isoformat(),
                "record_ids": boundary_record_ids,
            }
            if truncated and through is not None
            else None
        )
        records_prefetched = sum(stream.records_prefetched for stream in streams)
        records_examined = sum(stream.records_examined for stream in streams)
        lookahead_records = max(0, records_prefetched - records_examined)
        remaining_index_span_seconds = (
            max(0, int((until - through_value).total_seconds())) if truncated else 0
        )
        journal_checkpoints = [
            {
                "issn": stream.journal.get("issn"),
                "title": stream.journal.get("title") or stream.journal.get("name"),
                "records": stream.records_returned,
                "records_prefetched": stream.records_prefetched,
                "records_examined": stream.records_examined,
                "lookahead_records": max(
                    0,
                    stream.records_prefetched - stream.records_examined,
                ),
                "raw_records_seen": stream.raw_records_seen,
                "skipped_resume_records": stream.skipped_resume_records,
                "duplicate_records": stream.duplicate_records,
                "total_results": stream.total_results,
                "pages_fetched": stream.pages_fetched,
                "exhausted": stream.exhausted,
                "next_cursor": None if stream.exhausted else stream.cursor,
            }
            for stream in streams
        ]
        checkpoint = {
            "strategy": "index-date-kway-stable-boundary-v3",
            "from_indexed_at": since.isoformat(),
            "requested_through_indexed_at": until.isoformat(),
            "through_indexed_at": through_value.isoformat(),
            "next_from_indexed_at": through_value.isoformat() if truncated else None,
            "resume_after": next_resume_after,
            "truncated": truncated,
            "max_records": max_records,
            "page_size": page_size,
            "records_seen": records_prefetched,
            "records_prefetched": records_prefetched,
            "records_examined": records_examined,
            "lookahead_records": lookahead_records,
            "records_returned": len(records),
            "fetch_efficiency_ratio": round(
                len(records) / max(1, records_prefetched),
                6,
            ),
            "duplicate_records": sum(stream.duplicate_records for stream in streams),
            "resume_records_skipped": sum(
                stream.skipped_resume_records for stream in streams
            ),
            "pages_fetched": sum(stream.pages_fetched for stream in streams),
            "catch_up_required": truncated,
            "remaining_index_span_seconds": remaining_index_span_seconds,
            "journal_count": len(journal_checkpoints),
            "journals": journal_checkpoints,
            "sort": "indexed",
            "order": "asc",
        }
        return CrossrefIncrementalResult(records=records, checkpoint=checkpoint)

    async def _fill_journal_stream(
        self,
        client: httpx.AsyncClient,
        *,
        stream: _JournalStream,
        since: datetime,
        until: datetime,
        rows: int,
        resume_indexed_at: datetime | None,
        resume_record_ids: frozenset[str],
    ) -> None:
        """Fill an empty stream buffer, scanning resume-only pages if needed."""

        while not stream.buffer and not stream.exhausted:
            cursor = stream.cursor
            payload = await self.get_json(
                client,
                f"/journals/{stream.journal['issn']}/works",
                params={
                    "filter": (
                        f"from-index-date:{_crossref_datetime(since)},"
                        f"until-index-date:{_crossref_datetime(until)},type:journal-article"
                    ),
                    "cursor": cursor,
                    "rows": rows,
                    "sort": "indexed",
                    "order": "asc",
                    "mailto": self.mailto,
                },
            )
            stream.pages_fetched += 1
            message = payload.get("message") or {}
            try:
                stream.total_results = int(message.get("total-results") or stream.total_results)
            except (TypeError, ValueError):
                pass
            items = [item for item in message.get("items") or [] if isinstance(item, dict)]
            stream.raw_records_seen += len(items)
            eligible: list[dict[str, Any]] = []
            for item in items:
                if _was_consumed_at_boundary(
                    item,
                    indexed_at=resume_indexed_at,
                    record_ids=resume_record_ids,
                ):
                    stream.skipped_resume_records += 1
                    continue
                stable_id = _stable_record_id(item)
                if stable_id in stream.seen_record_ids:
                    stream.duplicate_records += 1
                    continue
                stream.seen_record_ids.add(stable_id)
                eligible.append(item)
            eligible.sort(key=_record_sort_key)
            stream.buffer.extend(eligible)
            stream.records_prefetched += len(eligible)

            next_cursor = str(message.get("next-cursor") or "")
            stream.exhausted = bool(
                not items
                or not next_cursor
                or next_cursor == cursor
                or len(items) < rows
                or (
                    stream.total_results
                    and stream.raw_records_seen >= stream.total_results
                )
            )
            if not stream.exhausted:
                stream.cursor = next_cursor

    async def search_works(
        self,
        *,
        query: str,
        since: datetime,
        until: datetime,
        max_records: int,
    ) -> list[dict[str, Any]]:
        """Run a bounded, publication-date targeted discovery query."""
        records: list[dict[str, Any]] = []
        cursor = "*"
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:
            while len(records) < max_records:
                rows = min(100, max_records - len(records))
                payload = await self.get_json(
                    client,
                    "/works",
                    params={
                        "query.bibliographic": query,
                        "filter": (
                            f"from-pub-date:{since.date().isoformat()},"
                            f"until-pub-date:{until.date().isoformat()},type:journal-article"
                        ),
                        "cursor": cursor,
                        "rows": rows,
                        "mailto": self.mailto,
                    },
                )
                message = payload.get("message") or {}
                items = [item for item in message.get("items") or [] if isinstance(item, dict)]
                records.extend(items)
                next_cursor = str(message.get("next-cursor") or "")
                if not items or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
        return records[:max_records]


__all__ = ["CrossrefClient", "CrossrefIncrementalResult"]
