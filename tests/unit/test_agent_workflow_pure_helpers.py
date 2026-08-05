from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from src.services.agent_workflow import helpers, prompts, serializers
from src.services.agent_workflow_service import agent_workflow_service


def test_prompt_module_matches_service_compatibility_methods():
    task = SimpleNamespace(task_uuid="task-1", task_name="Research")
    payload = {"mode": "research", "hints": {"language": "en"}}
    allowed_actions = {"export_data", "crawl_data"}

    direct = prompts.planner_prompt(
        prompt="Assess influenza",
        task_uuid=task.task_uuid,
        task_name=task.task_name,
        payload=payload,
        search_scope="web+db",
        allowed_actions=allowed_actions,
    )
    compatible = agent_workflow_service._planner_prompt(
        prompt="Assess influenza",
        task=task,
        payload=payload,
        search_scope="web+db",
        allowed_actions=allowed_actions,
    )

    assert compatible == direct
    assert json.loads(direct.split("\n\n", 1)[1])["allowed_actions"] == ["crawl_data", "export_data"]
    assert agent_workflow_service._planner_system_prompt() == prompts.planner_system_prompt()
    assert agent_workflow_service._analysis_system_prompt() == prompts.analysis_system_prompt()
    assert agent_workflow_service._review_system_prompt() == prompts.review_system_prompt()
    assert agent_workflow_service._synthesizer_system_prompt() == prompts.synthesizer_system_prompt()


def test_structured_prompt_module_matches_service_compatibility_methods():
    payload = {"evidence": [{"content_hash": "hash-1"}], "message": "流感"}

    assert agent_workflow_service._analysis_prompt(payload) == prompts.analysis_prompt(payload)
    assert agent_workflow_service._review_prompt(payload) == prompts.review_prompt(payload)
    assert agent_workflow_service._synthesizer_prompt(payload) == prompts.synthesizer_prompt(payload)
    assert "流感" in prompts.analysis_prompt(payload)


def test_task_serializer_matches_service_compatibility_method():
    created_at = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        id=3,
        task_uuid="task-3",
        task_name="Research",
        task_type="agent_workflow",
        status="completed",
        priority="normal",
        country_id=None,
        report_id=None,
        progress=100,
        created_at=created_at,
        started_at=None,
        completed_at=created_at,
        description="description",
        input_data={"prompt": "hello"},
        output_data={"summary": "done"},
        metadata_={"source": "test"},
    )

    direct = serializers.serialize_task(task)

    assert agent_workflow_service._serialize_task(task) == direct
    assert direct["created_at"] == "2026-08-05T12:00:00+00:00"
    assert direct["metadata"] == {"source": "test"}


def test_run_row_serializer_still_composes_service_wrappers():
    task = SimpleNamespace(
        id=3, task_uuid="task-3", task_name="Research", task_type="agent_workflow",
        status="completed", priority="normal", country_id=None, report_id=None, progress=100,
        created_at=None, started_at=None, completed_at=None, description=None,
        input_data={}, output_data={}, metadata_={},
    )
    run_values = {
        "id": 4, "task_id": 3, "mode": "research", "output_format": "evidence_report",
        "prompt": "hello", "status": "completed", "risk_level": "low", "country_id": None,
        "search_scope": "db", "memory_scope": "project", "allowed_actions": [], "plan_json": [],
        "summary": "done", "findings": [], "citations": [], "artifacts": [], "open_questions": [],
        "actions_taken": [], "result_json": {}, "budget_tokens_total": 1000,
        "budget_tokens_used": 10, "replan_count": 0, "search_round_count": 1,
        "review_round_count": 1, "step_count": 3, "error_message": None, "metadata_": {},
        "created_at": None, "updated_at": None, "started_at": None, "ended_at": None,
    }
    run = SimpleNamespace(**run_values)
    row = SimpleNamespace(Task=task, AgentWorkflowRun=run, country_code=None, country_name=None)

    serialized = agent_workflow_service._serialize_run_row(row)

    assert serialized["task"] == serializers.serialize_task(task)
    assert serialized["run"] == serializers.serialize_run(run)


def test_small_helper_module_matches_service_compatibility_methods():
    values = [None, "a", "a", {"b": 2, "a": 1}, {"a": 1, "b": 2}]

    assert agent_workflow_service._coerce_int("12", 0) == helpers.coerce_int("12", 0) == 12
    assert agent_workflow_service._coerce_float("bad", 0.6) == helpers.coerce_float("bad", 0.6) == 0.6
    assert agent_workflow_service._unique_items(values) == helpers.unique_items(values) == ["a", {"b": 2, "a": 1}]
    assert agent_workflow_service._parse_datetime("2026-08-05T12:00:00Z") == helpers.parse_datetime(
        "2026-08-05T12:00:00Z"
    )


def test_top_level_helper_compatibility_aliases_remain_available():
    from src.services import agent_workflow_service as service_module

    assert service_module._compact_text("  a   b  ") == helpers.compact_text("  a   b  ") == "a b"
    assert service_module._ensure_list("one") == helpers.ensure_list("one") == ["one"]
    assert service_module._extract_keywords("Please analyze influenza data", 3) == helpers.extract_keywords(
        "Please analyze influenza data", 3
    )
