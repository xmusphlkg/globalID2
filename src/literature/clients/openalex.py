"""Batched OpenAlex metadata enrichment keyed by DOI."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..normalization import normalize_doi
from .base import AsyncRequestLimiter, LiteratureHttpClient


class OpenAlexClient(LiteratureHttpClient):
    BASE_URL = "https://api.openalex.org"
    MAX_FILTER_VALUES = 100

    def __init__(
        self,
        *,
        mailto: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
        retries: int = 3,
    ) -> None:
        contact = mailto.strip() or "research-radar@globalinfectiousdisease.com"
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.mailto = contact
        self.api_key = api_key.strip()

    async def enrich_by_dois(
        self,
        dois: list[str],
        *,
        batch_size: int = 100,
        concurrency: int = 2,
        min_interval_seconds: float = 0.05,
    ) -> dict[str, dict[str, Any]]:
        normalized = list(dict.fromkeys(doi for value in dois if (doi := normalize_doi(value))))
        size = min(self.MAX_FILTER_VALUES, max(1, batch_size))
        batches = [normalized[offset : offset + size] for offset in range(0, len(normalized), size)]
        if not batches:
            return {}

        semaphore = asyncio.Semaphore(max(1, concurrency))
        limiter = AsyncRequestLimiter(min_interval_seconds)
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:

            async def fetch(batch: list[str]) -> list[dict[str, Any]]:
                async with semaphore:
                    await limiter.wait()
                    params: dict[str, Any] = {
                        "filter": "doi:" + "|".join(f"https://doi.org/{doi}" for doi in batch),
                        "per_page": len(batch),
                        "select": (
                            "id,doi,ids,open_access,best_oa_location,"
                            "primary_topic,topics,keywords,concepts,authorships,"
                            "cited_by_count,referenced_works_count,referenced_works,related_works"
                        ),
                        "mailto": self.mailto,
                    }
                    if self.api_key:
                        params["api_key"] = self.api_key
                    payload = await self.get_json(client, "/works", params=params)
                    return [row for row in payload.get("results") or [] if isinstance(row, dict)]

            pages = await asyncio.gather(*(fetch(batch) for batch in batches))

        requested = set(normalized)
        result: dict[str, dict[str, Any]] = {}
        for row in (item for page in pages for item in page):
            ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
            doi = normalize_doi(row.get("doi") or ids.get("doi"))
            if doi and doi in requested:
                result[doi] = row
        return result


__all__ = ["OpenAlexClient"]
