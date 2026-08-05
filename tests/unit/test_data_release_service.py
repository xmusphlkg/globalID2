from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.domain import TaskStatus
from src.services import data_release_service as release_module
from src.services.data_release_service import DataReleaseService


@pytest.mark.asyncio
async def test_subscription_options_sync_auto_failure_is_non_blocking(monkeypatch):
    service = DataReleaseService()
    entries = []

    monkeypatch.setattr(service, "_subscription_options_sync_plan", lambda: (True, "auto sync enabled"))
    monkeypatch.setattr(service, "_subscription_options_sync_is_strict", lambda: False)

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("fetch failed")

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    monkeypatch.setattr(service, "_run_logged_command", fail_command)
    monkeypatch.setattr(
        "src.services.data_release_service.task_manager.add_workbook_entry",
        add_workbook_entry,
    )

    synced = await service._sync_subscription_options_if_needed("task-1", job_id="site-release")

    assert synced is False
    assert entries
    assert entries[0]["entry_type"] == "warning"
    assert entries[0]["title"] == "Subscription Options Sync Failed"


@pytest.mark.asyncio
async def test_terminate_process_tree_signals_entire_process_group(monkeypatch):
    service = DataReleaseService()
    signals = []

    class FakeProcess:
        pid = 4321
        returncode = None

        async def wait(self):
            self.returncode = -15
            return self.returncode

    monkeypatch.setattr(
        release_module.os,
        "killpg",
        lambda process_group, sent_signal: signals.append(
            (process_group, sent_signal)
        ),
    )

    await service._terminate_process_tree(FakeProcess())

    assert signals == [
        (4321, release_module.signal.SIGTERM),
        (4321, release_module.signal.SIGKILL),
    ]


@pytest.mark.asyncio
async def test_run_logged_command_timeout_terminates_process_group_and_records_error(
    monkeypatch,
    tmp_path,
):
    service = DataReleaseService()
    entries = []
    created_with = {}
    terminated = []

    class BlockingStream:
        async def readline(self):
            await asyncio.Event().wait()

    class FakeProcess:
        pid = 9876
        returncode = None
        stdout = BlockingStream()

        async def wait(self):
            await asyncio.Event().wait()

    process = FakeProcess()

    async def create_subprocess(*_args, **kwargs):
        created_with.update(kwargs)
        return process

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    async def is_cancel_requested(_task_uuid):
        return False

    async def terminate_process_tree(target):
        terminated.append(target)
        target.returncode = -release_module.signal.SIGTERM

    monkeypatch.setattr(
        release_module.asyncio,
        "create_subprocess_exec",
        create_subprocess,
    )
    monkeypatch.setattr(
        release_module.task_manager,
        "add_workbook_entry",
        add_workbook_entry,
    )
    monkeypatch.setattr(
        release_module.task_manager,
        "is_cancel_requested",
        is_cancel_requested,
    )
    monkeypatch.setattr(service, "_terminate_process_tree", terminate_process_tree)

    with pytest.raises(RuntimeError, match=r"Test Command timed out after 0\.02 seconds"):
        await service._run_logged_command(
            "task-timeout",
            title="Test Command",
            cmd=["long-running-command", "--flag"],
            cwd=tmp_path,
            timeout_seconds=0.02,
        )

    assert created_with["start_new_session"] is (release_module.os.name == "posix")
    assert terminated == [process]
    assert entries[0]["metadata"]["timeout_seconds"] == 0.02
    assert entries[-1]["entry_type"] == "error"
    assert entries[-1]["title"] == "Test Command Timed Out"
    assert "terminated process group 9876" in entries[-1]["content"]
    assert entries[-1]["metadata"]["event"] == "command_timeout"


@pytest.mark.asyncio
async def test_subscription_options_sync_strict_failure_is_blocking(monkeypatch):
    service = DataReleaseService()

    monkeypatch.setattr(service, "_subscription_options_sync_plan", lambda: (True, "strict sync enabled"))
    monkeypatch.setattr(service, "_subscription_options_sync_is_strict", lambda: True)

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(service, "_run_logged_command", fail_command)

    with pytest.raises(RuntimeError, match="fetch failed"):
        await service._sync_subscription_options_if_needed("task-1", job_id="site-release")


