"""Rate-limited Unpaywall lookups for legal open-access locations."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from ..normalization import normalize_doi
from .base import AsyncRequestLimiter, LiteratureHttpClient


class UnpaywallClient(LiteratureHttpClient):
    BASE_URL = "https://api.unpaywall.org"

    def __init__(self, *, email: str, timeout_seconds: float = 30.0, retries: int = 3) -> None:
        contact = email.strip() or "research-radar@globalinfectiousdisease.com"
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.email = contact

    async def enrich_by_dois(
        self,
        dois: list[str],
        *,
        concurrency: int = 3,
        min_interval_seconds: float = 0.05,
    ) -> dict[str, dict[str, Any]]:
        normalized = list(dict.fromkeys(doi for value in dois if (doi := normalize_doi(value))))
        if not normalized:
            return {}

        semaphore = asyncio.Semaphore(max(1, concurrency))
        limiter = AsyncRequestLimiter(min_interval_seconds)
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True) as client:

            async def fetch(doi: str) -> tuple[str, dict[str, Any] | None]:
                async with semaphore:
                    await limiter.wait()
                    try:
                        payload = await self.get_json(
                            client,
                            f"/v2/{quote(doi, safe='')}",
                            params={"email": self.email},
                        )
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            return doi, None
                        raise
                    response_doi = normalize_doi(payload.get("doi"))
                    return doi, payload if response_doi in {None, doi} else None

            rows = await asyncio.gather(*(fetch(doi) for doi in normalized))
        return {doi: payload for doi, payload in rows if payload is not None}


__all__ = ["UnpaywallClient"]
