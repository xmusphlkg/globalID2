"""Durable literature review resumes without coroutine pools or process-local claims."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai import runtime
from src.ai.runtime import payloads, store
from src.domain import (
    AIReviewDrain,
    AIReviewWorkflow,
    AIWorkItem,
    Base,
    LiteratureArticle,
    LiteratureSummary,
    LiteratureStatusEvent,
    PublishDirty,
    Task,
    TaskStatus,
    TaskType,
)
from src.literature.enrichment import SUMMARY_FIELDS, source_fingerprint
from src.literature.review_workflow import run_literature_review
from src.literature.review_workflow import repository as module
from src.literature.review_workflow import coordinator
from src.literature.review_workflow.repository import ReviewWorkflowRepository, now
from src.services.ai_content_governance_service import AIContentGovernanceService
from src.services.exceptions import TaskCancelledError

ABSTRACT = "Patients with dengue were assessed for infection and clinical outcomes. The investigators measured changes over the study period."
QUOTE = 'Relevant because "Patients with dengue were assessed for infection"'
SETTINGS = {
    "buffer_size": 4,
    "max_items": None,
    "allow_degraded_review": False,
    "deadline_seconds": 21600,
}


@pytest.mark.parametrize(
    "capacity,budget,expected",
    [
        (217, 512, 434),
        (400, 512, 512),
        (8, 512, 64),
        (0, 512, 64),
        (217, 128, 128),
        (217, 1, 1),
        (1, 32, 32),
    ],
)
def test_supply_target_tracks_text_permits_within_explicit_budget(
    capacity, budget, expected
):
    routes = [
        {"available_for_routing": True, "runtime_model_capacity": capacity},
        {"available_for_routing": False, "runtime_model_capacity": 1000},
        {
            "available_for_routing": True,
            "runtime_model_capacity": 1000,
            "capabilities": ["image"],
        },
    ]
    assert coordinator.review_buffer_target(routes, budget) == expected


@pytest_asyncio.fixture
async def workflow_db(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite")
    test_url = os.environ.get("TEST_REVIEW_DATABASE_URL")
    schema = f"test_review_{uuid4().hex}" if test_url else None
    admin = create_async_engine(test_url) if test_url else None
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        test_url or f"sqlite+aiosqlite:///{tmp_path / 'workflows.db'}",
        pool_size=5 if test_url else 1,
        max_overflow=0,
        pool_timeout=2,
        **(
            {"connect_args": {"server_settings": {"search_path": schema}}}
            if test_url
            else {}
        ),
    )
    tables = [
        Task.__table__,
        AIWorkItem.__table__,
        AIReviewDrain.__table__,
        AIReviewWorkflow.__table__,
        LiteratureArticle.__table__,
        LiteratureSummary.__table__,
        LiteratureStatusEvent.__table__,
        PublishDirty.__table__,
    ]
    async with engine.begin() as connection:
        if not test_url:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
        # Include foreign-key dependencies without touching another schema.
        for table in tables:
            for foreign_key in table.foreign_keys:
                if foreign_key.column.table not in tables:
                    tables.append(foreign_key.column.table)
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=tables)
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(module, "get_database", database)

    async def routes():
        return [
            {
                "available_for_routing": True,
                "runtime_model_capacity": 217,
                "capabilities": ["text"],
            }
        ]

    monkeypatch.setattr(coordinator, "get_runtime_routes", routes)
    monkeypatch.setattr(payloads, "ROOT", tmp_path)
    repo = ReviewWorkflowRepository(AIContentGovernanceService())
    async with database() as db:
        db.add_all(
            [
                Task(
                    task_uuid=name,
                    task_name=name,
                    task_type=TaskType.AGENT_WORKFLOW,
                    status=TaskStatus.RUNNING,
                    priority="low",
                    input_data={},
                    metadata_={},
                )
                for name in ("parent", "other")
            ]
        )
    yield SimpleNamespace(engine=engine, maker=maker, database=database, repo=repo)
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def article(env, key="a", **kwargs):
    async with env.database() as db:
        row = LiteratureArticle(
            article_id=key,
            slug=key,
            title="Dengue surveillance",
            abstract_text=ABSTRACT,
            **kwargs,
        )
        db.add(row)
        await db.flush()
        return row


async def initialise(env, *, kind="articles", parent="parent", **settings):
    await env.repo.ensure_drain(parent, kind, {**SETTINGS, **settings})
    await env.repo.feed(parent, kind, target_size=512)
    return (await env.repo.open_work(parent, kind))[0]


async def complete(env, workflow_id, text, *, model=None, status="succeeded"):
    async with env.database() as db:
        workflow = await db.get(AIReviewWorkflow, workflow_id)
        item = await db.get(AIWorkItem, workflow.activity_id)
        model = model or ("provider:a" if workflow.step == "author" else "provider:b")
        item.status = status
        item.route_key = model
        item.result = {
            "text": text,
            "conversation": {"metadata": {"runtime_route": {"model_key": model}}},
        }


async def accepted(env, workflow_id, *, decision="publish", reason=QUOTE):
    await env.repo.advance("parent", workflow_id)
    await complete(
        env, workflow_id, json.dumps({"decision": decision, "reasons": [reason]})
    )
    await env.repo.advance("parent", workflow_id)
    await complete(env, workflow_id, '{"verdict":"accept"}')
    await env.repo.advance("parent", workflow_id)


async def test_restart_after_each_step_reuses_activity_and_atomically_publishes(
    workflow_db,
):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    assert await env.repo.advance("parent", work_id)
    env.repo = ReviewWorkflowRepository(AIContentGovernanceService())
    assert await env.repo.advance("parent", work_id) is False
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 1
    await complete(
        env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
    )
    await env.repo.advance("parent", work_id)
    env.repo = ReviewWorkflowRepository(AIContentGovernanceService())
    assert await env.repo.advance("parent", work_id) is False
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        item = await db.get(AIWorkItem, work.activity_id)
        assert item.payload["require_distinct_model"] is True
        assert set(item.payload["avoid_models"]) == {"provider:a", "a"}
    await complete(env, work_id, '{"verdict":"accept"}')
    await env.repo.advance("parent", work_id)
    assert await env.repo.advance("parent", work_id) is False
    async with env.database() as db:
        assert (
            await db.scalar(select(LiteratureArticle))
        ).publication_status == "published"
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 2
        assert await db.scalar(select(func.count(LiteratureStatusEvent.id))) == 1
        assert await db.scalar(select(func.count(PublishDirty.id))) == 1
        work = await db.get(AIReviewWorkflow, work_id)
        assert (
            work.outcome == "published"
            and work.state["history"][0]["author_model"] == "provider:a"
        )


async def test_global_subject_guard_and_finite_cursor_bound_buffer(workflow_db):
    env = workflow_db
    for i in range(7):
        await article(env, str(i))
    await env.repo.ensure_drain("parent", "articles", {**SETTINGS, "buffer_size": 2})
    await env.repo.ensure_drain("other", "articles", {**SETTINGS, "buffer_size": 2})
    counts = await asyncio.gather(
        env.repo.feed("parent", "articles", target_size=512),
        env.repo.feed("parent", "articles", target_size=512),
    )
    assert sum(counts) == 2
    assert await env.repo.feed("other", "articles", target_size=512) == 0
    assert await env.repo.feed("other", "articles", target_size=512) == 2
    async with env.database() as db:
        rows = (await db.execute(select(AIReviewWorkflow))).scalars().all()
        assert len({row.subject_id for row in rows}) == len(rows) == 4
        assert (await db.get(AIReviewDrain, ("parent", "articles", 0))).cursor == 2
        assert (await db.get(AIReviewDrain, ("other", "articles", 0))).cursor == 4
    # A late arrival belongs to a later parent, including after restart.
    await article(env, "late")
    old = await env.repo.ensure_drain(
        "parent", "articles", {**SETTINGS, "buffer_size": 99}
    )
    assert old["buffer_size"] == 2
    async with env.database() as db:
        assert (
            await db.get(AIReviewDrain, ("parent", "articles", 0))
        ).high_watermark == 7


async def test_two_parents_racing_admission_share_global_subject_guard(workflow_db):
    env = workflow_db
    await article(env)
    await env.repo.ensure_drain("parent", "articles", SETTINGS)
    await env.repo.ensure_drain("other", "articles", SETTINGS)
    admitted = await asyncio.gather(
        env.repo.feed("parent", "articles", target_size=512),
        env.repo.feed("other", "articles", target_size=512),
    )
    assert sorted(admitted) == [0, 1]
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIReviewWorkflow.id))) == 1


async def test_duplicate_coordinators_serialize_each_transition(workflow_db):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    assert sorted(
        await asyncio.gather(
            env.repo.advance("parent", work_id), env.repo.advance("parent", work_id)
        )
    ) == [False, True]
    await complete(
        env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
    )
    assert sorted(
        await asyncio.gather(
            env.repo.advance("parent", work_id), env.repo.advance("parent", work_id)
        )
    ) == [False, True]
    await complete(env, work_id, '{"verdict":"accept"}')
    assert sorted(
        await asyncio.gather(
            env.repo.advance("parent", work_id), env.repo.advance("parent", work_id)
        )
    ) == [False, True]
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 2
        assert await db.scalar(select(func.count(LiteratureStatusEvent.id))) == 1
        assert await db.scalar(select(func.count(PublishDirty.id))) == 1


async def test_summary_quality_review_uses_its_own_policy_without_article_quotes(
    workflow_db,
):
    env = workflow_db
    parent = await article(env, publication_status="published")
    async with env.database() as db:
        db.add(
            LiteratureSummary(
                article_id="a",
                language="en",
                status="review",
                generated_by="literature-evidence-agent",
                quality_score=0.95,
                generation_metadata={"source_fingerprint": source_fingerprint(parent)},
                evidence_map={
                    field: {"sources": ["abstract"], "confidence": 0.95}
                    for field in SUMMARY_FIELDS
                },
                **{field: "Faithful grounded finding" for field in SUMMARY_FIELDS},
            )
        )
    [work_id] = await initialise(env, kind="summaries")
    await accepted(env, work_id, reason="The evidence map supports the summary.")
    async with env.database() as db:
        assert (await db.get(AIReviewWorkflow, work_id)).outcome == "published"
        assert (await db.scalar(select(LiteratureSummary))).status == "published"


@pytest.mark.parametrize(
    "change", ["fingerprint", "editorial", "cancel_status", "cancel_metadata"]
)
async def test_late_result_cannot_overwrite_change_or_cancel(workflow_db, change):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    await complete(
        env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
    )
    await env.repo.advance("parent", work_id)
    await complete(env, work_id, '{"verdict":"accept"}')
    async with env.database() as db:
        if change.startswith("cancel"):
            parent = await db.scalar(select(Task).where(Task.task_uuid == "parent"))
            if change == "cancel_status":
                parent.status = TaskStatus.CANCELLED
            else:
                parent.metadata_ = {"cancel_requested": True}
        else:
            row = await db.scalar(select(LiteratureArticle))
            if change == "editorial":
                row.metadata_ = {"editorial_locked": True}
            else:
                row.title = "Edited by another transaction"
    if change.startswith("cancel"):
        with pytest.raises(TaskCancelledError):
            await env.repo.advance("parent", work_id)
        await env.repo.cancel_open("parent")
    else:
        await env.repo.advance("parent", work_id)
    async with env.database() as db:
        assert (
            await db.scalar(select(LiteratureArticle))
        ).publication_status == "review"
        assert await db.scalar(select(func.count(PublishDirty.id))) == 0
        assert await db.scalar(select(func.count(LiteratureStatusEvent.id))) == 0


@pytest.mark.parametrize(
    "decision,grounded,outcome,status",
    [
        ("publish", True, "published", "published"),
        ("exclude", True, "rejected", "excluded"),
        ("publish", False, "held", "review"),
        ("exclude", False, "held", "review"),
    ],
)
async def test_article_evidence_gate_is_applied_in_workflow(
    workflow_db, decision, grounded, outcome, status
):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await accepted(
        env, work_id, decision=decision, reason=QUOTE if grounded else "No quotation"
    )
    async with env.database() as db:
        assert (await db.scalar(select(LiteratureArticle))).publication_status == status
        assert (await db.get(AIReviewWorkflow, work_id)).outcome == outcome


async def test_two_opinion_hold_is_parked_and_does_not_loop(workflow_db):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    for _ in range(2):
        await accepted(env, work_id, decision="hold")
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        assert work.outcome == "held" and work.state["opinion"] == 2
        assert len(work.state["history"]) == 2
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 4
    await initialise(env, parent="other")
    assert (await env.repo.totals("other", "articles"))["claimed"] == 0


async def test_store_failure_rolls_back_transition_and_no_duplicate_on_retry(
    workflow_db, monkeypatch
):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    await complete(
        env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
    )
    original = store.submit_activities

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("store transaction failed")

    monkeypatch.setattr(store, "submit_activities", fail)
    with pytest.raises(RuntimeError, match="store transaction failed"):
        await env.repo.advance("parent", work_id)
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        assert work.step == "author" and work.state["author_model"] is None
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 1
    monkeypatch.setattr(store, "submit_activities", original)
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 2


async def test_dirty_failure_rolls_back_workflow_and_article_together(
    workflow_db, monkeypatch
):
    from src.services import ai_content_governance_service as governance

    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    await complete(
        env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
    )
    await env.repo.advance("parent", work_id)
    await complete(env, work_id, '{"verdict":"accept"}')

    async def fail(*args, **kwargs):
        raise RuntimeError("dirty failed")

    monkeypatch.setattr(governance, "_mark_review_dirty", fail)
    with pytest.raises(RuntimeError, match="dirty failed"):
        await env.repo.advance("parent", work_id)
    async with env.database() as db:
        assert (await db.get(AIReviewWorkflow, work_id)).status == "open"
        assert (
            await db.scalar(select(LiteratureArticle))
        ).publication_status == "review"
        assert await db.scalar(select(func.count(LiteratureStatusEvent.id))) == 0


@pytest.mark.parametrize("terminal", ["deadline", "failed", "same_model"])
async def test_unavailable_or_failed_models_defer_without_excluding(
    workflow_db, terminal
):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    if terminal == "deadline":
        async with env.database() as db:
            (await db.get(AIReviewWorkflow, work_id)).deadline_at = now() - timedelta(
                seconds=1
            )
    elif terminal == "failed":
        await complete(env, work_id, "", status="failed")
    else:
        await complete(
            env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
        )
        await env.repo.advance("parent", work_id)
        await complete(env, work_id, '{"verdict":"accept"}', model="another-provider:a")
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        assert work.outcome == "deferred"
        assert (
            await db.scalar(select(LiteratureArticle))
        ).publication_status == "review"
        if terminal == "deadline":
            assert (await db.get(AIWorkItem, work.activity_id)).status == "cancelled"
    await initialise(env, parent="other")
    assert (await env.repo.totals("other", "articles"))["claimed"] == 1


async def test_workflow_deadline_keeps_leased_permit_until_transport_stops(workflow_db):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        work.deadline_at = now() - timedelta(seconds=1)
        item = await db.get(AIWorkItem, work.activity_id)
        item.status, item.lease_owner, item.lease_token = (
            "leased",
            "executor",
            "lease-token",
        )
        item.leased_at = now()
        item.lease_expires_at = item.deadline_at = now() + timedelta(minutes=1)
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        item = await db.get(AIWorkItem, work.activity_id)
        assert work.outcome == "deferred"
        assert item.cancel_requested is True
        assert item.status == "leased" and item.lease_token == "lease-token"
        assert item.lease_expires_at is not None and item.deadline_at is not None


async def test_policy_upgrade_defers_existing_state_and_cancels_old_activity(
    workflow_db,
):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        (await db.get(AIReviewWorkflow, work_id)).policy_version = "obsolete-policy"
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        work = await db.get(AIReviewWorkflow, work_id)
        assert work.outcome == "deferred"
        assert (await db.get(AIWorkItem, work.activity_id)).status == "cancelled"


async def test_summary_prechecks_advance_cursor_without_model_work(workflow_db):
    env = workflow_db
    parent = await article(env, publication_status="published")
    async with env.database() as db:
        db.add_all(
            [
                LiteratureSummary(
                    article_id="a",
                    language="en",
                    generated_by="editor",
                    status="review",
                    generation_metadata={
                        "source_fingerprint": source_fingerprint(parent)
                    },
                ),
                LiteratureSummary(
                    article_id="a",
                    language="zh",
                    generated_by="literature-evidence-agent",
                    status="review",
                    generation_metadata={
                        "source_fingerprint": source_fingerprint(parent)
                    },
                ),
                LiteratureSummary(
                    article_id="a",
                    language="fr",
                    generated_by="literature-evidence-agent",
                    status="review",
                ),
            ]
        )
    ids = await initialise(env, kind="summaries")
    for work_id in ids:
        await env.repo.advance("parent", work_id)
    totals = await env.repo.totals("parent", "summaries")
    assert totals["skipped"] == totals["waiting"] == totals["archived"] == 1
    assert totals["cursor"] == 3 and totals["exhausted"]
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 0
        assert await db.scalar(select(func.count(PublishDirty.id))) == 1


async def test_coordinator_wait_releases_connection_and_restart_preserves_work(
    workflow_db, monkeypatch
):
    env = workflow_db
    for i in range(5):
        await article(env, str(i))
    started = asyncio.Event()

    async def wait(ids, **kwargs):
        assert env.engine.sync_engine.pool.checkedout() == 0
        assert len(ids) == 2
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "wait_for_activities", wait)
    parent = SimpleNamespace(
        task_uuid="parent",
        task_type=TaskType.AGENT_WORKFLOW,
        priority="low",
        input_data={},
    )
    async with runtime.task_context(parent):
        task = asyncio.create_task(
            run_literature_review(kind="articles", buffer_size=2)
        )
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 2
        assert (
            await db.scalar(
                select(func.count(AIReviewWorkflow.id)).where(
                    AIReviewWorkflow.status == "open"
                )
            )
            == 2
        )
    # Process interruption retains durable state. Explicit cancellation clears it.
    async with env.database() as db:
        (
            await db.scalar(select(Task).where(Task.task_uuid == "parent"))
        ).status = TaskStatus.CANCELLED
    async with runtime.task_context(parent):
        with pytest.raises(TaskCancelledError):
            await run_literature_review(kind="articles", buffer_size=2)
    async with env.database() as db:
        assert set((await db.execute(select(AIWorkItem.status))).scalars()) == {
            "cancelled"
        }
        assert set((await db.execute(select(AIReviewWorkflow.outcome))).scalars()) == {
            "cancelled"
        }


async def test_terminal_parent_orphan_releases_global_subject_guard(workflow_db):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    async with env.database() as db:
        (
            await db.scalar(select(Task).where(Task.task_uuid == "parent"))
        ).status = TaskStatus.FAILED
    await env.repo.recover_orphans()
    await initialise(env, parent="other")
    assert (await env.repo.totals("other", "articles"))["claimed"] == 1


async def test_orphan_recovery_rechecks_owner_after_requeue(workflow_db):
    env = workflow_db
    await article(env)
    [work_id] = await initialise(env)
    await env.repo.advance("parent", work_id)
    # The candidate scan may observe a failed task just before it is retried.
    await env.repo.cancel_open("parent", only_ids=[work_id], terminal_owner_only=True)
    async with env.database() as db:
        assert (await db.get(AIReviewWorkflow, work_id)).status == "open"
        assert (await db.scalar(select(AIWorkItem))).status == "queued"


async def test_deleting_parent_releases_workflow_and_drain_guard(workflow_db):
    env = workflow_db
    await article(env)
    await initialise(env)
    async with env.database() as db:
        await db.execute(delete(Task).where(Task.task_uuid == "parent"))
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIReviewWorkflow.id))) == 0
        assert await db.scalar(select(func.count()).select_from(AIReviewDrain)) == 0
    await initialise(env, parent="other")
    assert (await env.repo.totals("other", "articles"))["claimed"] == 1


async def test_manual_retry_same_uuid_starts_new_generation_and_retains_history(
    workflow_db,
):
    env = workflow_db
    await article(env)
    [old_id] = await initialise(env)
    await env.repo.advance("parent", old_id)
    await complete(env, old_id, "", status="failed")
    await env.repo.advance("parent", old_id)
    old_repo = env.repo
    assert (await old_repo.totals("parent", "articles"))["exhausted"]
    # Operator retry increments the logical execution; crash recovery does not.
    async with env.database() as db:
        parent = await db.scalar(select(Task).where(Task.task_uuid == "parent"))
        parent.metadata_ = {"ai_execution_generation": 1}
    env.repo = ReviewWorkflowRepository(AIContentGovernanceService(), generation=1)
    [new_id] = await initialise(env)
    assert new_id != old_id
    await accepted(env, new_id)
    async with env.database() as db:
        assert (await db.get(AIReviewWorkflow, old_id)).outcome == "deferred"
        assert (await db.get(AIReviewWorkflow, new_id)).outcome == "published"
        assert await db.scalar(select(func.count()).select_from(AIReviewDrain)) == 2
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 3
        work = await db.get(AIReviewWorkflow, new_id)
        assert (await db.get(AIWorkItem, work.activity_id)).payload[
            "execution_generation"
        ] == 1
    assert (await old_repo.totals("parent", "articles"))["generation"] == 0
    assert (await env.repo.totals("parent", "articles"))["generation"] == 1


async def test_retry_fences_old_coordinator_and_cancels_only_old_generation(
    workflow_db,
):
    env = workflow_db
    await article(env)
    [old_id] = await initialise(env)
    await env.repo.advance("parent", old_id)
    old_repo = env.repo
    async with env.database() as db:
        parent = await db.scalar(select(Task).where(Task.task_uuid == "parent"))
        parent.metadata_ = {"ai_execution_generation": 1}
        old_work = await db.get(AIReviewWorkflow, old_id)
        item = await db.get(AIWorkItem, old_work.activity_id)
        item.status, item.lease_owner, item.lease_token = (
            "leased",
            "executor",
            "old-token",
        )
        item.lease_expires_at = item.deadline_at = now() + timedelta(minutes=1)
    env.repo = ReviewWorkflowRepository(AIContentGovernanceService(), generation=1)
    [new_id] = await initialise(env)
    for action in (
        old_repo.feed("parent", "articles", target_size=512),
        old_repo.advance("parent", old_id),
        old_repo.ensure_drain("parent", "articles", SETTINGS),
    ):
        with pytest.raises(TaskCancelledError, match="generation changed"):
            await action
    await old_repo.cancel_open("parent")
    async with env.database() as db:
        old_work = await db.get(AIReviewWorkflow, old_id)
        assert old_work.outcome == "cancelled"
        assert (await db.get(AIReviewWorkflow, new_id)).status == "open"
        item = await db.get(AIWorkItem, old_work.activity_id)
        assert (
            item.cancel_requested
            and item.status == "leased"
            and item.lease_token == "old-token"
        )
    assert await old_repo.open_work("parent", "articles") == ([], [])
    assert (await env.repo.open_work("parent", "articles"))[0] == [new_id]


async def test_coordinator_refills_bounded_buffer_while_slow_activity_waits(
    workflow_db, monkeypatch
):
    env = workflow_db
    for i in range(6):
        await article(env, str(i))
    slow = None
    completed_before_slow = 0

    async def fake_provider(ids, **kwargs):
        nonlocal slow, completed_before_slow
        assert env.engine.sync_engine.pool.checkedout() == 0
        async with env.database() as db:
            open_count = await db.scalar(
                select(func.count(AIReviewWorkflow.id)).where(
                    AIReviewWorkflow.status == "open"
                )
            )
            assert open_count <= 2
            slow = slow or min(ids)
            candidates = [work_id for work_id in ids if work_id != slow]
            item = await db.get(AIWorkItem, max(candidates) if candidates else slow)
            if item.id == slow:
                item.status, item.error = "failed", "fake provider failed"
                return
            completed_before_slow += 1
            is_author = item.payload["agent_name"].endswith(":author")
            item.status = "succeeded"
            item.route_key = "fake:a" if is_author else "fake:b"
            item.result = {
                "text": json.dumps(
                    {"decision": "publish", "reasons": [QUOTE]}
                    if is_author
                    else {"verdict": "accept"}
                )
            }

    monkeypatch.setattr(runtime, "wait_for_activities", fake_provider)
    parent = SimpleNamespace(
        task_uuid="parent",
        task_type=TaskType.AGENT_WORKFLOW,
        priority="low",
        input_data={},
    )
    # A resume cannot enlarge an explicitly persisted budget.
    await env.repo.ensure_drain("parent", "articles", {**SETTINGS, "buffer_size": 2})
    async with runtime.task_context(parent):
        totals = await asyncio.wait_for(
            run_literature_review(kind="articles", buffer_size=512), 10
        )
    assert completed_before_slow == 10
    assert totals["published"] == 5 and totals["deferred"] == 1
    assert totals["claimed"] == totals["scanned"] == totals["cursor"] == 6
    assert totals["pending"] == 0 and totals["exhausted"]
    assert totals["buffer_budget"] == totals["buffer_target"] == 2


async def test_feeder_batches_insert_and_updates_cursor_once(workflow_db):
    env = workflow_db
    async with env.database() as db:
        db.add_all(
            [
                LiteratureArticle(
                    article_id=f"bulk-{i}",
                    slug=f"bulk-{i}",
                    title="Dengue surveillance",
                    abstract_text=ABSTRACT,
                )
                for i in range(64)
            ]
        )
    await env.repo.ensure_drain("parent", "articles", {**SETTINGS, "buffer_size": 512})
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip())

    event.listen(env.engine.sync_engine, "before_cursor_execute", record)
    try:
        assert await env.repo.feed("parent", "articles", target_size=434) == 64
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", record)
    assert sum(sql.startswith("UPDATE ai_review_drains") for sql in statements) == 1
    assert (
        sum(sql.startswith("INSERT INTO ai_review_workflows") for sql in statements)
        == 1
    )
    totals = await env.repo.totals("parent", "articles")
    assert totals["claimed"] == totals["scanned"] == totals["cursor"] == 64


async def test_supply_shrink_does_not_discard_existing_open_work(workflow_db):
    env = workflow_db
    for i in range(5):
        await article(env, str(i))
    await env.repo.ensure_drain("parent", "articles", {**SETTINGS, "buffer_size": 8})
    assert await env.repo.feed("parent", "articles", target_size=4) == 4
    assert await env.repo.feed("parent", "articles", target_size=1) == 0
    assert (await env.repo.totals("parent", "articles"))["pending"] == 4
    await env.repo.cancel_open("parent")
    assert await env.repo.feed("parent", "articles", target_size=1) == 1


async def test_batch_final_application_is_atomic_and_skips_late_edit(workflow_db):
    env = workflow_db
    await article(env, "z")
    await article(env, "a")
    ids = await initialise(env)
    assert await env.repo.advance_many("parent", ids[::-1]) == 2
    for work_id in ids:
        await complete(
            env, work_id, json.dumps({"decision": "publish", "reasons": [QUOTE]})
        )
    assert await env.repo.advance_many("parent", ids) == 2
    for work_id in ids:
        await complete(env, work_id, '{"verdict":"accept"}')
    async with env.database() as db:
        (
            await db.scalar(
                select(LiteratureArticle).where(LiteratureArticle.article_id == "z")
            )
        ).title = "Editor changed the topic"
    assert await env.repo.advance_many("parent", ids) == 2
    async with env.database() as db:
        rows = (
            (await db.execute(select(AIReviewWorkflow).order_by(AIReviewWorkflow.id)))
            .scalars()
            .all()
        )
        assert [row.outcome for row in rows] == ["stale", "published"]
        assert await db.scalar(select(func.count(LiteratureStatusEvent.id))) == 1
        assert await db.scalar(select(func.count(PublishDirty.id))) == 1


async def test_mixed_kind_batch_is_rejected_before_any_submission(workflow_db):
    env = workflow_db
    await article(env)
    async with env.database() as db:
        db.add(LiteratureSummary(article_id="a", language="en", status="review"))
    articles = await initialise(env)
    summaries = await initialise(env, kind="summaries")
    with pytest.raises(ValueError, match="one kind"):
        await env.repo.advance_many("parent", [*articles, *summaries])
    async with env.database() as db:
        assert await db.scalar(select(func.count(AIWorkItem.id))) == 0
        assert set((await db.execute(select(AIReviewWorkflow.step))).scalars()) == {
            "ready"
        }


@pytest.mark.parametrize("other_kind", ["summary", "manual"])
async def test_overlapping_batches_share_one_content_lock_order(
    workflow_db, other_kind
):
    from src.ai.peer_review import ReviewedOutput

    env = workflow_db
    # Numeric workflow order deliberately disagrees with article_id lock order.
    for key in ("z", "a"):
        await article(env, key)
    article_ids = await initialise(env)
    if other_kind == "summary":
        async with env.database() as db:
            db.add_all(
                [
                    LiteratureSummary(article_id=key, language="en", status="review")
                    for key in ("a", "z")
                ]
            )
        summary_ids = await initialise(env, kind="summaries", parent="other")
        second = env.repo.advance_many("other", summary_ids)
    else:

        async def manual():
            async with env.database() as db:
                rows = (await db.execute(select(LiteratureArticle))).scalars().all()
                reviewed = ReviewedOutput(
                    "published",
                    {"decision": "publish", "reasons": [QUOTE]},
                    "a",
                    "b",
                    "accept",
                    1,
                )
                return await env.repo.policy._apply_literature_results(
                    db,
                    "articles",
                    [
                        (
                            row.article_id,
                            env.repo.policy._article_fingerprint(row),
                            reviewed,
                        )
                        for row in reversed(rows)
                    ],
                    parent_task_uuid="other",
                )

        second = manual()
    await asyncio.wait_for(
        asyncio.gather(env.repo.advance_many("parent", article_ids[::-1]), second), 5
    )
    async with env.database() as db:
        if other_kind == "summary":
            assert set(
                (
                    await db.execute(
                        select(AIReviewWorkflow.outcome).where(
                            AIReviewWorkflow.kind == "summaries"
                        )
                    )
                ).scalars()
            ) == {"waiting"}
        else:
            assert set(
                (
                    await db.execute(select(LiteratureArticle.publication_status))
                ).scalars()
            ) == {"published"}


@pytest.mark.parametrize("fail_marker", [False, True])
async def test_batch_archives_and_completed_summary_reviews_apply_together(
    workflow_db, monkeypatch, fail_marker
):
    env = workflow_db
    parents = [
        await article(env, key, publication_status="published") for key in ("z", "a")
    ]
    async with env.database() as db:
        for row, complete_fields in zip(parents, (True, False)):
            db.add(
                LiteratureSummary(
                    article_id=row.article_id,
                    language="en",
                    status="review",
                    generated_by="literature-evidence-agent",
                    quality_score=0.95,
                    generation_metadata={"source_fingerprint": source_fingerprint(row)},
                    evidence_map={
                        field: {"sources": ["abstract"], "confidence": 0.95}
                        for field in SUMMARY_FIELDS
                    },
                    **(
                        {field: "Grounded finding" for field in SUMMARY_FIELDS}
                        if complete_fields
                        else {}
                    ),
                )
            )
    ids = await initialise(env, kind="summaries")
    await env.repo.advance("parent", ids[0])
    await complete(
        env, ids[0], '{"decision":"publish","reasons":["Faithful to the evidence map"]}'
    )
    await env.repo.advance("parent", ids[0])
    await complete(env, ids[0], '{"verdict":"accept"}')
    if fail_marker:
        from src.services import ai_content_governance_service as governance

        original = governance._mark_review_dirty
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second application marker failed")
            await original(*args, **kwargs)

        monkeypatch.setattr(governance, "_mark_review_dirty", fail_second)
        with pytest.raises(RuntimeError, match="second application marker failed"):
            await env.repo.advance_many("parent", ids[::-1])
    else:
        assert await env.repo.advance_many("parent", ids[::-1]) == 2
    async with env.database() as db:
        rows = (
            (await db.execute(select(AIReviewWorkflow).order_by(AIReviewWorkflow.id)))
            .scalars()
            .all()
        )
        if fail_marker:
            assert [row.outcome for row in rows] == [None, None]
            assert set(
                (await db.execute(select(LiteratureSummary.status))).scalars()
            ) == {"review"}
            assert await db.scalar(select(func.count(PublishDirty.id))) == 0
        else:
            assert [row.outcome for row in rows] == ["published", "archived"]
            # Both application groups mark the same scope in the same transaction.
            assert await db.scalar(select(func.count(PublishDirty.id))) == 2
