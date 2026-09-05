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
        async def read(self, _size=-1):
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
async def test_run_capture_timeout_terminates_process_and_returns_timeout(monkeypatch, tmp_path):
    service = DataReleaseService()
    terminated = []

    class FakeProcess:
        pid = 2468
        returncode = None

        async def communicate(self):
            await asyncio.Event().wait()

    process = FakeProcess()

    async def create_subprocess(*_args, **_kwargs):
        return process

    async def terminate_process_tree(target):
        terminated.append(target)
        target.returncode = -release_module.signal.SIGTERM

    monkeypatch.setattr(release_module.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(service, "_terminate_process_tree", terminate_process_tree)

    result = await service._run_capture(
        ["slow command", "--flag"],
        cwd=tmp_path,
        timeout=0.01,
    )

    assert terminated == [process]
    assert result == {
        "returncode": 124,
        "stdout": "Command timed out after 0.01s: 'slow command' --flag",
    }


@pytest.mark.asyncio
async def test_run_logged_command_cancellation_terminates_process(monkeypatch, tmp_path):
    service = DataReleaseService()
    terminated = []

    class FakeStream:
        async def read(self, _size=-1):
            return b""

    class FakeProcess:
        pid = 1357
        returncode = None
        stdout = FakeStream()

    process = FakeProcess()

    async def create_subprocess(*_args, **_kwargs):
        return process

    async def add_workbook_entry(*_args, **_kwargs):
        return None

    async def is_cancel_requested(_task_uuid):
        return True

    async def terminate_process_tree(target):
        terminated.append(target)
        target.returncode = -release_module.signal.SIGTERM

    monkeypatch.setattr(release_module.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_workbook_entry)
    monkeypatch.setattr(release_module.task_manager, "is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr(service, "_terminate_process_tree", terminate_process_tree)

    with pytest.raises(release_module.TaskCancelledError, match="Cancellation requested"):
        await service._run_logged_command(
            "task-cancel",
            title="Cancelled Command",
            cmd=["command"],
            cwd=tmp_path,
        )

    assert terminated == [process]


@pytest.mark.asyncio
async def test_run_logged_command_failure_includes_output_tail(monkeypatch, tmp_path):
    service = DataReleaseService()
    entries = []

    class FakeStream:
        def __init__(self):
            self.lines = iter((b"first line\n", b"failure detail\n", b""))

        async def read(self, _size=-1):
            return next(self.lines)

    class FakeProcess:
        pid = 9753
        returncode = 3
        stdout = FakeStream()

    async def create_subprocess(*_args, **_kwargs):
        return FakeProcess()

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    async def is_cancel_requested(_task_uuid):
        return False

    monkeypatch.setattr(release_module.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_workbook_entry)
    monkeypatch.setattr(release_module.task_manager, "is_cancel_requested", is_cancel_requested)

    with pytest.raises(
        RuntimeError,
        match=r"failed with exit code 3[\s\S]*failure detail",
    ):
        await service._run_logged_command(
            "task-failed",
            title="Failed Command",
            cmd=["command"],
            cwd=tmp_path,
        )

    output_entries = [entry for entry in entries if "Output" in entry["title"]]
    assert output_entries[0]["content"] == "first line\nfailure detail"


@pytest.mark.asyncio
async def test_run_logged_command_compacts_verbose_output_and_keeps_final_tail(
    monkeypatch,
    tmp_path,
):
    service = DataReleaseService()
    entries = []

    class FakeStream:
        def __init__(self):
            lines = [f"line {index}\n".encode() for index in range(1, 131)]
            lines[0] = b"\x1b[32mline 1\x1b[0m\n"
            self.lines = iter((*lines, b""))

        async def read(self, _size=-1):
            return next(self.lines)

    class FakeProcess:
        pid = 8642
        returncode = 0
        stdout = FakeStream()

    async def create_subprocess(*_args, **_kwargs):
        return FakeProcess()

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    async def is_cancel_requested(_task_uuid):
        return False

    monkeypatch.setattr(release_module.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_workbook_entry)
    monkeypatch.setattr(release_module.task_manager, "is_cancel_requested", is_cancel_requested)

    await service._run_logged_command(
        "task-verbose",
        title="Verbose Command",
        cmd=["command"],
        cwd=tmp_path,
    )

    output_entries = [entry for entry in entries if " Output #" in entry["title"]]
    assert [entry["title"] for entry in output_entries] == [
        "Verbose Command Output #1",
        "Verbose Command Output #2",
    ]
    assert output_entries[0]["content"].startswith("line 1\n")
    assert "\x1b" not in output_entries[0]["content"]

    completed = entries[-1]
    assert completed["title"] == "Verbose Command Completed"
    assert "130 lines total" in completed["content"]
    assert "50 lines omitted from chunk records" in completed["content"]
    assert completed["content"].endswith("line 130")
    assert completed["metadata"]["stored_output_chunks"] == 2
    assert completed["metadata"]["suppressed_output_chunks"] == 2
    assert completed["metadata"]["output_compacted"] is True


@pytest.mark.asyncio
async def test_run_logged_command_drains_output_chunks_without_newlines(
    monkeypatch,
    tmp_path,
):
    service = DataReleaseService()
    entries = []

    class FakeStream:
        def __init__(self):
            self.chunks = iter((b"x" * 9000, b"done", b""))

        async def read(self, _size=-1):
            return next(self.chunks)

    class FakeProcess:
        pid = 9754
        returncode = 0
        stdout = FakeStream()

    async def create_subprocess(*_args, **_kwargs):
        return FakeProcess()

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    async def is_cancel_requested(_task_uuid):
        return False

    monkeypatch.setattr(release_module.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_workbook_entry)
    monkeypatch.setattr(release_module.task_manager, "is_cancel_requested", is_cancel_requested)

    await service._run_logged_command(
        "task-chunks",
        title="Chunky Command",
        cmd=["command"],
        cwd=tmp_path,
    )

    output_entries = [entry for entry in entries if " Output #" in entry["title"]]
    assert output_entries
    assert "x" * 4000 in output_entries[0]["content"]
    assert entries[-1]["title"] == "Chunky Command Completed"


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


def test_generate_site_data_command_builds_direct_downloads_without_publishing(tmp_path):
    service = DataReleaseService()
    python_path = tmp_path / "python"

    command = service._generate_site_data_command(
        python_path=python_path,
        download_url_base="https://raw.example/data/main",
    )

    assert command == [
        str(python_path),
        "scripts/generate_site_data.py",
        "--direct-download-url-base",
        "https://raw.example/data/main",
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
async def test_release_preflight_checks_direct_download_repo_when_enabled(
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

    worktree_paths = []

    async def git_status_paths():
        return list(worktree_paths)

    async def no_paths():
        return []

    async def command_available(*_args, **_kwargs):
        return {"returncode": 0, "stdout": "4.0.0"}

    async def cloudflare_disabled(*_args, **_kwargs):
        return {"payload": {}, "blockers": []}

    check_calls = 0

    async def direct_repo_check(*_args, **_kwargs):
        nonlocal check_calls
        check_calls += 1
        return {
            "payload": {
                "repo_url": "git@example/data.git",
                "branch": "main",
                "raw_base_url": "https://raw.example/data/main",
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
    monkeypatch.setattr(service, "_git_status_paths", git_status_paths)
    monkeypatch.setattr(service, "_run_capture", command_available)
    monkeypatch.setattr(service, "_cloudflare_check", cloudflare_disabled)
    monkeypatch.setattr(service, "_tracked_generated_paths", no_paths)
    monkeypatch.setattr(service, "_download_repo_check", direct_repo_check)
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
    assert checks["git"]["branch"] == "main"
    assert checks["git"]["read_access_ok"] is True
    assert checks["git"]["write_access_ok"] is True
    assert checks["repository_boundary"]["enforced"] is True

    worktree_paths.append("CHANGELOG.md")
    dirty_checks = await service.integration_checks("site-release")

    assert dirty_checks["overall_ready"] is False
    assert dirty_checks["git"]["dirty_blocking_paths"] == ["CHANGELOG.md"]
    assert any("worktree is not clean" in item for item in dirty_checks["blockers"])


@pytest.mark.asyncio
async def test_release_preflight_does_not_invent_production_branch_blocker_on_cloudflare_api_failure(
    monkeypatch, tmp_path
):
    service = DataReleaseService()
    python_path = tmp_path / "python"
    python_path.touch()
    job = release_module.DataReleaseJobConfig(
        job_id="site-release",
        name="Site Release",
        include_git_push=True,
        include_cloudflare_deploy=True,
    )

    async def no_op():
        return None

    async def load_jobs():
        return [job]

    async def no_paths():
        return []

    async def command_available(*_args, **_kwargs):
        return {"returncode": 0, "stdout": "4.0.0"}

    async def clean_worktree():
        return []

    async def direct_repo_check(*_args, **_kwargs):
        return {
            "payload": {
                "repo_url": "git@example/data.git",
                "branch": "main",
                "raw_base_url": "https://raw.example/data/main",
                "read_access_ok": True,
                "write_access_ok": True,
                "read_check_output": "ok",
                "write_check_output": "ok",
                "ssh_transport": "default",
            },
            "blockers": [],
        }

    async def cloudflare_eof(*_args, **_kwargs):
        return {
            "payload": {
                "project_access_ok": False,
                "production_branch": None,
                "error": "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING]>",
            },
            "blockers": ["Cloudflare Pages project check failed."],
        }

    async def raw_archive_check():
        return {"payload": {"enabled": False}, "blockers": []}

    monkeypatch.setattr(service, "ensure_storage", no_op)
    monkeypatch.setattr(service, "load_jobs", load_jobs)
    monkeypatch.setattr(service, "_git_status_paths", clean_worktree)
    monkeypatch.setattr(service, "_run_capture", command_available)
    monkeypatch.setattr(service, "_cloudflare_check", cloudflare_eof)
    monkeypatch.setattr(service, "_tracked_generated_paths", no_paths)
    monkeypatch.setattr(service, "_download_repo_check", direct_repo_check)
    monkeypatch.setattr(service, "_raw_archive_check", raw_archive_check)
    monkeypatch.setattr(service, "_download_repo_url", lambda: "git@example/data.git")
    monkeypatch.setattr(
        service,
        "_download_repo_raw_base",
        lambda _job: "https://data.example/releases/test-release",
    )
    monkeypatch.setattr(service, "_python_executable", lambda: python_path)

    checks = await service.integration_checks("site-release")

    assert checks["overall_ready"] is False
    assert checks["blockers"] == ["Cloudflare Pages project check failed."]


def test_download_publish_command_uses_incremental_partition_publisher(tmp_path):
    service = DataReleaseService()
    command = service._publish_download_repo_command(
        python_path=tmp_path / "python",
        repo_url="git@example/data.git",
        commit_message="publish direct downloads",
    )

    assert command[1] == "scripts/publish_download_repo.py"
    assert "--push" in command
    assert "--source-dir" in command
    assert command[command.index("--branch") + 1] == "main"
    assert "--raw-base-url" not in command


def test_download_publish_command_honors_resolved_download_branch(tmp_path):
    service = DataReleaseService()

    command = service._publish_download_repo_command(
        python_path=tmp_path / "python",
        repo_url="git@example/data.git",
        commit_message="publish direct downloads",
        branch="release-data",
    )

    assert command[command.index("--branch") + 1] == "release-data"


def test_raw_archive_publish_command_uses_dedicated_incremental_publisher(
    monkeypatch, tmp_path
):
    service = DataReleaseService()
    config = SimpleNamespace(
        raw_data_dir=tmp_path / "raw",
        raw_archive=SimpleNamespace(
            enabled=True,
            repo_url="git@example/raw-archive.git",
            branch="archive-main",
            repository_dir=tmp_path / "raw-git-archive",
            git_timeout_seconds=1800,
        ),
    )
    monkeypatch.setattr(release_module, "get_config", lambda: config)
    monkeypatch.setattr(
        release_module.system_settings_service,
        "github_runtime",
        lambda: {
            "raw_archive_enabled": True,
            "raw_archive_repo_url": config.raw_archive.repo_url,
            "raw_archive_branch": config.raw_archive.branch,
        },
    )

    command = service._publish_raw_archive_command(python_path=tmp_path / "python")

    assert command[1] == "scripts/publish_raw_git_archive.py"
    assert command[command.index("--source-dir") + 1] == str(config.raw_data_dir)
    assert command[command.index("--repo-url") + 1] == config.raw_archive.repo_url
    assert "--chunk-mib" not in command
    assert "--commit-batch-mib" not in command
    assert "--zstd-level" not in command
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
            branch="main",
            repository_dir=tmp_path / "archive",
            git_timeout_seconds=1800,
        ),
    )
    monkeypatch.setattr(release_module, "get_config", lambda: config)
    monkeypatch.setattr(
        release_module.system_settings_service,
        "github_runtime",
        lambda: {
            "raw_archive_enabled": False,
            "raw_archive_repo_url": "",
            "raw_archive_branch": "main",
        },
    )

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


@pytest.mark.asyncio
async def test_site_release_identity_prefers_matching_deployment_branch(monkeypatch):
    service = DataReleaseService()
    commit = "a" * 40

    async def git_head_full():
        return commit

    async def current_git_branch():
        return "development"

    async def git_branch_commit(branch):
        assert branch == "master"
        return commit

    async def git_status_paths():
        return []

    monkeypatch.setattr(service, "_git_head_full", git_head_full)
    monkeypatch.setattr(service, "_current_git_branch", current_git_branch)
    monkeypatch.setattr(service, "_git_branch_commit", git_branch_commit)
    monkeypatch.setattr(service, "_git_status_paths", git_status_paths)

    identity = await service._site_release_identity("master")

    assert identity["source_branch"] == "master"
    assert identity["deployment_branch"] == "master"
    assert identity["source_commit"] == commit
    assert identity["commit_dirty"] is False


@pytest.mark.asyncio
async def test_site_release_identity_keeps_checkout_branch_when_deployment_differs(monkeypatch):
    service = DataReleaseService()

    async def git_status_paths():
        return []

    monkeypatch.setattr(service, "_git_status_paths", git_status_paths)

    async def git_head_full():
        return "a" * 40

    async def current_git_branch():
        return "feature/release-preview"

    async def git_branch_commit(_branch):
        return "b" * 40

    monkeypatch.setattr(service, "_git_head_full", git_head_full)
    monkeypatch.setattr(service, "_current_git_branch", current_git_branch)
    monkeypatch.setattr(service, "_git_branch_commit", git_branch_commit)

    identity = await service._site_release_identity("master")

    assert identity["source_branch"] == "feature/release-preview"
    assert identity["deployment_branch"] == "master"
