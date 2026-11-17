"""Explicit recovery plans rebuild all eligible scopes and acknowledge exact IDs."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from dashboard.api.routers import publish
from src.domain import Country, Task
from src.domain.collection import PublishDirty
from src.services import data_release_service as release_module
from src.services.data_release import publication_execution as execution, publication_planning as planning, projection
from src.services.data_release.data_selection import validate_data_selection
from src.services.data_release_service import DataReleaseJobConfig
from tests.unit.test_release_dirty_selection import release_db  # noqa: F401


@pytest.fixture
def content_api(release_db, monkeypatch, tmp_path):
    maker, service, _ = release_db
    config = SimpleNamespace(timezone="UTC", surveillance_json_enabled=True,
        situation_json_enabled=True, literature_json_enabled=True, batch_max_countries=1)
    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[DataReleaseJobConfig(job_id="site-release", name="Site")]))
    monkeypatch.setattr(service, "_r2_papers_config", lambda: SimpleNamespace(s3_endpoint="https://r2.invalid", bucket="test"))
    monkeypatch.setattr(publish, "data_release_service", service)
    monkeypatch.setattr(publish, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(publish, "get_config", lambda: SimpleNamespace(data_dir=tmp_path / "plans", data_release=config))
    eligibility = lambda code: {"public_release_enabled": code not in {"AR", "CO"}}
    monkeypatch.setattr(publish, "get_country_bootstrap_config", eligibility)
    monkeypatch.setattr(release_module, "get_country_bootstrap_config", eligibility)
    monkeypatch.setattr("src.services.data_release.data_selection.get_country_bootstrap_config", eligibility)
    monkeypatch.setattr(execution, "configuration_digest", lambda *args: "frozen-config")
    monkeypatch.setattr(execution, "source_fingerprint", lambda *args: {})
    monkeypatch.setattr(planning, "rendering_configuration", lambda *args: {})
    monkeypatch.setattr(planning, "inspect_source", lambda *args: {
        "revision": "a" * 40, "changes": [], "fingerprints": {}, "warnings": [],
        "generations": {"site": None, "surveillance": None, "situation": None, "literature": None}})

    class ReadOnlyBaseline:
        def __init__(self, config): pass
        def get_object(self, key): return b"baseline"
        def close(self): pass
    monkeypatch.setattr(execution, "R2S3Client", ReadOnlyBaseline)

    async def database():
        async with maker() as db:
            yield db
    app = FastAPI()
    app.include_router(publish.router, prefix="/api/v1")
    app.dependency_overrides[publish.get_db] = database
    return app, maker, service


async def create(client, channel="surveillance", **kwargs):
    response = await client.post("/api/v1/publish/plans", json={"kind": "content", "channel": channel, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def start(client, plan):
    return await client.post("/api/v1/publish/runs", json={"plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"], "idempotency_key": "exact-content-batch"})


async def test_explicit_rebuild_plan_freezes_entire_eligible_inventory_without_acknowledging_blocked(content_api, monkeypatch):
    app, maker, _ = content_api
    async with maker() as db:
        db.add_all([Country(code="AR", name="AR", name_en="AR", is_active=True),
                    Country(code="US-AK", name="Alaska", name_en="Alaska", is_active=True)])
        rows = [PublishDirty(line="surveillance", scope_key=code, source_task_uuid="source",
            marked_at=datetime.now(timezone.utc), change_set={"version": 1, "precise": True})
            for code in ["AR", "US", "JP"]]
        db.add_all(rows)
        await db.commit()
        selected = [rows[1].id, rows[2].id]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        plan = await create(client, surveillance_rebuild_all=True, suppress_subscription_dispatch=True)
        assert plan["surveillance_rebuild_all"] is True
        assert plan["country_codes"] == ["JP", "US", "US-AK"]
        assert plan["dirty_ids"] == selected
        assert not plan["pages_deploy_required"]
        response = await start(client, plan)
        assert response.status_code == 202, response.text
    async with maker() as db:
        task = (await db.execute(select(Task))).scalar_one()
        inputs = task.input_data
        assert inputs["surveillance_rebuild_all"] is True
        assert inputs["data_only"] is True and inputs["full"] is False
        assert inputs["incremental_country_codes"] == ["JP", "US", "US-AK"]
        assert inputs["publish_dirty_ids"] == selected
        assert inputs["continuation_dirty_ids"] == []
        assert inputs["publication_expected_generations"]["surveillance"]
        await validate_data_selection(db, country_codes=inputs["incremental_country_codes"],
            dirty_ids=selected, surveillance_rebuild_all=True)
        assert all(row.published_at is None for row in (await db.execute(select(PublishDirty))).scalars())
        later = PublishDirty(line="surveillance", scope_key="JP", source_task_uuid="later",
            marked_at=datetime.now(timezone.utc))
        db.add(later)
        await db.commit()
        monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
            "countries_by_code": {code: {} for code in inputs["incremental_country_codes"]},
        }))
        monkeypatch.setattr(projection, "_publish_country", AsyncMock())
        monkeypatch.setattr(projection, "_retire_disabled_publications", AsyncMock(return_value=[]))
        await projection.commit_release_projection(db, task_uuid=task.task_uuid,
            country_codes=inputs["incremental_country_codes"], full=True, dirty_ids=selected)
        await db.commit()
        rows = (await db.execute(select(PublishDirty))).scalars().all()
        assert {row.id for row in rows if row.published_task_uuid == task.task_uuid} == set(selected)
        assert all(row.published_at is None for row in rows if row.id not in selected)


@pytest.mark.parametrize("body", [
    {"kind": "full", "surveillance_rebuild_all": True},
    {"kind": "content", "channel": "situation", "surveillance_rebuild_all": True},
    {"kind": "content", "channel": "surveillance", "country_codes": ["US"], "surveillance_rebuild_all": True},
])
async def test_rebuild_rejects_other_modes_and_country_filters(content_api, body):
    app, _, _ = content_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/publish/plans", json=body)).status_code == 422


async def test_rebuild_requires_pending_markers_and_frozen_eligible_inventory(content_api):
    app, maker, _ = content_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        plan = await create(client, surveillance_rebuild_all=True)
        assert plan["executable"] is False
        assert plan["blocked_reason"] == "no_publishable_changes_detected"
        async with maker() as db:
            db.add(PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source",
                marked_at=datetime.now(timezone.utc), change_set={"version": 1, "precise": True}))
            await db.commit()
        plan = await create(client, surveillance_rebuild_all=True)
        async with maker() as db:
            db.add(Country(code="DE", name="Germany", name_en="Germany", is_active=True))
            await db.commit()
        response = await start(client, plan)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "publication_country_inventory_changed"
    async with maker() as db:
        assert not (await db.execute(select(Task))).scalars().all()


async def test_unplanned_full_json_mode_remains_rejected(content_api):
    _, _, service = content_api
    with pytest.raises(ValueError, match="mutually exclusive"):
        await service.trigger_job("site-release", manual=True, trigger="test", full=True, data_only=True)
    with pytest.raises(ValueError, match="frozen manual"):
        await service.trigger_job("site-release", manual=True, trigger="test", data_only=True,
            surveillance_rebuild_all=True)


async def test_rebuild_remote_baseline_cannot_change_between_plan_and_enqueue(content_api, monkeypatch):
    app, maker, _ = content_api
    async with maker() as db:
        db.add(PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source",
            marked_at=datetime.now(timezone.utc), change_set={"version": 1, "precise": True}))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        plan = await create(client, surveillance_rebuild_all=True)
        monkeypatch.setattr(execution.R2S3Client, "get_object", lambda self, key: b"newer-baseline")
        response = await start(client, plan)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "publication_baseline_changed"
    async with maker() as db:
        assert not (await db.execute(select(Task))).scalars().all()
