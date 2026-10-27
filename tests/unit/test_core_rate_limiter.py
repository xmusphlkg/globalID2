import asyncio
from types import SimpleNamespace

import pytest

from src.core import rate_limiter as limiter_module


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(limiter_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(limiter_module, "get_config", lambda: SimpleNamespace(
        ai=SimpleNamespace(enable_rate_limiting=True, rate_limit=2),
    ))
    return clock


@pytest.mark.parametrize("kwargs", [
    {"max_requests": 0}, {"max_requests": -1}, {"max_requests": True},
    {"max_requests": 1.5}, {"window_seconds": 0}, {"window_seconds": -1},
    {"window_seconds": float("nan")}, {"window_seconds": float("inf")},
])
def test_invalid_limits_are_rejected(clock, kwargs):
    with pytest.raises(ValueError):
        limiter_module.RateLimiter(**kwargs)


def test_exact_window_boundary_expires_requests(clock):
    limiter = limiter_module.RateLimiter(max_requests=1, window_seconds=10)
    limiter.record_request()
    assert limiter.wait_time() == 10
    clock.now += 10
    assert limiter.can_proceed() is True
    assert limiter.get_stats()["current_requests"] == 0


async def test_concurrent_waiters_recheck_capacity_at_every_window(clock, monkeypatch):
    limiter = limiter_module.RateLimiter(max_requests=2, window_seconds=10)
    sleepers = []

    async def sleep(delay):
        assert delay > 0
        future = asyncio.get_running_loop().create_future()
        sleepers.append(future)
        await future

    monkeypatch.setattr(limiter_module, "asyncio", SimpleNamespace(sleep=sleep))
    admissions = []

    async def request():
        await limiter.acquire()
        admissions.append(clock.now)

    tasks = [asyncio.create_task(request()) for _ in range(6)]
    try:
        await asyncio.sleep(0)
        assert admissions == [100, 100]
        for now in (110, 120):
            clock.now = now
            waiting = list(sleepers)
            sleepers.clear()
            for future in waiting:
                future.set_result(None)
            await asyncio.sleep(0)
            assert admissions.count(now) == 2
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert admissions == [100, 100, 110, 110, 120, 120]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_cancellation_while_waiting_does_not_reserve_capacity(clock, monkeypatch):
    limiter = limiter_module.RateLimiter(max_requests=1, window_seconds=10)
    await limiter.acquire()
    waiting = asyncio.Event()

    async def sleep(delay):
        waiting.set()
        await asyncio.Future()

    monkeypatch.setattr(limiter_module, "asyncio", SimpleNamespace(sleep=sleep))
    task = asyncio.create_task(limiter.acquire())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(limiter.requests) == [100]
    clock.now = 110
    await limiter.acquire()
    assert list(limiter.requests) == [110]


async def test_disabled_limiter_does_not_wait_or_record(clock):
    limiter = limiter_module.RateLimiter(max_requests=1)
    limiter.config.ai.enable_rate_limiting = False
    for _ in range(10):
        await limiter.acquire()
    assert not limiter.requests


def test_recording_without_stats_prunes_expired_history(clock):
    limiter = limiter_module.RateLimiter(max_requests=1, window_seconds=10)
    for now in range(100, 1000, 10):
        clock.now = now
        limiter.record_request()
    assert list(limiter.requests) == [990]
