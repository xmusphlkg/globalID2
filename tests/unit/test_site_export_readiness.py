"""Export checks existing schema and never repairs, seeds, or imports data."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation import site_data_database as module


@pytest.mark.parametrize("tables,columns", [(False, True), (True, False)])
async def test_unmigrated_schema_fails_without_writes(monkeypatch, tables, columns):
    results = iter([tables, columns])
    statements = []

    async def execute(statement, params=None):
        statements.append(str(statement))
        return SimpleNamespace(scalar_one=lambda: next(results))

    @asynccontextmanager
    async def database():
        yield SimpleNamespace(execute=execute)

    seed = AsyncMock(side_effect=AssertionError("export must not seed countries"))
    monkeypatch.setattr(module, "get_db", database)
    monkeypatch.setattr(module, "ensure_standard_country_rows", seed)
    with pytest.raises(RuntimeError, match="apply database migrations"):
        await module.ensure_site_export_database_ready()
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    seed.assert_not_awaited()


async def test_migrated_schema_reads_count_without_seeding(monkeypatch):
    statements = []

    async def execute(statement, params=None):
        statements.append(str(statement))
        return SimpleNamespace(scalar_one=lambda: True, scalar=lambda: 295)

    @asynccontextmanager
    async def database():
        yield SimpleNamespace(execute=execute)

    seed = AsyncMock(side_effect=AssertionError("export must not seed countries"))
    monkeypatch.setattr(module, "get_db", database)
    monkeypatch.setattr(module, "ensure_standard_country_rows", seed)
    assert await module.ensure_site_export_database_ready() == 295
    assert len(statements) == 3
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    seed.assert_not_awaited()
