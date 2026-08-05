from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.domain import AgentWorkflowConversation
from src.services.agent_workflow import repository
from src.services.agent_workflow_types import EvidenceRef, PlanNode


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, result=None):
        self.result = result
        self.added = []
        self.flushes = 0

    async def execute(self, _query):
        return _ScalarResult(self.result)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_existing_step_retry_resets_transient_failure_fields() -> None:
    old = datetime(2026, 8, 4, tzinfo=timezone.utc)
    step = SimpleNamespace(
        status="failed",
        attempt=2,
        input_payload={},
        started_at=old,
        ended_at=old,
        error_message="temporary",
    )
    db = _DB(step)
    node = PlanNode(
        step_key="analysis",
        step_type="analysis",
        title="Analysis",
        instruction="Analyze",
    )

    result = await repository.start_step(
        db, SimpleNamespace(id=7, steps=[]), node, now=lambda: NOW
    )

    assert result is step
    assert step.status == "running"
    assert step.attempt == 3
    assert step.started_at == NOW
    assert step.ended_at is None
    assert step.error_message is None
    assert step.input_payload["step_key"] == "analysis"
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_finish_step_persists_evidence_and_conversation() -> None:
    db = _DB()
    run = SimpleNamespace(id=11)
    step = SimpleNamespace(id=12, step_type="analysis", metadata_={"kept": True})
    evidence = EvidenceRef(
        evidence_type="web",
        source_type="official",
        source_name="source",
        title="title",
        content_snippet="content",
    )
    upserts = []

    async def upsert(db_arg, run_arg, step_arg, item):
        upserts.append((db_arg, run_arg, step_arg, item))

    await repository.finish_step(
        db,
        run,
        step,
        {
            "output_payload": {"summary": "done"},
            "output_summary": "done",
            "evidence": [evidence],
            "conversations": [{"response": "answer", "timestamp": None}],
            "tokens": {"total": 4},
        },
        now=lambda: NOW,
        parse_datetime=lambda value: None,
        upsert_evidence=upsert,
    )

    assert step.status == "completed"
    assert step.ended_at == NOW
    assert step.metadata_ == {"kept": True, "evidence_count": 1}
    assert upserts == [(db, run, step, evidence)]
    assert len(db.added) == 1
    assert isinstance(db.added[0], AgentWorkflowConversation)
    assert db.added[0].timestamp == NOW
    assert db.flushes == 2


@pytest.mark.asyncio
async def test_fail_step_updates_step_and_run_atomically_before_flush() -> None:
    db = _DB()
    run = SimpleNamespace(status="running", error_message=None, ended_at=None)
    step = SimpleNamespace(status="running", error_message=None, ended_at=None)

    await repository.fail_step(
        db, run, step, RuntimeError("offline"), now=lambda: NOW
    )

    assert (step.status, run.status) == ("failed", "failed")
    assert step.error_message == run.error_message == "offline"
    assert step.ended_at == run.ended_at == NOW
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_terminal_existing_run_keeps_status_while_refreshing_inputs() -> None:
    existing = SimpleNamespace(
        prompt="old",
        mode="old",
        output_format="old",
        country_id=None,
        search_scope="db",
        memory_scope="old",
        allowed_actions=[],
        metadata_={"kept": True},
        status="failed",
        started_at=None,
    )
    db = _DB(existing)
    task = SimpleNamespace(id=9)

    result = await repository.get_or_create_run(
        db,
        task,
        prompt="new",
        mode="research",
        output_format="report",
        country_id=2,
        search_scope="web+db",
        memory_scope="project",
        allowed_actions={"export_data"},
        payload={"new": 1},
        total_token_budget=100,
        now=lambda: NOW,
    )

    assert result is existing
    assert existing.status == "failed"
    assert existing.started_at is None
    assert existing.metadata_ == {"kept": True, "new": 1}
    assert existing.allowed_actions == ["export_data"]
    assert db.flushes == 1
