"""Fresh DDL, ORM, transactional boundaries, and optional read-only PG parity."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.check_collection_schema import (
    TABLES,
    compare_postgresql_schema,
    compare_table_metadata,
)
from src.domain.ai_work_item import AIWorkItem
from src.domain.collection import PublishDirty, RunStage, SourceArtifact
from src.services.ingestion.stages import mark_dirty, next_attempt

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ["0022_collection_stages", "0023_ai_work_items", "0024_collection_jsonb"]


def migration(name):
    spec = importlib.util.spec_from_file_location(
        f"schema_{name}", ROOT / "migrations/versions" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_postgresql_upgrade_sql_changes_only_new_tables():
    stream = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": stream}
    )
    with Operations.context(context):
        for name in VERSIONS:
            migration(name).upgrade()
    sql = stream.getvalue()
    assert {
        line.split()[2] for line in sql.splitlines() if line.startswith("CREATE TABLE ")
    } == {table.name for table in TABLES}
    assert sql.count(" TYPE JSONB USING ") == 4
    assert sql.count("SET DEFAULT '{}'::jsonb") == 2
    assert "PRIMARY KEY (id)" in sql and sql.count("BIGSERIAL") == 4
    assert "WHERE published_at IS NULL" in sql and "WHERE status = 'queued'" in sql
    assert (
        "DROP TABLE" not in sql
        and "disease_records" not in sql
        and "public_disease_series" not in sql
    )


class MigrationTables:
    def __init__(self):
        self.metadata = sa.MetaData()

    def create_table(self, name, *elements, **kwargs):
        return sa.Table(name, self.metadata, *elements, **kwargs)

    def create_index(self, name, table, columns, **kwargs):
        sa.Index(
            name,
            *[self.metadata.tables[table].c[column] for column in columns],
            **kwargs,
        )

    def alter_column(self, table, name, **kwargs):
        column = self.metadata.tables[table].c[name]
        if "type_" in kwargs:
            column.type = kwargs["type_"]
        if "nullable" in kwargs:
            column.nullable = kwargs["nullable"]
        if "server_default" in kwargs:
            value = kwargs["server_default"]
            column.server_default = (
                sa.DefaultClause(value) if value is not None else None
            )


def test_fresh_migrations_and_orm_match_types_defaults_indexes_and_checks(monkeypatch):
    recorder = MigrationTables()
    previous = "0021_situation_alerts"
    for name in VERSIONS:
        module = migration(name)
        assert module.down_revision == previous
        monkeypatch.setattr(module, "op", recorder)
        module.upgrade()
        previous = module.revision
    differences = []
    for table in TABLES:
        differences.extend(
            compare_table_metadata(
                table, recorder.metadata.tables[table.name], postgresql.dialect()
            )
        )
    assert not differences, differences


@pytest.mark.parametrize(
    "model,column",
    [
        (RunStage, "metrics"),
        (SourceArtifact, "metadata"),
        (AIWorkItem, "payload"),
        (AIWorkItem, "result"),
    ],
)
def test_structured_columns_are_jsonb_in_postgresql_and_portable_json_in_sqlite(
    model, column
):
    value = model.__table__.c[column]
    assert value.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert value.type.compile(dialect=sqlite.dialect()) == "JSON"


def test_sqlite_server_defaults_and_database_constraints_are_enforced():
    engine = sa.create_engine("sqlite://")
    metadata = RunStage.metadata
    metadata.create_all(engine, tables=TABLES)
    now = "2026-10-30 12:00:00+00:00"
    with engine.begin() as db:
        db.execute(
            sa.text(
                "INSERT INTO run_stages (task_uuid,stage,status,started_at) VALUES (:task,:stage,:status,:time)"
            ),
            {
                "task": "schema-contract",
                "stage": "collect",
                "status": "running",
                "time": now,
            },
        )
        row = db.execute(sa.select(RunStage.__table__)).mappings().one()
        assert row["attempt"] == 1 and row["metrics"] == {}
        db.execute(
            sa.text(
                "INSERT INTO source_artifacts (task_uuid,attempt,relative_path,sha256,byte_size,kind,created_at) VALUES (:task,1,:path,:hash,7,:kind,:time)"
            ),
            {
                "task": "schema-contract",
                "path": "data/raw/us/test.csv",
                "hash": "a" * 64,
                "kind": "csv_page",
                "time": now,
            },
        )
        row = db.execute(sa.select(SourceArtifact.__table__)).mappings().one()
        assert row["stage"] == "collect" and row["metadata"] == {}
        db.execute(
            sa.text(
                "INSERT INTO ai_work_items (parent_task_uuid,line,priority,payload,available_at,created_at) VALUES (:task,:line,:priority,:payload,:time,:time)"
            ),
            {
                "task": "schema-contract",
                "line": "report_section",
                "priority": "high",
                "payload": "{}",
                "time": now,
            },
        )
        row = db.execute(sa.select(AIWorkItem.__table__)).mappings().one()
        assert (
            row["capability"] == "text"
            and row["status"] == "queued"
            and row["attempt"] == 1
        )
        invalid = [
            sa.insert(RunStage).values(
                task_uuid="schema-contract",
                stage="collect",
                status="completed",
                started_at=datetime.now(timezone.utc),
            ),
            sa.insert(RunStage).values(
                task_uuid="invalid-stage",
                stage="extract",
                status="running",
                started_at=datetime.now(timezone.utc),
            ),
            sa.insert(PublishDirty).values(
                line="invalid-line",
                scope_key="*",
                source_task_uuid="schema-contract",
                marked_at=datetime.now(timezone.utc),
            ),
            sa.insert(AIWorkItem).values(
                parent_task_uuid="schema-contract",
                line="report_section",
                priority="high",
                payload={},
                attempt=4,
            ),
            sa.insert(AIWorkItem).values(
                parent_task_uuid="schema-contract",
                line="report_section",
                priority="highest",
                payload={},
            ),
        ]
        for statement in invalid:
            with db.begin_nested(), pytest.raises(sa.exc.IntegrityError):
                db.execute(statement)
    engine.dispose()


async def test_attempt_history_and_dirty_marker_share_the_formal_transaction():
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite://")
    facts = sa.Table(
        "schema_contract_facts",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: RunStage.metadata.create_all(sync, tables=TABLES)
        )
        await connection.run_sync(facts.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    stamp = datetime.now(timezone.utc)
    try:
        async with maker.begin() as db:
            assert await next_attempt(db, "same-task") == 1
            db.add_all(
                RunStage(
                    task_uuid="same-task",
                    stage=stage,
                    attempt=1,
                    status="completed",
                    started_at=stamp,
                )
                for stage in ("collect", "organize", "store")
            )
        async with maker.begin() as db:
            assert await next_attempt(db, "same-task") == 2
            db.add_all(
                RunStage(
                    task_uuid="same-task",
                    stage=stage,
                    attempt=2,
                    status="completed",
                    started_at=stamp,
                )
                for stage in ("collect", "organize", "store")
            )
        with pytest.raises(RuntimeError, match="rollback formal and dirty"):
            async with maker.begin() as db:
                await db.execute(sa.insert(facts).values(id=1))
                await mark_dirty(
                    db, line="surveillance", scope_key="US", task_uuid="same-task"
                )
                raise RuntimeError("rollback formal and dirty")
        async with maker() as db:
            assert await db.scalar(sa.select(sa.func.count()).select_from(facts)) == 0
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(PublishDirty))
                == 0
            )
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(RunStage)) == 6
            )
        async with maker.begin() as db:
            await db.execute(sa.insert(facts).values(id=1))
            await mark_dirty(
                db, line="surveillance", scope_key="US", task_uuid="same-task"
            )
        async with maker() as db:
            assert await db.scalar(sa.select(sa.func.count()).select_from(facts)) == 1
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(PublishDirty))
                == 1
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    os.getenv("GLOBALID_CHECK_COLLECTION_SCHEMA") != "1",
    reason="explicit read-only PostgreSQL schema audit",
)
def test_live_postgresql_collection_schema_matches_orm():
    from src.core.config import get_config

    engine = sa.create_engine(get_config().database.url_sync)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            assert not compare_postgresql_schema(connection)
            connection.rollback()
    finally:
        engine.dispose()


def test_saved_openapi_collection_surface_matches_current_app_without_regeneration():
    from dashboard.api.main import app

    saved = json.loads((ROOT / "dashboard/openapi.json").read_text())
    live = app.openapi()
    prefixes = ("/api/v1/collection", "/api/v1/models", "/api/v1/publish")
    assert {path for path in live["paths"] if path.startswith(prefixes)} == {
        path for path in saved["paths"] if path.startswith(prefixes)
    }
    for name in ("ArtifactOut", "StageOut", "RunOut", "LineOut", "PendingOut"):
        assert (
            live["components"]["schemas"][name] == saved["components"]["schemas"][name]
        )
    operation_ids = [
        operation["operationId"]
        for path in live["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "get" in live["paths"]["/api/v1/sources/config"]
    properties = live["components"]["schemas"]["ArtifactOut"]["properties"]
    assert set(properties) == {
        "attempt",
        "relative_path",
        "sha256",
        "byte_size",
        "source_url",
        "kind",
    }
    assert not any(
        "artifact" in path and ("download" in path or "content" in path)
        for path in live["paths"]
    )
