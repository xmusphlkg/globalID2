"""Small, polite HTTP helper shared by the international authority adapters.

Besides bounded retries it protects both sides of a flaky link:

* each host serves at most ``HOST_CONCURRENCY`` requests at a time, however many
  threads ask;
* a host that cannot be *reached* (connect timeout, TLS handshake, refused) trips
  a per-host breaker.  While it is open every call fails at once with
  :class:`HostUnreachable`, so a dead site costs one timeout instead of one per
  document, and callers can reschedule instead of counting a failed attempt.

Slow or erroring responses from a reachable host are ordinary failures and are
retried here with backoff.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import httpx

USER_AGENT = "GIDS Situation Room/2.0 (+https://globalinfectiousdisease.com/situation/methodology/)"
DEFAULT_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0
RETRY_STATUS = {429, 500, 502, 503, 504}

HOST_CONCURRENCY = 3
BREAKER_THRESHOLD = 2
BREAKER_COOLDOWN = 600.0
#: Unreachable but breaker not yet open: how soon a caller should look again.
SOFT_RETRY_AFTER = 120.0

_UNREACHABLE = (httpx.ConnectTimeout, httpx.ConnectError)


class HostUnreachable(Exception):
    """The host did not accept a connection; ``retry_after`` is in seconds."""

    def __init__(self, host: str, retry_after: float, reason: str = "") -> None:
        super().__init__(f"{host} unreachable{': ' + reason if reason else ''}; retry in {int(retry_after)}s")
        self.host = host
        self.retry_after = retry_after


class _Host:
    __slots__ = ("failures", "open_until", "slots")

    def __init__(self, concurrency: int) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.slots = threading.BoundedSemaphore(concurrency)


class HostGuard:
    """Per-host concurrency limit plus a circuit breaker; safe across threads."""

    def __init__(
        self,
        *,
        concurrency: int = HOST_CONCURRENCY,
        threshold: int = BREAKER_THRESHOLD,
        cooldown: float = BREAKER_COOLDOWN,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._concurrency = concurrency
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._hosts: dict[str, _Host] = {}
        self._lock = threading.Lock()

    def _host(self, name: str) -> _Host:
        with self._lock:
            return self._hosts.setdefault(name, _Host(self._concurrency))

    def remaining(self, name: str) -> float:
        return max(0.0, self._host(name).open_until - self._clock())

    def admit(self, name: str) -> None:
        """Raise :class:`HostUnreachable` while the breaker is open."""

        state = self._host(name)
        wait = state.open_until - self._clock()
        if wait > 0:
            raise HostUnreachable(name, wait, "circuit open")
        if state.open_until:
            # Cooldown over: one failure is enough to trip again.
            state.open_until = 0.0
            state.failures = max(0, self._threshold - 1)

    @contextmanager
    def slot(self, name: str) -> Iterator[None]:
        state = self._host(name)
        with state.slots:
            yield

    def succeeded(self, name: str) -> None:
        state = self._host(name)
        state.failures = 0
        state.open_until = 0.0

    def failed(self, name: str) -> float:
        """Record an unreachable attempt; returns when to look again, in seconds."""

        state = self._host(name)
        state.failures += 1
        if state.failures >= self._threshold:
            state.open_until = self._clock() + self._cooldown
            return self._cooldown
        return SOFT_RETRY_AFTER


GUARD = HostGuard()


def _backoff(attempt: int) -> None:
    time.sleep(min(2**attempt, 8))


def fetch(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
    client: httpx.Client | None = None,
    headers: dict[str, str] | None = None,
    guard: HostGuard = GUARD,
) -> httpx.Response:
    """GET ``url``.  Raises :class:`HostUnreachable`, or the last HTTP error after retries."""

    from src.services.ingestion.capture import assert_source_access
    assert_source_access()
    host = urlparse(url).hostname or ""
    guard.admit(host)
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    limits = httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT, timeout))
    attempts = max(1, retries)

    def _get(active: httpx.Client) -> httpx.Response:
        for attempt in range(1, attempts + 1):
            try:
                with guard.slot(host):
                    response = active.get(url, params=params, timeout=limits)
            except _UNREACHABLE as error:
                raise HostUnreachable(host, guard.failed(host), type(error).__name__) from error
            except httpx.TransportError:
                if attempt >= attempts:
                    raise
                _backoff(attempt)
                continue
            guard.succeeded(host)
            if response.status_code in RETRY_STATUS and attempt < attempts:
                _backoff(attempt)
                continue
            response.raise_for_status()
            from src.services.ingestion.capture import capture_sync, is_capturing
            if is_capturing():
                response._content = capture_sync(response.content, str(response.url), response.headers.get("Content-Type", ""))
            return response
        raise RuntimeError("unreachable")  # pragma: no cover - the loop returns or raises

    if client is not None:
        return _get(client)
    with httpx.Client(headers=request_headers, follow_redirects=True) as owned:
        return _get(owned)


__all__ = ["GUARD", "HostGuard", "HostUnreachable", "USER_AGENT", "fetch"]
