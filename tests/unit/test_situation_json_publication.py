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
        assert [row.source_task_uuid for row in markers] == ["grouped"]
    # A changed attachment can be reviewed again without changing public facts.
    from src.domain import SituationDocument
    from src.core.database import get_db
    async with get_db() as session:
        await session.execute(update(SituationDocument).values(text_hash="different-input-hash"))
        await session.execute(update(SituationNotice).values(ai_status="pending", ai_next_attempt_at=None))
    assert (await extract_pending(Scripted([DRAFT], [{"verdict": "accept", "issues": []}]), task_uuid="same-facts"))["published"] == 1
    await rebuild_outbreaks(as_of=AS_OF, task_uuid="same-groups")
    async with db() as session:
        assert len(list((await session.execute(select(PublishDirty))).scalars())) == 1


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
    assert len(mapping["filter_points"]) == 3
    assert all("notice_id" in point for point in mapping["filter_points"])
    assert all(set(point).isdisjoint({"quote", "summary_en", "summary_zh", "summary_fr", "ai", "url", "cases", "deaths"}) for point in mapping["filter_points"])
    ids = {row["outbreak_id"] for row in mapping["points"]}
    assert len(ids) == 1 and None not in ids
    for outbreak_id in ids:
        assert f"outbreaks/{outbreak_id}.json" in manifest["files"]
    weeks = [json.loads((public / descriptor["url"]).read_text())
             for name, descriptor in manifest["files"].items()
             if name.startswith("weekly/") and name != "weekly/index.json"]
    assert weeks
    for week in weeks:
        summaries = {row["outbreak_id"]: row for row in week["outbreaks"]}
        assert set(week["new_outbreaks"] + week["updated_outbreaks"]) <= summaries.keys()
        assert all(row["disease_name_en"] and row["country_codes"] for row in summaries.values())
        assert all("alerts" not in row for row in summaries.values())
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


async def test_history_filter_index_keeps_older_reports_after_latest_place_marker_selection(db, tmp_path):
    await store_notices(_run(
        _event("history", ref={"kind": "who_don", "slug": "history"}, published="2026-10-21"),
        _event("latest", ref={"kind": "who_don", "slug": "latest"}, published="2026-10-23"),
    ), run_id="history")
    await fetch_pending_documents(fetch=_fetcher([]))
    assert (await extract_pending(Scripted([DRAFT, DRAFT], [
        {"verdict": "accept", "issues": []}, {"verdict": "accept", "issues": []},
    ])))["published"] == 2
    # The older channel must remain filterable although the latest marker uses
    # another channel. Each place still has a single default marker.
    async with db() as session:
        await session.execute(update(SituationAlert).where(
            SituationAlert.published_date == datetime(2026, 10, 21).date(),
        ).values(source_id="ecdc_events"))
        await session.commit()
    public = tmp_path / "public"
    await export_json_publication(tmp_path / "state", public, release_id="history-index")
    mapping = json.loads((public / "map-data.json").read_text())
    assert len(mapping['points']) == 3
    assert {point['source_id'] for point in mapping['points']} == {'who_don'}
    assert len(mapping['filter_points']) == 6
    historical = [point for point in mapping['filter_points'] if point['source_id'] == 'ecdc_events']
    assert len(historical) == 3
    assert {point['published_date'] for point in historical} == {'2026-10-21'}
    assert {point['country_code'] for point in historical} == {'CD', 'KE', 'NG'}
    assert all(point['outbreak_id'] for point in historical)
    assert b'SECRET-FULL-TEXT-MARKER' not in (public / 'map-data.json').read_bytes()


async def test_monitoring_publication_structures_coverage_even_without_alerts(db, tmp_path):
    from src.domain import SituationDocument, SituationExtraction
    notice_id = 'who_don:monitoring-coverage'
    await store_notices(_run(_event('monitoring-coverage', ref={'kind': 'who_don', 'slug': 'coverage'})), run_id='coverage')
    async with db() as session:
        await session.execute(update(SituationNotice).where(SituationNotice.notice_id == notice_id).values(
            title_en='Weekly surveillance roundup', ai_status='extracted'))
        await session.execute(update(SituationDocument).where(SituationDocument.notice_id == notice_id).values(
            status='ready', text='# Contents\n## Influenza – Japan\nNothing unusual.\nSECRET-FULL-TEXT-MARKER'))
        session.add(SituationExtraction(notice_id=notice_id, document_hash='coverage-document',
            prompt_version='test', status='published', document_type='monitoring_product', alert_count=0,
            review_notes=[]))
        await session.commit()
    public = tmp_path / 'public'
    await export_json_publication(tmp_path / 'state', public, release_id='coverage')
    mapping = json.loads((public / 'map-data.json').read_text())
    assert mapping['points'] == [] and mapping['filter_points'] == []
    product, = mapping['monitoring']
    assert product['title_en'] == 'Weekly surveillance roundup'
    assert product['alert_count'] == 0
    assert product['disease_ids'] == ['D038']
    assert product['country_codes'] == ['JP']
    assert b'SECRET-FULL-TEXT-MARKER' not in (public / 'map-data.json').read_bytes()
