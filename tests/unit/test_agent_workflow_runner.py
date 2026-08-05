from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.agent_workflow import runner
from src.services.agent_workflow_types import AgentFinalResult, PlanNode
from src.services.exceptions import TaskCancelledError


class FakeDatabase:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeTaskManager:
    def __init__(self, cancellations: list[bool] | None = None) -> None:
        self.cancellations = list(cancellations or [])
        self.entries: list[dict] = []

    async def is_cancel_requested(self, _task_uuid: str) -> bool:
        return self.cancellations.pop(0) if self.cancellations else False

    async def add_workbook_entry(self, task_uuid: str, **entry) -> None:
        self.entries.append({"task_uuid": task_uuid, **entry})


class FakeLogger:
    def warning(self, *_args) -> None:
        pass


class FakeWorkflowService:
    total_token_budget = 500

    def __init__(
        self,
        nodes: list[PlanNode],
        *,
        completed_steps: dict[str, SimpleNamespace] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.nodes = nodes
        self.completed_steps = dict(completed_steps or {})
        self.failure = failure
        self.executed: list[str] = []
        self.failed_steps: list[str] = []
        self.memory_stored = False
        self.run = SimpleNamespace(
            id=7,
            status="running",
            result_json={},
            metadata_={},
            error_message=None,
        )

    def _normalize_actions(self, _value):
        return {"crawl_data"}

    def _coerce_int(self, value):
        return value

    async def _get_or_create_run(self, _db, _task, **_kwargs):
        return self.run

    async def _ensure_plan(self, *_args):
        return list(self.nodes)

    async def _load_completed_steps(self, _db, _run_id):
        return self.completed_steps

    def _build_initial_context(self, **kwargs):
        return {**kwargs, "evidence": [], "actions": [], "findings": []}

    def _merge_step_context(self, context, output, step_type):
        return {**context, step_type: output}

    async def _start_step(self, _db, _run, node):
        return SimpleNamespace(
            step_key=node.step_key,
            step_type=node.step_type,
            status="running",
            output_payload={},
            tokens={},
        )

    async def _execute_node(self, *, node, context, **_kwargs):
        self.executed.append(node.step_key)
        if self.failure is not None:
            raise self.failure
        return {
            "output_payload": {
                "step": node.step_key,
                "prior": context.get("analysis", {}).get("step"),
                "approved": True,
            }
        }

    async def _finish_step(self, _db, _run, step, result):
        step.status = "completed"
        step.output_payload = result["output_payload"]
        step.tokens = {"total_tokens": 4}

    async def _fail_step(self, _db, run, step, exc):
        step.status = "failed"
        run.status = "failed"
        run.error_message = str(exc)
        self.failed_steps.append(step.step_key)

    async def _refresh_run_progress(self, *_args, **_kwargs):
        pass

    def _maybe_schedule_replan_nodes(self, **_kwargs):
        return []

    async def _persist_plan(self, *_args):
        pass

    async def _build_final_output(self, _db, _run, _task, _prompt, context, _nodes):
        return AgentFinalResult(
            summary="done",
            evidence_count=len(context.get("evidence", [])),
            step_count=len(self.completed_steps),
        )

    def _aggregate_tokens(self, token_payloads):
        return sum(item.get("total_tokens", 0) for item in token_payloads)

    async def _store_workflow_memory(self, *_args):
        self.memory_stored = True


def _task():
    return SimpleNamespace(
        id=1,
        task_uuid="task-1",
        task_name="Research",
        description=None,
        country_id=None,
        input_data={"prompt": "Investigate outbreaks"},
    )


async def _execute(service, manager, db):
    @asynccontextmanager
    async def database_factory():
        yield db

    return await runner.execute_workflow(
        service,
        _task(),
        database_factory=database_factory,
        task_manager=manager,
        now=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
        compact_text=lambda value, limit: str(value)[:limit],
        stable_hash=lambda value: f"hash:{value}",
        logger=FakeLogger(),
    )


@pytest.mark.asyncio
async def test_runner_completes_run_and_persists_memory():
    service = FakeWorkflowService(
        [PlanNode("analysis", "analysis", "Analyze"), PlanNode("final", "finalize", "Finish")]
    )
    manager = FakeTaskManager()

    result = await _execute(service, manager, FakeDatabase())

    assert result["summary"] == "done"
    assert service.executed == ["analysis", "final"]
    assert service.run.status == "completed"
    assert service.run.step_count == 2
    assert service.run.budget_tokens_used == 8
    assert service.memory_stored is True
    assert [entry["title"] for entry in manager.entries] == [
        "Agent Workflow Started",
        "Agent Workflow Completed",
    ]


@pytest.mark.asyncio
async def test_runner_marks_step_and_run_failed():
    service = FakeWorkflowService(
        [PlanNode("analysis", "analysis", "Analyze")], failure=RuntimeError("model unavailable")
    )
    manager = FakeTaskManager()

    with pytest.raises(RuntimeError, match="model unavailable"):
        await _execute(service, manager, FakeDatabase())

    assert service.failed_steps == ["analysis"]
    assert service.run.status == "failed"
    assert service.run.error_message == "model unavailable"
    assert manager.entries[-1]["title"] == "Agent Workflow Failed"


@pytest.mark.asyncio
async def test_runner_marks_run_cancelled_before_starting_next_step():
    service = FakeWorkflowService([PlanNode("analysis", "analysis", "Analyze")])
    manager = FakeTaskManager([True])

    with pytest.raises(TaskCancelledError, match="Cancellation requested"):
        await _execute(service, manager, FakeDatabase())

    assert service.executed == []
    assert service.run.status == "cancelled"
    assert service.run.metadata_["cancelled"] is True
    assert manager.entries[-1]["title"] == "Agent Workflow Cancelled"


@pytest.mark.asyncio
async def test_runner_resumes_after_completed_step_without_reexecuting_it():
    completed = SimpleNamespace(
        step_key="analysis",
        step_type="analysis",
        status="completed",
        output_payload={"step": "analysis"},
        tokens={"total_tokens": 3},
    )
    service = FakeWorkflowService(
        [PlanNode("analysis", "analysis", "Analyze"), PlanNode("final", "finalize", "Finish")],
        completed_steps={"analysis": completed},
    )

    result = await _execute(service, FakeTaskManager(), FakeDatabase())

    assert result["summary"] == "done"
    assert service.executed == ["final"]
    assert service.completed_steps["final"].output_payload["prior"] == "analysis"
    assert service.run.status == "completed"
