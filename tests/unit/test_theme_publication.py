from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release import theme_publication as theme
from src.services.data_release import publication_execution as execution
from src.services.data_release.publication_planning import create_plan
from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Error
from src.services.data_release.surveillance_publish import _atomic_receipt
from dashboard.api.schemas.collection import PublishRunCreate

ROOT = Path(__file__).resolve().parents[2]


class Store:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def get_object(self, key):
        return self.objects.get(key)

    def put_object_conditional(self, key, body, *, previous, **kwargs):
        if self.objects.get(key) != previous:
            raise R2S3Error("conflict", status=412)
        self.objects[key] = body
        self.writes.append(key)

    def close(self):
        pass

    def public(self, url):
        return self.objects[url.removeprefix("https://static.example/")]


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "shared").mkdir(parents=True)
    (root / "astro-site/scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "shared/site-theme-v1.json", root / "shared/site-theme-v1.json")
    shutil.copyfile(ROOT / "astro-site/scripts/build-theme.mjs", root / "astro-site/scripts/build-theme.mjs")
    store = Store()
    config = R2PapersConfig("account", "public-assets", "key", "secret", "https://static.example")
    monkeypatch.setattr(theme, "R2S3Client", lambda _: store)
    monkeypatch.setattr(execution, "R2S3Client", lambda _: store)
    monkeypatch.setattr(theme.time, "sleep", lambda _: None)
    release = SimpleNamespace(theme_publish_enabled=True, theme_r2_bucket="public-assets",
        theme_public_base_url="https://static.example", theme_compat_v1_ready=True,
        theme_stylesheet_url="https://static.example/" + theme.ENTRY,
        r2_papers_bucket="private-papers", r2_reports_bucket="private-reports",
        surveillance_json_enabled=True, situation_json_enabled=True, literature_json_enabled=True)
    service = SimpleNamespace(_config=lambda: release, _r2_papers_config=lambda: config,
                              trigger_job=AsyncMock(return_value={"status": "queued", "task_uuid": "task"}))
    return SimpleNamespace(root=root, directory=tmp_path / "candidate", data_dir=tmp_path / "data",
                           store=store, config=config, service=service)


def prepared(f):
    return theme.prepare(root=f.root, directory=f.directory, config=f.config, client=f.store)


def test_theme_publish_verifies_assets_before_switch_and_does_zero_page_work(fixture):
    f = fixture
    prepared(f)
    observed = []
    def read(url):
        observed.append((url, list(f.store.writes)))
        return f.store.public(url)
    result = theme.publish(directory=f.directory, config=f.config, plan_id="plan", client=f.store, read=read)
    assert observed[0][0].endswith(".css") and theme.ENTRY not in observed[0][1]
    assert result["public_verified"] and result["authoritative_committed"]
    assert result["content_render_count"] == result["html_assembly_count"] == 0
    assert not result["pages_deployment_required"]
    assert f.store.writes[-1] == theme.ENTRY


def test_theme_stale_baseline_cannot_overwrite_newer_theme(fixture):
    f = fixture
    prepared(f)
    f.store.objects[theme.ENTRY] = b'@import url("../../versions/sha256/' + b'a' * 64 + b'.css");\n'
    with pytest.raises(R2S3Error, match="baseline_conflict"):
        theme.publish(directory=f.directory, config=f.config, plan_id="plan", client=f.store, read=f.store.public)
    assert not f.store.writes


def test_postcommit_verification_failure_recovers_same_candidate_without_reupload(fixture):
    f = fixture
    prepared(f)
    def stale_public(url):
        return b"old cache" if url.endswith(theme.ENTRY) else f.store.public(url)
    with pytest.raises(RuntimeError, match="verification_pending"):
        theme.publish(directory=f.directory, config=f.config, plan_id="plan", client=f.store, read=stale_public)
    receipt = json.loads((f.directory / "theme-receipt.json").read_bytes())
    assert receipt["authoritative_committed"] and not receipt["public_verified"]
    writes = list(f.store.writes)
    result = theme.publish(directory=f.directory, config=f.config, plan_id="plan", client=f.store, read=f.store.public)
    assert result["public_verified"] and f.store.writes == writes


def test_missing_public_immutable_asset_prevents_pointer_commit(fixture):
    f = fixture
    prepared(f)
    with pytest.raises(RuntimeError, match="verification_pending"):
        theme.publish(directory=f.directory, config=f.config, plan_id="plan", client=f.store, read=lambda _: b"404")
    assert theme.ENTRY not in f.store.objects


def make_plan(f, *, rollback_of=None):
    return create_plan(root=f.root, data_dir=f.data_dir, job_id="site-release", config=f.service._config(),
        dirty_rows=[], job_configuration={"job_id": "site-release"}, execution_service=f.service,
        requested_kind="theme", rollback_of=rollback_of)


def test_theme_plan_freezes_compiler_source_and_digest(fixture):
    f = fixture
    plan = make_plan(f)
    assert plan["executable"] and not plan["preview_only"] and plan["kind"] == "theme"
    assert execution.load_plan(f.data_dir, plan["plan_id"], plan["plan_digest"]) == plan
    execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={"job_id": "site-release"})
    (f.root / "shared/site-theme-v1.json").write_text("changed")
    with pytest.raises(execution.PublicationPlanError, match="source_changed"):
        execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={"job_id": "site-release"})
    # Once queued, it uses only the frozen CSS and never recompiles changed input.
    execution.validate_execution(plan, root=f.root, service=f.service, queued=True)


def test_plan_expiry_and_configuration_changes_are_rejected(fixture):
    f = fixture
    plan = make_plan(f)
    plan["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(execution.PublicationPlanError, match="expired"):
        execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={"job_id": "site-release"})


