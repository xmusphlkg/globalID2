"""HTTP/authentication and command contracts; all actions use injected doubles."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dashboard.api.deps import get_db
from dashboard.api.http import install_http_contract
from dashboard.api.routers import realtime
from src.services.data_release.realtime_control import RealtimeControlError


def client(monkeypatch):
    service = SimpleNamespace(enqueue=AsyncMock(return_value={"channel": "surveillance", "status": "queued",
        "task_uuid": "json-task", "reason": None, "href": "/publish/tasks?detail=json-task"}),
        control=AsyncMock(return_value={"channel": "surveillance", "enabled": True, "paused": True,
            "effective_enabled": False, "updated_at": None, "error_code": None}),
        pending=AsyncMock(return_value={"items": [], "page": 2, "page_size": 10, "total": 13, "total_pages": 2}),
        runs=AsyncMock(return_value={"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 0}),
        overview=AsyncMock())
    monkeypatch.setattr(realtime, "realtime_control_service", service)
    app = FastAPI()
    install_http_contract(app)
    app.include_router(realtime.router, prefix="/api/v1")
    async def database(): yield object()
    app.dependency_overrides[get_db] = database
    return TestClient(app), service


@pytest.mark.parametrize("endpoint,retry", [("runs", False), ("retry", True)])
def test_manual_endpoint_has_typed_envelope_and_passes_only_channel_action(monkeypatch, endpoint, retry):
    http, service = client(monkeypatch)
    response = http.post(f"/api/v1/publish/realtime/surveillance/{endpoint}", json={})
    assert response.status_code == 202
    payload = response.json()
    assert set(payload) == {"data", "meta"} and payload["data"]["status"] == "queued"
    assert payload["meta"]["request_id"]
    service.enqueue.assert_awaited_once_with("surveillance", job_id=None, **({"retry": True} if retry else {}))


@pytest.mark.parametrize("endpoint,body", [
    ("runs", {"full": True}), ("runs", {"data_line": "situation"}),
    ("retry", {"task_uuid": "old-failed-candidate"}), ("retry", {"publish_dirty_ids": [1]}),
    ("control", {"paused": "false"}), ("control", {"paused": 1}),
    ("control", {"paused": True, "enabled": True}), ("control", {"paused": True, "environment": "secret"}),
])
def test_requests_cannot_widen_action_or_change_deployment_gate(monkeypatch, endpoint, body):
    http, service = client(monkeypatch)
    response = http.request("PATCH" if endpoint == "control" else "POST",
        f"/api/v1/publish/realtime/surveillance/{endpoint}", json=body)
    assert response.status_code == 422
    service.enqueue.assert_not_awaited()
    service.control.assert_not_awaited()


def test_retry_reservation_returns_problem_and_current_task_link(monkeypatch):
    http, service = client(monkeypatch)
    service.enqueue.side_effect = RealtimeControlError("realtime_task_reserved", 409, task_uuid="existing-json")
    response = http.post("/api/v1/publish/realtime/situation/retry", json={})
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "realtime_task_reserved" and payload["task_uuid"] == "existing-json"
    assert payload["href"] == "/publish/tasks?detail=existing-json"
    assert "application/problem+json" in response.headers["content-type"]


def test_control_updates_only_paused(monkeypatch):
    http, service = client(monkeypatch)
    response = http.patch("/api/v1/publish/realtime/surveillance/control", json={"paused": True})
    assert response.status_code == 200
    assert response.json()["data"]["paused"] is True
    service.control.assert_awaited_once_with("surveillance", paused=True)


def test_pending_pagination_uses_existing_envelope_meta_and_is_bounded(monkeypatch):
    http, service = client(monkeypatch)
    response = http.get("/api/v1/publish/realtime/surveillance/pending?page=2&page_size=10&eligibility=blocked")
    assert response.status_code == 200
    assert response.json()["meta"]["pagination"] == {"page": 2, "page_size": 10, "total": 13, "total_pages": 2}
    assert response.headers["cache-control"] == "no-store"
    assert http.get("/api/v1/publish/realtime/surveillance/pending?page_size=101").status_code == 422
    assert http.get("/api/v1/publish/realtime/surveillance/runs?page=0").status_code == 422
    assert http.get("/api/v1/publish/realtime/invalid/runs").status_code == 422


def test_run_status_validation_never_silently_widens_query(monkeypatch):
    http, service = client(monkeypatch)
    assert http.get("/api/v1/publish/realtime/situation/runs?status=unexpected").status_code == 422
    service.runs.assert_not_awaited()


def test_openapi_has_typed_read_and_action_models(monkeypatch):
    http, _ = client(monkeypatch)
    paths = http.get("/openapi.json").json()["paths"]
    for suffix, name in (("runs", "RealtimeActionCreate"), ("retry", "RealtimeActionCreate")):
        operation = paths[f"/api/v1/publish/realtime/{{channel}}/{suffix}"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/" + name)
    assert "RealtimeOverviewOut" in str(paths["/api/v1/publish/realtime/overview"]["get"])


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/overview", None), ("POST", "/surveillance/runs", {}),
    ("POST", "/situation/retry", {}), ("PATCH", "/situation/control", {"paused": True}),
])
def test_main_registers_routes_and_auth_rejects_before_db_or_control(monkeypatch, method, path, body):
    from dashboard.api import deps
    from dashboard.api.main import create_app
    monkeypatch.setattr(deps, "configured_dashboard_api_key", lambda: "test-only-key")
    service = SimpleNamespace(enqueue=AsyncMock(), control=AsyncMock(), overview=AsyncMock())
    monkeypatch.setattr(realtime, "realtime_control_service", service)
    app = create_app()
    async def no_database():
        raise AssertionError("Unauthorized request must not access DB")
        yield
    app.dependency_overrides[get_db] = no_database
    response = TestClient(app).request(method, "/api/v1/publish/realtime" + path, json=body)
    assert response.status_code == 401
    service.enqueue.assert_not_awaited()
    service.control.assert_not_awaited()
    service.overview.assert_not_awaited()
