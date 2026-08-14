from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import data_release_service as release_module
from src.services.data_release import pipeline
from src.services.data_release_service import DataReleaseService


def _runtime(manager) -> pipeline.ReleasePipelineRuntime:
    return pipeline.ReleasePipelineRuntime(
        task_manager=manager,
        get_config=lambda: SimpleNamespace(),
        get_database=lambda: None,
        task_model=object,
        root_dir=Path("/repo"),
        astro_dir=Path("/repo/astro-site"),
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
async def test_pipeline_local_only_success_preserves_stage_order():
    commands = []
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

        def _update_situation_room_command(self, **_kwargs):
            return ["update-situation-room"]

        def _validate_situation_release_command(self, **_kwargs):
            return ["validate-situation-release"]

        def _publish_download_repo_command(self, **_kwargs):
            return ["publish-downloads"]

        async def _run_logged_command(self, _task_uuid, *, title, **_kwargs):
            commands.append(title)

        def _write_site_release_manifest(self, identity):
            return identity

        async def _sync_subscription_options_if_needed(self, *_args, **_kwargs):
            return False

    manager = Manager()
    runtime = _runtime(manager)
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

    assert commands == ["Refresh Situation Room", "Generate Site Data", "Build Astro Site", "Validate Situation Release Gate"]
    assert progress == [5, 15, 22, 35, 60, 88, 100]
    assert output["direct_downloads_published"] is False
    assert output["raw_archive_published"] is False
    assert output["pages_deployed"] is False
    assert stored_task.output_data == output


@pytest.mark.asyncio
async def test_pipeline_preflight_failure_stops_before_release_commands():
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
        await pipeline.execute_release_task(Service(), task, runtime=_runtime(Manager()))

    assert len(entries) == 1
    assert entries[0][1]["metadata"]["event"] == "release_preflight"


@pytest.mark.asyncio
async def test_service_execute_release_task_is_compatibility_facade(monkeypatch):
    captured = {}

    async def execute(service, task, *, runtime):
        captured.update(service=service, task=task, runtime=runtime)
        return {"ok": True}

    monkeypatch.setattr(release_module.release_pipeline, "execute_release_task", execute)
    service = DataReleaseService()
    task = SimpleNamespace(task_uuid="task-2")

    assert await service.execute_release_task(task) == {"ok": True}
    assert captured["service"] is service
    assert captured["task"] is task
    assert captured["runtime"].download_repo_branch == "main"
    assert captured["runtime"].raw_archive_branch == "main"
