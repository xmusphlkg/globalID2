from __future__ import annotations

import pytest

from src.core import database


@pytest.mark.asyncio
async def test_dispose_database_closes_pool_and_clears_cached_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_maker", object())

    await database.dispose_database()

    assert engine.disposed is True
    assert database._engine is None
    assert database._session_maker is None


@pytest.mark.asyncio
async def test_dispose_database_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_maker", None)

    await database.dispose_database()

    assert database._engine is None
    assert database._session_maker is None
