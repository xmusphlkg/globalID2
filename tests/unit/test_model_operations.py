"""API and CLI model actions execute through queued worker operations."""

import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from src.domain import TaskPriority, TaskStatus, TaskType
from src.services import model_operations


async def test_worker_operation_waits_for_existing_task_contract(monkeypatch):
    enqueue = AsyncMock(return_value=SimpleNamespace(task_uuid="uuid"))
    wait = AsyncMock(return_value={"result": {"success": True}})
    monkeypatch.setattr(model_operations, "enqueue_model_operation", enqueue)
    monkeypatch.setattr(model_operations, "wait_task_output", wait)
    assert await model_operations.run_worker_operation(
        "check_model", {"model_id": 1}
    ) == {"success": True}
    enqueue.assert_awaited_once_with("check_model", {"model_id": 1})
    wait.assert_awaited_once_with("uuid")


async def test_model_health_api_only_requests_worker_operation(monkeypatch):
    from dashboard.api.routers import ai

    bootstrap = AsyncMock()
    run = AsyncMock(return_value=[{"success": True}])
    monkeypatch.setattr(ai, "bootstrap_model_center_from_env", bootstrap)
    monkeypatch.setattr(ai, "run_worker_operation", run)
    assert await ai.test_all_models(structured=False) == [{"success": True}]
    run.assert_awaited_once_with("check_all_models", {"structured": False})


async def test_mapping_api_only_requests_worker_operation(monkeypatch):
    from dashboard.api.routers import mappings

    run = AsyncMock(return_value={"category_id": 42, "status": "completed"})
    monkeypatch.setattr(mappings, "run_worker_operation", run)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 42)),
        rollback=AsyncMock(),
    )
    result = await mappings.suggest_mapping_v3("category", db=db)
    assert result["status"] == "completed"
    run.assert_awaited_once_with("mapping_suggest", {"category_id": 42})
    db.rollback.assert_awaited_once()


