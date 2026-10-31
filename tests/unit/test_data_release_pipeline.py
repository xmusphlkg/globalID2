from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import data_release_service as release_module
from src.services.data_release import pipeline
from src.services.data_release_service import DataReleaseService


def _runtime(manager, root_dir: Path) -> pipeline.ReleasePipelineRuntime:
    return pipeline.ReleasePipelineRuntime(
        task_manager=manager,
        get_config=lambda: SimpleNamespace(),
        get_database=lambda: None,
        task_model=object,
        root_dir=root_dir,
        astro_dir=root_dir / "astro-site",
        download_repo_branch="main",
        raw_archive_branch="main",
        generate_timeout_seconds=1800,
        astro_build_timeout_seconds=900,
        download_publish_timeout_seconds=900,
        cloudflare_deploy_timeout_seconds=900,
    )


@pytest.mark.asyncio
async def test_repository_publishers_run_concurrently():
    started: set[str] = set()
    both_started = asyncio.Event()

    async def publish(name: str) -> None:
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)

    completed = await pipeline._run_repository_publications(
        [
            ("raw_archive", publish("raw_archive")),
            ("direct_downloads", publish("direct_downloads")),
        ]
    )

    assert completed == {"raw_archive", "direct_downloads"}


@pytest.mark.asyncio
async def test_repository_publisher_failures_are_aggregated_after_all_finish():
    completed = []

    async def fail() -> None:
        raise RuntimeError("raw transport failed")

    async def succeed() -> None:
        completed.append("downloads")

    with pytest.raises(RuntimeError, match="raw_archive: raw transport failed"):
        await pipeline._run_repository_publications(
            [("raw_archive", fail()), ("direct_downloads", succeed())]
        )

    assert completed == ["downloads"]


@pytest.mark.asyncio
async def test_pipeline_local_only_success_preserves_stage_order(tmp_path):
    commands = []
    timeouts = []
    command_envs = {}
    progress = []
    stored_task = SimpleNamespace(output_data=None)

    class Manager:
        async def add_workbook_entry(self, *_args, **_kwargs):
            return None

        async def update_task_progress(self, _task_uuid, value):
            progress.append(value)

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, task_id):
            assert task_id == 7
            return stored_task

        async def commit(self):
            return None

    class Service:
        def _config(self):
            return SimpleNamespace(timezone="UTC")

        async def load_jobs(self):
            return [
                SimpleNamespace(
                    job_id="site-release",
                    name="Site Release",
                    timezone="UTC",
                    cloudflare_project_name="globalid",
                    github_branch="main",
                    include_git_push=False,
                    include_cloudflare_deploy=False,
                    commit_message_template="publish {branch}",
                )
            ]

        async def integration_checks(self, _job_id):
            return {
                "overall_ready": True,
                "blockers": [],
                "git": {"ssh_transport": "default"},
                "cloudflare": {"production_branch": "main"},
                "raw_archive": {},
            }

        def _cloudflare_project_name(self, value):
            return value

        def _download_repo_url(self):
            return "git@example/snapshot.git"

        def _build_git_env(self, *_args, **_kwargs):
            return {"GIT_TERMINAL_PROMPT": "0"}

        def _download_repo_raw_base(self, _job):
            return "https://raw.example/data/main"

        def _download_repo_branch(self, _job):
            return "main"

        def _render_commit_message(self, *_args, **_kwargs):
            return "publish main"

        def _python_executable(self):
            return Path("/venv/python")

        async def _current_git_branch(self):
            return "main"

        async def _site_release_identity(self, branch):
            return {
                "release_id": "release-1",
                "source_commit": "abc123",
                "source_branch": "feature",
                "deployment_branch": branch,
                "built_at": "2026-08-05T00:00:00+00:00",
                "commit_dirty": False,
            }

        def _generate_site_data_command(self, **_kwargs):
            return ["generate"]

        def _update_situation_notices_command(self, **_kwargs):
            return ["update-situation-room"]

        def _validate_situation_release_command(self, **_kwargs):
            return ["validate-situation-release"]

        def _publish_download_repo_command(self, **_kwargs):
            return ["publish-downloads"]

        async def _run_logged_command(self, _task_uuid, *, title, **_kwargs):
            commands.append(title)
            timeouts.append(_kwargs.get("timeout_seconds"))
            command_envs[title] = _kwargs.get("env")

        def _write_site_release_manifest(self, identity):
            return identity

        async def _sync_subscription_options_if_needed(self, *_args, **_kwargs):
            return False

    manager = Manager()
    runtime = _runtime(manager, tmp_path)
    runtime = pipeline.ReleasePipelineRuntime(
        **{
            **runtime.__dict__,
            "get_config": lambda: SimpleNamespace(
                raw_archive=SimpleNamespace(enabled=False, repo_url="")
            ),
            "get_database": Database,
        }
    )
    task = SimpleNamespace(
        id=7,
        task_uuid="task-success",
        input_data={"release_job_id": "site-release"},
    )

    output = await pipeline.execute_release_task(Service(), task, runtime=runtime)

    assert commands == [
        "Export Situation",
        "Generate Site Data",
        "Build Astro Site",
        "Validate Situation Release Gate",
    ]
    assert timeouts[0] == pipeline.SITUATION_ROOM_REFRESH_TIMEOUT_SECONDS
    assert (
        command_envs["Build Astro Site"]["NODE_OPTIONS"] == "--max-old-space-size=6144"
    )
    assert progress == [5, 15, 22, 35, 45, 60, 88, 100]
    assert output["direct_downloads_published"] is False
    assert output["raw_archive_published"] is False
    assert output["pages_deployed"] is False
    assert stored_task.output_data == output


