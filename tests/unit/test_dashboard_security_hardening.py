from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from dashboard.api import deps
from dashboard.api.main import DEFAULT_CORS_ORIGINS, configured_cors_origins, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _request(headers: dict[str, str] | None = None):
    return SimpleNamespace(headers=Headers(headers or {}))


def test_cors_origins_defaults_when_env_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_CORS_ORIGINS", raising=False)
    assert configured_cors_origins() == DEFAULT_CORS_ORIGINS


def test_cors_origins_configurable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DASHBOARD_CORS_ORIGINS",
        " https://dash.example.com , https://ops.example.com , https://dash.example.com ",
    )
    assert configured_cors_origins() == ["https://dash.example.com", "https://ops.example.com"]

    app = create_app()
    client = TestClient(app)

    allowed = client.options(
        "/health/live",
        headers={
            "Origin": "https://dash.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "https://dash.example.com"

    disallowed = client.options(
        "/health/live",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert disallowed.headers.get("access-control-allow-origin") is None


def test_production_without_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION", raising=False)
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="", is_production=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        deps.require_dashboard_api_key(_request())

    assert exc_info.value.status_code == 503
    assert "Dashboard API key must be configured in production" in str(exc_info.value.detail)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/overview")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "service_unavailable"


def test_production_unauthenticated_override_allows_explicit_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION", "1")
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="", is_production=True),
    )

    deps.require_dashboard_api_key(_request())


def test_development_without_api_key_remains_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION", raising=False)
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="", is_production=False),
    )

    deps.require_dashboard_api_key(_request())


def test_docker_compose_hardening() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    postgres_ports = compose["services"]["postgres"]["ports"]
    assert "${GLOBALID_POSTGRES_BIND_ADDRESS:-127.0.0.1}:5432:5432" in postgres_ports

    qdrant_healthcheck = compose["services"]["qdrant"].get("healthcheck")
    assert isinstance(qdrant_healthcheck, dict)
    assert qdrant_healthcheck.get("test")


def test_systemd_application_services_have_security_directives() -> None:
    systemd_dir = REPO_ROOT / "deploy" / "systemd"
    for service_file in sorted(systemd_dir.glob("*.service")):
        text = service_file.read_text(encoding="utf-8")
        if service_file.name == "globalid-docker.service":
            assert "NoNewPrivileges=yes" not in text
            continue
        assert "NoNewPrivileges=yes" in text, service_file.name
        assert "PrivateTmp=yes" in text, service_file.name
