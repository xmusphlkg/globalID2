"""Real database checks for literature writes and their publication ownership."""

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from dashboard.api.routers import literature as router
from dashboard.api.schemas.literature import LiteratureArticleUpdate
from src.ai import work_queue
from src.core.config import LiteratureSettings
from src.domain import (
    Base, LiteratureArticle, LiteratureEvidenceGap, LiteratureIngestRun,
    LiteratureSignalArticleLink, LiteratureStatusEvent, LiteratureSummary,
    Task, TaskPriority, TaskStatus, TaskType,
)
from src.domain.collection import PublishDirty
from src.literature import metadata_backfill as backfill
from src.literature.transactions import LiteratureCommitError, commit_literature_change
from src.literature.types import ArticleCandidate, Classification, Match
from src.services import literature_gap_service as gaps
from src.services.ingestion import stages


NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


@pytest.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [
        table for name, table in Base.metadata.tables.items()
        if name.startswith("literature_") or name in {"tasks", "publish_dirty", "standard_diseases"}
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    monkeypatch.setattr(gaps, "get_db", db)
    monkeypatch.setattr(gaps, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(
        gap_discovery_enabled=True, gap_discovery_candidate_limit=1,
        europe_pmc_enabled=False, pubmed_enabled=False, autopilot_enabled=False,
    )))
    monkeypatch.setattr(work_queue, "current_parent", lambda: None)
    async with db() as session:
        session.add(LiteratureArticle(
            article_id="article", slug="article", title="Dengue surveillance and public health in Japan",
            doi="10.1000/article", journal="Public Health", authors=[{"name": "Editor"}],
            published_at=NOW, publication_status="published",
        ))
        session.add(LiteratureEvidenceGap(
            gap_id="gap", signal_id="signal", signal_kind="disease", signal_section="national",
            disease_id="D001", disease_name="Dengue", country_codes=["JP"], country_names=["Japan"],
            gap_type="catalogue_coverage_gap", first_detected_at=NOW, last_detected_at=NOW,
            status="open", latest_metrics={},
        ))
        session.add(LiteratureSignalArticleLink(
            id=1, gap_id="gap", signal_id="signal", article_id="article",
            relation_level="candidate", status="review", source="fixture", confidence=0.9,
        ))
    try:
        yield db
    finally:
        await engine.dispose()


async def seed_parent(database):
    task_uuid = str(uuid4())
    async with database() as session:
        session.add(Task(
            task_uuid=task_uuid, task_type=TaskType.DISCOVER_LITERATURE_GAPS,
            task_name="Real parent", status=TaskStatus.RUNNING, priority=TaskPriority.NORMAL,
        ))
    return task_uuid


def fail_dirty(monkeypatch, mode):
    original = stages.mark_dirty

    async def broken(db, **kwargs):
        if mode == "before_add":
            raise RuntimeError("dirty write failed")
        await original(db, **kwargs)
        if mode == "after_add":
            # Flush formal rows and the marker before failing, to exercise rollback
            # of actual database writes rather than just pending ORM objects.
            await db.flush()
            raise RuntimeError("dirty write failed after flush")
        if mode == "insert":
            db.add(PublishDirty(
                line="invalid", scope_key="*", source_task_uuid=kwargs["task_uuid"], marked_at=NOW,
            ))

    monkeypatch.setattr(stages, "mark_dirty", broken)


async def assert_owner(database, *, parent=None, count=1):
    async with database() as session:
        dirty = list((await session.execute(select(PublishDirty).order_by(PublishDirty.id))).scalars())
        tasks = list((await session.execute(select(Task))).scalars())
        assert len(dirty) == count
        by_uuid = {task.task_uuid: task for task in tasks}
        for row in dirty:
            assert (row.line, row.scope_key, row.published_at) == ("literature", "*", None)
            assert row.source_task_uuid in by_uuid
            if parent:
                assert row.source_task_uuid == parent
                assert len(tasks) == 1
                assert by_uuid[parent].status == TaskStatus.RUNNING
            else:
                operation = by_uuid[row.source_task_uuid]
                assert operation.status == TaskStatus.COMPLETED
                assert operation.task_type == TaskType.PROCESS_DATA
                assert operation.metadata_["execution_mode"] == "synchronous"
                assert operation.input_data["operation"]
        if not count:
            assert len(tasks) == (1 if parent else 0)


