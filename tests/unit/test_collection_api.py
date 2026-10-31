"""Behavior contracts for the collection/model/publication facade."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.api.deps import get_db
from dashboard.api.main import app as dashboard_app
from dashboard.api.routers import collection, models, publish
from dashboard.api.schemas.collection import SurveillanceRunCreate
from src.domain.task import TaskType


class Result:
    def __init__(self, rows=(), one=None):
        self.rows, self.one = rows, one

    def scalar_one_or_none(self):
        return self.one

    def scalars(self):
        return self

    def all(self):
        return self.rows


class DB:
    def __init__(self, results):
        self.results = iter(results)

    async def execute(self, *args, **kwargs):
        return next(self.results)

    async def get(self, *args, **kwargs):
        return None


def client(db):
    app = FastAPI()
    for module in (collection, models, publish):
        app.include_router(module.router)

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def test_run_has_all_attempts_and_only_artifact_metadata():
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        task_uuid="run-1",
        task_type=TaskType.CRAWL_DATA,
        status="failed",
        country_id=None,
        input_data={"source": "nndss_api"},
    )
    stages = [
        SimpleNamespace(
            stage=stage,
            attempt=attempt,
            status="failed" if stage == "organize" else "completed",
            metrics={},
            error=None,
            started_at=now,
            finished_at=now,
        )
        for attempt in (1, 2)
        for stage in ("collect", "organize", "store")
    ]
    artifact = SimpleNamespace(
        attempt=1,
        relative_path="data/raw/us/run-1/page.csv",
        sha256="f" * 64,
        byte_size=7,
        source_url="https://example.org/source",
        kind="csv_page",
        content=b"private",
    )
    response = client(
        DB([Result(one=task), Result(stages), Result([artifact]), Result([])])
    ).get("/collection/runs/run-1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["attempt"] == 2 and len(data["stages"]) == 6
    assert set(data["artifacts"][0]) == {
        "attempt",
        "relative_path",
        "sha256",
        "byte_size",
        "source_url",
        "kind",
    }
    assert "private" not in response.text


def test_duplicate_country_returns_409_and_missing_fill_defaults_false(monkeypatch):
    enqueue = AsyncMock(side_effect=RuntimeError("A crawl task is already running"))
    monkeypatch.setattr(collection.SourceOperationsService, "enqueue_run", enqueue)
    response = client(DB([])).post(
        "/collection/runs", json={"line": "surveillance", "country_code": "US"}
    )
    assert response.status_code == 409
    assert enqueue.call_args.args[1]["fill_missing"] is False
    assert (
        SurveillanceRunCreate(line="surveillance", country_code="US").fill_missing
        is False
    )


def test_leased_item_priority_is_immutable():
    row = SimpleNamespace(id=1, status="leased", priority="low")
    response = client(DB([Result(one=row)])).post(
        "/models/queue/1/priority", json={"priority": "high"}
    )
    assert response.status_code == 409 and row.priority == "low"


def test_routes_omit_credentials_and_count_live_leases(monkeypatch):
    monkeypatch.setattr(
        models,
        "get_runtime_routes",
        AsyncMock(
            return_value=[
                {
                    "model_key": "route",
                    "api_key": "secret-key",
                    "extra_headers": {"Authorization": "secret"},
                    "available_for_routing": True,
                    "runtime_model_capacity": 3,
                }
            ]
        ),
    )
    response = client(DB([Result([("route", 2)])])).get("/models/routes")
    assert response.status_code == 200
    assert response.json()["data"][0]["in_flight"] == 2
    assert "secret" not in response.text and "api_key" not in response.text


def test_publish_pending_groups_only_scopes():
    rows = [("surveillance", "US"), ("literature", "*"), ("knowledge", "D001")]
    response = client(DB([Result(rows)])).get("/publish/pending")
    assert response.json()["data"] == {
        "surveillance": ["US"],
        "literature": True,
        "situation": False,
        "knowledge": ["D001"],
    }


def test_full_release_uses_existing_enqueuer(monkeypatch):
    trigger = AsyncMock(return_value={"task_uuid": "release-1", "status": "queued"})
    monkeypatch.setattr(publish.data_release_service, "trigger_job", trigger)
    response = client(DB([])).post("/publish/runs", json={"full": True})
    assert response.status_code == 202
    assert trigger.call_args.kwargs["full"] is True
    assert trigger.call_args.args == ("site-release",)


def test_sources_config_static_route_precedes_country_route():
    from dashboard.api.routers import sources

    paths = [route.path for route in sources.router.routes]
    assert "/sources/config" in paths
    dynamic = [i for i, path in enumerate(paths) if path == "/sources/{country_code}"]
    assert not dynamic or paths.index("/sources/config") < min(dynamic)
    app = FastAPI()
    app.include_router(sources.router)

    async def override():
        yield DB([Result([])])

    app.dependency_overrides[get_db] = override
    response = TestClient(app).get("/sources/config")
    assert response.status_code == 200 and response.json() == []


def test_openapi_has_collection_facade_and_no_artifact_byte_route():
    paths = dashboard_app.openapi()["paths"]
    assert "/api/v1/collection/runs/{task_uuid}" in paths
    assert "/api/v1/models/queue/{item_id}/priority" in paths
    assert "/api/v1/publish/pending" in paths
    assert not any(
        "artifact" in path and ("download" in path or "content" in path)
        for path in paths
    )
