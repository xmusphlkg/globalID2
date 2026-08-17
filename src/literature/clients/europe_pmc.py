"""Europe PMC DOI enrichment for biomedical identifiers and OA metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .base import LiteratureHttpClient


class EuropePmcClient(LiteratureHttpClient):
    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(self, *, timeout_seconds: float = 30.0, retries: int = 3) -> None:
        super().__init__(
            user_agent="GIDS-Research-Radar/1.0",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

    async def enrich_by_dois(self, dois: list[str], *, batch_size: int = 20) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:
            for offset in range(0, len(dois), batch_size):
                batch = [doi for doi in dois[offset : offset + batch_size] if doi]
                if not batch:
                    continue
                query = " OR ".join(f'DOI:"{doi}"' for doi in batch)
                payload = await self.get_json(
                    client,
                    "/search",
                    params={"query": query, "format": "json", "pageSize": min(100, len(batch) * 2), "resultType": "core"},
                )
                rows = ((payload.get("resultList") or {}).get("result") or [])
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    doi = str(row.get("doi") or "").strip().lower()
                    if doi:
                        result[doi] = row
        return result

    async def search_recent(
        self,
        *,
        query: str,
        since: datetime,
        until: datetime,
        max_records: int,
    ) -> list[dict[str, Any]]:
        """Search recent biomedical records using Europe PMC query syntax."""
        bounded_query = (
            f"({query}) AND FIRST_PDATE:[{since.date().isoformat()} TO {until.date().isoformat()}]"
        )
        records: list[dict[str, Any]] = []
        cursor = "*"
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:
            while len(records) < max_records:
                payload = await self.get_json(
                    client,
                    "/search",
                    params={
                        "query": bounded_query,
                        "format": "json",
                        "pageSize": min(100, max_records - len(records)),
                        "resultType": "core",
                        "cursorMark": cursor,
                    },
                )
                rows = [
                    row
                    for row in ((payload.get("resultList") or {}).get("result") or [])
                    if isinstance(row, dict)
                ]
                records.extend(rows)
                next_cursor = str(payload.get("nextCursorMark") or "")
                if not rows or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
        return records[:max_records]


__all__ = ["EuropePmcClient"]
