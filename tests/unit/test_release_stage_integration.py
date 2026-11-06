"""Exercise retries through the real pipeline, with local artifact-producing commands."""
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release.pipeline import ReleasePipelineRuntime, execute_release_task


@pytest.fixture
def release(tmp_path, monkeypatch):
    from src.services.data_release import projection

    commit = AsyncMock()
    monkeypatch.setattr(projection, "commit_release_projection", commit)
    astro = tmp_path / "astro-site"
    stored = SimpleNamespace(output_data=None)
    task = SimpleNamespace(id=1, task_uuid="stage-test", input_data={"release_job_id": "site-release"})
    manager = SimpleNamespace(add_workbook_entry=AsyncMock(), update_task_progress=AsyncMock())
    database = SimpleNamespace(get=AsyncMock(return_value=stored), commit=AsyncMock())

    @asynccontextmanager
    async def db():
        yield database

    snapshot_state = {"open": False, "count": 0}

    @asynccontextmanager
    async def read_snapshot():
        snapshot_state["open"] = True
        snapshot_state["count"] += 1
        try:
            yield {"GLOBALID_RELEASE_DB_SNAPSHOT": "00000003-0000001B-1"}
        finally:
            snapshot_state["open"] = False

    def put(path, content="{}"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    class Service:
        failure = None
        commands = []
        identity = dict(release_id="stage-release", source_commit="abc", source_branch="main",
                        deployment_branch="main", built_at="2026-11-05T00:00:00Z", commit_dirty=False)
        job = SimpleNamespace(job_id="site-release", name="Site", timezone="UTC", github_branch="main",
                              cloudflare_project_name="site", include_git_push=True, include_cloudflare_deploy=True)
        _config = lambda self: SimpleNamespace(timezone="UTC")
        _download_repo_branch = lambda self, job: "main"
        _download_repo_url = lambda self: "git@example/test.git"
        _download_repo_raw_base = lambda self, job: "https://example.test/data"
        _cloudflare_project_name = lambda self, value: value
        _cloudflare_account_id = lambda self: "account"
        _cloudflare_api_token = lambda self: "fake-test-token"
        _build_git_env = lambda self, *args, **kwargs: {}
        _render_commit_message = lambda self, *args, **kwargs: "publish"
        _python_executable = lambda self: Path("/test/python")
        _generate_site_data_command = lambda self, **kwargs: ["generate"]
        _update_situation_notices_command = lambda self, **kwargs: ["situation"]
        _validate_situation_release_command = lambda self, **kwargs: ["validate"]
        _split_papers_bundle_command = lambda self, **kwargs: ["papers"]
        _split_reports_bundle_command = lambda self, **kwargs: ["reports"]
        _publish_download_repo_command = lambda self, **kwargs: ["downloads"]
        _cloudflare_deploy_command = lambda self, **kwargs: ["deploy"]

        async def load_jobs(self):
            return [self.job]

        async def integration_checks(self, job, **kwargs):
            return {"overall_ready": True, "cloudflare": {"production_branch": "main"}}

        async def _release_identity_for_task(self, *args):
            return dict(self.identity), {}

        async def _record_release_checkpoint(self, *args):
            pass

        async def _sync_subscription_options_if_needed(self, *args, **kwargs):
            return False

        def _write_site_release_manifest(self, identity):
            from src.generation.site_data_writer import _write_if_changed
            import json
            _write_if_changed(astro / "dist/release.json", json.dumps(identity).encode())
            return identity

        async def _run_logged_command(self, task_uuid, *, title, **kwargs):
            self.commands.append(title)
            if title in {"Export Situation", "Generate Site Data", "Refresh Report Inputs"}:
                assert snapshot_state["open"]
                assert kwargs["env"]["GLOBALID_RELEASE_DB_SNAPSHOT"] == "00000003-0000001B-1"
            elif title != "Validate Report Inputs":
                assert not snapshot_state["open"], "Build/upload must not retain a database snapshot"
                assert "GLOBALID_RELEASE_DB_SNAPSHOT" not in (kwargs.get("env") or {})
            if title == self.failure:
                raise RuntimeError("injected " + title)
            if title == "Generate Site Data":
                for name in ("meta.json", "downloads.json", "research/routes.json", "_release-projection/manifest.json"):
                    put(astro / "src/data" / name)
                put(astro / "public/site-data/index.json")
                for name in ("countries/test.json", "diseases/test.json", "manifest.json"):
                    put(tmp_path / "exports/site-downloads" / name)
            if title == "Build Astro Site":
                put(astro / "dist/index.html", "<html>test</html>")

    runtime = ReleasePipelineRuntime(manager, lambda: SimpleNamespace(raw_archive=SimpleNamespace(enabled=False, repo_url="")),
                                     db, object, tmp_path, astro, "main", "main", 60, 60, 60, 60,
                                     read_snapshot=read_snapshot)
    service = Service()
    service.commands = []
    return SimpleNamespace(service=service, task=task, runtime=runtime, commit=commit, put=put, root=tmp_path,
                           astro=astro, stored=stored, manager=manager, snapshot_state=snapshot_state)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["Build Astro Site", "Deploy Cloudflare Pages"])