@pytest.mark.parametrize("ownership", ["manual", "explicit", "context"])
async def test_successful_write_has_real_parent_or_atomic_operation(database, monkeypatch, ownership):
    parent = await seed_parent(database) if ownership != "manual" else None
    if ownership == "context":
        monkeypatch.setattr(work_queue, "current_parent", lambda: SimpleNamespace(task_uuid=parent))
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        article.title = "Updated public title"
        source = await commit_literature_change(
            session, operation="test_edit", task_uuid=parent if ownership == "explicit" else None,
            actor="editor@example.org", details={"article_id": "article"},
        )
    await assert_owner(database, parent=parent)
    async with database() as session:
        assert (await session.execute(select(LiteratureArticle.title))).scalar_one() == "Updated public title"
        task = (await session.execute(select(Task).where(Task.task_uuid == source))).scalar_one()
        if not parent:
            assert task.input_data["actor"] == "editor@example.org"
            assert task.input_data["details"] == {"article_id": "article"}


@pytest.mark.parametrize("mode", ["before_add", "after_add", "insert", "commit", "unknown_parent"])
async def test_failed_write_rolls_back_even_when_caller_catches(database, monkeypatch, mode):
    if mode in {"before_add", "after_add", "insert"}:
        fail_dirty(monkeypatch, mode)
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        article.title = "Must roll back"
        if mode == "commit":
            async def broken_commit():
                await session.flush()
                raise RuntimeError("commit failed")
            monkeypatch.setattr(session, "commit", broken_commit)
        with pytest.raises(LiteratureCommitError):
            await commit_literature_change(
                session, operation="failed_edit", task_uuid=str(uuid4()) if mode == "unknown_parent" else None,
            )
        if mode == "commit":
            monkeypatch.undo()
    await assert_owner(database, count=0)
    async with database() as session:
        assert (await session.execute(select(LiteratureArticle.title))).scalar_one() != "Must roll back"


@pytest.mark.parametrize("mode", ["success", "after_add", "insert"])
@pytest.mark.parametrize("operation", ["review_link", "update_gap", "limit_candidates", "gap_error", "refresh"])
async def test_gap_mutations_and_dirty_are_atomic(database, monkeypatch, mode, operation):
    service = gaps.LiteratureGapService()
    if operation == "limit_candidates":
        async with database() as session:
            session.add(LiteratureArticle(article_id="second", slug="second", title="Second candidate"))
            session.add(LiteratureSignalArticleLink(
                id=2, gap_id="gap", signal_id="signal", article_id="second", relation_level="candidate",
                status="review", source="fixture", confidence=0.1,
            ))
    if operation == "refresh":
        monkeypatch.setattr(gaps, "latest_report_v3", AsyncMock(return_value={"snapshot_id": "new-snapshot"}))
        monkeypatch.setattr(service, "_catalogues", AsyncMock(return_value=([], [], {})))
        monkeypatch.setattr(gaps, "build_surveillance_evidence", lambda *args, **kwargs: {
            "signals": [], "evidence_gaps": [], "visibility": "public", "metrics": {"active_signals": 0},
        })
    if mode != "success":
        fail_dirty(monkeypatch, mode)

    async def mutate():
        if operation == "review_link":
            return await service.review_link(
                1, status="confirmed", relation_level="exact_disease_geography",
                reviewer="editor@example.org", note="Verified",
            )
        if operation == "update_gap":
            return await service.update_gap("gap", status="dismissed", note="Not needed")
        if operation == "limit_candidates":
            return await service.enforce_candidate_limits()
        if operation == "gap_error":
            return await service._mark_gap_error("gap", RuntimeError("provider unavailable"))
        return await service.refresh_from_snapshot()

    if mode == "success":
        await mutate()
    else:
        with pytest.raises(LiteratureCommitError):
            await mutate()
    await assert_owner(database, count=1 if mode == "success" else 0)
    async with database() as session:
        gap = (await session.execute(select(LiteratureEvidenceGap))).scalar_one()
        link = (await session.execute(select(LiteratureSignalArticleLink).where(LiteratureSignalArticleLink.id == 1))).scalar_one()
        expected = {"review_link": "covered", "update_gap": "dismissed", "gap_error": "error", "refresh": "inactive"}
        assert gap.status == (expected.get(operation, "open") if mode == "success" else "open")
        assert link.status == ("confirmed" if operation == "review_link" and mode == "success" else "review")
        if operation == "limit_candidates":
            overflow = await session.get(LiteratureSignalArticleLink, 2)
            assert overflow.status == ("deprioritized" if mode == "success" else "review")
            assert bool(gap.latest_metrics) == (mode == "success")


