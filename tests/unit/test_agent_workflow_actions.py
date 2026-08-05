from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domain import TaskStatus, TaskType
from src.services.agent_workflow import actions
from src.services.agent_workflow_service import agent_workflow_service


def test_action_normalization_and_service_compatibility() -> None:
    assert actions.normalize_actions([" crawl_data ", "", "crawl_data", None]) == {
        "crawl_data"
    }
    assert agent_workflow_service._normalize_actions(None) == actions.DEFAULT_ALLOWED_ACTIONS


def test_action_detection_keeps_priority_and_allow_list() -> None:
    allowed = {"crawl_data", "export_data", "generate_report"}
    assert actions.looks_like_action_task("download the latest data", allowed)
    assert actions.infer_action_name("crawl and export the data", allowed) == "crawl_data"
    assert actions.infer_action_name("generate report", {"crawl_data"}) is None


def test_action_defaults_and_task_type_mapping() -> None:
    assert actions.infer_action_parameters("export", "export_data") == {
        "country_code": "CN",
        "formats": ["csv", "json"],
        "mode": "latest",
    }
    knowledge = actions.infer_action_parameters(
        "update influenza knowledge", "update_disease_knowledge"
    )
    assert knowledge["source"] == ["who", "wikidata", "wikipedia"]
    assert actions.map_action_to_task_type("generate_report") == TaskType.GENERATE_REPORT
    with pytest.raises(ValueError, match="Unsupported action"):
        actions.map_action_to_task_type("delete_everything")


def test_service_action_wrappers_match_extracted_policy() -> None:
    prompt = "fetch source data"
    allowed = {"crawl_data"}
    assert agent_workflow_service._looks_like_action_task(prompt, allowed)
    assert agent_workflow_service._infer_action_name(prompt, allowed) == "crawl_data"
    assert agent_workflow_service._infer_action_parameters(prompt, "crawl_data") == (
        actions.infer_action_parameters(prompt, "crawl_data")
    )
    assert agent_workflow_service._map_action_to_task_type("crawl_data") == TaskType.CRAWL_DATA


@pytest.mark.asyncio
async def test_internal_action_rejects_missing_or_disallowed_action_before_side_effects() -> None:
    task = SimpleNamespace(id=1, task_uuid="parent-1")
    run = SimpleNamespace(country_id=None)
    missing = SimpleNamespace(action=None, parameters={}, instruction="", step_key="step")
    denied = SimpleNamespace(
        action="export_data", parameters={}, instruction="", step_key="step"
    )

    with pytest.raises(ValueError, match="require an action"):
        await actions.run_internal_action(
            task=task,
            run=run,
            node=missing,
            allowed_actions={"export_data"},
            execute_action=None,
            extract_artifacts=lambda result: [],
            now=lambda: None,
            logger=SimpleNamespace(warning=lambda *args: None),
        )
    with pytest.raises(ValueError, match="allow-list"):
        await actions.run_internal_action(
            task=task,
            run=run,
            node=denied,
            allowed_actions={"crawl_data"},
            execute_action=None,
            extract_artifacts=lambda result: [],
            now=lambda: None,
            logger=SimpleNamespace(warning=lambda *args: None),
        )


@pytest.mark.asyncio
async def test_internal_action_failure_marks_child_task_failed(monkeypatch) -> None:
    child = SimpleNamespace(id=2, task_uuid="child-1", task_name="Agent action")
    statuses = []

    class Manager:
        async def create_task(self, **kwargs):
            return child

        async def update_task_status(self, task_uuid, status, **kwargs):
            statuses.append((task_uuid, status, kwargs))

        async def add_workbook_entry(self, *_args, **_kwargs):
            return None

    async def fail(_action, _task, _payload):
        raise RuntimeError("upstream failed")

    monkeypatch.setattr(actions, "task_manager", Manager())
    node = SimpleNamespace(
        action="export_data",
        parameters={},
        instruction="Export",
        step_key="export",
    )
    with pytest.raises(RuntimeError, match="upstream failed"):
        await actions.run_internal_action(
            task=SimpleNamespace(id=1, task_uuid="parent-1"),
            run=SimpleNamespace(country_id=None),
            node=node,
            allowed_actions={"export_data"},
            execute_action=fail,
            extract_artifacts=lambda result: [],
            now=lambda: None,
            logger=SimpleNamespace(warning=lambda *args: None),
        )

    assert statuses[0][1] == TaskStatus.RUNNING
    assert statuses[-1][1] == TaskStatus.FAILED
    assert statuses[-1][2] == {"error_message": "upstream failed"}
