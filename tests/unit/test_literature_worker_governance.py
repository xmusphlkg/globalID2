"""Governance queues outside a worker and atomically dirties its stored facts."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from src.ai import work_queue
from src.core.config import LiteratureSettings
from src.domain import Base, Task, TaskStatus, TaskType, TaskPriority, LiteratureArticle
from src.domain.collection import PublishDirty
from src.services import literature_automation_service as automation
from src.services import model_operations


async def test_governance_apply_queues_without_worker_parent_and_never_exports(monkeypatch):
    service = automation.LiteratureAutomationService()
    monkeypatch.setattr(automation, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(autopilot_enabled=True)))
    monkeypatch.setattr(work_queue, "current_parent", lambda: None)
    enqueue = AsyncMock(return_value=SimpleNamespace(task_uuid="governance-task"))
    mutate = AsyncMock()
    monkeypatch.setattr(model_operations, "enqueue_model_operation", enqueue)
    monkeypatch.setattr(service, "_reconcile_database", mutate)
    result = await service.reconcile()
    assert result["status"] == "queued"
    assert result["task_uuid"] == "governance-task"
    assert enqueue.await_args.args[0] == "literature_reconcile"
    mutate.assert_not_awaited()


@pytest.mark.parametrize("fail_marker", [False, True])
async def test_governance_formal_publication_and_dirty_share_transaction(monkeypatch, fail_marker):
    from src.services.ingestion import stages
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [table for name, table in Base.metadata.tables.items() if name.startswith("literature_") or name in {"tasks", "publish_dirty"}]
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
    async with database() as session:
        session.add(Task(id=1, task_uuid=task_uuid, task_type=TaskType.AGENT_WORKFLOW, task_name="Literature governance", status=TaskStatus.RUNNING, priority=TaskPriority.NORMAL, input_data={"operation": "literature_reconcile"}))
        session.add(LiteratureArticle(id=1, article_id="fixture", slug="fixture", title="Fixture", publication_status="review"))
    monkeypatch.setattr(automation, "get_db", database)
    monkeypatch.setattr(automation, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(autopilot_enabled=True)))
    monkeypatch.setattr(work_queue, "current_parent", lambda: SimpleNamespace(task_uuid=task_uuid))
    monkeypatch.setattr(automation, "decide_article", lambda *args, **kwargs: automation.AutomationDecision("publish", ("fixture",)))
    if fail_marker:
        async def broken_marker(*args, **kwargs):
            raise RuntimeError("dirty marker failed")
        monkeypatch.setattr(stages, "mark_dirty", broken_marker)
    try:
        if fail_marker:
            with pytest.raises(RuntimeError, match="dirty marker failed"):
                await automation.LiteratureAutomationService().reconcile()
        else:
            await automation.LiteratureAutomationService().reconcile()
        async with database() as session:
            article = (await session.execute(select(LiteratureArticle))).scalar_one()
            pending = list((await session.execute(select(PublishDirty))).scalars())
            assert article.publication_status == ("review" if fail_marker else "published")
            assert len(pending) == (0 if fail_marker else 1)
            if pending:
                assert pending[0].source_task_uuid == task_uuid
                assert pending[0].line == "literature"
                assert pending[0].published_at is None
    finally:
        await engine.dispose()
