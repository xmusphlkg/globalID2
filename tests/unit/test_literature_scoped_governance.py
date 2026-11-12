"""Batch governance preserves unrelated decisions and complete gap coverage."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.ai import runtime as work_queue
from src.core.config import LiteratureSettings
from src.domain import (
    Base,
    LiteratureArticle,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    LiteratureSummary,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.services import literature_automation_service as automation
from src.services import model_operations


async def test_explicit_empty_scope_never_queues_or_reads_the_corpus(monkeypatch):
    service = automation.LiteratureAutomationService()
    monkeypatch.setattr(automation, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(autopilot_enabled=True)))
    transaction = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "_reconcile_database", transaction)
    monkeypatch.setattr(model_operations, "enqueue_model_operation", enqueue)

    result = await service.reconcile(article_ids=[])

    assert result["status"] == "not_required"
    assert result["scope"] == "articles"
    assert result["scope_article_count"] == 0
    transaction.assert_not_awaited()
    enqueue.assert_not_awaited()


async def test_scoped_governance_keeps_scope_when_queued_outside_worker(monkeypatch):
    monkeypatch.setattr(automation, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(autopilot_enabled=True)))
    monkeypatch.setattr(work_queue, "current_parent", lambda: None)
    enqueue = AsyncMock(return_value=SimpleNamespace(task_uuid="queued-governance"))
    monkeypatch.setattr(model_operations, "enqueue_model_operation", enqueue)

    result = await automation.LiteratureAutomationService().reconcile(article_ids=["b", "a", "a"])

    assert result["status"] == "queued"
    assert enqueue.await_args.args[1]["article_ids"] == ["a", "b"]


@pytest.mark.parametrize("dry_run", [False, True])
async def test_scope_changes_only_touched_articles_and_uses_all_gap_links(monkeypatch, dry_run):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [
        table for name, table in Base.metadata.tables.items()
        if name.startswith("literature_") or name in {"tasks", "publish_dirty"}
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    task_uuid = str(uuid4())
    at = datetime.now(timezone.utc)
    try:
        async with database() as db:
            db.add(Task(id=1, task_uuid=task_uuid, task_type=TaskType.SYNC_LITERATURE,
                        task_name="Sync", status=TaskStatus.RUNNING, priority=TaskPriority.NORMAL))
            for index, article_id in enumerate(("target", "outside", "unrelated"), 1):
                db.add(LiteratureArticle(
                    id=index, article_id=article_id, slug=article_id, title="Test evidence",
                    publication_status="published" if article_id == "outside" else "review",
                ))
                db.add(LiteratureSummary(
                    id=index, article_id=article_id, language="en", status="review",
                    main_findings="A test finding", generation_metadata={},
                ))
            for index, gap_id in enumerate(("touched-gap", "other-gap"), 1):
                db.add(LiteratureEvidenceGap(
                    id=index, gap_id=gap_id, signal_id=gap_id, signal_kind="test",
                    signal_section="test", disease_id="dengue", disease_name="Dengue",
                    gap_type="geography", status="covered", first_detected_at=at,
                    last_detected_at=at, latest_metrics={},
                ))
            db.add_all([
                LiteratureSignalArticleLink(id=1, gap_id="touched-gap", signal_id="signal-1", article_id="target", relation_level="exact_disease_geography", status="review", source="test"),
                LiteratureSignalArticleLink(id=2, gap_id="touched-gap", signal_id="signal-1", article_id="outside", relation_level="exact_disease_geography", status="confirmed", source="test"),
                LiteratureSignalArticleLink(id=3, gap_id="touched-gap", signal_id="signal-1", article_id="unrelated", relation_level="candidate", status="review", source="test"),
                LiteratureSignalArticleLink(id=4, gap_id="other-gap", signal_id="signal-2", article_id="unrelated", relation_level="candidate", status="review", source="test"),
            ])
        monkeypatch.setattr(automation, "get_db", database)
        monkeypatch.setattr(automation, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(autopilot_enabled=True)))
        monkeypatch.setattr(work_queue, "current_parent", lambda: SimpleNamespace(task_uuid=task_uuid))
        evaluated = {"article": [], "link": [], "summary": []}

        def decide_article(article, *_args, **_kwargs):
            evaluated["article"].append(article.article_id)
            return automation.AutomationDecision("exclude", ("test",))

        def decide_link(link, *_args, **_kwargs):
            evaluated["link"].append(link.id)
            return automation.AutomationDecision("reject", ("test",))

        def decide_summary(summary, *_args, **_kwargs):
            evaluated["summary"].append(summary.article_id)
            return automation.AutomationDecision("archive", ("test",))

        monkeypatch.setattr(automation, "decide_article", decide_article)
        monkeypatch.setattr(automation, "decide_evidence_link", decide_link)
        monkeypatch.setattr(automation, "decide_summary", decide_summary)
        result = await automation.LiteratureAutomationService().reconcile(
            article_ids=["target"], dry_run=dry_run, batch_size=1,
        )

        assert evaluated == {"article": ["target"], "link": [1], "summary": ["target"]}
        assert result["scope"] == "articles"
        assert result["scope_article_count"] == 1
        assert result["gaps_reopened"] == 0
        assert result["articles_excluded"] == 1
        assert result["summaries_archived"] == 1
        async with database() as db:
            articles = {row.article_id: row for row in (await db.execute(select(LiteratureArticle))).scalars()}
            summaries = {row.article_id: row for row in (await db.execute(select(LiteratureSummary))).scalars()}
            gaps = {row.gap_id: row for row in (await db.execute(select(LiteratureEvidenceGap))).scalars()}
            links = {row.id: row for row in (await db.execute(select(LiteratureSignalArticleLink))).scalars()}
            runs = list((await db.execute(select(LiteratureIngestRun))).scalars())
            assert articles["target"].publication_status == ("review" if dry_run else "excluded")
            assert summaries["target"].status == ("review" if dry_run else "archived")
            assert articles["outside"].publication_status == "published"
            assert articles["unrelated"].publication_status == "review"
            assert summaries["outside"].status == summaries["unrelated"].status == "review"
            assert links[1].status == ("review" if dry_run else "rejected")
            assert links[3].status == links[4].status == "review"
            assert gaps["touched-gap"].status == "covered"
            assert gaps["other-gap"].latest_metrics == {}
            if dry_run:
                assert runs == []
            else:
                assert len(runs) == 1
                assert runs[0].source == "research-radar-autopilot-scoped"
                assert runs[0].checkpoint["scope"] == "articles"
                assert runs[0].checkpoint["scope_article_count"] == 1
    finally:
        await engine.dispose()