@pytest.mark.parametrize("mode", ["success", "after_add", "insert"])
async def test_manual_article_status_summary_event_and_dirty_share_transaction(database, monkeypatch, mode):
    refresh = AsyncMock()
    monkeypatch.setattr(router.literature_gap_service, "refresh_from_snapshot", refresh)
    request = Request({"type": "http", "headers": []})
    request.state.user = SimpleNamespace(email="editor@example.org")
    body = LiteratureArticleUpdate(
        publication_status="excluded", summary_language="en",
        summary={"main_findings": "Reviewed findings"}, summary_status="draft",
    )
    if mode != "success":
        fail_dirty(monkeypatch, mode)
    async with database() as session:
        if mode == "success":
            result = await router.update_article("article", body, request, session)
            assert result.data.publication_status == "excluded"
        else:
            with pytest.raises(LiteratureCommitError):
                await router.update_article("article", body, request, session)
    await assert_owner(database, count=1 if mode == "success" else 0)
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        summaries = list((await session.execute(select(LiteratureSummary))).scalars())
        events = list((await session.execute(select(LiteratureStatusEvent))).scalars())
        assert article.publication_status == ("excluded" if mode == "success" else "published")
        assert len(summaries) == len(events) == (1 if mode == "success" else 0)
        assert bool(article.metadata_.get("editorial_locked")) == (mode == "success")
        if mode == "success":
            dirty = (await session.execute(select(PublishDirty))).scalar_one()
            refresh.assert_awaited_once_with(task_uuid=dirty.source_task_uuid)
        else:
            refresh.assert_not_awaited()


async def test_rejected_manual_edit_rolls_back_without_dirty_or_operation(database):
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException):
        async with database() as session:
            await router.update_article(
                "article", LiteratureArticleUpdate(publication_status="excluded", is_featured=True), request, session,
            )
    await assert_owner(database, count=0)
    async with database() as session:
        assert (await session.execute(select(LiteratureArticle.publication_status))).scalar_one() == "published"


class MetadataProvider:
    async def enrich_by_dois(self, dois, **kwargs):
        return {
            doi: {"id": "https://openalex.org/W123", "doi": f"https://doi.org/{doi}"}
            for doi in dois
        }