async def test_retry_skips_completed_stages(release, failure):
    r = release
    r.service.failure = failure
    with pytest.raises(RuntimeError, match="injected"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert r.snapshot_state == {"open": False, "count": 1}
    r.commit.assert_not_awaited()
    r.service.commands.clear()
    r.service.failure = None
    result = await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert r.snapshot_state == {"open": False, "count": 1}, "Retry must reuse files without reopening a newer database view"
    assert "Generate Site Data" not in r.service.commands
    assert ("Build Astro Site" in r.service.commands) == (failure == "Build Astro Site")
    assert result["pages_deployed"]
    r.commit.assert_awaited_once()
    assert not (r.root / "exports/release-stages/stage-test.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("edit,regenerate", [("template", False), ("generator", True), ("data", True)])
async def test_changed_inputs_invalidate_the_correct_stage(release, edit, regenerate):
    r = release
    r.service.failure = "Deploy Cloudflare Pages"
    with pytest.raises(RuntimeError):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    path = {"template": r.astro / "src/pages/index.astro", "generator": r.root / "src/generation/change.py",
            "data": r.astro / "src/data/meta.json"}[edit]
    r.put(path, "changed")
    r.service.commands.clear()
    r.service.failure = None
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert ("Generate Site Data" in r.service.commands) == regenerate
    assert "Build Astro Site" in r.service.commands


@pytest.mark.asyncio
async def test_code_only_does_not_export_publish_downloads_or_ack_pending(release):
    r = release
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    r.service.commands.clear()
    r.commit.reset_mock()
    r.task.task_uuid = "code-release"
    r.task.input_data = {"release_job_id": "site-release", "code_only": True}
    r.service.identity = {**r.service.identity, "release_id": "code-release"}
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert "Build Astro Site" in r.service.commands
    assert "Generate Site Data" not in r.service.commands
    assert "Export Situation" not in r.service.commands
    assert "Publish Partitioned Data Downloads" not in r.service.commands
    r.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_only_rejects_unpublished_local_snapshot(release):
    r = release
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    r.put(r.astro / "src/data/meta.json", "unpublished changes")
    r.task.input_data = {"release_job_id": "site-release", "code_only": True}
    r.service.commands.clear()
    with pytest.raises(RuntimeError, match="normal data release"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert not r.service.commands


@pytest.mark.asyncio
async def test_archive_task_dispatch_bypasses_main_pipeline(release, monkeypatch):
    from src.services.data_release import raw_archive_jobs
    execute = AsyncMock(return_value={"raw_archive_published": True})
    monkeypatch.setattr(raw_archive_jobs, "execute", execute)
    r = release
    r.task.input_data = {"raw_archive_only": True}
    assert await execute_release_task(r.service, r.task, runtime=r.runtime) == {"raw_archive_published": True}
    execute.assert_awaited_once()
    assert not r.service.commands


@pytest.mark.asyncio
async def test_deploy_success_then_ack_failure_reuses_frozen_artifacts(release):
    r = release
    r.commit.side_effect = RuntimeError("database acknowledgement failed")
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert (r.root / "exports/successful-site-release.json").is_file()
    r.service.commands.clear()
    r.commit.side_effect = None
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert "Generate Site Data" not in r.service.commands
    assert "Build Astro Site" not in r.service.commands
    assert r.commit.await_count == 2


@pytest.mark.asyncio
async def test_invalid_report_input_fails_before_expensive_generation(release):
    r = release
    r.put(r.astro / "scripts/check-report-inputs.mjs", "// fixture")
    r.service.failure = "Validate Report Inputs"
    with pytest.raises(RuntimeError, match="Validate Report Inputs"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert r.service.commands == ["Refresh Report Inputs", "Validate Report Inputs"]
    r.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_success_is_recorded_before_database_checkpoint(release):
    import json
    r = release
    r.service._record_release_checkpoint = AsyncMock(side_effect=RuntimeError("checkpoint DB down"))
    with pytest.raises(RuntimeError, match="checkpoint DB down"):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    marker = json.loads((r.root / "exports/successful-site-release.json").read_text())
    assert marker["release_id"] == "stage-release"
    assert marker["build_digest"]
    r.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_deploy_checkpoint_cannot_replace_later_success(release):
    from src.services.data_release.stage_receipts import StageReceipts
    r = release
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    prior = r.stored.output_data["site_release"]
    StageReceipts(r.root, "another-task", {}, {}).mark_published(
        {**prior, "release_id": "newer-release"},
        data_paths=[r.astro / "src/data", r.astro / "public/site-data"],
    )
    r.service._release_identity_for_task = AsyncMock(return_value=(dict(r.service.identity), {
        "cloudflare_deploy": {"release_id": prior["release_id"], "artifact_digest": prior["build_digest"],
                              "project_name": "site", "account_id": "account", "deployment_branch": "main"},
    }))
    r.service.commands.clear()
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert "Deploy Cloudflare Pages" in r.service.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("project", ["site", "different-project"])
async def test_deploy_checkpoint_reuse_requires_same_target(release, project):
    r = release
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    prior = r.stored.output_data["site_release"]
    r.service.job.cloudflare_project_name = project
    r.service._release_identity_for_task = AsyncMock(return_value=(dict(r.service.identity), {
        "cloudflare_deploy": {"release_id": prior["release_id"], "artifact_digest": prior["build_digest"],
                              "project_name": "site", "account_id": "account", "deployment_branch": "main"},
    }))
    r.service.commands.clear()
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert ("Deploy Cloudflare Pages" in r.service.commands) == (project != "site")


async def test_export_failure_closes_snapshot_before_retry(release):
    r = release
    r.service.failure = 'Generate Site Data'
    with pytest.raises(RuntimeError, match='injected'):
        await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert r.snapshot_state == {'open': False, 'count': 1}
    r.commit.assert_not_awaited()
    r.service.failure = None
    await execute_release_task(r.service, r.task, runtime=r.runtime)
    assert r.snapshot_state == {'open': False, 'count': 2}
