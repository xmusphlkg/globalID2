from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import data_release_service as release_module
from src.services.data_release import pipeline
from src.services.data_release_service import DataReleaseService


@asynccontextmanager
async def read_snapshot():
    yield {"GLOBALID_RELEASE_DB_SNAPSHOT": "00000003-0000001B-1"}


def _mock_ready_outputs(root_dir: Path) -> None:
    """Produce minimal artifacts so stage-order fakes honor the ready contract."""
    for relative, content in (
        ("src/data/meta.json", "{}"),
        ("public/site-data/meta.json", "{}"),
        ("dist/index.html", "<html>fixture</html>"),
    ):
        path = root_dir / "astro-site" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


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
        read_snapshot=read_snapshot,
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
async def test_repository_publisher_preserves_release_stage_for_retry_classification():
    from src.services.data_release.process_runner import ReleaseCommandError

    async def fail_r2() -> None:
        raise ReleaseCommandError(
            "Publish Papers Bundle to R2 failed with exit code 1",
            release_stage="r2_papers_publish",
            title="Publish Papers Bundle to R2",
            returncode=1,
        )

    async def succeed() -> None:
        return None

    with pytest.raises(ReleaseCommandError) as raised:
        await pipeline._run_repository_publications(
            [("r2_papers", fail_r2()), ("direct_downloads", succeed())]
        )

    assert raised.value.release_stage == "r2_papers_publish"
    assert "r2_papers:" in str(raised.value)


@pytest.mark.asyncio
async def test_repository_pool_timeout_is_retryable_but_mixed_failures_are_not():
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from src.services.data_release.resilience import classify_release_failure

    async def pool_failure():
        raise PoolTimeout("QueuePool limit of size 10 overflow 20 reached, connection timed out")

    async def other_failure():
        raise RuntimeError("invalid output")

    with pytest.raises(RuntimeError) as single:
        await pipeline._run_repository_publications([("papers", pool_failure())])
    assert classify_release_failure(single.value).retryable
    with pytest.raises(RuntimeError) as mixed:
        await pipeline._run_repository_publications([("papers", pool_failure()), ("other", other_failure())])
    assert not classify_release_failure(mixed.value).retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize('research_mode', [None, 'full', 'content', 'code', 'detached'])
