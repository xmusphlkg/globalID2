"""Offline persistence harness for the legacy disease_records contract.

Expected records must be authored in fixtures, never built by a production
projector. SQLite executes the real INSERT/ON CONFLICT statements and enforces
the production composite primary key. This does not validate PostgreSQL locks,
TimescaleDB, registry coverage, or source-series persistence.
"""

from contextlib import asynccontextmanager
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import DiseaseRecord
from src.data.processors.mapping_lookup import normalize_mapping_key


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"
CONTRACT_FIXTURES = FIXTURES / "legacy_projection"


def contract_fixture():
    """Read only; there is deliberately no golden-update environment flag."""
    return json.loads((CONTRACT_FIXTURES / "contracts.json").read_text())


@asynccontextmanager
async def records_database(countries):
    """Create a fresh, explicitly memory-only database for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(DiseaseRecord.__table__.create)
            await connection.execute(text(
                "CREATE TABLE countries (id INTEGER PRIMARY KEY, code TEXT UNIQUE)"
            ))
            await connection.execute(
                text("INSERT INTO countries (code, id) VALUES (:code, :id)"),
                [{"code": code, "id": identity} for code, identity in countries.items()],
            )
        yield sessions
    finally:
        await engine.dispose()


async def stored_records(sessions):
    """Read committed values through a separate session, including extra rows."""
    async with sessions() as db:
        result = await db.execute(text(
            "SELECT time, disease_id, country_id, cases, deaths FROM disease_records"
        ))
        return [dict(row) for row in result.mappings()]


def saved_artifact(payload, *, country, filename, kind, metadata=None, url=None):
    """Keep replay confined to the disposable cwd chosen by the test."""
    path = Path("data/raw") / country.lower() / "legacy-contract" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return SimpleNamespace(
        relative_path=path.as_posix(), byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(), kind=kind,
        metadata_=metadata or {}, source_url=url or "https://fixture.invalid/source",
    )


def fixture_mapping(monkeypatch, updater, mappings):
    """Replace registry I/O only; execute the updater's real projection and SQL.

    Per-source maps also catch routing NHSS labels through the NNDSS mapping.
    The real country-ID SELECT runs against the fixture countries table.
    """
    async def load(db, country_code, *, source_id=None, **kwargs):
        assert country_code in {
            updater.country_code,
            getattr(updater, "series_country_code", updater.country_code),
        }
        selected = mappings.get(source_id, mappings.get("*", {}))
        return {normalize_mapping_key(label): identity for label, identity in selected.items()}

    module = importlib.import_module(type(updater).__module__)
    monkeypatch.setattr(module, "load_country_mapping_dict", load)
    if hasattr(updater, "_get_source_latest_dates"):
        monkeypatch.setattr(updater, "_get_source_latest_dates", AsyncMock(return_value={}))
    if hasattr(updater, "get_db_model_refresh_time"):
        monkeypatch.setattr(updater, "get_db_model_refresh_time", AsyncMock(return_value=None))


async def import_records(sessions, updater, rows, *, latest=None, source_latest=None, force=False):
    async with sessions.begin() as db:
        return await updater.import_rows(
            db, rows, db_latest_date=latest,
            source_latest_date=source_latest, force=force,
        )
