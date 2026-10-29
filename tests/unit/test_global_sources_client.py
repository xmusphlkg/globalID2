"""Offline tests for the shared authority HTTP client: retries, host breaker, concurrency."""

from __future__ import annotations

import httpx
import pytest

from src.services.global_sources import client as http
from src.services.global_sources.client import HostGuard, HostUnreachable, fetch


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http, "_backoff", lambda attempt: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_server_errors_are_retried_then_succeed():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) < 3 else 200, json={"ok": True})

    assert fetch("https://a.example/x", client=_client(handler), guard=HostGuard()).json() == {"ok": True}
    assert len(calls) == 3


def test_persistent_server_error_raises_after_retries():
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://a.example/x", client=_client(lambda r: httpx.Response(500)), guard=HostGuard(), retries=2)


def test_unreachable_host_trips_the_breaker_and_later_calls_fail_without_a_request():
    clock = Clock()
    guard = HostGuard(threshold=2, cooldown=600, clock=clock)
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ConnectTimeout("handshake timed out")

    web = _client(handler)
    first = pytest.raises(HostUnreachable, fetch, "https://dead.example/a", client=web, guard=guard)
    assert first.value.retry_after == http.SOFT_RETRY_AFTER  # breaker still closed
    second = pytest.raises(HostUnreachable, fetch, "https://dead.example/b", client=web, guard=guard)
    assert second.value.retry_after == 600  # now open
    assert len(attempts) == 2

    third = pytest.raises(HostUnreachable, fetch, "https://dead.example/c", client=web, guard=guard)
    assert len(attempts) == 2  # rejected locally, no network
    assert 0 < third.value.retry_after <= 600


def test_breaker_is_per_host():
    guard = HostGuard(threshold=1, cooldown=600, clock=Clock())

    def handler(request):
        if request.url.host == "dead.example":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={})

    web = _client(handler)
    with pytest.raises(HostUnreachable):
        fetch("https://dead.example/", client=web, guard=guard)
    assert fetch("https://alive.example/", client=web, guard=guard).status_code == 200


def test_breaker_closes_after_cooldown_and_one_failure_reopens_it():
    clock = Clock()
    guard = HostGuard(threshold=2, cooldown=600, clock=clock)
    guard.failed("h")
    guard.failed("h")
    with pytest.raises(HostUnreachable):
        guard.admit("h")
    clock.now += 601
    guard.admit("h")  # probe allowed
    guard.failed("h")
    with pytest.raises(HostUnreachable):
        guard.admit("h")


def test_success_resets_the_failure_count():
    guard = HostGuard(threshold=2, clock=Clock())
    guard.failed("h")
    guard.succeeded("h")
    guard.failed("h")
    guard.admit("h")  # one failure since the reset: still closed


def test_read_timeouts_from_a_reachable_host_are_ordinary_retries():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={})

    guard = HostGuard()
    assert fetch("https://slow.example/", client=_client(handler), guard=guard).status_code == 200
    guard.admit("slow.example")  # a slow host is not an unreachable one
