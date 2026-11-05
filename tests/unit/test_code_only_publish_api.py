"""Dashboard code publication has its own preflight and exclusive request mode."""
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dashboard.api.routers import publish, release


def client():
    app = FastAPI()
    app.include_router(publish.router)
    app.include_router(release.router)
    return TestClient(app)


def checks(ready=True):
    return {
        "checked_at": "2026-11-05T00:00:00Z", "overall_ready": ready,
        "blockers": [] if ready else ["Run a normal data release first"],
        "git": {"env_var": "DOWNLOAD_REPO", "branch": "downloads", "read_access_ok": True,
                "write_access_ok": True, "require_clean_worktree": True},
        "cloudflare": {"token_present": True, "account_id_present": True, "project_access_ok": True},
        "commands": {"python_path": "python", "python_exists": True, "wrangler_available": True},
        "repository_boundary": {"enforced": True},
    }


@pytest.mark.parametrize("query,code_only", [("", False), ("?code_only=true", True), ("?code_only=false", False)])
def test_preflight_forwards_explicit_mode(monkeypatch, query, code_only):
    inspect = AsyncMock(return_value=checks())
    monkeypatch.setattr(release.data_release_service, "integration_checks", inspect)
    response = client().get(f"/releases/site-release/checks{query}")
    assert response.status_code == 200
    inspect.assert_awaited_once_with("site-release", code_only=code_only)
    assert response.json()["overall_ready"] is True


def test_code_preflight_exposes_snapshot_blocker(monkeypatch):
    monkeypatch.setattr(release.data_release_service, "integration_checks", AsyncMock(return_value=checks(False)))
    response = client().get("/releases/site-release/checks?code_only=true")
    assert response.status_code == 200
    assert response.json()["blockers"] == ["Run a normal data release first"]
    assert response.json()["overall_ready"] is False


def test_code_request_reaches_service_without_full_or_reports_mode(monkeypatch):
    enqueue = AsyncMock(return_value={"task_uuid": "code-1", "status": "queued"})
    monkeypatch.setattr(publish.data_release_service, "trigger_job", enqueue)
    response = client().post("/publish/runs", json={"job_id": "site-release", "full": False, "code_only": True})
    assert response.status_code == 202
    enqueue.assert_awaited_once_with("site-release", manual=True, trigger="dashboard", full=False,
                                    reports_only=False, code_only=True)


@pytest.mark.parametrize("modes", [
    {"full": True, "code_only": True}, {"reports_only": True, "code_only": True},
    {"full": True, "reports_only": True},
])
def test_mutually_exclusive_modes_are_rejected_before_enqueue(monkeypatch, modes):
    enqueue = AsyncMock()
    monkeypatch.setattr(publish.data_release_service, "trigger_job", enqueue)
    response = client().post("/publish/runs", json=modes)
    assert response.status_code == 400
    enqueue.assert_not_called()