def test_admitted_theme_keeps_destination_and_can_recover_after_pause(fixture):
    f = fixture
    plan = make_plan(f)
    f.service._config().theme_publish_enabled = False
    f.service._config().theme_r2_bucket = "new-default"
    execution.validate_execution(plan, root=f.root, service=f.service, queued=True)
    frozen_config, reason = theme.configuration(f.service, frozen=plan["_execution"]["theme_destination"])
    assert reason is None and frozen_config.bucket == "public-assets"
    plan["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    f.service._config().theme_r2_bucket = "different-assets"
    with pytest.raises(execution.PublicationPlanError, match="configuration_changed"):
        execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={"job_id": "site-release"})


def test_verified_theme_rollback_is_another_fenced_plan(fixture):
    f = fixture
    first = make_plan(f)
    first_dir = f.data_dir / "publication-plans" / first["plan_id"]
    theme.publish(directory=first_dir, config=f.config, plan_id=first["plan_id"], client=f.store, read=f.store.public)
    source = f.root / "shared/site-theme-v1.json"
    tokens = json.loads(source.read_bytes())
    tokens["root"]["--color-brand"] = "10 20 30"
    source.write_text(json.dumps(tokens))
    second = make_plan(f)
    second_dir = f.data_dir / "publication-plans" / second["plan_id"]
    theme.publish(directory=second_dir, config=f.config, plan_id=second["plan_id"], client=f.store, read=f.store.public)
    rollback = make_plan(f, rollback_of=first["plan_id"])
    rollback_dir = f.data_dir / "publication-plans" / rollback["plan_id"]
    result = theme.publish(directory=rollback_dir, config=f.config, plan_id=rollback["plan_id"], client=f.store, read=f.store.public)
    assert result["version"] == first["_execution"]["theme_prepared"]["content_sha256"]
    assert result["version"] != second["_execution"]["theme_prepared"]["content_sha256"]
    assert rollback["rollback_of"] == first["plan_id"]
    assert len(execution.theme_history(f.data_dir)["items"]) == 3


def test_unverified_theme_cannot_be_a_rollback_target(fixture):
    f = fixture
    plan = make_plan(f)
    with pytest.raises(execution.PublicationPlanError, match="rollback_target_unverified"):
        make_plan(f, rollback_of=plan["plan_id"])


@pytest.mark.parametrize("body", [
    {"plan_id": "id"}, {"plan_digest": "digest"},
    {"plan_id": "id", "plan_digest": "digest", "idempotency_key": "abcdefgh", "full": True},
])
def test_plan_and_legacy_run_contracts_cannot_mix(body):
    with pytest.raises(ValueError):
        PublishRunCreate.model_validate(body)


@pytest.mark.asyncio
async def test_durable_idempotent_replay_does_not_revalidate_expired_plan_or_enqueue(fixture):
    f = fixture
    plan = make_plan(f)
    body = PublishRunCreate(plan_id=plan["plan_id"], plan_digest=plan["plan_digest"], idempotency_key="stable-key")
    existing = SimpleNamespace(input_data={"publication_plan": {"plan_digest": plan["plan_digest"]}},
                               status=SimpleNamespace(value="completed"))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: existing)))
    result = await execution.enqueue_plan(body, db=db, root=f.root, data_dir=f.data_dir, service=f.service)
    assert result["idempotent_replay"] and result["status"] == "completed"
    f.service.trigger_job.assert_not_awaited()
    existing.input_data["publication_plan"]["plan_digest"] = "other"
    with pytest.raises(execution.PublicationPlanError, match="idempotency_conflict"):
        await execution.enqueue_plan(body, db=db, root=f.root, data_dir=f.data_dir, service=f.service)


def test_theme_misconfiguration_fails_closed_without_publication(fixture):
    f = fixture
    f.service._config().theme_r2_bucket = "private-papers"
    plan = make_plan(f)
    assert not plan["executable"]
    assert plan["blocked_reason"] == "theme_requires_dedicated_public_bucket"
    assert not f.store.writes


def test_content_plan_exposes_exact_remote_baseline_and_selected_ids(fixture):
    f = fixture
    pointer = b'{"release_id":"previous"}'
    f.store.objects['site-data/situation/current.json'] = pointer
    plan = create_plan(root=f.root, data_dir=f.data_dir, job_id='site-release', config=f.service._config(),
        dirty_rows=[{'id': 7, 'line': 'situation', 'scope_key': '*'}], job_configuration={'job_id': 'site-release'},
        execution_service=f.service, requested_kind='content', channel='situation')
    assert plan['execution_mode'] == 'content' and plan['dirty_ids'] == [7]
    assert plan['expected_generations'] == {'situation': theme.sha(pointer)}
    execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={'job_id': 'site-release'})
    f.store.objects['site-data/situation/current.json'] = b'{"release_id":"newer"}'
    with pytest.raises(execution.PublicationPlanError, match='baseline_changed'):
        execution.validate_execution(plan, root=f.root, service=f.service, job_configuration={'job_id': 'site-release'})


@pytest.mark.parametrize("mime", ["text/html", "application/octet-stream", ""])
def test_public_css_requires_browser_usable_content_type(monkeypatch, mime):
    class Reply:
        headers = {"Content-Type": mime}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return "https://static.example/theme.css"
        def read(self, size): return b":root {}"
    monkeypatch.setattr(theme, "urlopen", lambda *args, **kwargs: Reply())
    with pytest.raises(ValueError, match="content_type_invalid"):
        theme.public_read("https://static.example/theme.css")
