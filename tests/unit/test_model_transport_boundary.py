"""Provider egress cannot be invoked from an API or business coroutine."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai.agents.base import BaseAgent
from src.ai import model_center
from src.domain import TaskStatus, TaskType


class Agent(BaseAgent):
    async def process(self, **kwargs):
        return {}


@pytest.mark.parametrize("entry", ["_complete_transport", "_complete_with_runtime_route", "_complete_with_admitted_runtime_route", "_send_runtime_route_request", "_safe_openai_completion_create"])
async def test_completion_transport_rejects_non_dispatcher_before_io(monkeypatch, entry):
    constructor = Mock(side_effect=AssertionError("provider must not be constructed"))
    monkeypatch.setattr("src.ai.agents.base.AsyncOpenAI", constructor)
    monkeypatch.setattr("src.ai.agents.base.AsyncAnthropic", constructor)
    agent = Agent(name="boundary")
    arguments = {
        "_complete_transport": ("prompt",),
        "_complete_with_runtime_route": ({}, "prompt"),
        "_complete_with_admitted_runtime_route": ({}, "prompt"),
        "_send_runtime_route_request": ({}, "prompt"),
        "_safe_openai_completion_create": (None, {}),
    }
    with pytest.raises(RuntimeError, match="worker dispatcher"):
        await getattr(agent, entry)(*arguments[entry])
    constructor.assert_not_called()


@pytest.mark.parametrize("entry", ["test_route_connection", "_test_openai_compatible", "_test_anthropic"])
async def test_health_transport_rejects_non_dispatcher_before_io(monkeypatch, entry):
    constructor = Mock(side_effect=AssertionError("provider must not be constructed"))
    monkeypatch.setattr(model_center, "AsyncOpenAI", constructor)
    monkeypatch.setattr(model_center, "AsyncAnthropic", constructor)
    args = ({},) if entry == "test_route_connection" else ({}, "model")
    with pytest.raises(RuntimeError, match="worker dispatcher"):
        await getattr(model_center, entry)(*args)
    constructor.assert_not_called()


async def test_business_completion_submits_durable_work(monkeypatch):
    submit = AsyncMock(return_value="queued result")
    monkeypatch.setattr("src.ai.work_queue.submit_completion", submit)
    agent = Agent(name="boundary")
    assert await agent.complete("prompt", use_cache=False) == "queued result"
    submit.assert_awaited_once_with(agent, "prompt", None, False, {})
    for removed in ("_init_clients", "_complete_with_provider", "_complete_openai_compatible", "_complete_anthropic", "test_connection"):
        assert not hasattr(BaseAgent, removed)


async def test_semantic_literature_failure_fails_parent_and_keeps_metrics(monkeypatch):
    from src.ai.work_queue import AIWorkFailed
    from src.services import _lifecycle, task_executor
    from src.services.literature_service import literature_service

    task = SimpleNamespace(id=1, task_uuid="semantic-enrichment-failure", task_type=TaskType.ENRICH_LITERATURE, input_data={}, priority="high")
    stored = SimpleNamespace(output_data=None)
    result = {"processed": 2, "enriched": 1, "failed": 1}
    committed = []
    class Session:
        async def get(self, *_args):
            return stored
        async def commit(self):
            committed.append(stored.output_data)
    @asynccontextmanager
    async def database():
        yield Session()
    update = AsyncMock()
    monkeypatch.setattr(task_executor, "get_database", database)
    monkeypatch.setattr(literature_service, "execute_enrichment_task", AsyncMock(return_value=result))
    monkeypatch.setattr(_lifecycle.task_manager, "update_task_status", update)
    monkeypatch.setattr(_lifecycle.task_manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(_lifecycle.task_alert_service, "send_task_alert", AsyncMock())
    with pytest.raises(AIWorkFailed, match="1 article"):
        await task_executor._run_literature_enrichment(task)
    assert committed == [result]
    assert [call.args[1] for call in update.await_args_list] == [TaskStatus.RUNNING, TaskStatus.FAILED]
