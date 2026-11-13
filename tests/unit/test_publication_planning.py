"""Preview honesty, source dependency boundaries and cached account usage."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dashboard.api.routers import publish
from dashboard.api.schemas.publication import PublicationPlanOut, PublicationUsageOut
from src.domain import DataReleaseJob
from src.services.data_release import publication_planning as planning

NOW = datetime(2026, 11, 13, 1, tzinfo=timezone.utc)


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Publication Test")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/site-theme-v1.json").write_text('{"color":"blue"}')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports/successful-site-release.json").write_text(json.dumps({
        "source_commit": git(tmp_path, "rev-parse", "HEAD"), "release_id": "site-one"}))
    return tmp_path


def config(**kwargs):
    return SimpleNamespace(surveillance_json_enabled=True, situation_json_enabled=True,
                           literature_json_enabled=True, **kwargs)


def plan(root, **kwargs):
    return planning.create_plan(root=root, data_dir=root / "data", job_id="site-release", config=config(),
                                now=NOW, dirty_rows=kwargs.pop("dirty_rows", []), **kwargs)


@pytest.mark.parametrize("path,kind", [
    ("shared/site-theme-v1.json", "theme"),
    ("astro-site/src/styles/global.css", "template"),
    ("astro-site/src/components/Card.astro", "template"),
    ("astro-site/src/pages/countries/[country]/index.astro", "template"),
    ("astro-site/src/data/countries/US.json", "content"),
    ("astro-site/package-lock.json", "full"),
    ("cloudflare/surveillance-data/src/index.ts", "routing"),
    ("astro-site/public/_routes.json", "routing"),
])
def test_dependency_classification_is_conservative(path, kind):
    assert planning.classify_change(path)["kind"] == kind


def test_theme_preview_freezes_source_without_claiming_available_fast_path(repository):
    token = repository / "shared/site-theme-v1.json"
    token.write_text('{"color":"red"}')
    result = plan(repository)
    PublicationPlanOut.model_validate(result)
    assert result["kind"] == "theme"
    assert result["preview_only"] and not result["executable"]
    assert result["html_page_count"] is None
    assert result["pages_deploy_required"]
    assert "independent_theme_publication_not_available" in result["warnings"]
    stored = repository / "data/publication-plans" / f"{result['plan_id']}.json"
    assert json.loads(stored.read_text()) == result
    token.write_text('{"color":"green"}')
    assert plan(repository)["input_digest"] != result["input_digest"]
    assert json.loads(stored.read_text()) == result


def test_json_dirty_scope_never_becomes_a_fake_content_record_count(repository):
    dirty = [{"id": 42, "line": "literature", "scope_key": "*", "change_set": None, "marked_at": NOW}]
    result = plan(repository, dirty_rows=dirty)
    assert result["channels"] == ["literature"]
    assert result["dirty_ids"] == [42]
    assert result["pending_scopes"] == {"literature": ["*"]}
    assert result["html_page_count"] == 0
    assert result["content_record_count"] is None
    assert not result["pages_deploy_required"]
    assert result["expected_generations"]["surveillance"] is None
    other = plan(repository, dirty_rows=[{**dirty[0], "id": 43}])
    assert other["input_digest"] != result["input_digest"]


def test_missing_publication_baseline_never_claims_no_changes(repository):
    (repository / "exports/successful-site-release.json").unlink()
    result = plan(repository)
    assert result["kind"] == "full"
    assert result["html_page_count"] is None
    assert "published_source_baseline_unavailable" in result["warnings"]


def test_dirty_published_source_is_not_treated_as_the_recorded_commit(repository):
    receipt = repository / "exports/successful-site-release.json"
    receipt.write_text(json.dumps({**json.loads(receipt.read_text()), "commit_dirty": True}))
    result = plan(repository)
    assert result["kind"] == "full" and result["html_page_count"] is None
    assert "published_dirty_source_baseline_unavailable" in result["warnings"]


def test_committed_changes_since_published_source_are_included(repository):
    token = repository / "shared/site-theme-v1.json"
    token.write_text('{"color":"yellow"}')
    git(repository, "add", "shared/site-theme-v1.json")
    git(repository, "commit", "-qm", "theme")
    assert plan(repository)["changed_files"][0]["path"] == "shared/site-theme-v1.json"


def test_generator_change_does_not_claim_zero_content_work(repository):
    directory = repository / "src/generation"
    directory.mkdir(parents=True)
    (directory / "content.py").write_text("# a changed content projection\n")
    result = plan(repository)
    assert result["kind"] == "full"
    assert result["content_record_count"] is None


def test_corrupt_local_receipt_identity_is_unknown_not_a_schema_failure(repository):
    (repository / "exports/research-current-receipt.json").write_text('{"release_id": {"broken": true}}')
    result = plan(repository)
    PublicationPlanOut.model_validate(result)
    assert result["expected_generations"]["literature"] is None


def test_untracked_secrets_and_documents_are_not_publication_inputs(repository):
    (repository / "astro-site").mkdir()
    (repository / "astro-site/.env.production").write_text("SECRET=do-not-expose")
    (repository / "README.md").write_text("notes")
    result = plan(repository)
    assert result["changed_files"] == []
    assert "SECRET" not in json.dumps(result)


def test_move_outside_source_tree_still_records_removed_input(repository):
    (repository / "docs").mkdir()
    git(repository, "mv", "shared/site-theme-v1.json", "docs/retired-theme.json")
    result = plan(repository)
    assert [change["path"] for change in result["changed_files"]] == ["shared/site-theme-v1.json"]
    assert result["pages_deploy_required"]


def snapshot(**overrides):
    return {"schema_version": 1, "plan": "free", "observed_at": NOW.isoformat(),
            "period_start": NOW.replace(hour=0).isoformat(),
            "period_end": (NOW.replace(hour=0) + timedelta(days=1)).isoformat(),
            "source": "cloudflare_api", "account_total": 123, "limit": 100000,
            "remaining": 99877, "services": [{"id": "papers", "requests": 100}], **overrides}


@pytest.mark.parametrize("overrides,age,status", [
    ({}, 0, "known"), ({}, 16, "stale"),
    ({"account_total": None}, 0, "unknown"), ({"plan": "unknown"}, 0, "known"),
    ({"plan": "unknown"}, 16, "stale"),
    ({"observed_at": (NOW + timedelta(hours=1)).isoformat()}, 0, "unknown"),
    ({"observed_at": "2026-11-13T01:00:00"}, 0, "unknown"),
    ({"account_total": -1}, 0, "unknown"),
    ({"period_start": None}, 0, "unknown"),
])
def test_usage_is_cached_and_never_conflates_missing_with_zero(tmp_path, overrides, age, status):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps(snapshot(**overrides)))
    result = planning.read_usage(path, now=NOW + timedelta(minutes=age))
    dto = PublicationUsageOut.model_validate(result)
    assert dto.status == status
    if status == "stale":
        assert dto.account_total == 123 and dto.reason == "usage_snapshot_stale"
    if overrides.get("account_total", 123) is None:
        assert dto.account_total is None
    if overrides.get("plan") == "unknown":
        assert dto.account_total == 123
        assert dto.limit is None and dto.remaining is None and dto.projected_usage is None


def test_missing_usage_is_unknown(tmp_path):
    result = PublicationUsageOut.model_validate(planning.read_usage(tmp_path / "absent"))
    assert result.status == "unknown" and result.account_total is None


def test_capabilities_report_literature_and_actual_automatic_pause(monkeypatch):
    monkeypatch.setattr(planning, "is_automatic_json_paused", lambda line: line == "literature")
    result = planning.capabilities(config())
    literature = next(c for c in result["channels"] if c["id"] == "literature")
    assert literature["enabled"] and not literature["automatic"]
    assert literature["realtime_controls"]
    assert next(c for c in result["channels"] if c["id"] == "surveillance")["realtime_controls"]
    assert literature["reason"] == "automatic_publication_paused"
    assert result["plan_execution"] and not result["rollback"]


def test_plan_endpoint_only_reads_db_and_never_calls_enqueuer(repository, monkeypatch):
    job = DataReleaseJob(job_id="site-release", name="test")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: job),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]), commit=AsyncMock(), add=AsyncMock())
    app = FastAPI()
    app.include_router(publish.router)
    async def dependency():
        yield db
    app.dependency_overrides[publish.get_db] = dependency
    monkeypatch.setattr(publish, "ROOT_DIR", repository)
    monkeypatch.setattr(publish, "get_config", lambda: SimpleNamespace(data_dir=repository / "data", data_release=config()))
    trigger = AsyncMock()
    monkeypatch.setattr(publish.data_release_service, "trigger_job", trigger)
    response = TestClient(app).post("/publish/plans", json={})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["preview_only"]
    db.commit.assert_not_awaited()
    db.add.assert_not_called()
    trigger.assert_not_awaited()
    # A preview cannot silently become an unfrozen legacy release.
    rejected = TestClient(app).post("/publish/runs", json={"plan_id": response.json()["data"]["plan_id"]})
    assert rejected.status_code == 422
    trigger.assert_not_awaited()
