"""A committed candidate survives edits without changing bytes or destinations."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.domain import DataReleaseJob, Task
from src.domain.collection import PublishDirty
from src.services.data_release import publication_execution as execution
from src.services.data_release import publication_recovery as recovery
from src.services.data_release.artifact_checkpoint import source_fingerprint
from src.services.data_release.pipeline import execute_release_task
from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Error
from src.services.data_release.surveillance_publish import capture_surveillance_baseline, publish_surveillance_data
from tests.unit.test_release_dirty_selection import release_db  # noqa: F401
from tests.unit.test_release_stage_integration import release  # noqa: F401
from tests.unit.test_surveillance_publish import CONFIG, Store, generation


def test_environment_never_persists_credentials_and_replays_original_destinations(monkeypatch):
    from src.services.data_release_service import DataReleaseService
    service = DataReleaseService()
    config = SimpleNamespace(r2_account_id="account", r2_papers_bucket="original", r2_access_key_id="old-key",
        r2_secret_access_key="old-secret", situation_json_enabled=True)
    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(service, "_cloudflare_account_id", lambda: "account")
    monkeypatch.setattr(service, "_cloudflare_api_token", lambda: "fresh-token")
    monkeypatch.setattr(service, "_env_file_values", lambda: {"SUBSCRIPTIONS__ADMIN_API_TOKEN": "hidden"})
    for name in ("SUBSCRIPTIONS__API_KEY", "GIDS_AUTHORIZATION", "PUBLIC_SESSION_COOKIE", "R2_RANDOM_NEW_SECRET"):
        monkeypatch.setenv(name, "must-not-persist")
    frozen = recovery.freeze_environment(service, {"job_id": "site-release", "name": "Original"})
    serialized = json.dumps(frozen)
    assert all(secret not in serialized for secret in ("old-key", "old-secret", "fresh-token", "hidden", "must-not-persist"))
    config.r2_papers_bucket, config.situation_json_enabled = "different", False
    config.r2_access_key_id, config.r2_secret_access_key = "new-key", "new-secret"
    scoped = recovery.replay_environment(service, frozen)
    assert scoped._config().r2_papers_bucket == "original"
    assert scoped._config().situation_json_enabled
    assert scoped._config().r2_access_key_id == "new-key"
    assert scoped._config().r2_secret_access_key == "new-secret"
    assert service._config().r2_papers_bucket == "different"
    config.r2_account_id = "different-account"
    with pytest.raises(execution.PublicationPlanError, match="credential_account_changed"):
        recovery.replay_environment(service, frozen)


async def test_replayed_environment_parses_in_real_config_subprocess(monkeypatch):
    from src.core.config import DataReleaseSettings
    from src.services.data_release_service import DataReleaseService
    service = DataReleaseService()
    cfg = DataReleaseSettings.model_construct(surveillance_json_enabled=True, situation_json_enabled=True,
        batch_max_countries=7, theme_publish_enabled=False, r2_papers_bucket="frozen-bucket", r2_account_id="account")
    monkeypatch.setattr(service, "_config", lambda: cfg)
    monkeypatch.setattr(service, "_cloudflare_account_id", lambda: "account")
    captured = {}
    async def command(*args, **kwargs):
        captured.update(kwargs["env"])
    monkeypatch.setattr(service, "_run_logged_command", command)
    frozen = recovery.freeze_environment(service, {"job_id": "site-release", "name": "Site"})
    monkeypatch.setenv("DATA_RELEASE__SURVEILLANCE_JSON_ENABLED", "false")
    monkeypatch.setenv("DATA_RELEASE__BATCH_MAX_COUNTRIES", "99")
    monkeypatch.setenv("GIDS_THEME_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIPTIONS__PENDING_EXPIRY_DAYS", "30")
    scoped = recovery.replay_environment(service, frozen)
    await scoped._run_logged_command("task", env={})
    code = ('import json; from src.core.config import get_config; c=get_config().data_release; '
            'print("CHECK="+json.dumps([c.surveillance_json_enabled,c.batch_max_countries,'
            'c.theme_publish_enabled,c.r2_papers_bucket]))')
    from src.services.data_release.process_runner import subprocess_environment
    process = subprocess.run([sys.executable, "-c", code], env=subprocess_environment(captured),
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert 'CHECK=[true, 7, false, "frozen-bucket"]' in process.stdout
    assert "SUBSCRIPTIONS__PENDING_EXPIRY_DAYS" not in captured
    # Absent numeric CLI knobs must stay absent, rather than becoming invalid
    # int('') values or inheriting newly introduced defaults on an old task.
    monkeypatch.setenv("R2_PAPERS_UPLOAD_WORKERS", "unexpected-new-value")
    captured["R2_PAPERS_UPLOAD_WORKERS"] = None
    captured["R2_PAPERS_MAX_TOTAL_BYTES"] = None
    process = subprocess.run([sys.executable, "scripts/publish_papers_r2.py", "--help"],
        env=subprocess_environment(captured), capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("after_commit", ["recover", "newer", "missing", "altered"])
async def test_real_situation_commit_then_source_config_job_edits_and_ack(release_db, tmp_path, monkeypatch, after_commit):
    maker, service, _ = release_db
    async with maker() as db:
        job = (await db.execute(select(DataReleaseJob))).scalar_one()
        job_values = {column.name: getattr(job, column.name) for column in DataReleaseJob.__table__.columns}
        rows = [PublishDirty(line="situation", scope_key="*", source_task_uuid="source",
            marked_at=datetime.now(timezone.utc)) for _ in range(2)]
        db.add_all(rows)
        await db.commit()
        ids = [row.id for row in rows]
    @asynccontextmanager
    async def database():
        async with maker() as db:
            yield db
    @asynccontextmanager
    async def snapshot():
        yield {}
    config = SimpleNamespace(situation_json_enabled=True, r2_account_id="account", r2_papers_bucket="bucket",
        r2_access_key_id="key", r2_secret_access_key="secret", r2_endpoint_url="")
    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(service, "_cloudflare_account_id", lambda: "account")
    monkeypatch.setattr(service, "_cloudflare_api_token", lambda: "token")
    task = SimpleNamespace(task_uuid="frozen-situation", input_data={"data_only": True, "data_line": "situation",
        "publish_dirty_ids": [ids[0]], "publication_plan": {"plan_id": "plan", "plan_digest": "digest"}})
    run = tmp_path / "exports/situation-runs" / task.task_uuid
    public = tmp_path / "exports/situation-data"
    store, commands = Store(), []
    committed = False
    async def command(_uuid, **kwargs):
        nonlocal committed
        cmd, env = kwargs["cmd"], kwargs["env"]
        commands.append(cmd)
        if "scripts/export_situation_data.py" in cmd:
            generation(public, release="situation-" + task.task_uuid)
            return
        # Actual publisher receives the frozen target and the latest keys.
        assert env["R2_PAPERS_BUCKET"] == "bucket"
        assert env["R2_ACCOUNT_ID"] == "account"
        expected_key = "new-key" if committed else "key"
        assert env["R2_ACCESS_KEY_ID"] == expected_key
        target = R2PapersConfig(env["R2_ACCOUNT_ID"], env["R2_PAPERS_BUCKET"],
                              env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"])
        if "--capture-baseline-only" in cmd:
            capture_surveillance_baseline(run, target, prefix="site-data/situation", client=store)
        else:
            publish_surveillance_data(run, target, prefix="site-data/situation", client=store,
                objects_dir=public / "objects", require_starting_baseline=True)
            if not committed:
                committed = True
                raise RuntimeError("connection lost after real pointer commit")
    monkeypatch.setattr(service, "_run_logged_command", command)
    plan = {"executable": True, "execution_mode": "content", "job_id": job_values["job_id"],
        "_execution": {"job_configuration_digest": execution.digest(job_values),
            "configuration_digest": execution.configuration_digest(service, tmp_path),
            "source_fingerprint": source_fingerprint(tmp_path),
            "environment": recovery.freeze_environment(service, job_values)}}
    monkeypatch.setattr(execution, "load_plan", lambda *args: plan)
    runtime = SimpleNamespace(root_dir=tmp_path, get_config=lambda: SimpleNamespace(data_dir=tmp_path / "data"),
        get_database=database, read_snapshot=snapshot, task_model=Task, generate_timeout_seconds=60,
        download_publish_timeout_seconds=60,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()))
    with pytest.raises(RuntimeError, match="after real pointer commit"):
        await execute_release_task(service, task, runtime=runtime)
    assert "site-data/situation/current.json" in store.objects
    writes = list(store.writes)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/changed.py").write_text("# new generator/template source")
    config.situation_json_enabled, config.r2_papers_bucket = False, "new-default-bucket"
    config.r2_access_key_id, config.r2_secret_access_key = "new-key", "new-secret"
    async with maker() as db:
        job = (await db.execute(select(DataReleaseJob))).scalar_one()
        job.enabled, job.cloudflare_project_name = False, "changed-project"
        await db.commit()
    if after_commit == "newer":
        store.objects["site-data/situation/current.json"] = b'{"newer":true}'
    elif after_commit == "missing":
        (run / "ready.json").unlink()
    elif after_commit == "altered":
        (run / "current.json").write_text('{"changed":true}')
    if after_commit == "recover":
        result = await execute_release_task(service, task, runtime=runtime)
        assert result["release_id"] == "situation-" + task.task_uuid
    else:
        with pytest.raises((ValueError, R2S3Error)):
            await execute_release_task(service, task, runtime=runtime)
    assert store.writes == writes
    assert sum("scripts/export_situation_data.py" in cmd for cmd in commands) == 1
    async with maker() as db:
        selected = await db.get(PublishDirty, ids[0])
        assert bool(selected.published_at) is (after_commit == "recover")
        assert (await db.get(PublishDirty, ids[1])).published_at is None


@pytest.mark.parametrize("mode", ["full", "code_only", "country_template"])
def test_prepared_static_plan_ignores_source_and_non_target_changes_but_fences_new_release(tmp_path, mode):
    env = {"schema": 1}
    plan = {"executable": True, "execution_mode": mode, "_execution": {
        "environment": env, "job_configuration_digest": "old", "configuration_digest": "old",
        "source_fingerprint": "old", "site_baseline": "baseline"}}
    marker = tmp_path / "exports/successful-site-release.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"release_id": "prepared", "task_uuid": "same-task"}))
    execution.validate_execution(plan, root=tmp_path, service=None, queued=True, task_uuid="same-task",
                                 recovery={"prepared": True}, job_configuration={"changed": True})
    marker.write_text(json.dumps({"release_id": "newer", "task_uuid": "other-task"}))
    with pytest.raises(execution.PublicationPlanError, match="baseline_changed"):
        execution.validate_execution(plan, root=tmp_path, service=None, queued=True, task_uuid="same-task",
                                     recovery={"prepared": True})


def test_recovery_never_recreates_a_missing_prepared_candidate():
    with pytest.raises(ValueError, match="prepared_candidate_unavailable"):
        recovery.require_candidate(SimpleNamespace(_publication_recovery={"prepared": True}), False)


@pytest.mark.parametrize("mode", ["full", "code_only"])
@pytest.mark.parametrize("after_commit", ["recover", "newer_remote", "missing_artifact"])
async def test_static_deploy_then_edits_reuses_candidate_and_never_overwrites_newer(release, monkeypatch, mode, after_commit):
    r = release
    # Establish the published data snapshot required by a template-only plan.
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    r.commit.reset_mock()
    r.task.task_uuid = "prepared-static"
    r.task.input_data.update({mode: True, "publication_plan": {
        "plan_id": "static-plan", "plan_digest": "static-digest", "site_baseline": "stage-release"}})
    cfg = SimpleNamespace(timezone="UTC")
    r.service._config = lambda: cfg
    job_values = {column.name: getattr(r.service.job, column.name, None) for column in DataReleaseJob.__table__.columns}
    # dataclass defaults apply only when a field is absent, not explicit None.
    frozen_job = vars(r.service.job)
    frozen = recovery.freeze_environment(r.service, frozen_job)
    plan = {"executable": True, "execution_mode": mode, "job_id": "site-release", "_execution": {
        "environment": frozen, "job_configuration_digest": execution.digest(job_values),
        "configuration_digest": execution.configuration_digest(r.service, r.root),
        "source_fingerprint": source_fingerprint(r.root), "site_baseline": "stage-release"}}
    monkeypatch.setattr(execution, "load_plan", lambda *args: plan)
    r.database.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(**job_values))
    r.runtime = replace(r.runtime, get_config=lambda: SimpleNamespace(data_dir=r.root / "data",
                        raw_archive=SimpleNamespace(enabled=False, repo_url="")))
    r.service.identity = {**r.service.identity, "release_id": "prepared-release"}
    checkpoints = {}
    async def record(_task, stage, value):
        checkpoints[stage] = value
    async def identity(*args):
        return dict(r.service.identity), dict(checkpoints)
    r.service._record_release_checkpoint = record
    r.service._release_identity_for_task = identity
    r.service.integration_checks = AsyncMock(return_value={"overall_ready": True,
        "cloudflare": {"production_branch": "main", "subdomain": "site.pages.dev"}})
    r.service._cloudflare_check = AsyncMock(return_value={"blockers": [], "payload": {
        "project_access_ok": True, "production_branch": "main"}})
    r.service._verify_cloudflare_production_release = AsyncMock(side_effect=[
        RuntimeError("public verify interrupted after deploy"), {"verified": True}])
    from src.generation import release_projection_snapshot
    monkeypatch.setattr(release_projection_snapshot, "validate_read_snapshot_projection", lambda *args: None)
    with pytest.raises(RuntimeError, match="after deploy"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert checkpoints["cloudflare_deploy"]["release_id"] == "prepared-release"
    commands = list(r.service.commands)
    r.commit.assert_not_awaited()
    r.put(r.astro / "src/pages/index.astro", "<h1>new source</h1>")
    r.put(r.astro / "src/data/changelog.ts", "export const entries = ['new source'];")
    cfg.timezone = "Asia/Shanghai"
    r.service.job.cloudflare_project_name = "new-default-project"
    r.service._cloudflare_project_name = lambda _value: "new-default-project"
    public = json.loads((r.astro / "dist/release.json").read_text())
    if after_commit == "newer_remote":
        public["release_id"] = "other-writer"
    if after_commit == "missing_artifact":
        (r.astro / "dist/index.html").unlink()
    r.service._public_json = AsyncMock(return_value=public)
    if after_commit == "recover":
        result = await execute_release_task(r.service, r.task, runtime=r.runtime)
        assert result["website_verification"] == "verified"
        assert result["cloudflare_project_name"] == "site"
        assert r.service.commands.count("Build Astro Site") == commands.count("Build Astro Site")
        assert r.service.commands.count("Deploy Cloudflare Pages") == commands.count("Deploy Cloudflare Pages")
        assert r.commit.await_count == (1 if mode == "full" else 0)
    else:
        with pytest.raises(ValueError, match="baseline_changed|prepared_candidate_unavailable"):
            await execute_release_task(r.service, r.task, runtime=r.runtime)
        assert r.service.commands == commands
        r.commit.assert_not_awaited()


@pytest.mark.parametrize("url", ["https://user:password@github.com/repo", "https://example.test/?api_key=secret",
                                "https://example.test/?X-Amz-Signature=secret", "https://example.test/?signing_key=secret",
                                "https://example.test/?signing-key=secret"])
def test_frozen_targets_reject_credential_urls(url):
    with pytest.raises(ValueError, match="credential_url_not_allowed"):
        recovery._safe_values({"download_repo_url": url})


@pytest.mark.parametrize("corruption", ["shard_bytes", "shard_key", "entries", "payload", "missing"])
def test_papers_recovery_validates_complete_catalogue_closure(tmp_path, corruption):
    from src.services.data_release.literature_recovery import freeze_papers, load_papers
    from tests.unit.test_papers_content import make_candidate
    run, stage = tmp_path / "run", tmp_path / "stage"
    generation(run, release="candidate")
    candidate = make_candidate(stage, {"research/articles/a/index.html": "A"})
    freeze_papers(run, candidate, None)
    args = dict(stage=stage, origin="https://papers.example", release_id="candidate")
    assert load_papers(run, **args)[0] == candidate
    prepared = run / "papers-prepared.json"
    data = json.loads(prepared.read_bytes())
    if corruption == "shard_bytes":
        data["shards"][0][1] = base64.b64encode(b"{}").decode()
    elif corruption == "shard_key":
        data["shards"][0][0] = "releases/sha256/catalogue-shards/wrong.json"
    elif corruption == "entries":
        data["entries"][0]["bytes"] = 999
    elif corruption == "payload":
        (stage / "research/articles/a/index.html").write_text("B")
    prepared.write_text(json.dumps(data))
    if corruption == "missing":
        prepared.unlink()
    with pytest.raises(ValueError, match="regeneration is forbidden"):
        load_papers(run, **args)


@pytest.mark.parametrize("corruption", ["missing_dependencies", "duplicate_dependency", "wrong_dependency"])
def test_template_recovery_requires_every_catalogue_record_and_template(tmp_path, corruption):
    from src.services.data_release.literature_recovery import freeze_papers, load_papers
    from tests.unit.test_papers_content import make_template_candidate
    run, stage = tmp_path / "run", tmp_path / "stage"
    generation(run, release="candidate")
    candidate = make_template_candidate(stage, run / "paper-templates")
    freeze_papers(run, candidate, None)
    args = dict(stage=stage, origin="https://papers.example", release_id="candidate")
    assert load_papers(run, **args)[0] == candidate
    path = run / "papers-prepared.json"
    value = json.loads(path.read_bytes())
    if corruption == "missing_dependencies":
        value["template_objects"] = []
    elif corruption == "duplicate_dependency":
        value["template_objects"].append(value["template_objects"][0])
    else:
        value["template_objects"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="regeneration is forbidden"):
        load_papers(run, **args)


def test_static_research_prepares_before_upload_then_uses_original_catalogue_after_source_edit(tmp_path, monkeypatch):
    from src.services.data_release import site_research_publish as publisher
    from tests.unit.test_site_research_publish import sources
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args["run"], args["config"], prefix="site-data/research-publication", client=store)
    first = publisher.publish_site_research(**args, prepare_only=True, client=store)
    assert first["generation"] and not store.writes
    publisher.publish_site_research(**args, client=store)
    writes = list(store.writes)
    def no_rebuild(*args, **kwargs):
        raise AssertionError("Changed source must not recompile the candidate")
    monkeypatch.setattr(publisher, "prepare_papers_content_from_manifest", no_rebuild)
    monkeypatch.setenv("GIDS_PUBLICATION_RECOVERY_ONLY", "1")
    result = publisher.publish_site_research(**args, client=store)
    assert result["json"]["pointer_changed"] is False
    assert store.writes == writes
    store.objects["site-data/research-publication/current.json"] = b'{"newer":true}'
    with pytest.raises(R2S3Error, match="baseline changed"):
        publisher.publish_site_research(**args, client=store)
    assert store.writes == writes


def test_recovery_receipt_drops_raw_preflight_diagnostics(tmp_path):
    task = SimpleNamespace(task_uuid="private", input_data={"publication_plan": {"plan_digest": "same"}})
    ready = tmp_path / "ready.json"
    ready.write_text("{}")
    recovery.save_recovery(tmp_path, task, ready, checks={"overall_ready": True, "git": {
        "ssh_transport": "default", "write_check_output": "Authorization: Bearer do-not-store"}})
    assert "do-not-store" not in recovery.recovery_path(tmp_path, task).read_text()


def test_reports_recovery_rejects_independent_newer_manifest(tmp_path, monkeypatch):
    from src.services.data_release import papers_r2
    config = replace(CONFIG, manifest_key="reports-manifest.json")
    service = SimpleNamespace(_r2_reports_config=lambda: config)
    manifest = tmp_path / "exports/reports-site/reports-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version":2}')
    store = Store()
    store.objects[config.manifest_key] = b'{"version":1}'
    store.close = lambda: None
    monkeypatch.setattr(papers_r2, "R2S3Client", lambda config: store)
    fence = recovery.reports_fence(service, tmp_path)
    store.objects[config.manifest_key] = b'{"version":2}'
    assert recovery.reports_fence(service, tmp_path, fence) == fence
    store.objects[config.manifest_key] = b'{"version":3}'
    with pytest.raises(ValueError, match="reports_baseline_changed"):
        recovery.reports_fence(service, tmp_path, fence)