@pytest.mark.asyncio
async def test_pipeline_passes_local_gates_before_remote_publication(tmp_path):
    commands = []
    stored_task = SimpleNamespace(output_data=None)

    class Manager:
        async def add_workbook_entry(self, *_args, **_kwargs):
            return None

        async def update_task_progress(self, *_args):
            return None

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args):
            return stored_task

        async def commit(self):
            return None

    class Service:
        def _config(self):
            return SimpleNamespace(timezone="UTC")

        async def load_jobs(self):
            return [
                SimpleNamespace(
                    job_id="site-release",
                    name="Site Release",
                    timezone="UTC",
                    cloudflare_project_name="globalid",
                    github_branch="main",
                    include_git_push=True,
                    include_cloudflare_deploy=False,
                    commit_message_template="publish {branch}",
                )
            ]

        async def integration_checks(self, _job_id):
            return {
                "overall_ready": True,
                "blockers": [],
                "git": {"ssh_transport": "default"},
                "cloudflare": {"production_branch": "main"},
                "raw_archive": {"ssh_transport": "default"},
            }

        def _cloudflare_project_name(self, value):
            return value

        def _download_repo_url(self):
            return "git@example/downloads.git"

        def _download_repo_raw_base(self, _job):
            return "https://raw.example/main"

        def _download_repo_branch(self, _job):
            return "main"

        def _build_git_env(self, *_args, **_kwargs):
            return {}

        def _render_commit_message(self, *_args, **_kwargs):
            return "publish main"

        def _python_executable(self):
            return Path("/venv/python")

        async def _current_git_branch(self):
            return "main"

        async def _site_release_identity(self, branch):
            return {
                "release_id": "release-1",
                "source_commit": "abc123",
                "source_branch": "main",
                "deployment_branch": branch,
                "built_at": "2026-08-05T00:00:00+00:00",
                "commit_dirty": False,
            }

        def _generate_site_data_command(self, **_kwargs):
            return ["generate"]

        def _update_situation_notices_command(self, **_kwargs):
            return ["update"]

        def _validate_situation_release_command(self, **_kwargs):
            return ["validate"]

        def _publish_download_repo_command(self, **_kwargs):
            return ["publish-downloads"]

        def _publish_raw_archive_command(self, **_kwargs):
            return ["publish-raw"]

        async def _run_logged_command(self, _task_uuid, *, title, **_kwargs):
            commands.append(title)

        def _write_site_release_manifest(self, identity):
            return identity

        async def _sync_subscription_options_if_needed(self, *_args, **_kwargs):
            return False

    raw_archive = SimpleNamespace(
        enabled=True,
        repo_url="git@example/raw.git",
        branch="main",
        git_timeout_seconds=60,
    )
    runtime = pipeline.ReleasePipelineRuntime(
        **{
            **_runtime(Manager(), tmp_path).__dict__,
            "get_config": lambda: SimpleNamespace(raw_archive=raw_archive),
            "get_database": Database,
        }
    )
    task = SimpleNamespace(
        id=9,
        task_uuid="task-publish-order",
        input_data={"release_job_id": "site-release"},
    )

    await pipeline.execute_release_task(Service(), task, runtime=runtime)

    assert commands.index("Build Astro Site") < commands.index(
        "Archive Raw Crawler Data"
    )
    assert commands.index("Validate Situation Release Gate") < commands.index(
        "Publish Partitioned Data Downloads"
    )
    commands.clear()
    task.input_data = {"release_job_id": "site-release", "incremental_site": True}
    await pipeline.execute_release_task(Service(), task, runtime=runtime)
    assert "Archive Raw Crawler Data" not in commands
    assert "Publish Partitioned Data Downloads" not in commands


