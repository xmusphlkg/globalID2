import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from redis.exceptions import ConnectionError, TimeoutError

from src.core import cache as cache_module


@pytest.fixture
def cache(monkeypatch):
    config = SimpleNamespace(
        ai=SimpleNamespace(enable_cache=True, cache_ttl=2),
        redis=SimpleNamespace(url="redis://unused.invalid/0"),
    )
    monkeypatch.setattr(cache_module, "get_config", lambda: config)
    return cache_module.CacheService()


@pytest.fixture
def client(monkeypatch):
    client = SimpleNamespace(
        ping=AsyncMock(return_value=True), aclose=AsyncMock(),
        get=AsyncMock(return_value='{"value": 1}'), set=AsyncMock(return_value=True),
        delete=AsyncMock(return_value=0), exists=AsyncMock(return_value=1),
        ttl=AsyncMock(return_value=60),
    )
    monkeypatch.setattr(cache_module.redis, "from_url", Mock(return_value=client))
    return client


@pytest.mark.parametrize("command,args,expected", [
    ("get", ("key",), None), ("set", ("key", {"value": 1}), False),
    ("delete", ("key",), False), ("exists", ("key",), False),
    ("get_ttl", ("key",), -2),
])
async def test_unavailable_cache_returns_fallback_even_on_first_connect(cache, client, command, args, expected):
    client.ping.side_effect = ConnectionError("offline")
    assert await getattr(cache, command)(*args) == expected
    assert cache._redis is None
    client.aclose.assert_awaited_once()


async def test_failed_connection_can_recover(cache, client):
    client.ping.side_effect = [ConnectionError("offline"), True]
    assert await cache.get("key") is None
    assert await cache.get("key") == {"value": 1}
    assert client.ping.await_count == 2


async def test_concurrent_readers_wait_for_one_successful_connection(cache, client):
    ready = asyncio.Event()
    started = asyncio.Event()

    async def ping():
        started.set()
        await ready.wait()

    client.ping.side_effect = ping
    first = asyncio.create_task(cache.get("a"))
    await started.wait()
    second = asyncio.create_task(cache.get("b"))
    await asyncio.sleep(0)
    assert cache._redis is None
    assert client.ping.await_count == 1
    client.get.assert_not_awaited()
    ready.set()
    assert await asyncio.wait_for(asyncio.gather(first, second), 1) == [{"value": 1}] * 2


@pytest.mark.parametrize("close_error", [None, ConnectionError("close failed")])
async def test_cancelled_connect_closes_candidate_and_allows_reconnect(cache, client, close_error):
    started = asyncio.Event()

    async def ping():
        started.set()
        await asyncio.Future()

    client.ping.side_effect = ping
    client.aclose.side_effect = close_error
    pending = asyncio.create_task(cache.connect())
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    client.aclose.assert_awaited_once()
    assert cache._redis is None
    client.ping.side_effect = None
    await asyncio.wait_for(cache.connect(), 1)
    assert cache._redis is client


async def test_direct_connect_still_reports_failure_to_health_checks(cache, client):
    client.ping.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        await cache.connect()


async def test_default_ttl_converts_configured_hours_to_seconds(cache, client):
    assert await cache.set("key", {"value": 1}) is True
    client.set.assert_awaited_once_with("globalid:key", '{"value": 1}', ex=7200)
    assert await cache.set("key", False, ttl=15) is True
    client.set.assert_awaited_with("globalid:key", "false", ex=15)


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5])
async def test_invalid_ttl_does_not_connect(cache, client, ttl):
    assert await cache.set("key", "value", ttl=ttl) is False
    client.ping.assert_not_awaited()


async def test_serialization_failure_does_not_connect(cache, client):
    assert await cache.set("key", object()) is False
    client.ping.assert_not_awaited()


async def test_disabled_cache_does_not_connect(cache, client):
    cache.config.ai.enable_cache = False
    assert await cache.get("key") is None
    assert await cache.set("key", "value") is False
    client.ping.assert_not_awaited()


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("0", 0), ('""', ""), ("[]", []), ("invalid json", None),
])
async def test_cache_decodes_falsy_values_and_ignores_corrupt_json(cache, client, raw, expected):
    client.get.return_value = raw
    assert await cache.get("key") == expected


async def test_command_timeout_degrades_but_cancellation_propagates(cache, client):
    client.get.side_effect = TimeoutError("timeout")
    assert await cache.get("key") is None
    client.get.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await cache.get("key")


async def test_disconnect_clears_client_even_when_close_fails(cache, client):
    await cache.connect()
    client.aclose.side_effect = ConnectionError("close failed")
    with pytest.raises(ConnectionError):
        await cache.disconnect()
    assert cache._redis is None
    client.aclose.side_effect = None
    await cache.connect()
    assert client.ping.await_count == 2


async def test_delete_missing_key_is_successful_and_ttl_sentinels_survive(cache, client):
    assert await cache.delete("missing") is True
    for ttl in (-1, -2, 60):
        client.ttl.return_value = ttl
        assert await cache.get_ttl("key") == ttl
