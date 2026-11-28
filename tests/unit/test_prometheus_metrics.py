from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.api import deps
from dashboard.api.http import install_http_contract
from dashboard.api.main import create_app
from src.core import database as database_module
from src.core.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    PrometheusRegistry,
    collect_db_pool_metrics,
    extract_route_template,
    metrics_registry,
)
from src.core.operational_metrics import operational_metrics_collector


@pytest.fixture(autouse=True)
def _reset_global_metrics():
    metrics_registry.reset()
    operational_metrics_collector.reset()
    yield
    metrics_registry.reset()
    operational_metrics_collector.reset()


def test_registry_renders_valid_prometheus_text_format_with_histogram_and_counter() -> None:
    reg = PrometheusRegistry(buckets=(0.01, 0.1, 1.0))
    reg.record_http_request(method="get", route="/api/v1/items/{item_id}", status_code=200, duration_seconds=0.005)
    reg.record_http_request(method="GET", route="/api/v1/items/{item_id}", status_code=201, duration_seconds=0.05)
    reg.record_http_request(method="POST", route="/api/v1/items", status_code=500, duration_seconds=0.5)
    reg.set_task_queue_snapshot({"pending": 4, "running": 2})

    output = reg.render_prometheus(include_db_pool=False)

    assert "# TYPE gids_process_uptime_seconds gauge" in output
    assert "# TYPE gids_http_requests_total counter" in output
    assert "# TYPE gids_http_request_duration_seconds histogram" in output
    assert 'gids_http_requests_total{method="GET",route="/api/v1/items/{item_id}",status_class="2xx"} 2' in output
    assert 'gids_http_requests_total{method="POST",route="/api/v1/items",status_class="5xx"} 1' in output

    # Cumulative buckets for the two GET 2xx observations (0.005s and 0.05s)
    assert 'gids_http_request_duration_seconds_bucket{method="GET",route="/api/v1/items/{item_id}",status_class="2xx",le="0.01"} 1' in output
    assert 'gids_http_request_duration_seconds_bucket{method="GET",route="/api/v1/items/{item_id}",status_class="2xx",le="0.1"} 2' in output
    assert 'gids_http_request_duration_seconds_bucket{method="GET",route="/api/v1/items/{item_id}",status_class="2xx",le="1"} 2' in output
    assert 'gids_http_request_duration_seconds_bucket{method="GET",route="/api/v1/items/{item_id}",status_class="2xx",le="+Inf"} 2' in output
    assert 'gids_http_request_duration_seconds_sum{method="GET",route="/api/v1/items/{item_id}",status_class="2xx"} 0.055' in output
    assert 'gids_http_request_duration_seconds_count{method="GET",route="/api/v1/items/{item_id}",status_class="2xx"} 2' in output

    # Task queue snapshot gauge
    assert "# TYPE gids_task_queue_tasks gauge" in output
    assert 'gids_task_queue_tasks{status="pending"} 4' in output
    assert 'gids_task_queue_tasks{status="running"} 2' in output

    reg.clear_task_queue_snapshot()
    assert "gids_task_queue_tasks" not in reg.render_prometheus(include_db_pool=False)


def test_extract_route_template_uses_route_path_and_never_raw_ids() -> None:
    matched_req = SimpleNamespace(scope={"route": SimpleNamespace(path="/api/v1/tasks/{task_uuid}")})
    unmatched_req = SimpleNamespace(scope={})

    assert extract_route_template(matched_req) == "/api/v1/tasks/{task_uuid}"
    assert extract_route_template(unmatched_req) == "unmatched"


def test_db_pool_metrics_do_not_initialize_engine_when_uninitialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database_module, "_engine", None)

    def _fail_get_engine():
        raise AssertionError("get_engine() must not be called while collecting metrics")

    monkeypatch.setattr(database_module, "get_engine", _fail_get_engine)

    assert collect_db_pool_metrics() is None
    rendered = metrics_registry.render_prometheus(include_db_pool=True)
    assert "gids_db_pool_size" not in rendered


def test_db_pool_metrics_render_pool_gauges_when_engine_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = SimpleNamespace(
        size=lambda: 10,
        checkedin=lambda: 7,
        checkedout=lambda: 3,
        overflow=lambda: 1,
    )
    fake_engine = SimpleNamespace(sync_engine=SimpleNamespace(pool=fake_pool))
    monkeypatch.setattr(database_module, "_engine", fake_engine)

    rendered = metrics_registry.render_prometheus(include_db_pool=True)

    assert "# TYPE gids_db_pool_size gauge" in rendered
    assert "gids_db_pool_size 10" in rendered
    assert "gids_db_pool_checked_in 7" in rendered
    assert "gids_db_pool_checked_out 3" in rendered
    assert "gids_db_pool_overflow 1" in rendered


def test_middleware_records_low_cardinality_route_template_and_skips_metrics_path() -> None:
    app = FastAPI()
    install_http_contract(app)

    @app.get("/api/v1/items/{item_id}")
    async def get_item(item_id: str):
        return {"id": item_id}

    client = TestClient(app)
    raw_uuid = "123e4567-e89b-12d3-a456-426614174000"
    resp = client.get(f"/api/v1/items/{raw_uuid}")
    assert resp.status_code == 200

    # Unmatched 404 path should collapse to "unmatched" rather than leaking raw path
    resp_404 = client.get("/api/v1/does-not-exist/secret-99999")
    assert resp_404.status_code == 404

    rendered = metrics_registry.render_prometheus(include_db_pool=False)
    assert 'route="/api/v1/items/{item_id}"' in rendered
    assert raw_uuid not in rendered
    assert "secret-99999" not in rendered
    assert 'route="unmatched",status_class="4xx"' in rendered


def test_metrics_endpoint_is_excluded_from_openapi_and_respects_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRICS_PUBLIC", raising=False)
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="metrics-secret", is_production=False),
    )

    app = create_app()
    assert "/metrics" not in app.openapi()["paths"]

    client = TestClient(app)

    # Without API key -> 401
    unauth = client.get("/metrics")
    assert unauth.status_code == 401
    assert unauth.headers["content-type"].startswith("application/problem+json")

    # Trigger one live route first so HTTP metrics are populated
    live = client.get("/health/live")
    assert live.status_code == 200

    # With API key -> 200 Prometheus text format; /metrics itself not recorded
    ok = client.get("/metrics", headers={"x-dashboard-api-key": "metrics-secret"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    assert 'gids_http_requests_total{method="GET",route="/health/live",status_class="2xx"} 1' in ok.text
    assert 'route="/metrics"' not in ok.text

    # Bearer token also works
    ok_bearer = client.get("/metrics", headers={"authorization": "Bearer metrics-secret"})
    assert ok_bearer.status_code == 200

    # METRICS_PUBLIC=1 allows unauthenticated scrapes
    monkeypatch.setenv("METRICS_PUBLIC", "1")
    public_resp = client.get("/metrics")
    assert public_resp.status_code == 200
    assert "gids_process_uptime_seconds" in public_resp.text


def test_metrics_endpoint_fails_closed_in_production_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRICS_PUBLIC", raising=False)
    monkeypatch.delenv("DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION", raising=False)
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="", is_production=True),
    )

    app = create_app()
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "service_unavailable"
