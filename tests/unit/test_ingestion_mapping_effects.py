"""Organization resolves names read-only; commit owns counters and suggestions."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.data.normalizers import disease_mapper_db as mapper_module
from src.services.ingestion.mapping_effects import persist_mapping_effects


async def test_deferred_mapping_never_writes_during_organization(monkeypatch):
    calls = []
    class Session:
        async def execute(self, statement, parameters):
            calls.append(str(statement))
            assert str(statement).lstrip().startswith("SELECT")
            return SimpleNamespace(fetchone=lambda: ("D007", 1) if parameters["name"] == "known" else None)
    @asynccontextmanager
    async def database():
        yield Session()
    monkeypatch.setattr(mapper_module, "get_db", database)
    mapper = mapper_module.DiseaseMapperDB("CN")
    mapper.defer_writes = True
    mapper._record_unknown_disease = AsyncMock(side_effect=AssertionError("organize wrote suggestions"))
    assert await mapper.map_local_to_id("known") == "D007"
    assert await mapper.map_local_to_id("unknown") is None
    assert mapper.pending_effects == [{"kind": "usage", "country": "CN", "name": "known"}, {"kind": "unknown", "country": "CN", "name": "unknown"}]
    assert len(calls) == 2


async def test_report_rollback_discards_mapping_side_effects():
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE disease_mappings (country_code TEXT, local_name TEXT, usage_count INTEGER, last_used_at TEXT)"))
            await connection.execute(text("CREATE TABLE disease_learning_suggestions (country_code TEXT, local_name TEXT, occurrence_count INTEGER, first_seen_at TEXT, last_seen_at TEXT, UNIQUE(country_code,local_name))"))
            await connection.execute(text("INSERT INTO disease_mappings VALUES ('CN','known',0,NULL)"))
        async with maker() as db:
            await persist_mapping_effects(db, [{"kind": "usage", "country": "CN", "name": "known"}, {"kind": "unknown", "country": "CN", "name": "unknown"}])
            await db.rollback()
        async with maker() as db:
            assert (await db.execute(text("SELECT usage_count FROM disease_mappings"))).scalar_one() == 0
            assert (await db.execute(text("SELECT COUNT(*) FROM disease_learning_suggestions"))).scalar_one() == 0
    finally:
        await engine.dispose()
