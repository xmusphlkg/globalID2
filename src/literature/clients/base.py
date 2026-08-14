"""Shared resilient JSON HTTP client for public literature APIs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class LiteratureHttpClient:
    def __init__(self, *, user_agent: str, timeout_seconds: float = 30.0, retries: int = 3) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    async def get_json(self, client: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Literature API returned a non-object JSON payload")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    await asyncio.sleep(0.5 * (2**attempt))
        assert last_error is not None
        raise last_error


__all__ = ["LiteratureHttpClient"]
