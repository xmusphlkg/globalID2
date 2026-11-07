"""Situation transactions and independent manifest stay coherent across retries."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from src.domain import Base, SituationAlert, SituationNotice, SituationOutbreak, Task
from src.domain.collection import PublishDirty
from src.services.data_release import data_pipeline, situation_data_pipeline as pipeline
from src.services.situation_notices.publication import export_json_publication
from src.services.situation_notices import export as export_module
from src.services.situation_notices.pipeline import extract_pending, fetch_pending_documents, rebuild_outbreaks, store_notices
from tests.unit.test_situation_pipeline_db import AS_OF, DRAFT, Scripted, _event, _fetcher, _run, db
from tests.unit.test_release_dirty_selection import release_db
from tests.unit.test_surveillance_publish import CONFIG, Store
from src.services.data_release.surveillance_publish import publish_surveillance_data


async def add_dirty_table(maker):
    async with maker() as session:
        await session.run_sync(lambda sync: PublishDirty.__table__.create(sync.connection(), checkfirst=True))
        await session.commit()


async def test_changed_transaction_publishes_once_and_rebuild_noop_does_not_mark(db):
    await add_dirty_table(db)
    event = _event("changed", ref={"kind": "who_don", "slug": "changed"})
    await store_notices(_run(event), run_id="first", task_uuid="collect")
    await store_notices(_run(event), run_id="same", task_uuid="unchanged")
    await rebuild_outbreaks(as_of=AS_OF, task_uuid="empty-rebuild")
    async with db() as session:
        markers = list((await session.execute(select(PublishDirty))).scalars())
        assert [row.source_task_uuid for row in markers] == ["collect"]
    await fetch_pending_documents(fetch=_fetcher([]))
    counts = await extract_pending(Scripted([DRAFT], [{"verdict": "accept", "issues": []}]), task_uuid="accepted")
    assert counts["published"] == 1
    await rebuild_outbreaks(as_of=AS_OF, task_uuid="grouped")
    await rebuild_outbreaks(as_of=AS_OF, task_uuid="grouped-again")
    async with db() as session:
        markers = list((await session.execute(select(PublishDirty))).scalars())
        assert [row.source_task_uuid for row in markers] == ["collect", "accepted", "grouped"]
    # A changed attachment can be reviewed again without changing public facts.
    from src.domain import SituationDocument
    from src.core.database import get_db
    async with get_db() as session:
        await session.execute(update(SituationDocument).values(text_hash="different-input-hash"))
        await session.execute(update(SituationNotice).values(ai_status="pending", ai_next_attempt_at=None))
    assert (await extract_pending(Scripted([DRAFT], [{"verdict": "accept", "issues": []}]), task_uuid="same-facts"))["published"] == 1
    await rebuild_outbreaks(as_of=AS_OF, task_uuid="same-groups")
    async with db() as session:
        assert len(list((await session.execute(select(PublishDirty))).scalars())) == 3


async def test_json_snapshot_derives_groups_before_managed_rebuild_and_preserves_private_text(db, tmp_path):
    await store_notices(_run(_event("midrun", ref={"kind": "who_don", "slug": "midrun"})), run_id="first")
    await fetch_pending_documents(fetch=_fetcher([]))
    assert (await extract_pending(Scripted([DRAFT], [{"verdict": "accept", "issues": []}])))["published"] == 1
    async with db() as session:
        assert not list((await session.execute(select(SituationOutbreak))).scalars())
    state, public = tmp_path / "state", tmp_path / "public"
    await export_json_publication(state, public, release_id="situation-midrun")
    manifest = json.loads((public / "current.json").read_text())
    assert set(manifest["files"]) >= {"map-data.json", "sources.json", "weekly/index.json", "by-country/CD.json", "by-disease/D050.json"}
    mapping = json.loads((public / manifest["files"]["map-data.json"]["url"]).read_text())
    assert len(mapping["points"]) == 3
    ids = {row["outbreak_id"] for row in mapping["points"]}
    assert len(ids) == 1 and None not in ids
    for outbreak_id in ids:
        assert f"outbreaks/{outbreak_id}.json" in manifest["files"]
    for descriptor in manifest["files"].values():
        body = (public / descriptor["url"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == descriptor["sha256"]
        assert b"SECRET-FULL-TEXT-MARKER" not in body
    empty = json.loads((public / manifest["files"]["by-country/JP-25.json"]["url"]).read_text())
    assert empty["country"]["code"] == "JP-25"
    assert empty["alert_count"] == 0 and empty["alerts"] == []


async def test_standalone_api_sets_read_only_repeatable_read_before_any_select(db, tmp_path, monkeypatch):
    from src.core.database_snapshot import SNAPSHOT_ENV
    monkeypatch.delenv(SNAPSHOT_ENV, raising=False)
    original = export_module.get_db
    statements = []
    @asynccontextmanager
    async def database():
        async with original() as session:
            class PostgreSQLSession:
                def get_bind(self):
                    return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
                async def execute(self, statement):
                    sql = str(statement)
                    statements.append(sql)
                    if sql.startswith("SET TRANSACTION"):
                        return None
                    return await session.execute(statement)
            yield PostgreSQLSession()
    monkeypatch.setattr(export_module, "get_db", database)
    await export_module.export_site(output_dir=tmp_path, as_of=AS_OF, derive_outbreaks=True)
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert all(statement.startswith("SELECT") for statement in statements[1:])


@pytest.mark.parametrize("line,scope,ids", [("surveillance", "US", None), ("situation", "US", None), ("situation", "*", [999])])
async def test_situation_selection_rejects_other_line_scope_and_missing_ids(release_db, line, scope, ids):
    maker, _service, _ = release_db
    async with maker() as session:
        row = PublishDirty(line=line, scope_key=scope, source_task_uuid="source", marked_at=datetime.now(timezone.utc))
        session.add(row)
        await session.commit()
        with pytest.raises(ValueError, match="actual dirty"):
            await pipeline.validate_situation_selection(session, ids or [row.id])


async def test_selection_rejects_another_task_ack_but_allows_own_ack_retry(release_db):
    maker, _service, _ = release_db
    async with maker() as session:
        row = PublishDirty(line="situation", scope_key="*", source_task_uuid="source",
            marked_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc), published_task_uuid="published")
        session.add(row)
        await session.commit()
        await pipeline.validate_situation_selection(session, [row.id], task_uuid="published")
        with pytest.raises(ValueError, match="already consumed"):
            await pipeline.validate_situation_selection(session, [row.id], task_uuid="older-candidate")


async def test_independent_pipeline_freezes_retry_and_acks_only_selected_ids(release_db, tmp_path, monkeypatch):
    maker, service, _ = release_db
    async with maker() as session:
        rows = [PublishDirty(line=line, scope_key="US" if line == "surveillance" else "*",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc)) for line in ("situation", "situation", "surveillance")]
        session.add_all(rows)
        await session.commit()
        ids = [row.id for row in rows]
    @asynccontextmanager
    async def database():
        async with maker() as session:
            yield session
    snapshots = []
    @asynccontextmanager
    async def snapshot():
        snapshots.append("snapshot")
        yield {"TEST_SNAPSHOT": "same"}
    runtime = SimpleNamespace(root_dir=tmp_path, get_database=database, read_snapshot=snapshot,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()),
        generate_timeout_seconds=60, download_publish_timeout_seconds=60, task_model=Task)
    task = SimpleNamespace(task_uuid="situation-test", input_data={"data_only": True,
        "data_line": "situation", "publish_dirty_ids": [ids[0]]})
    store = Store()
    fail = True
    commands = []
    async def command(_uuid, **kwargs):
        nonlocal fail
        cmd = kwargs["cmd"]
        commands.append(cmd)
        if "--capture-baseline-only" in cmd:
            assert snapshots == []
            return
        if "scripts/export_situation_data.py" in cmd:
            assert kwargs["env"]["TEST_SNAPSHOT"] == "same"
            public = tmp_path / "exports/situation-data"
            public.mkdir(parents=True)
            body = b'{"points":[]}'
            sha = hashlib.sha256(body).hexdigest()
            (public / "objects").mkdir()
            (public / f"objects/{sha}.json").write_bytes(body)
            (public / "current.json").write_text(json.dumps({"schema_version": 1,
                "release_id": "situation-situation-test", "files": {"map-data.json":
                {"url": f"objects/{sha}.json", "sha256": sha, "bytes": len(body)}}}))
        else:
            assert cmd[cmd.index("--prefix") + 1] == "site-data/situation"
            assert "--require-starting-baseline" in cmd
            if fail:
                fail = False
                raise RuntimeError("Connection reset")
            publish_surveillance_data(tmp_path / "exports/situation-runs/situation-test", CONFIG,
                prefix="site-data/situation", objects_dir=tmp_path / "exports/situation-data/objects", client=store)
    monkeypatch.setattr(service, "_run_logged_command", command)
    monkeypatch.setattr(service, "_python_executable", lambda: "python")
    monkeypatch.setattr(service, "_cloudflare_account_id", lambda: "account")
    monkeypatch.setattr(service, "_cloudflare_api_token", lambda: "token")
    with pytest.raises(RuntimeError, match="Connection reset"):
        await data_pipeline.execute_data_release(service, task, runtime=runtime)
    async with maker() as session:
        assert not (await session.get(PublishDirty, ids[0])).published_at
    result = await data_pipeline.execute_data_release(service, task, runtime=runtime)
    assert result["data_line"] == "situation" and not result["pages_deployed"]
    assert len(snapshots) == 1
    assert sum("scripts/export_situation_data.py" in cmd for cmd in commands) == 1
    assert sum("--capture-baseline-only" in cmd for cmd in commands) == 1
    async with maker() as session:
        assert (await session.get(PublishDirty, ids[0])).published_task_uuid == task.task_uuid
        assert not (await session.get(PublishDirty, ids[1])).published_at
        assert not (await session.get(PublishDirty, ids[2])).published_at
    assert "site-data/situation/current.json" in store.objects
    assert "site-data/surveillance/current.json" not in store.objects