@pytest.mark.parametrize("mode", ["success", "dry_run", "after_add", "insert"])
async def test_backfill_commits_only_with_dirty_and_advances_checkpoint_after_commit(database, monkeypatch, tmp_path, mode):
    checkpoint = tmp_path / "checkpoint.json"
    if mode in {"after_add", "insert"}:
        fail_dirty(monkeypatch, mode)
    kwargs = dict(
        apply=mode != "dry_run", providers=("openalex",), batch_size=1, limit=1,
        checkpoint_path=checkpoint, config=LiteratureSettings(), db_factory=database,
        min_interval_seconds=0, openalex_client=MetadataProvider(),
    )
    if mode in {"after_add", "insert"}:
        with pytest.raises(LiteratureCommitError):
            await backfill.backfill_existing_literature_metadata(**kwargs)
    else:
        await backfill.backfill_existing_literature_metadata(**kwargs)
    await assert_owner(database, count=1 if mode == "success" else 0)
    assert checkpoint.exists() == (mode == "success")
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        assert article.openalex_id == ("W123" if mode == "success" else None)
        assert article.publication_status == "published"


async def test_no_match_backfill_and_empty_limit_queue_do_not_create_dirty(database, tmp_path):
    provider = SimpleNamespace(enrich_by_dois=AsyncMock(return_value={}))
    await backfill.backfill_existing_literature_metadata(
        apply=True, providers=("openalex",), checkpoint_path=tmp_path / "checkpoint.json",
        config=LiteratureSettings(), db_factory=database, min_interval_seconds=0,
        openalex_client=provider,
    )
    await assert_owner(database, count=0)
    # The single existing candidate is already within the configured queue limit.
    await gaps.LiteratureGapService().enforce_candidate_limits()
    # The first enforcement fills in queue metrics; subsequent enforcement is a no-op.
    await assert_owner(database)
    await gaps.LiteratureGapService().enforce_candidate_limits()
    await assert_owner(database)


async def test_failed_backfill_batch_keeps_previous_commit_and_checkpoint(database, monkeypatch, tmp_path):
    async with database() as session:
        session.add(LiteratureArticle(
            article_id="second", slug="second", title="Second article", doi="10.1000/second",
        ))
    checkpoint = tmp_path / "checkpoint.json"
    parent = await seed_parent(database)
    original = stages.mark_dirty
    calls = 0

    async def marker(session, **kwargs):
        nonlocal calls
        calls += 1
        await original(session, **kwargs)
        await session.flush()
        if calls == 2:
            raise RuntimeError("dirty write failed in second batch")

    # A provider ID is unique per article, including across separate batches.
    provider = SimpleNamespace(enrich_by_dois=AsyncMock(side_effect=[
        {"10.1000/article": {"id": "https://openalex.org/W1"}},
        {"10.1000/second": {"id": "https://openalex.org/W2"}},
    ]))
    monkeypatch.setattr(stages, "mark_dirty", marker)
    with pytest.raises(LiteratureCommitError, match="second batch"):
        await backfill.backfill_existing_literature_metadata(
            apply=True, providers=("openalex",), batch_size=1, checkpoint_path=checkpoint,
            config=LiteratureSettings(), db_factory=database, min_interval_seconds=0,
            openalex_client=provider, task_uuid=parent,
        )
    await assert_owner(database, parent=parent)
    assert json.loads(checkpoint.read_text())["last_database_id"] == 1
    async with database() as session:
        rows = list((await session.execute(select(LiteratureArticle).order_by(LiteratureArticle.id))).scalars())
        assert [row.openalex_id for row in rows] == ["W1", None]


