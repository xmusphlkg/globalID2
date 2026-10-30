"""Knowledge task transaction boundaries, using real isolated SQL storage.

Model prose quality has its own tests. Here accepted model payloads are doubles;
source qualification, stage persistence, formal upserts and dirty rows are real.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")

from src.ai import work_queue
from src.domain import Base, DiseaseKnowledgeBrief, DiseaseKnowledgeSource, PublishDirty, RunStage
from src.knowledge import DiseaseKnowledgeFetcher, SourceFetchReport, attach_profile_schema
from src.services import disease_knowledge_service as knowledge
from src.services.ingestion import stages


async def noop(*_args, **_kwargs):
    pass


def task(task_uuid="brief", **inputs):
    return SimpleNamespace(
        task_uuid=str(uuid5(NAMESPACE_URL, task_uuid)),
        input_data={"disease_id": "D066", "generator": "ai", **inputs},
        tags=[],
    )


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.sqlite'}")
    tables = [model.__table__ for model in (
        DiseaseKnowledgeBrief, DiseaseKnowledgeSource, PublishDirty, RunStage,
    )]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(active_sessions=0, writes=[], fetches=0, models=[], observe=False)

    @asynccontextmanager
    async def database():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    @asynccontextmanager
    async def business_database():
        state.active_sessions += 1
        try:
            async with database() as db:
                yield db
        finally:
            state.active_sessions -= 1

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def check_formal_write(_conn, _cursor, statement, _params, _context, _many):
        sql = statement.lower().strip()
        if state.observe and sql.startswith(("insert", "update", "delete")) and any(
            table in sql for table in ("disease_knowledge_sources", "disease_knowledge_briefs", "publish_dirty")
        ):
            run = service._stage_run
            assert run is not None and run.current == "store", sql
            state.writes.append((run.task_uuid, run.attempt, run.current, sql))

    disease = attach_profile_schema({
        "disease_id": "D066",
        "name_en": "Other viral infections characterized by skin lesions",
        "name_zh": "其他以皮损为特征的病毒感染",
        "icd_10": "B08", "source": "ICD-10",
    })
    candidate = DiseaseKnowledgeFetcher._build_icd10_definition_source(disease)
    assert candidate is not None
    service = object.__new__(knowledge.DiseaseKnowledgeUpdateService)
    service.fetcher = object()
    monkeypatch.setattr(service, "_find_disease", lambda _id: disease)
    monkeypatch.setattr(knowledge, "get_database", business_database)
    monkeypatch.setattr(stages, "get_db", database)
    monkeypatch.setattr(knowledge, "_log_task", noop)
    monkeypatch.setattr(knowledge, "_raise_if_cancelled", noop)
    monkeypatch.setattr(knowledge, "_acquire_disease_knowledge_lock", noop)
    monkeypatch.setattr(knowledge.task_manager, "update_task_progress", noop)

    async def fetch(*_args, **_kwargs):
        assert state.active_sessions == 0
        assert service._stage_run.current == "collect"
        state.fetches += 1
        return SourceFetchReport([candidate], {"registry_definition": "success"}, {})

    async def generate(**kwargs):
        assert state.active_sessions == 0, "Model waiting must not retain a business session"
        assert service._stage_run.current == "organize"
        state.models.append(kwargs["language"])
        await asyncio.sleep(0)
        source = kwargs["sources"][0]
        return {"payload": {
            "disease_id": "D066", "language": kwargs["language"], "status": "published",
            "brief": "A source grounded overview of this registry category [1].",
            "definition": "The registry defines this group of viral infections with skin lesions [1].",
            "source_ids": [source["id"]],
            "source_attribution": [{"source_id": source["id"], "url": source["url"]}],
        }, "trace": {}}

    monkeypatch.setattr(knowledge, "_fetch_sources_for_update", fetch)
    generator = SimpleNamespace(generate_with_trace=generate)
    monkeypatch.setattr(knowledge, "_generator_for_mode", lambda *_args: generator)
    monkeypatch.setattr(knowledge, "_generated_profile_failures", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(knowledge, "_bilingual_alignment_failures", lambda *_args: [])

    async def seed():
        async with database() as db:
            await knowledge._upsert_source(db, candidate)

    async def rows(model):
        async with database() as db:
            return list((await db.execute(select(model).order_by(model.id))).scalars().all())

    state.service, state.database, state.rows = service, database, rows
    state.seed, state.generator, state.candidate = seed, generator, candidate
    yield state
    await engine.dispose()


def assert_stages(rows, *, attempts=(1,), failed=None, source_only=False):
    del source_only  # Briefs and source refreshes both complete collect.
    assert len(rows) == 3 * len(attempts)
    for attempt in attempts:
        current = {row.stage: row for row in rows if row.attempt == attempt}
        assert set(current) == {"collect", "organize", "store"}
        if failed == "collect":
            assert current["collect"].status == "failed"
            assert current["organize"].status == current["store"].status == "skipped"
            continue
        assert current["collect"].status == "completed"
        if failed:
            assert current[failed].status == "failed"
            if failed == "organize":
                assert current["store"].status == "skipped"
            else:
                assert current["organize"].status == "completed"
        else:
            assert current["organize"].status == current["store"].status == "completed"


async def test_source_refresh_writes_only_in_store_and_retries_once(harness):
    h = harness
    h.observe = True
    for attempt in (1, 2):
        result = await h.service.execute_source_refresh_task(task("sources"))
        assert result["source_only"] is True
        assert result["source_gap"] is False
        assert len(await h.rows(DiseaseKnowledgeSource)) == 1
        assert len(await h.rows(PublishDirty)) == attempt
    assert_stages(await h.rows(RunStage), attempts=(1, 2), source_only=True)
    assert h.fetches == 2 and not h.models
    assert all(row.scope_key == "sources" for row in await h.rows(PublishDirty))
    assert h.writes and h.service._stage_run is None


@pytest.mark.parametrize("source_only", [True, False])
async def test_dirty_failure_rolls_back_formal_writes(harness, monkeypatch, source_only):
    h = harness
    if not source_only:
        await h.seed()
    original = stages.StageRun.mark_dirty

    async def fail_after_dirty(run, db, scope):
        await original(run, db, scope)
        await db.flush()
        raise RuntimeError("dirty transaction failed")

    monkeypatch.setattr(stages.StageRun, "mark_dirty", fail_after_dirty)
    h.observe = True
    execute = h.service.execute_source_refresh_task if source_only else h.service.execute_task
    with pytest.raises(RuntimeError, match="dirty transaction failed"):
        await execute(task())
    assert not await h.rows(PublishDirty)
    assert not await h.rows(DiseaseKnowledgeBrief)
    if source_only:
        assert not await h.rows(DiseaseKnowledgeSource)
    assert_stages(await h.rows(RunStage), failed="store", source_only=source_only)


@pytest.mark.parametrize("inputs", [
    {}, {"force": True}, {"targeted_repair": True},
    {"source_refreshed_task_uuid": "certificate", "force": True},
])
async def test_brief_reads_stored_sources_without_session_during_models(harness, inputs):
    h = harness
    await h.seed()
    h.observe = True
    result = await h.service.execute_task(task(**inputs))
    assert result["fetched_sources"] == 0
    assert h.fetches == 0 and h.models == ["en", "zh"]
    assert len(await h.rows(DiseaseKnowledgeBrief)) == 2
    assert [row.scope_key for row in await h.rows(PublishDirty)] == ["D066"]
    assert_stages(await h.rows(RunStage))


@pytest.mark.parametrize("dry_run", [False, True])
async def test_missing_stored_sources_fails_without_fetch_or_nested_attempt(harness, dry_run):
    h = harness
    h.observe = True
    with pytest.raises(knowledge.KnowledgeEvidenceInsufficientError):
        await h.service.execute_task(task(targeted_repair=True, force=True, dry_run=dry_run))
    assert h.fetches == 0 and not h.models and not h.writes
    assert_stages(await h.rows(RunStage), failed="organize")


async def test_update_task_rejects_source_only_payload(harness):
    with pytest.raises(ValueError, match="REFRESH_DISEASE_KNOWLEDGE_SOURCES"):
        await harness.service.execute_task(task(source_only=True))
    assert harness.fetches == 0
    assert_stages(await harness.rows(RunStage), failed="collect")


@pytest.mark.parametrize("swallow_failure", [False, True])
async def test_any_model_child_failure_fails_parent_without_brief_writes(harness, monkeypatch, swallow_failure):
    h = harness
    await h.seed()
    original = h.generator.generate_with_trace
    parent = work_queue.ParentContext("brief", "knowledge_brief", "low")

    async def generate(**kwargs):
        assert h.active_sessions == 0
        if kwargs["language"] == "zh":
            parent.failures.append("ZH child failed")
            if not swallow_failure:
                raise work_queue.AIWorkFailed("ZH child failed")
        return await original(**kwargs)

    monkeypatch.setattr(h.generator, "generate_with_trace", generate)
    token = work_queue._PARENT.set(parent)
    h.observe = True
    try:
        with pytest.raises(work_queue.AIWorkFailed, match="ZH child failed"):
            await h.service.execute_task(task())
    finally:
        work_queue._PARENT.reset(token)
    assert not await h.rows(DiseaseKnowledgeBrief)
    assert not await h.rows(PublishDirty)
    assert_stages(await h.rows(RunStage), failed="organize")


@pytest.mark.parametrize("change", ["source", "brief"])
async def test_concurrent_edit_during_model_wait_is_preserved(harness, monkeypatch, change):
    h = harness
    await h.seed()
    original = h.generator.generate_with_trace

    async def generate(**kwargs):
        assert h.active_sessions == 0
        if kwargs["language"] == "en":
            async with h.database() as db:
                if change == "source":
                    row = (await db.execute(select(DiseaseKnowledgeSource))).scalar_one()
                    row.content_text = "Another refresh replaced this evidence."
                else:
                    db.add(DiseaseKnowledgeBrief(
                        disease_id="D066", language="en", brief="Another task's current brief",
                    ))
        return await original(**kwargs)

    monkeypatch.setattr(h.generator, "generate_with_trace", generate)
    with pytest.raises(knowledge.KnowledgeEvidenceInsufficientError, match="changed during generation"):
        await h.service.execute_task(task())
    assert not await h.rows(PublishDirty)
    briefs = await h.rows(DiseaseKnowledgeBrief)
    assert [row.brief for row in briefs] == (["Another task's current brief"] if change == "brief" else [])
    assert_stages(await h.rows(RunStage), failed="store")


async def test_concurrent_tasks_on_one_service_keep_their_stage_run(harness, monkeypatch):
    h = harness
    entered = []
    both_entered = asyncio.Event()

    async def execute(current_task):
        entered.append(current_task.task_uuid)
        if len(entered) == 2:
            both_entered.set()
        await both_entered.wait()
        assert h.service._stage_run.task_uuid == current_task.task_uuid
        await h.service._stage_run.transition("organize")
        await h.service._stage_run.transition("store")
        return {}

    monkeypatch.setattr(h.service, "_execute_task", execute)
    await asyncio.gather(h.service.execute_task(task("first")), h.service.execute_task(task("second")))
    rows = await h.rows(RunStage)
    for task_uuid in ("first", "second"):
        assert_stages([row for row in rows if row.task_uuid == task(task_uuid).task_uuid])
    assert h.service._stage_run is None


async def test_second_brief_write_failure_rolls_back_first_brief_and_dirty(harness, monkeypatch):
    h = harness
    await h.seed()
    original = knowledge._upsert_brief

    async def write_then_fail(db, payload):
        row = await original(db, payload)
        if payload["language"] == "zh":
            raise RuntimeError("second brief write failed")
        return row

    monkeypatch.setattr(knowledge, "_upsert_brief", write_then_fail)
    h.observe = True
    with pytest.raises(RuntimeError, match="second brief write failed"):
        await h.service.execute_task(task())
    assert not await h.rows(DiseaseKnowledgeBrief)
    assert not await h.rows(PublishDirty)
    assert_stages(await h.rows(RunStage), failed="store")


async def test_non_public_archival_is_store_and_marks_dirty(harness, monkeypatch):
    h = harness
    disease = {"disease_id": "D999", "name_en": "Total", "category": "Summary"}
    monkeypatch.setattr(h.service, "_find_disease", lambda _id: disease)
    async with h.database() as db:
        db.add(DiseaseKnowledgeBrief(disease_id="D999", language="en", brief="Old summary", status="published"))
    h.observe = True
    result = await h.service.execute_task(task(disease_id="D999"))
    assert result["archived_profile_count"] == 1
    assert (await h.rows(DiseaseKnowledgeBrief))[0].status == "archived"
    assert [row.scope_key for row in await h.rows(PublishDirty)] == ["D999"]
    rows = await h.rows(RunStage)
    assert_stages(rows)
    collect = next(row for row in rows if row.stage == "collect")
    assert collect.metrics["step"] == "registry"
    assert collect.metrics["source_only"] is False
    assert not h.fetches and not h.models


async def test_superseded_repair_completes_stages_without_model_or_dirty(harness, monkeypatch):
    h = harness
    h.observe = True

    async def superseded(*_args, **_kwargs):
        assert h.service._stage_run.current == "collect"
        return True

    monkeypatch.setattr(knowledge, "_automatic_model_repair_is_superseded", superseded)
    result = await h.service.execute_task(
        task(targeted_repair=True, source_refreshed_task_uuid="certificate")
    )
    assert result["skip_reason"] == "superseded_by_newer_published_profile"
    assert h.fetches == 0 and not h.models and not h.writes
    assert not await h.rows(PublishDirty)
    assert not await h.rows(DiseaseKnowledgeBrief)
    rows = await h.rows(RunStage)
    assert_stages(rows)
    assert {row.metrics["skip_reason"] for row in rows} == {
        "superseded_by_newer_published_profile"
    }
    assert all(row.metrics["brief_written"] is False for row in rows)


async def test_source_fetch_failure_leaves_three_rows_and_no_formal_changes(harness, monkeypatch):
    async def fail_fetch(*_args, **_kwargs):
        assert harness.active_sessions == 0
        raise RuntimeError("source failed")

    monkeypatch.setattr(knowledge, "_fetch_sources_for_update", fail_fetch)
    with pytest.raises(RuntimeError, match="source failed"):
        await harness.service.execute_source_refresh_task(task())
    rows = await harness.rows(RunStage)
    assert len(rows) == 3 and {row.attempt for row in rows} == {1}
    assert {row.stage: row.status for row in rows} == {
        "collect": "failed", "organize": "skipped", "store": "skipped",
    }
    assert not await harness.rows(DiseaseKnowledgeSource)
    assert not await harness.rows(PublishDirty)


async def test_cancellation_during_model_wait_releases_session_and_marks_failure(harness, monkeypatch):
    h = harness
    await h.seed()
    waiting = asyncio.Event()

    async def wait_for_model(**_kwargs):
        assert h.active_sessions == 0
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.generator, "generate_with_trace", wait_for_model)
    running = asyncio.create_task(h.service.execute_task(task()))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert h.active_sessions == 0
    assert not await h.rows(DiseaseKnowledgeBrief)
    assert not await h.rows(PublishDirty)
    assert_stages(await h.rows(RunStage), failed="organize")


async def test_translation_child_failure_does_not_fall_back_to_another_model(harness, monkeypatch):
    h = harness
    await h.seed()

    async def failed_translation(**_kwargs):
        assert h.active_sessions == 0
        raise work_queue.AIWorkFailed("translation child failed")

    monkeypatch.setattr(knowledge.get_config().ai, "knowledge_translate_zh_from_en", True)
    monkeypatch.setattr(knowledge, "_translation_source_usable", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(h.generator, "translate_from_payload_with_trace", failed_translation, raising=False)
    with pytest.raises(work_queue.AIWorkFailed, match="translation child failed"):
        await h.service.execute_task(task())
    assert h.models == ["en"]
    assert not await h.rows(DiseaseKnowledgeBrief)
    assert not await h.rows(PublishDirty)
    assert_stages(await h.rows(RunStage), failed="organize")