async def test_pipeline_local_only_success_preserves_stage_order(tmp_path, monkeypatch, resume, research_mode):
    commands = []
    timeouts = []
    command_envs = {}
    command_args = {}
    boundary_events = []
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
            return SimpleNamespace(timezone="UTC", r2_papers_bucket='papers',
                r2_papers_public_base_url='https://papers.example', r2_access_key_id='key',
                r2_secret_access_key='secret', r2_endpoint_url='', r2_account_id='account',
                literature_json_enabled=research_mode == 'detached')

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

        async def integration_checks(self, _job_id, **_kwargs):
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

        def _r2_papers_enabled(self, _job=None):
            return research_mode is not None

        def _split_papers_bundle_command(self, **_kwargs):
            return ["split-papers"]

        def _publish_papers_r2_command(self, **_kwargs):
            from src.services.data_release.commands import build_publish_papers_r2_command
            return build_publish_papers_r2_command(python_path=_kwargs['python_path'], source_dir=tmp_path / 'exports/papers-site')

        def _cloudflare_account_id(self):
            return "account"

        def _cloudflare_api_token(self):
            return "token"

        def _publish_download_repo_command(self, **_kwargs):
            return ["publish-downloads"]

        async def _run_logged_command(self, _task_uuid, *, title, **_kwargs):
            commands.append(title)
            if title == "Build Astro Site":
                _mock_ready_outputs(tmp_path)
            boundary_events.append(title)
            timeouts.append(_kwargs.get("timeout_seconds"))
            command_envs[title] = _kwargs.get("env")
            command_args[title] = _kwargs.get('cmd')

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
    if research_mode is not None:
        # Exercise the production guard rather than the lightweight fixture
        # path that skips baseline capture when the publisher script is absent.
        publisher = tmp_path / 'scripts/publish_papers_r2.py'
        publisher.parent.mkdir()
        publisher.write_text('# publication entrypoint present\n')
        if research_mode in {'code', 'content'}:
            task.input_data[f'{research_mode}_only'] = True
        if research_mode == 'detached':
            task.input_data['code_only'] = True
        if research_mode in {'code', 'detached'}:
            from src.services.data_release.stage_receipts import StageReceipts
            # This fixture isolates stage order; private workspace/data-byte
            # behavior is covered by the artifact-producing integration tests.
            from src.services.data_release import code_workspace
            monkeypatch.setattr(code_workspace, 'prepare', lambda *_args, **_kwargs: runtime.astro_dir)
            monkeypatch.setattr(StageReceipts, 'assert_published_data',
                lambda *_args: boundary_events.append('assert published inputs'))
            for name in ('meta.json', 'downloads.json', 'research/routes.json'):
                path = runtime.astro_dir / 'src/data' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')

    expected_release = 'release-1'
    if resume:
        from src.services.data_release.artifact_checkpoint import ArtifactCheckpoint

        identity = await Service()._site_release_identity("main")
        if research_mode is not None:
            identity = {**identity, 'release_id': 'restored-release'}
            expected_release = identity['release_id']
        monkeypatch.setattr(ArtifactCheckpoint, "restore", lambda *_args: identity)

    output = await pipeline.execute_release_task(Service(), task, runtime=runtime)

    expected_commands = [
        "Export Situation",
        "Generate Site Data",
        "Build Astro Site",
        "Validate Situation Release Gate",
    ]
    if research_mode is not None:
        if research_mode in {'code', 'detached'}:
            expected_commands = expected_commands[2:]
        if research_mode in {'code', 'content', 'detached'}:
            expected_commands.insert(expected_commands.index('Build Astro Site'), 'Reuse Published Research Snapshot')
        expected_commands.append('Split Papers Bundle')
        expected_commands.append('Prepare Papers Research Candidate')
        expected_commands = ['Capture Research Publication Baseline', *([] if resume else expected_commands),
                             'Publish Papers Bundle to R2']
        assert commands == expected_commands
        expected_run = str(tmp_path / 'exports/site-research-runs/task-success' / expected_release)
        for title in ('Capture Research Publication Baseline', 'Publish Papers Bundle to R2'):
            env = command_envs[title]
            assert env['GIDS_RESEARCH_PUBLICATION_RUN'] == expected_run
            assert env['GIDS_RESEARCH_RELEASE_ID'] == expected_release
            assert env['GIDS_PRESERVE_RESEARCH_DATA'] == ('1' if research_mode in {'code', 'content', 'detached'} else '0')
        baseline_args = command_args['Capture Research Publication Baseline']
        assert command_args['Publish Papers Bundle to R2'][1] == 'scripts/publish_papers_r2.py'
        assert '--capture-baseline-only' in baseline_args
        assert baseline_args[baseline_args.index('--release-id') + 1] == expected_release
        if not resume and research_mode in {'code', 'content', 'detached'}:
            reuse = command_args['Reuse Published Research Snapshot']
            assert reuse[1] == 'scripts/reuse_published_research.py'
            assert reuse[reuse.index('--run') + 1] == expected_run
            assert commands.index('Capture Research Publication Baseline') < commands.index('Reuse Published Research Snapshot') < commands.index('Build Astro Site')
            if research_mode == 'content':
                assert commands.index('Generate Site Data') < commands.index('Reuse Published Research Snapshot')
            else:
                assert boundary_events.count('assert published inputs') == 1
                assert boundary_events.index('assert published inputs') < boundary_events.index('Reuse Published Research Snapshot')
        if resume:
            assert 'Reuse Published Research Snapshot' not in commands
    else:
        assert commands == ([] if resume else expected_commands)
    if not resume:
        if research_mode not in {'code', 'detached'}:
            assert dict(zip(commands, timeouts))['Export Situation'] == pipeline.SITUATION_ROOM_REFRESH_TIMEOUT_SECONDS
        assert command_envs["Build Astro Site"]["NODE_OPTIONS"] == "--max-old-space-size=12288"
    assert progress == ([5, 15, 60, 88, 100] if resume else
                        [5, 15, 45, 60, 88, 100] if research_mode in {'code', 'detached'} else [5, 15, 22, 35, 45, 60, 88, 100])
    assert output["release_timing"]["within_target"]
    assert output["direct_downloads_published"] is False
    assert output["raw_archive_published"] is False
    assert output["pages_deployed"] is False
    assert stored_task.output_data == output