async def test_cancellation_rolls_back_flushed_facts_and_marker(database, monkeypatch):
    original = stages.mark_dirty

    async def cancelled(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise asyncio.CancelledError()

    monkeypatch.setattr(stages, "mark_dirty", cancelled)
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        article.title = "Cancelled title"
        with pytest.raises(asyncio.CancelledError):
            await commit_literature_change(session, operation="cancelled")
    await assert_owner(database, count=0)
    async with database() as session:
        assert (await session.execute(select(LiteratureArticle.title))).scalar_one() != "Cancelled title"


@pytest.mark.parametrize("failure", [None, "start", "store"])
async def test_discovery_marks_each_formal_transaction_with_real_parent(database, monkeypatch, failure):
    parent = await seed_parent(database)
    candidate = ArticleCandidate(
        article_id="article", slug="article", title="New public bibliographic title from gap discovery",
        doi="10.1000/article", journal="Public Health", published_at=NOW,
    )
    fetch = AsyncMock(return_value=[{"fixture": True}])
    monkeypatch.setattr(gaps, "CrossrefClient", lambda **kwargs: SimpleNamespace(search_works=fetch))
    monkeypatch.setattr(gaps, "normalize_crossref", lambda raw: candidate)
    monkeypatch.setattr(gaps, "classify_candidate", lambda *args, **kwargs: Classification(
        diseases=[Match("D001", "Dengue", 0.95)], countries=[Match("JP", "Japan", 0.95)],
    ))
    original = stages.mark_dirty
    calls = 0

    async def marker(session, **kwargs):
        nonlocal calls
        calls += 1
        await original(session, **kwargs)
        await session.flush()
        if calls == {"start": 1, "store": 2}.get(failure):
            raise RuntimeError("dirty write failed")

    monkeypatch.setattr(stages, "mark_dirty", marker)
    service = gaps.LiteratureGapService()

    async def discover():
        return await service._discover_gap(
            "gap", disease_catalogue=[], country_catalogue=[], taxonomy={}, now=NOW, task_uuid=parent,
        )

    if failure:
        with pytest.raises(LiteratureCommitError):
            await discover()
    else:
        result = await discover()
        assert result["updated"] == result["candidate_links"] == 1
    await assert_owner(database, parent=parent, count={None: 2, "start": 0, "store": 1}[failure])
    async with database() as session:
        article = (await session.execute(select(LiteratureArticle))).scalar_one()
        gap = (await session.execute(select(LiteratureEvidenceGap))).scalar_one()
        link = (await session.execute(select(LiteratureSignalArticleLink))).scalar_one()
        assert (article.title == candidate.title) == (failure is None)
        assert gap.status == {None: "review", "start": "open", "store": "searching"}[failure]
        assert link.relation_level == ("exact_disease_geography" if failure is None else "candidate")
    if failure == "start":
        fetch.assert_not_awaited()


@pytest.mark.parametrize("fail_store", [False, True])
async def test_gap_execute_propagates_parent_and_does_not_swallow_commit_failure(database, monkeypatch, fail_store):
    parent = await seed_parent(database)
    service = gaps.LiteratureGapService()
    refresh = AsyncMock(return_value={"available": False})
    monkeypatch.setattr(service, "refresh_from_snapshot", refresh)
    monkeypatch.setattr(service, "_catalogues", AsyncMock(return_value=([], [], {})))
    monkeypatch.setattr(gaps, "_load_json", lambda path: {})
    monkeypatch.setattr(gaps.task_manager, "update_task_progress", AsyncMock())
    limits = AsyncMock(return_value={"deprioritized": 0})
    monkeypatch.setattr(service, "enforce_candidate_limits", limits)
    record_error = AsyncMock()
    monkeypatch.setattr(service, "_mark_gap_error", record_error)

    async def discover(gap_id, **kwargs):
        assert kwargs["task_uuid"] == parent
        if fail_store:
            raise LiteratureCommitError("dirty write failed")
        return {"candidate_links": 0}

    monkeypatch.setattr(service, "_discover_gap", discover)
    async with database() as session:
        task = (await session.execute(select(Task))).scalar_one()
    if fail_store:
        with pytest.raises(LiteratureCommitError, match="dirty write failed"):
            await service.execute(task)
        limits.assert_not_awaited()
    else:
        await service.execute(task)
        limits.assert_awaited_once_with(task_uuid=parent)
    refresh.assert_awaited_once_with(task_uuid=parent)
    record_error.assert_not_awaited()
    async with database() as session:
        run = (await session.execute(select(LiteratureIngestRun))).scalar_one()
        assert run.task_uuid == parent
        assert run.status == ("failed" if fail_store else "completed")