@pytest.mark.asyncio
async def test_release_failure_cooldown_suppresses_recent_failed_auto_release(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    failed_task = SimpleNamespace(
        task_uuid="failed-release-1",
        status=TaskStatus.FAILED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(minutes=10),
        completed_at=now - timedelta(minutes=5),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=720))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return failed_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    reason = await service._release_failure_cooldown_reason("site-release")

    assert reason is not None
    assert "failed-release-1" in reason
    assert "cooling down" in reason


@pytest.mark.asyncio
async def test_release_failure_cooldown_ignores_old_or_other_jobs(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    old_task = SimpleNamespace(
        task_uuid="old-failure",
        status=TaskStatus.FAILED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(hours=24),
        completed_at=now - timedelta(hours=24),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=60))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return old_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    assert await service._release_failure_cooldown_reason("site-release") is None


@pytest.mark.asyncio
async def test_release_failure_cooldown_clears_after_later_success(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    successful_task = SimpleNamespace(
        task_uuid="successful-release",
        status=TaskStatus.COMPLETED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(minutes=1),
        completed_at=now - timedelta(minutes=1),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=720))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return successful_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    assert await service._release_failure_cooldown_reason("site-release") is None


def test_cloudflare_deploy_command_targets_explicit_production_branch():
    service = DataReleaseService()

    command = service._cloudflare_deploy_command(
        project_name="globalidv2",
        branch="master",
        source_commit="a" * 40,
        commit_message="publish verified release",
        commit_dirty=False,
    )

    assert command[command.index("--branch") + 1] == "master"
    assert command[command.index("--commit-hash") + 1] == "a" * 40
    assert command[command.index("--commit-message") + 1] == "publish verified release"
    assert "--commit-dirty=false" in command


def test_generate_site_data_command_builds_v2_without_legacy_git_publisher(tmp_path):
    service = DataReleaseService()
    python_path = tmp_path / "python"

    command = service._generate_site_data_command(
        python_path=python_path,
        snapshot_url_base="https://data.example/snapshot-v2",
    )

    assert command == [
        str(python_path),
        "scripts/generate_site_data.py",
        "--github-snapshot-url-base",
        "https://data.example/snapshot-v2",
    ]
    assert {
        "--download-mode",
        "--publish-downloads",
        "--download-repo-url",
        "--download-repo-branch",
        "--download-commit-message",
        "--download-url-base",
    }.isdisjoint(command)


@pytest.mark.asyncio
async def test_release_preflight_checks_v2_snapshot_repo_when_enabled(
    monkeypatch, tmp_path
):
    service = DataReleaseService()
    python_path = tmp_path / "python"
    python_path.touch()
    job = release_module.DataReleaseJobConfig(
        job_id="site-release",
        name="Site Release",
        include_git_push=True,
        include_cloudflare_deploy=False,
    )

    async def no_op():
        return None

    async def load_jobs():
        return [job]

    async def no_paths():
        return []

    async def command_available(*_args, **_kwargs):
        return {"returncode": 0, "stdout": "4.0.0"}

    async def cloudflare_disabled(*_args, **_kwargs):
        return {"payload": {}, "blockers": []}

    check_calls = 0

    async def v2_repo_check(*_args, **_kwargs):
        nonlocal check_calls
        check_calls += 1
        return {
            "payload": {
                "repo_url": "git@example/data.git",
                "branch": "snapshot-v2",
                "raw_base_url": "https://data.example/snapshot-v2",
                "read_access_ok": True,
                "write_access_ok": True,
                "read_check_output": "ok",
                "write_check_output": "ok",
                "ssh_transport": "default",
            },
            "blockers": [],
        }

    monkeypatch.setattr(service, "ensure_storage", no_op)
    monkeypatch.setattr(service, "load_jobs", load_jobs)
    monkeypatch.setattr(service, "_git_status_paths", no_paths)
    monkeypatch.setattr(service, "_run_capture", command_available)
    monkeypatch.setattr(service, "_cloudflare_check", cloudflare_disabled)
    monkeypatch.setattr(service, "_tracked_generated_paths", no_paths)
    monkeypatch.setattr(service, "_download_repo_check", v2_repo_check)
    monkeypatch.setattr(service, "_download_repo_url", lambda: "git@example/data.git")
    monkeypatch.setattr(
        service,
        "_download_repo_raw_base",
        lambda _job: "https://data.example/releases/test-release",
    )
    monkeypatch.setattr(service, "_python_executable", lambda: python_path)
    checks = await service.integration_checks("site-release")

    assert checks["overall_ready"] is True
    assert check_calls == 1
    assert checks["git"]["branch"] == "snapshot-v2"
    assert checks["git"]["read_access_ok"] is True
    assert checks["git"]["write_access_ok"] is True
    assert checks["repository_boundary"]["enforced"] is True


