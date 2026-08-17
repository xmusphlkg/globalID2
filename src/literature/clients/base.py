"""Shared resilient JSON HTTP client for public literature APIs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class AsyncRequestLimiter:
    """Space request starts across concurrent workers without blocking the loop."""

    def __init__(self, min_interval_seconds: float = 0.0) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_request_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = loop.time() + self.min_interval_seconds


class LiteratureHttpClient:
    def __init__(self, *, user_agent: str, timeout_seconds: float = 30.0, retries: int = 3) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    async def get_json(self, client: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = max(1, self.retries)
        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Literature API returned a non-object JSON payload")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                status_code = response.status_code if response is not None else None
                retryable = (
                    isinstance(exc, (httpx.RequestError, ValueError))
                    or status_code in _RETRYABLE_STATUS_CODES
                )
                if not retryable or attempt + 1 >= attempts:
                    raise
                retry_after = response.headers.get("Retry-After") if response is not None else None
                try:
                    server_delay = float(retry_after) if retry_after is not None else 0.0
                except ValueError:
                    server_delay = 0.0
                await asyncio.sleep(min(60.0, max(server_delay, 0.5 * (2**attempt))))
        assert last_error is not None
        raise last_error


__all__ = ["AsyncRequestLimiter", "LiteratureHttpClient"]
