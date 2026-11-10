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


@pytest.mark.parametrize("entry", ["agent", "complete", "send", "negotiate"])
async def test_completion_transport_rejects_non_dispatcher_before_io(monkeypatch, entry):
    from src.ai import providers
    constructor = Mock(side_effect=AssertionError("provider must not be constructed"))
    monkeypatch.setattr(providers, "AsyncOpenAI", constructor)
    monkeypatch.setattr(providers, "AsyncAnthropic", constructor)
    transport = providers.ProviderTransport()
    with pytest.raises(RuntimeError, match="worker dispatcher"):
        if entry == "agent":
            await Agent(name="boundary")._complete_transport("prompt")
        elif entry == "negotiate":
            await providers._create_with_optional_negotiation(AsyncMock(), {})
        else:
            await getattr(transport, entry)({}, "prompt")
    constructor.assert_not_called()


@pytest.mark.parametrize("entry", ["route", "openai", "anthropic", "file"])
async def test_health_transport_rejects_non_dispatcher_before_io(monkeypatch, entry):
    from src.ai.catalogue import probes
    constructor = Mock(side_effect=AssertionError("provider must not be constructed"))
    with pytest.raises(RuntimeError, match="worker dispatcher"):
        if entry == "route":
            await model_center.test_route_connection({})
        elif entry == "file":
            await probes.probe_native_file({}, openai_factory=constructor, anthropic_factory=constructor)
        else:
            await getattr(probes, "probe_" + entry)({}, "model", client_factory=constructor)
    constructor.assert_not_called()


async def test_business_completion_submits_durable_work(monkeypatch):
    submit = AsyncMock(return_value="queued result")
    monkeypatch.setattr("src.ai.runtime.submit_completion", submit)
    agent = Agent(name="boundary")
    assert await agent.complete("prompt", use_cache=False) == "queued result"
    submit.assert_awaited_once_with(agent, "prompt", None, False, {})
    for removed in ("_init_clients", "_complete_with_provider", "_complete_openai_compatible", "_complete_anthropic", "test_connection"):
        assert not hasattr(BaseAgent, removed)


async def test_semantic_literature_failure_fails_parent_and_keeps_metrics(monkeypatch):
    from src.ai.runtime import AIWorkFailed
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
