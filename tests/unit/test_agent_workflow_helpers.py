from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domain.task import TaskType
from src.services import task_executor
from src.services.agent_workflow_service import agent_workflow_service
from src.services.agent_workflow_types import EvidenceRef


def test_normalize_findings_populates_supporting_evidence():
    evidence = [
        EvidenceRef(
            evidence_type="web",
            source_type="who",
            source_name="who.int",
            title="WHO guidance",
            content_snippet="WHO guidance snippet",
            content_hash="hash-1",
        ),
        EvidenceRef(
            evidence_type="db",
            source_type="disease_records",
            source_name="disease_records",
            title="Disease record",
            content_snippet="Disease record snippet",
            content_hash="hash-2",
        ),
    ]

    findings = agent_workflow_service._normalize_findings(
        {"findings": [{"claim": "Claim without references"}]},
        evidence,
    )

    assert findings[0]["supporting_evidence"] == ["hash-1"]
    assert findings[0]["claim"] == "Claim without references"


def test_build_initial_context_contains_plan():
    context = agent_workflow_service._build_initial_context(
        prompt="test prompt",
        payload={"foo": "bar"},
        search_scope="web+db+memory",
        memory_scope="project",
    )

    assert context["prompt"] == "test prompt"
    assert context["plan"] == []
    assert context["evidence"] == []


@pytest.mark.asyncio
async def test_dispatch_routes_agent_workflow(monkeypatch):
    async def _fake_run_agent_workflow(task):
        return {"task_uuid": task.task_uuid, "ok": True}

    monkeypatch.setattr(task_executor, "_run_agent_workflow", _fake_run_agent_workflow)

    task = SimpleNamespace(task_type=TaskType.AGENT_WORKFLOW, task_uuid="agent-123")
    result = await task_executor._dispatch(task)

    assert result["ok"] is True
    assert result["task_uuid"] == "agent-123"
