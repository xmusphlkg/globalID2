"""Credential-gated, metadata-only publisher discovery clients.

These clients deliberately use only documented search/metadata endpoints.  They
never request article HTML, PDFs, full text, or text-mining content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

import httpx

from .base import LiteratureHttpClient, ProviderNotConfiguredError


@dataclass(frozen=True, slots=True)
class PublisherApiResult:
    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


def _utc_date(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).date().isoformat()


def _checkpoint_range(
    checkpoint: dict[str, Any] | None,
    *,
    since: datetime,
    until: datetime,
) -> tuple[datetime, datetime]:
    if not isinstance(checkpoint, dict) or not checkpoint.get("truncated"):
        return since, until
    try:
        resumed_since = datetime.fromisoformat(str(checkpoint["from_date"]).replace("Z", "+00:00"))
        resumed_until = datetime.fromisoformat(str(checkpoint["through_date"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return since, until
    return resumed_since, resumed_until


def _checkpoint_offset(checkpoint: dict[str, Any] | None, *, default: int, minimum: int) -> int:
    if not isinstance(checkpoint, dict) or not checkpoint.get("truncated"):
        return default
    try:
        return max(minimum, int(checkpoint.get("next_start") or default))
    except (TypeError, ValueError):
        return default


class SpringerNatureClient(LiteratureHttpClient):
    """Search the Springer Nature Metadata API with strict response bounds."""

    BASE_URL = "https://api.springernature.com"
    MAX_PAGE_SIZE = 100
    MAX_RECORDS = 500

    def __init__(
        self,
        *,
        api_key: str,
        contact_email: str,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact_email.strip()})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.api_key = api_key.strip()
        self.transport = transport

    async def search_recent(
        self,
        *,
        query: str,
        since: datetime,
        until: datetime,
        max_records: int,
        checkpoint: dict[str, Any] | None = None,
    ) -> PublisherApiResult:
        if not self.api_key:
            raise ProviderNotConfiguredError("Springer Nature API credential is not configured")
        limit = min(self.MAX_RECORDS, max(0, max_records))
        if limit == 0:
            return PublisherApiResult([], {"provider": "springer-nature", "records_returned": 0})
        range_since, range_until = _checkpoint_range(checkpoint, since=since, until=until)
        terms = " ".join(str(query or "").split())[:1_000]
        date_query = f"onlinedatefrom:{_utc_date(range_since)} onlinedateto:{_utc_date(range_until)}"
        bounded_query = f"({terms}) {date_query}" if terms else date_query
        records: list[dict[str, Any]] = []
        pages = 0
        start = _checkpoint_offset(checkpoint, default=1, minimum=1)
        total: int | None = None
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            while len(records) < limit:
                page_size = min(self.MAX_PAGE_SIZE, limit - len(records))
                payload = await self.get_json(
                    client,
                    "/meta/v2/json",
                    params={
                        "q": bounded_query,
                        "api_key": self.api_key,
                        "p": page_size,
                        "s": start,
                    },
                )
                pages += 1
                if total is None:
                    try:
                        total = max(0, int(((payload.get("result") or [{}])[0]).get("total") or 0))
                    except (AttributeError, IndexError, TypeError, ValueError):
                        total = None
                rows = [row for row in payload.get("records") or [] if isinstance(row, dict)]
                consumed = rows[:page_size]
                records.extend(consumed)
                next_start = start + len(consumed)
                if len(consumed) < page_size or (total is not None and next_start > total):
                    start = next_start
                    break
                start = next_start
        truncated = bool(total is not None and start <= total)
        return PublisherApiResult(
            records=records[:limit],
            checkpoint={
                "provider": "springer-nature",
                "strategy": "bounded-offset-v1",
                "from_date": _utc_date(range_since),
                "through_date": _utc_date(range_until),
                "next_start": start,
                "pages_fetched": pages,
                "records_returned": min(len(records), limit),
                "records_total": total,
                "truncated": truncated,
                "max_records": limit,
            },
        )


class ElsevierClient(LiteratureHttpClient):
    """Search Scopus metadata without requesting abstracts or full text."""

    BASE_URL = "https://api.elsevier.com"
    MAX_PAGE_SIZE = 25
    MAX_RECORDS = 500
    MAX_PAGES = 20

    def __init__(
        self,
        *,
        api_key: str,
        contact_email: str,
        institutional_token: str = "",
        timeout_seconds: float = 30.0,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact_email.strip()})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.api_key = api_key.strip()
        self.institutional_token = institutional_token.strip()
        self.transport = transport

    async def search_recent(
        self,
        *,
        query: str,
        since: datetime,
        until: datetime,
        max_records: int,
        checkpoint: dict[str, Any] | None = None,
    ) -> PublisherApiResult:
        if not self.api_key:
            raise ProviderNotConfiguredError("Elsevier API credential is not configured")
        limit = min(self.MAX_RECORDS, max(0, max_records))
        if limit == 0:
            return PublisherApiResult([], {"provider": "elsevier", "records_returned": 0})
        range_since, range_until = _checkpoint_range(checkpoint, since=since, until=until)
        terms = " ".join(str(query or "").split())[:1_000] or "ALL(infectious disease)"
        records: list[dict[str, Any]] = []
        pages = 0
        start = _checkpoint_offset(checkpoint, default=0, minimum=0)
        total: int | None = None
        params_base = {
            "query": terms,
            "apiKey": self.api_key,
            "httpAccept": "application/json",
            "view": "STANDARD",
            # The API accepts an inclusive year range. Exact date filtering is
            # repeated locally below before records leave the client.
            "date": f"{range_since.year}-{range_until.year}",
        }
        if self.institutional_token:
            params_base["insttoken"] = self.institutional_token
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            page_limit = min(self.MAX_PAGES, max(1, math.ceil(limit / self.MAX_PAGE_SIZE) * 3))
            while len(records) < limit and pages < page_limit:
                page_size = min(self.MAX_PAGE_SIZE, limit - len(records))
                payload = await self.get_json(
                    client,
                    "/content/search/scopus",
                    params={**params_base, "start": start, "count": page_size},
                )
                pages += 1
                search = payload.get("search-results") if isinstance(payload.get("search-results"), dict) else {}
                if total is None:
                    try:
                        total = max(0, int(search.get("opensearch:totalResults") or 0))
                    except (TypeError, ValueError):
                        total = None
                rows = [row for row in search.get("entry") or [] if isinstance(row, dict)]
                for row in rows:
                    cover_date = str(row.get("prism:coverDate") or "")[:10]
                    if cover_date and not (_utc_date(range_since) <= cover_date <= _utc_date(range_until)):
                        continue
                    records.append(row)
                    if len(records) >= limit:
                        break
                next_start = start + len(rows)
                if len(rows) < page_size or (total is not None and next_start >= total):
                    start = next_start
                    break
                start = next_start
        return PublisherApiResult(
            records=records[:limit],
            checkpoint={
                "provider": "elsevier",
                "strategy": "bounded-offset-v1",
                "from_date": _utc_date(range_since),
                "through_date": _utc_date(range_until),
                "next_start": start,
                "pages_fetched": pages,
                "records_returned": min(len(records), limit),
                "records_total": total,
                "truncated": bool(total is not None and start < total),
                "max_records": limit,
            },
        )


__all__ = ["ElsevierClient", "PublisherApiResult", "SpringerNatureClient"]
