"""Waiting model producers release their snapshot transaction and DB permit."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")
from src.ai.peer_review import ReviewedOutput
from src.domain import (
    Base,
    DiseaseKnowledgeSource,
    DiseaseLearningSuggestion,
    LiteratureArticle,
    LiteratureSummary,
    LiteratureStatusEvent,
    StandardDisease,
    PublishDirty,
    TaskType,
    Task,
    TaskStatus,
)
from src.literature.enrichment import SUMMARY_FIELDS, source_fingerprint
from src.services.ai_content_governance_service import AIContentGovernanceService

ABSTRACT = (
    "Patients with dengue were assessed for infection and clinical outcomes. "
    "The investigators measured changes over the study period."
)
QUOTE = 'Relevant because "Patients with dengue were assessed for infection"'


@pytest_asyncio.fixture
async def pool(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wait.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
    )
    tables = [
        DiseaseKnowledgeSource.__table__,
        DiseaseLearningSuggestion.__table__,
        LiteratureArticle.__table__,
        LiteratureSummary.__table__,
        LiteratureStatusEvent.__table__,
        StandardDisease.__table__,
        PublishDirty.__table__,
        Task.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, maker
    await engine.dispose()


def reviewed(decision):
    return ReviewedOutput(
        status="published",
        payload={"decision": decision, "reasons": [QUOTE]},
        author_model="a",
        reviewer_model="b",
        verdict="accept",
        rounds=1,
        score=1.0,
    )


@pytest.mark.parametrize("kind", ["article", "summary", "source", "learning"])
async def test_governance_model_wait_holds_no_checked_out_connection(
    pool, monkeypatch, kind
):
    engine, maker = pool
    async with maker() as db:
        if kind in {"article", "summary"}:
            article = LiteratureArticle(
                article_id="article",
                slug="article",
                title="Infection surveillance",
                abstract_text=ABSTRACT,
                publication_status="published" if kind == "summary" else "review",
            )
            db.add(article)
            await db.flush()
            if kind == "summary":
                db.add(
                    LiteratureSummary(
                        article_id="article",
                        language="en",
                        status="review",
                        generated_by="literature-evidence-agent",
                        quality_score=0.95,
                        evidence_map={field: {"sources": ["abstract"], "confidence": 0.95} for field in SUMMARY_FIELDS},
                        generation_metadata={
                            "source_fingerprint": source_fingerprint(article)
                        },
                        **{field: "Grounded finding" for field in SUMMARY_FIELDS},
                    )
                )
        elif kind == "source":
            db.add(
                DiseaseKnowledgeSource(
                    disease_id="D001",
                    source_type="who",
                    source_name="WHO",
                    url="https://example.test/source",
                    review_status="requires_review",
                )
            )
        else:
            db.add(
                DiseaseLearningSuggestion(
                    country_code="US", local_name="unresolved source category"
                )
            )
        await db.commit()
    service = AIContentGovernanceService()
    calls = []

    async def model(items, *args, **kwargs):
        calls.append(len(items))
        assert engine.pool.checkedout() == 0
        # A dispatcher can borrow the only connection while the parent waits.
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT 1")) == 1
        return [
            reviewed(
                {
                    "article": "publish",
                    "summary": "publish",
                    "source": "approve",
                    "learning": "unmapped",
                }[kind]
            )
            for item in items
        ]

    monkeypatch.setattr(service, "_review_all", model)
    methods = {
        "article": "review_literature_articles",
        "summary": "review_literature_summaries",
        "source": "review_knowledge_sources",
        "learning": "review_learning_suggestions",
    }
    from src.ai.runtime import task_context

    parent = SimpleNamespace(
        task_uuid="review-parent",
        task_type=TaskType.ENRICH_LITERATURE,
        priority="normal",
        input_data={},
    )
    async with task_context(parent):
        async with maker() as db:
            result = await getattr(service, methods[kind])(db)
    async with maker() as db:
        dirty = (await db.execute(select(PublishDirty))).scalars().all()
    if kind == "learning":
        assert dirty == []
    else:
        assert [(r.line, r.scope_key, r.source_task_uuid) for r in dirty] == [
            (
                "knowledge" if kind == "source" else "literature",
                "D001" if kind == "source" else "*",
                "review-parent",
            )
        ]
        assert dirty[0].published_at is None
    assert calls == [1]
    assert result["deferred"] == 0
    assert engine.pool.checkedout() == 0


async def test_governance_does_not_apply_late_reply_over_changed_article(
    pool, monkeypatch
):
    engine, maker = pool
    async with maker() as db:
        db.add(
            LiteratureArticle(article_id="article", slug="article", title="original", abstract_text=ABSTRACT)
        )
        await db.commit()
    service = AIContentGovernanceService()

    async def model(items, *args, **kwargs):
        async with maker() as concurrent:
            article = (await concurrent.execute(select(LiteratureArticle))).scalar_one()
            article.title = "new content after queued prompt"
            await concurrent.commit()
        return [reviewed("publish")]

    monkeypatch.setattr(service, "_review_all", model)
    async with maker() as db:
        result = await service.review_literature_articles(db)
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.title == "new content after queued prompt"
        assert article.publication_status == "review"
    assert result["deferred"] == 1


async def test_mapping_releases_read_transaction_before_model_wait(monkeypatch):
    from src.services import disease_mapping_ai_service as module

    checkout = False
    category = SimpleNamespace(
        id=1, ai_status="pending", ai_attempts=0, country_code="US"
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    class DB:
        async def get(self, *args):
            nonlocal checkout
            checkout = True
            return category

        async def execute(self, *args):
            nonlocal checkout
            checkout = True
            return Result()

        async def commit(self):
            nonlocal checkout
            checkout = False

        async def refresh(self, *args):
            nonlocal checkout
            checkout = True

    service = module.DiseaseMappingAIService()
    monkeypatch.setattr(
        service, "_has_approved_assertion", AsyncMock(side_effect=[False, True])
    )
    monkeypatch.setattr(service, "_deterministic_shortlist", lambda *a: [])
    monkeypatch.setattr(
        module.ModelCenterCompleter,
        "from_model_center",
        AsyncMock(return_value=object()),
    )

    async def model(*args, **kwargs):
        assert checkout is False
        return object()

    monkeypatch.setattr(service, "_run_review", model)
    result = await service.suggest_for_category(DB(), 1)
    assert result["status"] == "not_required"
    assert checkout is False


@pytest.mark.parametrize("kind", ["model", "provider", "mapping"])
async def test_api_releases_id_lookup_before_waiting_for_worker(monkeypatch, kind):
    from dashboard.api.routers import ai, mappings

    checkout = False

    class DB:
        async def execute(self, *args):
            nonlocal checkout
            checkout = True
            return SimpleNamespace(scalar_one_or_none=lambda: 42)

        async def rollback(self):
            nonlocal checkout
            checkout = False

    async def worker(*args, **kwargs):
        assert checkout is False
        return {"status": "completed"}

    monkeypatch.setattr(ai, "bootstrap_model_center_from_env", AsyncMock())
    monkeypatch.setattr(ai, "run_worker_operation", worker)
    monkeypatch.setattr(mappings, "run_worker_operation", worker)
    if kind == "mapping":
        result = await mappings.suggest_mapping_v3("category", db=DB())
    else:
        result = await getattr(ai, f"test_{kind}")("key", db=DB())
    assert result["status"] == "completed"


async def test_report_service_finishes_lookup_transaction_before_generation(
    monkeypatch,
):
    from src.services import report_service as module
    import src.generation as generation
    from src.domain import ReportStatus

    checkout = False
    country = SimpleNamespace(id=1, code="JP", name="Japan")
    report = SimpleNamespace(id=4, report_uuid="report-4", status=ReportStatus.APPROVED)

    class DB:
        async def commit(self):
            nonlocal checkout
            checkout = False

    @asynccontextmanager
    async def database():
        yield DB()

    service = module.ReportService()

    async def get_country(*args):
        nonlocal checkout
        checkout = True
        return country

    class Generator:
        async def generate(self, **kwargs):
            assert checkout is False
            return report

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(service, "_get_country", get_country)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        service, "_adjust_period_to_available_data", AsyncMock(return_value=(now, now))
    )
    monkeypatch.setattr(
        service, "_select_reuse_candidate", AsyncMock(return_value=(None, []))
    )
    monkeypatch.setattr(service, "_ensure_task_not_cancelled", AsyncMock())
    monkeypatch.setattr(service, "_collect_output_files", lambda _: [])
    monkeypatch.setattr(service, "_get_report_section_count", AsyncMock(return_value=0))
    monkeypatch.setattr(module.task_manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(module.task_manager, "update_task_progress", AsyncMock())
    monkeypatch.setattr(module.task_manager, "link_task_report", AsyncMock())
    monkeypatch.setattr(generation, "ReportGenerator", Generator)
    result = await service.execute(
        task=SimpleNamespace(task_uuid="task"),
        country_code="JP",
        report_type="weekly",
        period_start_iso=None,
        period_end_iso=None,
        language="zh",
        days=30,
        enable_review=True,
        send_email=False,
        reuse_from_failed=False,
    )
    assert result.report_id == 4


async def test_governance_store_failure_rolls_back_review_and_dirty_together(
    pool, monkeypatch
):
    engine, maker = pool
    async with maker() as db:
        db.add(
            LiteratureArticle(article_id="article", slug="article", title="original", abstract_text=ABSTRACT)
        )
        await db.commit()
    service = AIContentGovernanceService()
    monkeypatch.setattr(
        service, "_review_all", AsyncMock(return_value=[reviewed("publish")])
    )
    from src.ai.runtime import task_context

    parent = SimpleNamespace(
        task_uuid="review-parent",
        task_type=TaskType.ENRICH_LITERATURE,
        priority="normal",
        input_data={},
    )
    async with task_context(parent):
        async with maker() as db:
            commit = db.commit
            calls = 0

            async def fail_store():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("store transaction failed")
                await commit()

            monkeypatch.setattr(db, "commit", fail_store)
            with pytest.raises(RuntimeError, match="store transaction failed"):
                await service.review_literature_articles(db)
            await db.rollback()
    async with maker() as db:
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.publication_status == "review"
        assert (await db.execute(select(PublishDirty))).scalars().all() == []
        assert (await db.execute(select(LiteratureStatusEvent))).scalars().all() == []


@pytest.mark.parametrize("entry", ["batch", "application"])
@pytest.mark.parametrize("decision,grounded,expected", [
    ("hold", True, "review"),
    ("publish", True, "published"),
    ("exclude", True, "excluded"),
    ("publish", False, "review"),
    ("exclude", False, "review"),
    ("reject", True, "review"),
])
async def test_article_entry_points_share_application_rules(
    pool, monkeypatch, entry, decision, grounded, expected
):
    _engine, maker = pool
    service = AIContentGovernanceService()
    async with maker() as db:
        article = LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes",
            abstract_text=ABSTRACT,
        )
        db.add(article)
        await db.commit()
        fingerprint = service._article_fingerprint(article)
    review = reviewed(decision)
    if not grounded:
        review.payload["reasons"] = ["No supporting evidence was provided."]
    monkeypatch.setattr(service, "_review_all", AsyncMock(return_value=[review]))
    from src.ai.runtime import task_context

    parent = SimpleNamespace(
        task_uuid="review-parent", task_type=TaskType.ENRICH_LITERATURE,
        priority="normal", input_data={},
    )
    async with task_context(parent):
        async with maker() as db:
            if entry == "batch":
                await service.review_literature_articles(db)
            else:
                (await service._apply_literature_results(db, "articles", [("article", fingerprint, review)])).counts
                await db.commit()
    async with maker() as db:
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.publication_status == expected
        events = (await db.execute(select(LiteratureStatusEvent))).scalars().all()
        dirty = (await db.execute(select(PublishDirty))).scalars().all()
        if expected == "review":
            assert events == dirty == []
            assert article.metadata_["ai_content_governance"]["decision"] == "hold"
            if not grounded:
                assert article.metadata_["screening_hold_reason"] == "abstract_evidence_not_verified"
        else:
            assert [event.current_status for event in events] == [expected]
            assert len(dirty) == 1


@pytest.mark.parametrize("entry", ["batch", "application"])
async def test_article_review_preserves_editorial_lock(pool, monkeypatch, entry):
    _engine, maker = pool
    service = AIContentGovernanceService()
    async with maker() as db:
        article = LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes",
            abstract_text=ABSTRACT, metadata_={"editorial_locked": True},
        )
        db.add(article)
        await db.commit()
        fingerprint = service._article_fingerprint(article)
    model = AsyncMock(return_value=[reviewed("publish")])
    monkeypatch.setattr(service, "_review_all", model)
    async with maker() as db:
        if entry == "batch":
            result = await service.review_literature_articles(db)
            assert result["reviewed"] == 0
        else:
            result = (await service._apply_literature_results(
                db, "articles", [("article", fingerprint, reviewed("publish"))]
            )).counts
            assert result["stale"] == 1
            await db.commit()
    async with maker() as db:
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.publication_status == "review"
        assert article.metadata_ == {"editorial_locked": True}


@pytest.mark.parametrize("entry", ["batch", "application"])
async def test_explicit_retry_fences_old_batch_and_application_results(pool, monkeypatch, entry):
    from src.ai.runtime import task_context
    from src.services.exceptions import TaskExecutionSupersededError

    _engine, maker = pool
    service = AIContentGovernanceService()
    task = Task(task_uuid="review-parent", task_type=TaskType.ENRICH_LITERATURE,
                task_name="review", status=TaskStatus.RUNNING, input_data={}, metadata_={})
    async with maker() as db:
        row = LiteratureArticle(article_id="article", slug="article", title="Dengue outcomes", abstract_text=ABSTRACT)
        db.add_all([task, row])
        await db.commit()
        fingerprint = service._article_fingerprint(row)
    monkeypatch.setattr(service, "_review_all", AsyncMock(return_value=[reviewed("publish")]))
    async with task_context(task):
        async with maker() as db:
            current = await db.scalar(select(Task).where(Task.task_uuid == task.task_uuid))
            current.metadata_ = {"ai_execution_generation": 1}
            await db.commit()
        async with maker() as db:
            with pytest.raises(TaskExecutionSupersededError):
                if entry == "batch":
                    await service.review_literature_articles(db)
                else:
                    await service._apply_literature_results(db, "articles", [("article", fingerprint, reviewed("publish"))])
    async with maker() as db:
        assert (await db.scalar(select(LiteratureArticle))).publication_status == "review"
        assert (await db.execute(select(PublishDirty))).scalars().all() == []
        assert (await db.execute(select(LiteratureStatusEvent))).scalars().all() == []


@pytest.mark.parametrize("cancel_flag", ["status", "metadata"])
async def test_completed_review_cannot_apply_after_parent_cancel(
    pool, monkeypatch, cancel_flag
):
    _engine, maker = pool
    task = Task(
        task_uuid="review-parent", task_type=TaskType.ENRICH_LITERATURE,
        task_name="review", status=TaskStatus.RUNNING, input_data={}, metadata_={},
    )
    async with maker() as db:
        db.add(task)
        db.add(LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes", abstract_text=ABSTRACT,
        ))
        await db.commit()
    service = AIContentGovernanceService()

    async def model(items, *args, **kwargs):
        assert len(items) == 1
        async with maker() as concurrent:
            parent = (await concurrent.execute(select(Task))).scalar_one()
            if cancel_flag == "status":
                parent.status = TaskStatus.CANCELLED
            else:
                parent.metadata_ = {"cancel_requested": True}
            await concurrent.commit()
        return [reviewed("publish")]

    monkeypatch.setattr(service, "_review_all", model)
    from src.ai.runtime import task_context
    from src.services.exceptions import TaskCancelledError

    async with task_context(task):
        async with maker() as db:
            with pytest.raises(TaskCancelledError):
                await service.review_literature_articles(db)
            await db.rollback()
    async with maker() as db:
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.publication_status == "review"
        assert (await db.execute(select(LiteratureStatusEvent))).scalars().all() == []
        assert (await db.execute(select(PublishDirty))).scalars().all() == []


@pytest.mark.parametrize("entry", ["batch", "application"])
async def test_summary_quality_review_does_not_require_article_screening_quotes(
    pool, monkeypatch, entry
):
    _engine, maker = pool
    service = AIContentGovernanceService()
    async with maker() as db:
        article = LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes",
            publication_status="published", abstract_text=ABSTRACT,
        )
        db.add(article)
        await db.flush()
        summary = LiteratureSummary(
            article_id="article", language="en", status="review",
            generated_by="literature-evidence-agent", quality_score=0.95,
            generation_metadata={"source_fingerprint": source_fingerprint(article)},
            evidence_map={field: {"sources": ["abstract"], "confidence": 0.95} for field in SUMMARY_FIELDS},
            **{field: "Grounded finding" for field in SUMMARY_FIELDS},
        )
        db.add(summary)
        await db.commit()
        fingerprint, summary_id = service._summary_fingerprint(summary), summary.id
    review = reviewed("publish")
    review.payload["reasons"] = ["The summary is aligned with its evidence map."]
    monkeypatch.setattr(service, "_review_all", AsyncMock(return_value=[review]))
    async with maker() as db:
        if entry == "batch":
            result = await service.review_literature_summaries(db)
        else:
            result = (await service._apply_literature_results(db, "summaries", [(summary_id, fingerprint, review)])).counts
            await db.commit()
        assert result["published"] == 1
    async with maker() as db:
        assert (await db.get(LiteratureSummary, summary_id)).status == "published"


@pytest.mark.parametrize("dry_run", [False, True])
async def test_incomplete_summary_archive_uses_atomic_application_boundary(pool, monkeypatch, dry_run):
    _engine, maker = pool
    async with maker() as db:
        article = LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes",
            publication_status="published", abstract_text=ABSTRACT,
        )
        db.add(article)
        await db.flush()
        summary = LiteratureSummary(
            article_id="article", language="en", status="review",
            generated_by="literature-evidence-agent",
            generation_metadata={"source_fingerprint": source_fingerprint(article)},
        )
        db.add(summary)
        await db.commit()
        summary_id = summary.id
    service = AIContentGovernanceService()
    model = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "_review_all", model)
    from src.ai.runtime import task_context

    parent = SimpleNamespace(
        task_uuid="review-parent", task_type=TaskType.ENRICH_LITERATURE,
        priority="normal", input_data={},
    )
    async with task_context(parent):
        async with maker() as db:
            result = await service.review_literature_summaries(db, dry_run=dry_run)
            assert result["archived"] == 1
            assert result["applied"][0]["decision"] == "archive"
    assert model.call_args.args[0] == []
    async with maker() as db:
        assert (await db.get(LiteratureSummary, summary_id)).status == ("review" if dry_run else "archived")
        assert len((await db.execute(select(PublishDirty))).scalars().all()) == (0 if dry_run else 1)


async def test_dry_run_review_does_not_change_status_events_or_dirty_state(pool, monkeypatch):
    _engine, maker = pool
    async with maker() as db:
        db.add(LiteratureArticle(
            article_id="article", slug="article", title="Dengue outcomes", abstract_text=ABSTRACT,
        ))
        await db.commit()
    service = AIContentGovernanceService()
    monkeypatch.setattr(service, "_review_all", AsyncMock(return_value=[reviewed("publish")]))
    async with maker() as db:
        result = await service.review_literature_articles(db, dry_run=True)
        assert result["published"] == 1
        await db.commit()
    async with maker() as db:
        article = (await db.execute(select(LiteratureArticle))).scalar_one()
        assert article.publication_status == "review"
        assert article.metadata_ == {}
        assert (await db.execute(select(LiteratureStatusEvent))).scalars().all() == []
        assert (await db.execute(select(PublishDirty))).scalars().all() == []
