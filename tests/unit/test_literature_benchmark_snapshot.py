"""The disposable benchmark must never open its source with write access."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai import benchmark_snapshot


@pytest.mark.asyncio
async def test_source_snapshot_is_read_only_and_disposes_on_query_failure(monkeypatch):
    options = {}
    engine = SimpleNamespace(dispose=AsyncMock())

    def create_engine(url, **kwargs):
        options.update(kwargs)
        return engine

    class FailedSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, statement):
            raise RuntimeError("source query failed")

    monkeypatch.setattr(benchmark_snapshot, "get_config", lambda: SimpleNamespace(database=SimpleNamespace(url="unused")))
    monkeypatch.setattr(benchmark_snapshot, "create_async_engine", create_engine)
    monkeypatch.setattr(benchmark_snapshot, "async_sessionmaker", lambda *a, **kw: FailedSession)
    with pytest.raises(RuntimeError, match="source query failed"):
        await benchmark_snapshot.source_snapshot(1)
    assert options["connect_args"]["server_settings"]["default_transaction_read_only"] == "on"
    assert options["hide_parameters"] is True
    engine.dispose.assert_awaited_once()