@pytest.mark.asyncio
async def test_pipeline_passes_local_gates_before_remote_publication(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from src.services.data_release import raw_archive_jobs
    enqueue = AsyncMock(return_value={"status": "queued", "task_uuid": "archive-1"})
    monkeypatch.setattr(raw_archive_jobs, "enqueue", enqueue)
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
            if title == "Build Astro Site":
                _mock_ready_outputs(tmp_path)

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

    assert "Archive Raw Crawler Data" not in commands
    enqueue.assert_awaited_once()
    assert stored_task.output_data["raw_archive_task"]["task_uuid"] == "archive-1"
    assert commands.index("Validate Situation Release Gate") < commands.index(
        "Publish Partitioned Data Downloads"
    )
    commands.clear()
    task.input_data = {"release_job_id": "site-release", "incremental_site": True}
    await pipeline.execute_release_task(Service(), task, runtime=runtime)
    assert "Archive Raw Crawler Data" not in commands
    assert "Publish Partitioned Data Downloads" not in commands
    enqueue.assert_awaited_once()


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
    (astro_dir / "src/data").mkdir(parents=True)
    (astro_dir / "src/data/meta.json").write_text("{}")
    (astro_dir / "public/site-data").mkdir(parents=True)
    (astro_dir / "public/site-data/index.json").write_text("{}")
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
    from src.services.data_release.stage_receipts import StageReceipts
    StageReceipts(tmp_path, "retry-task", {}, {}).mark_published(
        {**identity, "build_digest": original_digest},
    )

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
            checkpoint = {"release_id": "release-retry-1", "project_name": "globalid",
                          "account_id": "account", "deployment_branch": "main"}
            if checkpoint_state != "missing_digest":
                checkpoint["artifact_digest"] = original_digest
            return identity, {"cloudflare_deploy": checkpoint}

        def _generate_site_data_command(self, **_kwargs):
            return ["generate"]

        def _update_situation_notices_command(self, **_kwargs):
            return ["update"]

        def _validate_situation_release_command(self, **_kwargs):
            return ["validate"]

        def _r2_papers_enabled(self, _job=None):
            return False

        def _split_papers_bundle_command(self, **_kwargs):
            return ["split-papers"]

        def _split_reports_bundle_command(self, **_kwargs):
            return ["split-reports"]

        def _publish_papers_r2_command(self, **_kwargs):
            return ["publish-papers-r2"]

        def _cloudflare_account_id(self):
            return "account"

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
            raise AssertionError("Public website verification must never run")

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

    if failure_stage == "build":
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
    assert output["website_verification"] == "skipped"
    assert output["artifact_rollback"] is False
    assert any(entry["title"] == "Website Verification Skipped" for entry in entries)
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


async def test_failed_release_retains_generated_files_including_deletions(
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
    assert not original.exists()
    assert (snapshot / "new.json").read_text() == '{"version": 2}'


async def test_release_does_not_copy_artifact_trees_for_rollback(tmp_path, monkeypatch):
    import shutil

    runtime = _runtime(SimpleNamespace(), tmp_path)
    snapshot = runtime.astro_dir / "src" / "data"
    snapshot.mkdir(parents=True)
    (snapshot / "meta.json").write_text("original snapshot")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Publication must not create rollback copies")

    async def release(*_args, **_kwargs):
        (snapshot / "meta.json").write_text("new snapshot")
        return {"ok": True}

    monkeypatch.setattr(shutil, "copytree", forbidden)
    monkeypatch.setattr(pipeline, "_execute_release_task", release)
    assert await pipeline.execute_release_task(None, None, runtime=runtime) == {"ok": True}
    assert (snapshot / "meta.json").read_text() == "new snapshot"
    assert not (tmp_path / "exports" / "release-backups").exists()


async def test_later_channel_failure_does_not_restore_published_downloads(tmp_path, monkeypatch):
    from src.generation.direct_download_files import _write_if_changed

    runtime = _runtime(SimpleNamespace(), tmp_path)
    downloads = tmp_path / "exports" / "site-downloads"
    downloads.mkdir(parents=True)
    partition = downloads / "partition.csv"
    partition.write_bytes(b"old content")

    async def failed_release(*_args, **_kwargs):
        assert _write_if_changed(partition, b"published content")
        (downloads / "new.csv").write_bytes(b"published file")
        raise RuntimeError("another channel failed")

    monkeypatch.setattr(pipeline, "_execute_release_task", failed_release)
    with pytest.raises(RuntimeError, match="another channel failed"):
        await pipeline.execute_release_task(None, None, runtime=runtime)
    assert partition.read_bytes() == b"published content"
    assert (downloads / "new.csv").read_bytes() == b"published file"
