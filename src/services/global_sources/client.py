"""Small, polite HTTP helper shared by the international authority adapters."""

from __future__ import annotations

import time
from typing import Any

import httpx

USER_AGENT = "GIDS Situation Room/2.0 (+https://globalinfectiousdisease.com/situation/methodology/)"
DEFAULT_TIMEOUT = 30.0
RETRY_STATUS = {429, 500, 502, 503, 504}


def fetch(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
    client: httpx.Client | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET ``url`` with bounded retries; the final error propagates to the caller."""

    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)

    def _get(active: httpx.Client) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                response = active.get(url, params=params, timeout=timeout)
                if response.status_code in RETRY_STATUS and attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                return response
            except httpx.HTTPError as error:  # pragma: no cover - network dependent
                last_error = error
                if attempt >= retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
        assert last_error is not None  # pragma: no cover
        raise last_error

    if client is not None:
        return _get(client)
    with httpx.Client(headers=request_headers, follow_redirects=True) as owned:
        return _get(owned)
