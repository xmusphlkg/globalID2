from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard.api.routers import agent_runs
from src.domain.task import TaskStatus, TaskType
from src.services import agent_workflow_service as workflow_module


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, existing_run=None):
        self.existing_run = existing_run
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query):
        return _FakeResult(self.existing_run)

    def add(self, obj):
        self.added.append(obj)
        self.existing_run = obj

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_create_agent_run_queues_task_and_returns_detail(monkeypatch):
    created_tasks = []
    status_updates = []
    workbook_entries = []

    async def _fake_create_task(**kwargs):
        task = SimpleNamespace(
            id=101,
            task_uuid="task-101",
            task_name=kwargs["task_name"],
            status=TaskStatus.PENDING,
            priority=kwargs["priority"],
            country_id=kwargs.get("country_id"),
            description=kwargs.get("description"),
            input_data=kwargs.get("input_data") or {},
        )
        created_tasks.append(kwargs)
        return task

    async def _fake_get_task_by_uuid(task_uuid):
        return SimpleNamespace(task_uuid=task_uuid, status=TaskStatus.PENDING)

    async def _fake_update_task_status(task_uuid, status, error_message=None):
        status_updates.append((task_uuid, status))
        return SimpleNamespace(task_uuid=task_uuid, status=status)

    async def _fake_add_workbook_entry(*args, **kwargs):
        workbook_entries.append((args, kwargs))
        return SimpleNamespace(entry_uuid="entry-1")

    async def _fake_get_run_detail(task_uuid):
        return {
            "task": {
                "task_uuid": task_uuid,
                "status": "queued",
                "task_name": "Agent workflow: test prompt",
                "task_type": TaskType.AGENT_WORKFLOW.value,
            },
            "run": {"status": "queued", "prompt": "test prompt", "allowed_actions": ["crawl_data"]},
            "steps": [],
            "evidence": [],
            "conversations": [],
            "memories": [],
        }

    monkeypatch.setattr(agent_runs.task_manager, "create_task", _fake_create_task)
    monkeypatch.setattr(agent_runs.task_manager, "get_task_by_uuid", _fake_get_task_by_uuid)
    monkeypatch.setattr(agent_runs.task_manager, "update_task_status", _fake_update_task_status)
    monkeypatch.setattr(agent_runs.task_manager, "add_workbook_entry", _fake_add_workbook_entry)
    monkeypatch.setattr(agent_runs.agent_workflow_service, "get_run_detail", _fake_get_run_detail)

    db = _FakeDB()
    body = agent_runs.AgentWorkflowCreateRequest(
        prompt="test prompt",
        allowed_actions=["crawl_data"],
        task_name=None,
        description=None,
        priority="high",
    )

    detail = await agent_runs.create_agent_run(body=body, db=db)

    assert created_tasks
    assert created_tasks[0]["task_type"] == TaskType.AGENT_WORKFLOW
    assert status_updates == [("task-101", TaskStatus.QUEUED)]
    assert workbook_entries
    assert db.added
    assert detail["run"]["status"] == "queued"
    assert detail["task"]["task_uuid"] == "task-101"


@pytest.mark.asyncio
async def test_list_agent_runs_delegates_to_service(monkeypatch):
    payload = {"total": 1, "limit": 20, "offset": 0, "items": []}

    async def _fake_list_runs(**kwargs):
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 2
        assert kwargs["status"] == "running"
        assert kwargs["search"] == "flu"
        assert kwargs["country_id"] == 7
        return payload

    monkeypatch.setattr(agent_runs.agent_workflow_service, "list_runs", _fake_list_runs)

    result = await agent_runs.list_agent_runs(limit=10, offset=2, status="running", search="flu", country_id=7)

    assert result == payload


@pytest.mark.asyncio
async def test_service_list_runs_applies_non_search_filters_to_items_and_count(
    monkeypatch,
):
    statements = []

    class Result:
        def scalar_one(self):
            return 0

        def all(self):
            return []

    class DB:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    class Context:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(workflow_module, "get_database", lambda: Context())

    result = await workflow_module.agent_workflow_service.list_runs(
        status="running", country_id=7
    )

    assert result["items"] == []
    assert len(statements) == 2
    compiled = [str(statement) for statement in statements]
    for sql in compiled:
        assert "agent_runs.status" in sql
        assert "agent_runs.country_id" in sql
