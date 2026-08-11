from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from dashboard.api.http import install_http_contract
from dashboard.api.main import app as dashboard_app
from src.control_plane.operations import ScheduleApplicationService


def _contract_app() -> FastAPI:
    app = FastAPI()
    install_http_contract(app)

    @app.get("/api/v1/items")
    async def items(response: Response):
        response.headers["X-Total-Count"] = "3"
        response.headers["X-Limit"] = "2"
        response.headers["X-Offset"] = "0"
        return [{"id": "a"}, {"id": "b"}]

    @app.get("/api/v1/missing")
    async def missing():
        raise HTTPException(404, "Resource not found")

    @app.get("/api/v1/broken")
    async def broken():
        raise RuntimeError("private failure detail")

    return app


def test_v1_success_responses_use_data_meta_and_request_id() -> None:
    client = TestClient(_contract_app())
    response = client.get("/api/v1/items", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert response.json() == {
        "data": [{"id": "a"}, {"id": "b"}],
        "meta": {
            "request_id": "test-request",
            "pagination": {"page": 1, "page_size": 2, "total": 3, "total_pages": 2},
        },
    }


def test_errors_use_rfc_9457_problem_details() -> None:
    client = TestClient(_contract_app())
    response = client.get("/api/v1/missing")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "http_404"
    assert response.json()["detail"] == "Resource not found"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_unhandled_errors_are_safe_problem_details() -> None:
    client = TestClient(_contract_app(), raise_server_exceptions=False)
    response = client.get("/api/v1/broken")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "internal_error"
    assert "private failure detail" not in response.json()["detail"]


def test_openapi_contains_new_control_plane_resources_and_no_websocket() -> None:
    schema = dashboard_app.openapi()

    assert schema["x-gids-contract-version"] == "2026-08"
    for path in (
        "/api/v1/overview",
        "/api/v1/overview/events",
        "/api/v1/runtime/services",
        "/api/v1/events/stream",
        "/api/v1/schedules",
        "/api/v1/sources/{country_code}/runs",
        "/api/v1/analytics/trends",
        "/api/v1/catalog/browse",
        "/api/v1/ai/runs",
        "/api/v1/notification-campaigns",
        "/api/v1/reports/runs",
        "/api/v1/releases/config",
        "/api/v1/countries/{country_code}",
        "/api/v1/mappings/categories/{category_key}/suggest",
        "/api/v1/mappings/candidates/{candidate_key}/accept",
        "/api/v1/mappings/releases/{release_code}/activate",
        "/api/v1/ai/models/providers/{provider_key}",
        "/api/v1/ai/models/{model_key}",
        "/api/v1/overview/events/{event_key}",
        "/api/v1/reports/{report_uuid}/sections/{section_key}/conversations",
        "/api/v1/tasks/{task_uuid}/events",
        "/api/v1/tasks/{task_uuid}/retry",
    ):
        assert path in schema["paths"]
    for legacy_path in (
        "/api/v1/tasks/ws",
        "/api/v1/ai/agent-runs",
        "/api/v1/disease-mappings/v3/summary",
        "/api/v1/explorer/browse",
        "/api/v1/overview/trend",
        "/api/v1/release",
        "/api/v1/situation/candidates",
        "/api/v1/subscriptions/notifications",
    ):
        assert legacy_path not in schema["paths"]
    task_parameters = {
        item["name"] for item in schema["paths"]["/api/v1/tasks"]["get"]["parameters"]
    }
    assert {"page", "page_size"}.issubset(task_parameters)
    assert {"limit", "offset"}.isdisjoint(task_parameters)

    for methods in schema["paths"].values():
        for operation in methods.values():
            if not isinstance(operation, dict):
                continue
            parameter_names = {
                parameter.get("name")
                for parameter in operation.get("parameters", [])
                if isinstance(parameter, dict)
            }
            assert {"limit", "offset", "country_id"}.isdisjoint(parameter_names)


def test_schedule_ids_are_stable_and_typed() -> None:
    assert ScheduleApplicationService.schedule_id("ingestion", "de-rki") == "ingestion:de-rki"
    assert ScheduleApplicationService.parse_id("release:site-release") == ("release", "site-release")
