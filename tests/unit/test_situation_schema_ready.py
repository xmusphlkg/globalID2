from contextlib import asynccontextmanager

import pytest

from src.services.situation_v3 import pipeline


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [[], ["situation_snapshots"]])
async def test_situation_refresh_only_initializes_missing_schema(monkeypatch, missing):
    calls = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            return missing

    class DB:
        async def execute(self, statement, params):
            calls.append((str(statement), params))
            return Result()

    @asynccontextmanager
    async def get_db():
        yield DB()

    async def init_database():
        calls.append("init_database")

    monkeypatch.setattr(pipeline, "get_db", get_db)
    monkeypatch.setattr(pipeline, "init_database", init_database)

    await pipeline._ensure_situation_database_ready()

    assert "to_regclass" in calls[0][0]
    assert ("init_database" in calls) is bool(missing)
