from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from dashboard.api import deps


def _request(headers: dict[str, str] | None = None):
    return SimpleNamespace(headers=Headers(headers or {}))


def test_dashboard_auth_allows_requests_when_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(dashboard_api_key=""))

    deps.require_dashboard_api_key(_request())


def test_dashboard_auth_accepts_dashboard_api_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(dashboard_api_key="secret"))

    deps.require_dashboard_api_key(_request({"x-dashboard-api-key": "secret"}))


def test_dashboard_auth_accepts_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(dashboard_api_key="secret"))

    deps.require_dashboard_api_key(_request({"authorization": "Bearer secret"}))


def test_dashboard_auth_rejects_missing_or_wrong_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(dashboard_api_key="secret"))

    with pytest.raises(HTTPException) as missing:
        deps.require_dashboard_api_key(_request())
    with pytest.raises(HTTPException) as wrong:
        deps.require_dashboard_api_key(_request({"x-dashboard-api-key": "wrong"}))

    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401
