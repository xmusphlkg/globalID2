"""Source-only HTTP enqueue contracts; no network, database, or worker execution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.api.deps import get_db
from dashboard.api.routers import knowledge_sources
from dashboard.api.schemas.knowledge_sources import KnowledgeSourceRunCreate
from src.domain.task import TaskStatus
from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService, SOURCE_GROUPS


def make_client(monkeypatch, *, created=True, skipped=None):
    service = SimpleNamespace(
        list_catalogue=AsyncMock(return_value=[{"disease_id": "D001"}, {"disease_id": "D002"}]),
        enqueue_source_refresh_tasks=AsyncMock(return_value={
            "created_tasks": [SimpleNamespace(task_uuid="source-run", status=TaskStatus.QUEUED, input_data={"disease_id": "D001"})] if created else [],
            "skipped": skipped or [],
            "source_groups": ["who", "who_don"],
        }),
    )
    monkeypatch.setattr(knowledge_sources, "DiseaseKnowledgeUpdateService", lambda: service)
    app = FastAPI()
    app.include_router(knowledge_sources.router, prefix="/api/v1")
    db = object()

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app), service, db


def test_queues_only_sources_and_returns_typed_task_reference(monkeypatch):
    client, service, db = make_client(monkeypatch)
    response = client.post("/api/v1/knowledge/source-runs", json={"disease_ids": [" d001 ", "D001"], "source": ["who"]})
    assert response.status_code == 202
    payload = response.json()
    assert set(payload) == {"data", "meta"}
    data = payload["data"]
    assert data["requested_disease_ids"] == ["D001"]
    assert data["created_count"] == 1 and data["skipped_count"] == 0
    assert data["created_tasks"][0] == {
        "task_uuid": "source-run", "status": "queued", "resource_type": "task",
        "resource_id": "source-run", "href": "/collection/runs/source-run",
    }
    service.enqueue_source_refresh_tasks.assert_awaited_once_with(
        db, disease_ids=["D001"], source_groups=["who"], force=False,
        priority="normal", enqueue_ai_after_source_refresh=False,
        requested_by="collection-source-control", initiated_via="dashboard-knowledge-sources",
    )


@pytest.mark.parametrize("body", [
    {"disease_ids": []}, {"disease_ids": [" "]}, {"disease_ids": ["X"]},
    {"disease_ids": ["D001"], "source": ["invalid"]},
    {"disease_ids": ["D001"], "priority": "immediate"},
    {"disease_ids": ["D001"], "enqueue_ai_after_source_refresh": True},
    {"disease_ids": ["D001"], "generator": "ai"},
])
def test_invalid_request_cannot_enqueue_or_fall_back_to_generation(monkeypatch, body):
    client, service, _ = make_client(monkeypatch)
    response = client.post("/api/v1/knowledge/source-runs", json=body)
    assert response.status_code == 422
    service.enqueue_source_refresh_tasks.assert_not_awaited()


def test_active_and_ineligible_diseases_are_explicitly_skipped(monkeypatch):
    client, service, _ = make_client(monkeypatch, created=False, skipped=[{"disease_id": "D001", "reason": "active_task_exists", "task_uuid": "existing-run"}])
    response = client.post("/api/v1/knowledge/source-runs", json={"disease_ids": ["D001", "D002"]})
    data = response.json()["data"]
    assert data["created_count"] == 0 and data["skipped_count"] == 2
    assert data["skipped"] == [
        {"disease_id": "D001", "reason": "active_task_exists", "task_uuid": "existing-run"},
        {"disease_id": "D002", "reason": "not_eligible", "task_uuid": None},
    ]
    assert service.enqueue_source_refresh_tasks.call_args.kwargs["enqueue_ai_after_source_refresh"] is False


def test_declared_source_groups_match_service_registry():
    schema = KnowledgeSourceRunCreate.model_json_schema()
    assert set(schema["properties"]["source"]["items"]["enum"]) == set(SOURCE_GROUPS)


def test_new_route_has_typed_request_and_envelope(monkeypatch):
    client, _, _ = make_client(monkeypatch)
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/knowledge/source-runs"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/KnowledgeSourceRunCreate")
    assert operation["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/DataResponse_KnowledgeSourceRunResult_")


def test_main_application_registers_source_route_and_requires_auth(monkeypatch):
    from dashboard.api import deps
    from dashboard.api.main import create_app

    monkeypatch.setattr(deps, "configured_dashboard_api_key", lambda: "test-only-key")
    service = SimpleNamespace(list_catalogue=AsyncMock(), enqueue_source_refresh_tasks=AsyncMock())
    monkeypatch.setattr(knowledge_sources, "DiseaseKnowledgeUpdateService", lambda: service)
    app = create_app()
    assert "/api/v1/knowledge/source-runs" in app.openapi()["paths"]

    async def no_database():
        raise AssertionError("Authentication rejection must not access the database")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = no_database
    response = TestClient(app).post("/api/v1/knowledge/source-runs", json={"disease_ids": ["D001"]})
    assert response.status_code == 401
    service.list_catalogue.assert_not_awaited()
    service.enqueue_source_refresh_tasks.assert_not_awaited()


def test_existing_service_creates_source_tasks_and_queues_them_without_ai(monkeypatch):
    from src.domain.task import TaskType
    from src.services import disease_knowledge_service as service_module

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    db = SimpleNamespace(execute=AsyncMock(return_value=EmptyResult()))
    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "list_catalogue", AsyncMock(return_value=[{"disease_id": "D001", "name_en": "Influenza"}]))
    task = SimpleNamespace(task_uuid="real-service-run", status=TaskStatus.PENDING, input_data={})

    async def create(**kwargs):
        task.input_data = kwargs["input_data"]
        return task

    async def update(_uuid, status):
        task.status = status
        return task

    create_task = AsyncMock(side_effect=create)
    update_status = AsyncMock(side_effect=update)
    monkeypatch.setattr(service_module.task_manager, "create_task", create_task)
    monkeypatch.setattr(service_module.task_manager, "update_task_status", update_status)
    monkeypatch.setattr(service_module.task_manager, "add_workbook_entry", AsyncMock())
    app = FastAPI()
    app.include_router(knowledge_sources.router)

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    response = TestClient(app).post("/knowledge/source-runs", json={"disease_ids": ["D001"], "source": ["who"]})
    assert response.status_code == 202
    assert response.json()["data"]["created_tasks"][0]["status"] == "queued"
    assert create_task.call_args.kwargs["task_type"] == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES
    assert create_task.call_args.kwargs["input_data"]["source_only"] is True
    assert create_task.call_args.kwargs["input_data"]["enqueue_ai_after_source_refresh"] is False
    assert create_task.await_count == 1
    update_status.assert_awaited_once_with("real-service-run", TaskStatus.QUEUED)