@pytest.mark.asyncio
async def test_pipeline_preflight_failure_stops_before_release_commands(tmp_path):
    entries = []

    class Manager:
        async def add_workbook_entry(self, task_uuid, **kwargs):
            entries.append((task_uuid, kwargs))

    class Service:
        async def load_jobs(self):
            return [SimpleNamespace(job_id="site-release")]

        async def integration_checks(self, job_id):
            assert job_id == "site-release"
            return {"overall_ready": False, "blockers": ["Git unavailable"]}

        async def _run_logged_command(self, *_args, **_kwargs):
            raise AssertionError("release commands must not run after failed preflight")

    task = SimpleNamespace(
        input_data={"release_job_id": "site-release"},
        task_uuid="task-1",
    )

    with pytest.raises(RuntimeError, match="Release preflight failed: Git unavailable"):
        await pipeline.execute_release_task(
            Service(), task, runtime=_runtime(Manager(), tmp_path)
        )

    assert len(entries) == 1
    assert entries[0][1]["metadata"]["event"] == "release_preflight"


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_state", ["identical", "changed", "missing_digest"])
@pytest.mark.parametrize("failure_stage", [None, "build", "verification"])
async def test_retry_reuses_deploy_only_for_identical_build_content(
    tmp_path, checkpoint_state, failure_stage, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.services.data_release import projection

    commit_projection = AsyncMock()
    monkeypatch.setattr(projection, "commit_release_projection", commit_projection)
    commands = []
    entries = []
    recorded = []
    stored_task = SimpleNamespace(output_data=None)
    astro_dir = tmp_path / "astro-site"
    dist = astro_dir / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("first attempt published old observations")
    original_digest = pipeline._built_site_digest(dist)

    class Manager:
        async def add_workbook_entry(self, _task_uuid, **kwargs):
            entries.append(kwargs)

        async def update_task_progress(self, *_args):
            return None

    class Database:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _task_id):
            return stored_task

        async def commit(self):
            return None

    identity = {
        "release_id": "release-retry-1",
        "source_commit": "abc123",
        "source_branch": "main",
        "deployment_branch": "main",
        "built_at": "2026-08-05T00:00:00+00:00",
        "commit_dirty": False,
    }

    class Service:
        def _config(self):
            return SimpleNamespace(timezone="UTC")

        async def load_jobs(self):
            return [
                SimpleNamespace(
                    job_id="site-release",
                    name="Site Release",
                    timezone="UTC",
                    cloudflare_project_name="globalid",
                    github_branch="main",
                    include_git_push=False,
                    include_cloudflare_deploy=True,
                    commit_message_template="publish {branch}",
                )
            ]

        async def integration_checks(self, _job_id):
            return {
                "overall_ready": True,
                "blockers": [],
                "git": {"ssh_transport": "default"},
                "cloudflare": {
                    "production_branch": "main",
                    "subdomain": "globalid.example",
                },
                "raw_archive": {},
            }

        def _cloudflare_project_name(self, value):
            return value

        def _download_repo_url(self):
            return "git@example/snapshot.git"

        def _build_git_env(self, *_args, **_kwargs):
            return {}

        def _download_repo_raw_base(self, _job):
            return "https://raw.example/data/main"

        def _download_repo_branch(self, _job):
            return "main"

        def _render_commit_message(self, *_args, **_kwargs):
            return "publish main"

        def _python_executable(self):
            return Path("/venv/python")

        async def _current_git_branch(self):
            return "main"

        async def _release_identity_for_task(self, *_args):
            checkpoint = {"release_id": "release-retry-1"}
            if checkpoint_state != "missing_digest":
                checkpoint["artifact_digest"] = original_digest
            return identity, {"cloudflare_deploy": checkpoint}

        def _generate_site_data_command(self, **_kwargs):
            return ["generate"]

        def _update_situation_notices_command(self, **_kwargs):
            return ["update"]

        def _validate_situation_release_command(self, **_kwargs):
            return ["validate"]

        async def _run_logged_command(self, _task_uuid, *, title, **_kwargs):
            commands.append(title)
            if title == "Build Astro Site" and failure_stage == "build":
                raise RuntimeError("Astro build failed")
            if title == "Build Astro Site" and checkpoint_state == "changed":
                (dist / "index.html").write_text(
                    "new attempt published new observations"
                )

        def _write_site_release_manifest(self, value):
            return value

        async def _sync_subscription_options_if_needed(self, *_args, **_kwargs):
            return False

        def _cloudflare_deploy_command(self, **_kwargs):
            return ["deploy"]

        def _cloudflare_api_token(self):
            return "token"

        def _cloudflare_account_id(self):
            return "account"

        async def _record_release_checkpoint(self, *_args, **_kwargs):
            recorded.append(_args[2])

        async def _verify_cloudflare_production_release(self, **_kwargs):
            assert _kwargs["release_identity"][
                "build_digest"
            ] == pipeline._built_site_digest(dist)
            if failure_stage == "verification":
                raise RuntimeError("production verification failed")
            return {"verified": True, "production_url": "https://globalid.example"}

    runtime = _runtime(Manager(), tmp_path)
    runtime = pipeline.ReleasePipelineRuntime(
        **{
            **runtime.__dict__,
            "get_config": lambda: SimpleNamespace(
                raw_archive=SimpleNamespace(enabled=False, repo_url="")
            ),
            "get_database": Database,
            "root_dir": tmp_path,
            "astro_dir": astro_dir,
        }
    )
    task = SimpleNamespace(
        id=8,
        task_uuid="retry-task",
        input_data={"release_job_id": "site-release"},
    )

    if failure_stage:
        with pytest.raises(RuntimeError, match="failed"):
            await pipeline.execute_release_task(Service(), task, runtime=runtime)
        commit_projection.assert_not_awaited()
        return

    output = await pipeline.execute_release_task(Service(), task, runtime=runtime)
    commit_projection.assert_awaited_once()

    reused = checkpoint_state == "identical"
    assert ("Deploy Cloudflare Pages" not in commands) == reused
    assert len(recorded) == (0 if reused else 1)
    if recorded:
        assert recorded[0]["artifact_digest"] == pipeline._built_site_digest(dist)
    # Subscriber alerts relied on the retired v3 signal policy and are no
    # longer dispatched by releases.
    assert "Dispatch Reviewed Situation Alerts" not in commands
    assert output["pages_deployed"] is True
    assert "situation_alert_dispatch_attempted" not in output
    completion = next(
        entry for entry in entries if entry["title"] == "Data Release Completed"
    )
    assert "Situation alert dispatch" not in completion["content"]
    assert "Reviewed Situation alerts dispatched:" not in completion["content"]
    assert (
        any(entry["title"] == "Cloudflare Deploy Reused" for entry in entries) == reused
    )


