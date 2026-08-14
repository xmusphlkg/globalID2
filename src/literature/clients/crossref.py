"""Crossref incremental metadata client using index dates and cursor paging."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import LiteratureHttpClient


@dataclass(frozen=True, slots=True)
class CrossrefIncrementalResult:
    """Bounded Crossref records plus enough checkpoint data to resume safely."""

    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


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
    ) -> CrossrefIncrementalResult:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        per_journal = max(1, max_records)
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:
            async def fetch(journal: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
                async with semaphore:
                    records, state = await self._fetch_journal(
                        client,
                        issn=journal["issn"],
                        since=since,
                        until=until,
                        limit=per_journal,
                    )
                    return journal, records, state

            pages = await asyncio.gather(*(fetch(journal) for journal in journals))
        deduplicated: dict[str, dict[str, Any]] = {}
        journal_checkpoints: list[dict[str, Any]] = []
        for journal, page, state in pages:
            journal_checkpoints.append({
                "issn": journal.get("issn"),
                "title": journal.get("title") or journal.get("name"),
                **state,
            })
            for item in page:
                key = str(item.get("DOI") or "").strip().lower()
                if not key:
                    key = f"{item.get('title')}|{item.get('published')}"
                deduplicated[key] = item
        all_records = list(deduplicated.values())
        floor = datetime.min.replace(tzinfo=timezone.utc)
        all_records.sort(key=lambda item: _indexed_at(item) or floor)
        records = all_records[:max_records]
        truncated = len(all_records) > len(records) or any(
            not checkpoint.get("exhausted", True) for checkpoint in journal_checkpoints
        )
        through = max((value for item in records if (value := _indexed_at(item)) is not None), default=None)
        through_value = through if through and truncated else until
        checkpoint = {
            "strategy": "index-date-cursor",
            "from_indexed_at": since.isoformat(),
            "requested_through_indexed_at": until.isoformat(),
            "through_indexed_at": through_value.isoformat(),
            "next_from_indexed_at": through_value.isoformat() if truncated else None,
            "truncated": truncated,
            "max_records": max_records,
            "records_seen": len(all_records),
            "records_returned": len(records),
            "journal_count": len(journal_checkpoints),
            "journals": journal_checkpoints,
            "sort": "indexed",
            "order": "asc",
        }
        return CrossrefIncrementalResult(records=records, checkpoint=checkpoint)

    async def _fetch_journal(
        self,
        client: httpx.AsyncClient,
        *,
        issn: str,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor = "*"
        total_results = 0
        exhausted = True
        while len(records) < limit:
            rows = min(100, limit - len(records))
            payload = await self.get_json(
                client,
                f"/journals/{issn}/works",
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
            message = payload.get("message") or {}
            try:
                total_results = int(message.get("total-results") or total_results)
            except (TypeError, ValueError):
                total_results = total_results
            items = [item for item in message.get("items") or [] if isinstance(item, dict)]
            records.extend(items)
            next_cursor = str(message.get("next-cursor") or "")
            if not items or not next_cursor or next_cursor == cursor:
                exhausted = True
                break
            if len(items) < rows:
                exhausted = True
                break
            if total_results and len(records) >= total_results:
                exhausted = True
                break
            cursor = next_cursor
        else:
            exhausted = False
        return records[:limit], {
            "records": len(records[:limit]),
            "total_results": total_results,
            "exhausted": exhausted,
            "next_cursor": None if exhausted else cursor,
        }

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
