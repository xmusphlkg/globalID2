"""The static release endpoint and shared task list keep JSON channels explicit."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dashboard.api.routers import publish
from dashboard.api.schemas.task import TaskDetailOut, TaskOut
from src.control_plane.operations import _task_projection
from src.control_plane.publication_kind import publication_kind
from src.domain import Task, TaskPriority, TaskStatus, TaskType


def client(monkeypatch, *, surveillance=True, situation=True):
    app = FastAPI()
    app.include_router(publish.router)
    async def database():
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [
            ("surveillance", "IE"), ("situation", "*"), ("literature", "*"), ("knowledge", "D038"),
        ])))
    app.dependency_overrides[publish.get_db] = database
    monkeypatch.setattr(publish, "get_config", lambda: SimpleNamespace(data_release=SimpleNamespace(
        surveillance_json_enabled=surveillance, situation_json_enabled=situation)))
    return TestClient(app)


@pytest.mark.parametrize("surveillance,situation", [(True, True), (False, False), (True, False), (False, True)])
def test_pending_preserves_scopes_and_exposes_actual_channel_routing(monkeypatch, surveillance, situation):
    response = client(monkeypatch, surveillance=surveillance, situation=situation).get("/publish/pending")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "surveillance": ["IE"], "literature": True, "situation": True, "knowledge": ["D038"],
        "surveillance_json_enabled": surveillance, "situation_json_enabled": situation,
        "literature_json_enabled": False,
    }


@pytest.mark.parametrize("extra", [{"data_only": True}, {"data_only": False}, {"data_line": "situation"}, {"channel": "surveillance"}])
def test_static_endpoint_rejects_json_or_unknown_arguments_before_enqueuing(monkeypatch, extra):
    trigger = AsyncMock()
    monkeypatch.setattr(publish.data_release_service, "trigger_job", trigger)
    response = client(monkeypatch).post("/publish/runs", json={"job_id": "site-release", **extra})
    assert response.status_code == 422
    trigger.assert_not_awaited()


@pytest.mark.parametrize("mode", [{}, {"full": True}, {"reports_only": True}, {"code_only": True}])
def test_static_endpoint_preserves_explicit_static_modes(monkeypatch, mode):
    trigger = AsyncMock(return_value={"task_uuid": "static", "status": "queued"})
    monkeypatch.setattr(publish.data_release_service, "trigger_job", trigger)
    response = client(monkeypatch).post("/publish/runs", json={"job_id": "site-release", **mode})
    assert response.status_code == 202
    trigger.assert_awaited_once_with("site-release", manual=True, trigger="dashboard",
        full=mode.get("full", False), reports_only=mode.get("reports_only", False), code_only=mode.get("code_only", False))


@pytest.mark.parametrize("task_type,data,expected", [
    (TaskType.EXPORT_DATA, {}, "static"),
    (TaskType.EXPORT_DATA, {"data_line": "situation", "content_only": True}, "static"),
    (TaskType.EXPORT_DATA, {"data_only": True}, "surveillance_json"),
    (TaskType.EXPORT_DATA, {"data_only": True, "data_line": "surveillance"}, "surveillance_json"),
    (TaskType.EXPORT_DATA, {"data_only": True, "data_line": "situation"}, "situation_json"),
    (TaskType.EXPORT_DATA, {"data_only": True, "data_line": "literature"}, "literature_json"),
    (TaskType.EXPORT_DATA, {"data_only": True, "data_line": "unrecognized"}, "unknown_json"),
    (TaskType.EXPORT_DATA, {"data_only": True, "data_line": ["invalid"]}, "unknown_json"),
    (TaskType.EXPORT_DATA, {"reports_only": True}, "reports"),
    (TaskType.EXPORT_DATA, {"code_only": True}, "code"),
    (TaskType.EXPORT_DATA, {"raw_archive_only": True}, "raw_archive"),
    (TaskType.CRAWL_DATA, {"data_only": True, "data_line": "situation"}, None),
])
def test_classification_uses_execution_inputs_and_not_names_or_metadata(task_type, data, expected):
    task = Task(id=1, task_uuid="task", task_name="Misleading Situation JSON title",
        task_type=task_type, status=TaskStatus.COMPLETED, priority=TaskPriority.NORMAL,
        input_data={**data, "large_release_plan": "must stay out of the list"},
        metadata_={"data_only": True, "data_line": "situation"}, created_at=datetime.now(timezone.utc))
    assert publication_kind(task_type, data) == expected
    projection = _task_projection(task)
    listing = TaskOut.model_validate(projection).model_dump()
    detail = TaskDetailOut.model_validate({**projection, "input_data": task.input_data}).model_dump()
    assert listing["publication_kind"] == detail["publication_kind"] == expected
    assert "input_data" not in listing
    assert detail["input_data"] == task.input_data