@pytest.mark.asyncio
async def test_service_execute_release_task_is_compatibility_facade(monkeypatch):
    captured = {}

    async def execute(service, task, *, runtime):
        captured.update(service=service, task=task, runtime=runtime)
        return {"ok": True}

    monkeypatch.setattr(
        release_module.release_pipeline, "execute_release_task", execute
    )
    service = DataReleaseService()
    task = SimpleNamespace(task_uuid="task-2")

    assert await service.execute_release_task(task) == {"ok": True}
    assert captured["service"] is service
    assert captured["task"] is task
    assert captured["runtime"].download_repo_branch == "main"
    assert captured["runtime"].raw_archive_branch == "main"


async def test_failed_release_restores_generated_files_including_deletions(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    runtime = replace(
        _runtime(SimpleNamespace(), tmp_path),
        root_dir=tmp_path,
        astro_dir=tmp_path / "astro-site",
    )
    snapshot = runtime.astro_dir / "src" / "data"
    snapshot.mkdir(parents=True)
    original = snapshot / "old.json"
    original.write_text('{"version": 1}')

    async def failed_build(*_args, **_kwargs):
        original.unlink()
        (snapshot / "new.json").write_text('{"version": 2}')
        raise RuntimeError("Astro build failed")

    monkeypatch.setattr(pipeline, "_execute_release_task", failed_build)
    with pytest.raises(RuntimeError, match="Astro build failed"):
        await pipeline.execute_release_task(None, None, runtime=runtime)
    assert original.read_text() == '{"version": 1}'
    assert not (snapshot / "new.json").exists()


async def test_release_backups_use_export_volume_and_never_block_dispatcher(
    tmp_path, monkeypatch
):
    import shutil
    import tempfile
    import threading

    runtime = _runtime(SimpleNamespace(), tmp_path)
    snapshot = runtime.astro_dir / "src" / "data"
    snapshot.mkdir(parents=True)
    (snapshot / "meta.json").write_text("original snapshot")
    backup_root = tmp_path / "exports" / "release-backups"
    loop_thread = threading.get_ident()
    original_copytree = shutil.copytree
    original_cleanup = tempfile.TemporaryDirectory.cleanup
    copies, removals = [], []

    def copytree(source, destination, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        copies.append(Path(destination))
        return original_copytree(source, destination, *args, **kwargs)

    def cleanup(directory):
        assert threading.get_ident() != loop_thread
        removals.append(Path(directory.name))
        return original_cleanup(directory)

    async def release(*_args, **_kwargs):
        temporary = list(backup_root.iterdir())
        assert len(temporary) == 1
        backup = temporary[0] / "0" / "meta.json"
        (snapshot / "meta.json").write_text("new snapshot")
        assert backup.read_text() == "original snapshot"
        return {"ok": True}

    monkeypatch.setattr(shutil, "copytree", copytree)
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup)
    monkeypatch.setattr(pipeline, "_execute_release_task", release)
    assert await pipeline.execute_release_task(None, None, runtime=runtime) == {
        "ok": True
    }
    assert copies and all(path.is_relative_to(backup_root) for path in copies)
    assert removals and all(path.is_relative_to(backup_root) for path in removals)
    assert list(backup_root.iterdir()) == []