def test_report_cli_queues_worker_task_without_direct_execution(monkeypatch):
    import main

    create = AsyncMock(return_value=SimpleNamespace(task_uuid="report-uuid"))
    wait = AsyncMock(return_value={"report_id": 4, "status": "completed", "files": []})
    monkeypatch.setattr(main, "init_app", AsyncMock())
    monkeypatch.setattr(main.task_manager, "create_task", create)
    monkeypatch.setattr(main.task_manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(model_operations, "wait_task_output", wait)
    result = CliRunner().invoke(main.app, ["generate-report", "--country", "US"])
    assert result.exit_code == 0, result.output
    assert create.await_args.kwargs["task_type"] == TaskType.GENERATE_REPORT
    assert create.await_args.kwargs["status"] == TaskStatus.QUEUED
    assert create.await_args.kwargs["priority"] == TaskPriority.HIGH
    wait.assert_awaited_once_with("report-uuid")


@pytest_asyncio.fixture
async def notification_db(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite")
    from src.ai import work_queue as queue
    from src.domain import AIProviderConfig, Base, Task
    from src.domain.ai_work_item import AIWorkItem

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'notifications.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Task.__table__,
                    AIWorkItem.__table__,
                    AIProviderConfig.__table__,
                ],
            )
        )
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

    monkeypatch.setattr(queue, "get_database", database)
    monkeypatch.setattr(queue, "ROOT", tmp_path)
    monkeypatch.setattr(model_operations, "get_database", database)
    manager_module = importlib.import_module("src.core.task_manager")
    monkeypatch.setattr(manager_module, "get_db", database)
    monkeypatch.setattr(manager_module, "ensure_task_type_enum_schema", AsyncMock())
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_notification_translation_runs_only_in_dispatcher_before_campaign_creation(
    notification_db,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from dashboard.api.routers import subscriptions
    from src.ai import work_queue as queue
    from src.ai.agents.base import BaseAgent
    from src.domain import AIProviderConfig, Task
    from src.domain.ai_work_item import AIWorkItem

    async with notification_db() as db:
        db.add(
            AIProviderConfig(
                id=9,
                provider_key="fake",
                provider_name="Fake provider",
                display_name="Fake",
            )
        )
        await db.commit()
    route = {
        "model_id": 7,
        "provider_id": 9,
        "model_key": "fake:translator",
        "model_name": "translator",
        "provider_key": "fake",
        "available_for_routing": False,
        "runtime_model_capacity": 1,
        "api_key": "must-not-leak",
    }
    model_calls = []

    async def fake_transport(self, prompt, system=None, use_cache=True, **kwargs):
        assert queue.in_dispatcher()
        assert kwargs["_scheduled_route"] == route
        assert use_cache is False
        request = json.loads(prompt)
        assert request["source_locale"] == "zh"
        assert request["target_locales"] == {"en": "English", "ja": "Japanese"}
        assert request["subject"] == "更新"
        assert request["markdown"] == "# 更新\n\n**12** [链接](/zh/) 2026-10-30"
        assert (
            "Preserve Markdown structure, links, numbers, code blocks, disease names, and dates."
            in system
        )
        assert self.max_tokens == 5000
        assert self.temperature == 0.1
        model_calls.append(route["model_key"])
        text = json.dumps(
            {
                "translations": {
                    "en": {"subject": "Update", "markdown": "# Update"},
                    "ja": {"subject": "更新", "markdown": "# 更新"},
                }
            }
        )
        self._append_conversation_entry(
            prompt=prompt,
            system=system,
            response_text=text,
            provider=route["provider_key"],
            model=route["model_name"],
            metadata={
                "runtime_route": {
                    key: route[key]
                    for key in (
                        "model_id",
                        "provider_id",
                        "model_key",
                        "model_name",
                        "provider_key",
                    )
                }
            },
        )
        return text

    monkeypatch.setattr(BaseAgent, "_complete_transport", fake_transport)
    channel = AsyncMock(return_value={"ok": True, "campaign": {"id": "fake-campaign"}})
    monkeypatch.setattr(subscriptions, "_worker_request", channel)
    dispatcher = queue.AIWorkDispatcher(route_loader=AsyncMock(return_value=[route]))
    background = BackgroundTasks()
    request = asyncio.create_task(
        subscriptions.create_subscription_notification(
            subscriptions.NotificationCreateRequest(
                markdown="# 更新\n\n**12** [链接](/zh/) 2026-10-30",
                target_locales=["en", "ja"],
                start_sending=False,
            ),
            background,
        )
    )
    operation = None
    try:

        async def wait_for_parent():
            while True:
                async with notification_db() as db:
                    parent = (await db.execute(select(Task))).scalar_one_or_none()
                if parent is not None:
                    return parent
                await asyncio.sleep(0.01)

        parent = await asyncio.wait_for(wait_for_parent(), 3)
        assert parent.task_type == TaskType.AGENT_WORKFLOW
        assert parent.priority == TaskPriority.HIGH
        assert parent.status == TaskStatus.QUEUED
        assert parent.input_data["operation"] == "notification_translation"
        assert not model_calls
        channel.assert_not_awaited()
        # Identical requests attach to the existing active parent operation.
        duplicate = await model_operations.enqueue_model_operation(
            "notification_translation",
            parent.input_data["arguments"],
        )
        assert duplicate.task_uuid == parent.task_uuid

        async def execute_parent():
            async with notification_db() as db:
                stored = await db.get(Task, parent.id)
                stored.status = TaskStatus.RUNNING
                await db.commit()
            async with queue.task_context(parent):
                result = await model_operations.execute_model_operation(
                    parent.input_data["operation"],
                    parent.input_data["arguments"],
                )
                queue.assert_children_succeeded()
            async with notification_db() as db:
                stored = await db.get(Task, parent.id)
                stored.output_data = {"result": result}
                stored.status = TaskStatus.COMPLETED
                await db.commit()

        operation = asyncio.create_task(execute_parent())

        async def wait_for_work():
            while parent.task_uuid not in queue.waiting_parent_uuids():
                if operation.done():
                    await operation
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_work(), 3)
        assert await dispatcher.tick() == 0  # Cooling routes leave work queued.
        async with notification_db() as db:
            work = (await db.execute(select(AIWorkItem))).scalar_one()
            assert work.status == "queued"
            assert work.parent_task_uuid == parent.task_uuid
            assert work.priority == "high"
            assert work.capability == "text"
            assert "prompt" not in work.payload
            stored = await db.get(Task, parent.id)
            assert stored.status == TaskStatus.RUNNING
            assert stored.input_data["ai_waiting"] is True
        assert not model_calls
        assert not request.done()
        channel.assert_not_awaited()

        route["available_for_routing"] = True
        assert await dispatcher.tick() == 1
        await asyncio.wait_for(operation, 3)
        response = await asyncio.wait_for(request, 3)
        assert model_calls == ["fake:translator"]
        assert response == {
            "ok": True,
            "campaign": {"id": "fake-campaign"},
            "send_started": False,
        }
        channel.assert_awaited_once()
        sent = channel.await_args.kwargs["payload"]
        assert sent["ai"] == {
            "status": "translated",
            "locales": ["zh", "en", "ja"],
            "model_route": {
                "model_id": 7,
                "model_key": "fake:translator",
                "model_name": "translator",
                "provider_key": "fake",
                "provider_name": "Fake provider",
            },
        }
        assert "must-not-leak" not in json.dumps(sent)
        assert sent["contents"]["zh"]["markdown"] == sent["markdown"]
        assert sent["contents"]["en"] == {"subject": "Update", "markdown": "# Update"}
        assert not background.tasks
        assert parent.task_uuid not in queue.waiting_parent_uuids()
        async with notification_db() as db:
            work = (await db.execute(select(AIWorkItem))).scalar_one()
            assert work.status == "succeeded"
            assert work.route_key == "fake:translator"
    finally:
        tasks = [request, *dispatcher.active.values()]
        if operation is not None:
            tasks.append(operation)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_notification_model_operation_requires_queued_parent():
    with pytest.raises(RuntimeError, match="requires a queued parent task"):
        await model_operations.execute_model_operation(
            "notification_translation",
            {
                "source_locale": "zh",
                "target_locales": ["en"],
                "subject": "更新",
                "markdown": "# 更新",
            },
        )