def test_snapshot_publish_command_uses_only_v2_publisher(tmp_path):
    service = DataReleaseService()
    command = service._publish_github_snapshot_command(
        python_path=tmp_path / "python",
        repo_url="git@example/data.git",
        commit_message="publish v2",
    )

    assert command[1] == "scripts/publish_github_snapshot_v2.py"
    assert "--push" in command
    assert "--snapshot-dir" in command
    assert "publish_download_repo.py" not in command


def test_raw_archive_publish_command_uses_dedicated_incremental_publisher(
    monkeypatch, tmp_path
):
    service = DataReleaseService()
    config = SimpleNamespace(
        raw_data_dir=tmp_path / "raw",
        raw_archive=SimpleNamespace(
            repo_url="git@example/raw-archive.git",
            repository_dir=tmp_path / "raw-git-archive",
            chunk_mib=48,
            commit_batch_mib=96,
            zstd_level=6,
            git_timeout_seconds=1800,
        ),
    )
    monkeypatch.setattr(release_module, "get_config", lambda: config)

    command = service._publish_raw_archive_command(python_path=tmp_path / "python")

    assert command[1] == "scripts/publish_raw_git_archive.py"
    assert command[command.index("--source-dir") + 1] == str(config.raw_data_dir)
    assert command[command.index("--repo-url") + 1] == config.raw_archive.repo_url
    assert command[command.index("--chunk-mib") + 1] == "48"
    assert command[command.index("--commit-batch-mib") + 1] == "96"
    assert "--push" in command
    assert all("release" not in part.casefold() for part in command[2:])


@pytest.mark.asyncio
async def test_raw_archive_preflight_is_noop_when_disabled(monkeypatch, tmp_path):
    service = DataReleaseService()
    config = SimpleNamespace(
        raw_data_dir=tmp_path / "raw",
        raw_archive=SimpleNamespace(
            enabled=False,
            repo_url="",
            repository_dir=tmp_path / "archive",
            chunk_mib=48,
        ),
    )
    monkeypatch.setattr(release_module, "get_config", lambda: config)

    result = await service._raw_archive_check()

    assert result["blockers"] == []
    assert result["payload"]["enabled"] is False
    assert result["payload"]["ssh_transport"] == "disabled"


def test_cloudflare_deployment_match_requires_production_branch_and_commit():
    service = DataReleaseService()
    identity = {
        "deployment_branch": "master",
        "source_commit": "a" * 40,
    }
    deployment = {
        "environment": "production",
        "status": "success",
        "branch": "master",
        "commit_hash": "a" * 40,
    }

    assert service._cloudflare_deployment_matches(deployment, identity) is True
    assert service._cloudflare_deployment_matches(
        {**deployment, "environment": "preview"},
        identity,
    ) is False
    assert service._cloudflare_deployment_matches(
        {**deployment, "branch": "feature"},
        identity,
    ) is False


def test_site_release_manifest_records_visual_modules(monkeypatch, tmp_path):
    astro_dir = tmp_path / "astro-site"
    assets_dir = astro_dir / "dist" / "_astro"
    assets_dir.mkdir(parents=True)
    (assets_dir / "EpidemicCurve.ABC123.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "DiseaseMonthlyBar.DEF456.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "unrelated.GHI789.js").write_text("export {}", encoding="utf-8")
    manifest_path = astro_dir / "dist" / "release.json"
    monkeypatch.setattr(release_module, "ASTRO_DIR", astro_dir)
    monkeypatch.setattr(release_module, "SITE_RELEASE_MANIFEST", manifest_path)

    identity = {
        "release_id": "20260730T120000Z-abcdef123456",
        "built_at": "2026-07-30T12:00:00+00:00",
        "source_branch": "feature",
        "source_commit": "a" * 40,
        "deployment_branch": "master",
        "commit_dirty": False,
    }
    payload = DataReleaseService()._write_site_release_manifest(identity)

    assert payload["visual_modules"] == [
        "DiseaseMonthlyBar.DEF456.js",
        "EpidemicCurve.ABC123.js",
    ]
    assert manifest_path.read_text(encoding="utf-8").endswith("\n")
