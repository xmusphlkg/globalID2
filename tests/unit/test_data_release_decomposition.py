"""Unit tests verifying DataReleaseService decomposition, re-exports, and pure helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.services import data_release_service as release_module
from src.services.data_release import environment as release_env
from src.services.data_release import models as release_models
from src.services.data_release_service import (
    DataReleaseJobConfig,
    DataReleaseJobState,
    DataReleaseService,
)


class _DummyStatus(Enum):
    RUNNING = "running"


def test_reexport_parity_between_service_and_submodules():
    assert release_module.DataReleaseJobConfig is release_models.DataReleaseJobConfig
    assert release_module.DataReleaseJobState is release_models.DataReleaseJobState
    assert release_module._data_line is release_models._data_line
    assert release_module._data_enabled is release_models._data_enabled
    assert release_module._parked_json_reservation is release_models._parked_json_reservation
    assert release_module._iso is release_models._iso
    assert release_module._coerce_status is release_models._coerce_status
    assert release_module._as_aware_utc is release_models._as_aware_utc

    assert release_module.GENERATED_DATA_PATHS is release_env.GENERATED_DATA_PATHS
    assert release_module.SITE_VISUAL_MODULE_PREFIXES is release_env.SITE_VISUAL_MODULE_PREFIXES
    assert release_module.RELEASE_RUNTIME_MUTABLE_PATHS is release_env.RELEASE_RUNTIME_MUTABLE_PATHS
    assert release_module.AUTO_RELEASE_TASK_TYPES is release_env.AUTO_RELEASE_TASK_TYPES
    assert release_module.LITERATURE_RELEASE_TASK_TYPES is release_env.LITERATURE_RELEASE_TASK_TYPES
    assert release_module.GITHUB_SSH_PREFIXES is release_env.GITHUB_SSH_PREFIXES
    assert release_module.SUBSCRIPTION_SYNC_FALSE_VALUES is release_env.SUBSCRIPTION_SYNC_FALSE_VALUES
    assert release_module.SUBSCRIPTION_SYNC_STRICT_VALUES is release_env.SUBSCRIPTION_SYNC_STRICT_VALUES
    assert hasattr(release_module, "signal")


def test_release_conflict_clause_wrapper_honors_service_monkeypatch(monkeypatch):
    seen: list[str] = []

    def fake_paused(line: str) -> bool:
        seen.append(line)
        return line == "situation"

    monkeypatch.setattr(release_module, "is_automatic_json_paused", fake_paused)
    clause = release_module._release_conflict_clause({"data_only": True, "data_line": "surveillance"})

    assert seen == ["situation"]
    assert "retrying" in str(clause.compile(compile_kwargs={"literal_binds": True})).lower()


def test_models_scalar_helpers():
    assert release_models._data_line({}) == "surveillance"
    assert release_models._data_line({"data_line": "situation"}) == "situation"
    assert release_models._data_line({"data_line": "literature"}) == "literature"
    with pytest.raises(ValueError, match="Unknown JSON publication line"):
        release_models._data_line({"data_line": "unknown"})

    cfg = SimpleNamespace(surveillance_json_enabled=True, situation_json_enabled=False)
    assert release_models._data_enabled(cfg, {}) is True
    assert release_models._data_enabled(cfg, {"data_line": "situation"}) is False

    assert release_models._iso(None) is None
    dt = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert release_models._iso(dt) == "2026-07-01T12:00:00+00:00"

    assert release_models._coerce_status(_DummyStatus.RUNNING) == "running"
    assert release_models._coerce_status(None, fallback="queued") == "queued"
    assert release_models._coerce_status("done") == "done"

    assert release_models._as_aware_utc(None) is None
    naive = datetime(2026, 7, 1, 8, 30)
    assert release_models._as_aware_utc(naive) == datetime(2026, 7, 1, 8, 30, tzinfo=ZoneInfo("UTC"))
    tokyo = datetime(2026, 7, 1, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert release_models._as_aware_utc(tokyo) == datetime(2026, 7, 1, 0, 0, tzinfo=ZoneInfo("UTC"))


def test_compute_next_run_interval_daily_and_none():
    base = datetime(2026, 7, 1, 1, 30, tzinfo=ZoneInfo("UTC"))

    interval_job = DataReleaseJobConfig(job_id="j1", name="J1", interval_minutes=15, daily_time=None)
    assert release_models.compute_next_run(interval_job, now=base) == datetime(
        2026, 7, 1, 1, 45, tzinfo=ZoneInfo("UTC")
    )

    daily_future = DataReleaseJobConfig(job_id="j2", name="J2", daily_time="02:00", timezone="UTC")
    assert release_models.compute_next_run(daily_future, now=base) == datetime(
        2026, 7, 1, 2, 0, tzinfo=ZoneInfo("UTC")
    )

    daily_passed = DataReleaseJobConfig(job_id="j3", name="J3", daily_time="01:00", timezone="UTC")
    assert release_models.compute_next_run(daily_passed, now=base) == datetime(
        2026, 7, 2, 1, 0, tzinfo=ZoneInfo("UTC")
    )

    unscheduled = DataReleaseJobConfig(job_id="j4", name="J4", interval_minutes=None, daily_time=None)
    assert release_models.compute_next_run(unscheduled, now=base) is None

    # Service delegator matches pure function.
    service = DataReleaseService()
    assert service._compute_next_run(daily_future, now=base) == datetime(
        2026, 7, 1, 2, 0, tzinfo=ZoneInfo("UTC")
    )


def test_build_release_task_payload_modes():
    job = DataReleaseJobConfig(
        job_id="site-release",
        name="Site Data Release",
        include_git_push=True,
        include_cloudflare_deploy=True,
        github_branch="master",
    )
    archive = SimpleNamespace(enabled=True, repo_url="git@example/raw.git", branch="main")

    desc_code, payload_code = release_models.build_release_task_payload(
        job,
        default_timezone="UTC",
        branch="main",
        project_name="globalid",
        download_repo_url="git@example/data.git",
        download_url_base="https://raw.example/main",
        raw_archive=archive,
        generated_paths=("astro-site/src/data/meta.json",),
        manual=True,
        trigger="manual",
        trigger_task_uuid=None,
        full=False,
        reports_only=False,
        code_only=True,
        data_only=False,
        data_line="surveillance",
        theme_only=False,
        situation_detached=False,
        literature_detached=False,
        content_only=False,
        incremental_country_codes=[],
        incremental_site=True,
        release_plan={},
        dirty_ids=[],
        pending_ids=None,
        marked_before="2026-07-01T00:00:00+00:00",
        suppress_subscription_dispatch=True,
        publication_plan={"execution_mode": "country_template", "country_codes": ["US"], "expected_generations": {"US": 2}},
        surveillance_rebuild_all=False,
    )
    assert "code/templates" in desc_code[0]
    assert payload_code["include_git_push"] is False
    assert payload_code["include_cloudflare_deploy"] is True
    assert payload_code["suppress_subscription_dispatch"] is True
    assert payload_code["country_template_only"] is True
    assert payload_code["country_template_codes"] == ["US"]

    desc_theme, payload_theme = release_models.build_release_task_payload(
        job,
        default_timezone="UTC",
        branch="main",
        project_name="globalid",
        download_repo_url="git@example/data.git",
        download_url_base="https://raw.example/main",
        raw_archive=archive,
        generated_paths=(),
        manual=True,
        trigger="manual",
        trigger_task_uuid=None,
        full=False,
        reports_only=False,
        code_only=False,
        data_only=False,
        data_line="surveillance",
        theme_only=True,
        situation_detached=True,
        literature_detached=True,
        content_only=True,
        incremental_country_codes=[],
        incremental_site=True,
        release_plan={},
        dirty_ids=[1, 2],
        pending_ids=None,
        marked_before="2026-07-01T00:00:00+00:00",
        suppress_subscription_dispatch=False,
        publication_plan=None,
        surveillance_rebuild_all=False,
    )
    assert "theme candidate" in desc_theme[0]
    assert payload_theme["theme_only"] is True
    assert payload_theme["include_cloudflare_deploy"] is False
    assert payload_theme["publish_dirty_ids"] == []
    assert payload_theme["raw_archive_enabled"] is False


def test_environment_normalization_and_commit_message():
    job = DataReleaseJobConfig(
        job_id="site-release",
        name="Site",
        commit_message_template="release {job_id} on {branch} ({date})",
    )
    fixed = datetime(2026, 7, 15, 10, 20, 30, tzinfo=ZoneInfo("UTC"))
    assert (
        release_env.render_commit_message(
            job,
            branch="main",
            tz=ZoneInfo("UTC"),
            default_template="default {job_id}",
            now=fixed,
        )
        == "release site-release on main (2026-07-15)"
    )

    bad_job = DataReleaseJobConfig(job_id="site-release", name="Site", commit_message_template="{missing_key}")
    assert (
        release_env.render_commit_message(
            bad_job,
            branch="main",
            tz=ZoneInfo("UTC"),
            default_template="fallback {job_id} {branch}",
            now=fixed,
        )
        == "fallback site-release main"
    )

    fallback_called = False

    def fallback() -> str:
        nonlocal fallback_called
        fallback_called = True
        return "master"

    assert (
        release_env.normalize_download_repo_branch(
            None,
            default_branch="data-v2",
            legacy_branch="legacy",
            fallback_branch=fallback,
        )
        == "data-v2"
    )
    assert fallback_called is False

    assert (
        release_env.normalize_download_repo_branch(
            "master",
            default_branch="data-v2",
            legacy_branch="",
            fallback_branch=fallback,
        )
        == "data-v2"
    )
    assert fallback_called is True

    assert (
        release_env.normalize_download_repo_branch(
            "custom-branch",
            default_branch="data-v2",
            legacy_branch="master",
            fallback_branch=fallback,
        )
        == "custom-branch"
    )

    assert release_env.normalize_cloudflare_project_name(None, default_project_name="globalid-prod") == "globalid-prod"
    assert release_env.normalize_cloudflare_project_name("globalid", default_project_name="globalid-prod") == "globalid-prod"
    assert release_env.normalize_cloudflare_project_name("custom-proj", default_project_name="globalid-prod") == "custom-proj"


def test_environment_r2_configs_and_enabled_flags():
    release_cfg = SimpleNamespace(
        r2_account_id="",
        r2_access_key_id="ak-123",
        r2_secret_access_key="sk-456",
        r2_papers_bucket="papers-bucket",
        r2_papers_public_base_url="",
        r2_reports_bucket="reports-bucket",
        r2_reports_public_base_url="",
        r2_endpoint_url="",
        r2_papers_enabled=True,
        r2_reports_enabled=True,
    )
    papers = release_env.resolve_r2_papers_config(release_cfg, cloudflare_account_id="acct-999")
    assert papers is not None
    assert papers.account_id == "acct-999"
    assert papers.bucket == "papers-bucket"
    assert papers.public_base_url == release_env.DEFAULT_PAPERS_ORIGIN

    reports = release_env.resolve_r2_reports_config(release_cfg, cloudflare_account_id="acct-999")
    assert reports is not None
    assert reports.bucket == "reports-bucket"
    assert reports.manifest_key == "reports-manifest.json"
    assert reports.public_base_url == release_env.DEFAULT_REPORTS_ORIGIN

    deploy_on = DataReleaseJobConfig(job_id="a", name="A", include_cloudflare_deploy=True)
    deploy_off = DataReleaseJobConfig(job_id="b", name="B", include_cloudflare_deploy=False)
    assert release_env.r2_papers_enabled(release_cfg, deploy_on) is True
    assert release_env.r2_papers_enabled(release_cfg, deploy_off) is False
    assert release_env.r2_reports_enabled(release_cfg, deploy_on) is True
    assert release_env.r2_reports_enabled(release_cfg, deploy_off) is False

    missing_papers_check = release_env.r2_papers_check(None, probe=False)
    assert len(missing_papers_check["blockers"]) == 1
    assert "R2 papers credentials" in missing_papers_check["blockers"][0]

    missing_reports_check = release_env.r2_reports_check(None, probe=False)
    assert missing_reports_check["payload"]["public_base_url"] == release_env.DEFAULT_REPORTS_ORIGIN
    assert "R2 reports credentials" in missing_reports_check["blockers"][0]


@pytest.mark.asyncio
async def test_environment_git_helpers_and_manifest(tmp_path):
    async def fake_run_capture(cmd, *, cwd):
        assert cwd == tmp_path
        if cmd[:2] == ["git", "symbolic-ref"]:
            return {"returncode": 0, "stdout": "feature/clean\n"}
        if cmd[:2] == ["git", "rev-parse"] and cmd[2] == "HEAD":
            return {"returncode": 0, "stdout": ("a" * 40) + "\n"}
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            if cmd[3] == "refs/remotes/origin/master":
                return {"returncode": 0, "stdout": ("a" * 40) + "\n"}
            return {"returncode": 1, "stdout": ""}
        raise AssertionError(f"Unexpected cmd: {cmd}")

    assert (
        await release_env.current_git_branch(
            root_dir=tmp_path,
            run_capture_fn=fake_run_capture,
            fallback_fn=lambda: "detached",
        )
        == "feature/clean"
    )
    assert await release_env.git_head_full(root_dir=tmp_path, run_capture_fn=fake_run_capture) == "a" * 40
    assert (
        await release_env.git_branch_commit("master", root_dir=tmp_path, run_capture_fn=fake_run_capture)
        == "a" * 40
    )

    identity = await release_env.site_release_identity(
        "master",
        git_head_full_fn=lambda: release_env.git_head_full(root_dir=tmp_path, run_capture_fn=fake_run_capture),
        current_git_branch_fn=lambda: release_env.current_git_branch(
            root_dir=tmp_path,
            run_capture_fn=fake_run_capture,
            fallback_fn=lambda: "detached",
        ),
        git_branch_commit_fn=lambda b: release_env.git_branch_commit(
            b, root_dir=tmp_path, run_capture_fn=fake_run_capture
        ),
        git_status_paths_fn=_async_empty_list,
        now=datetime(2026, 7, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC")),
    )
    assert identity["source_branch"] == "master"
    assert identity["release_id"] == "20260701T120000Z-" + ("a" * 12)
    assert identity["commit_dirty"] is False

    astro_dir = tmp_path / "astro-site"
    with pytest.raises(RuntimeError, match="Astro build output is missing"):
        release_env.write_site_release_manifest(identity, astro_dir=astro_dir)

    assets_dir = astro_dir / "dist" / "_astro"
    assets_dir.mkdir(parents=True)
    (assets_dir / "EpidemicCurve.123.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "ChartFrame.456.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "other.789.js").write_text("export {}", encoding="utf-8")

    manifest = release_env.write_site_release_manifest(identity, astro_dir=astro_dir)
    assert manifest["visual_modules"] == ["ChartFrame.456.js", "EpidemicCurve.123.js"]
    written = json.loads((astro_dir / "dist" / "release.json").read_text(encoding="utf-8"))
    assert written == manifest


async def _async_empty_list() -> list[str]:
    return []


def test_environment_subscription_plan_pages_budget_and_integration_payload(tmp_path):
    script = tmp_path / "wrangler-env.sh"
    env_map = {
        "SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE": "auto",
        "SUBSCRIPTIONS__D1_DATABASE_NAME": "db",
        "SUBSCRIPTIONS__D1_DATABASE_ID": "id",
        "SUBSCRIPTIONS__REMOTE_ENVIRONMENT": "production",
        "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC": "production",
    }
    env_lookup = lambda name, default="": env_map.get(name, default)

    ok, reason = release_env.subscription_options_sync_plan(
        env_value=env_lookup,
        subscriptions_script=script,
        cloudflare_api_token="tok",
    )
    assert ok is False
    assert "missing" in reason

    script.touch()
    ok, reason = release_env.subscription_options_sync_plan(
        env_value=env_lookup,
        subscriptions_script=script,
        cloudflare_api_token="tok",
    )
    assert ok is True
    assert release_env.subscription_options_sync_is_strict(env_value=env_lookup) is False

    astro_dir = tmp_path / "astro-site"
    (astro_dir / "dist").mkdir(parents=True)
    (astro_dir / "dist" / "index.html").write_text("ok", encoding="utf-8")
    budget = release_env.pages_budget_snapshot(
        SimpleNamespace(pages_file_budget=10, pages_file_warn=5),
        astro_dir=astro_dir,
    )
    assert budget == {
        "dist_present": True,
        "file_count": 1,
        "budget": 10,
        "warn_at": 5,
        "ok": True,
        "warn": False,
    }

    job = DataReleaseJobConfig(
        job_id="site",
        name="Site",
        include_git_push=True,
        include_cloudflare_deploy=True,
    )
    payload = release_env.assemble_integration_checks_payload(
        job,
        code_only=False,
        branch="main",
        download_repo={
            "payload": {
                "repo_url": "git@example/data.git",
                "raw_base_url": "https://raw.example/main",
                "read_access_ok": True,
                "write_access_ok": True,
                "read_check_output": "ok",
                "write_check_output": "ok",
                "ssh_transport": "default",
            },
            "blockers": [],
        },
        worktree_all=["configs/literature/weekly_ai_reviews.json"],
        worktree=[],
        runtime_mutable_worktree=["configs/literature/weekly_ai_reviews.json"],
        tracked_generated_paths=[],
        python_path=Path("/usr/bin/python3"),
        python_exists=True,
        situation_notices_check={"returncode": 0, "stdout": "ok"},
        wrangler_check={"returncode": 0, "stdout": "4.0.0"},
        cloudflare={
            "payload": {"project_access_ok": True, "production_branch": "master"},
            "blockers": [],
        },
        raw_archive={"payload": {"enabled": False, "independent": True}, "blockers": []},
        r2_enabled=True,
        r2_papers={"payload": {"bucket_access_ok": True, "bucket": "papers", "public_base_url": "https://papers.example"}, "blockers": []},
        r2_reports_enabled=True,
        r2_reports={"payload": {"bucket_access_ok": True, "bucket": "reports", "public_base_url": "https://reports.example"}, "blockers": []},
        pages_budget=budget,
        checked_at="2026-07-01T00:00:00+00:00",
    )
    assert payload["overall_ready"] is True
    assert payload["blockers"] == []
    assert payload["channels"]["pages_main"]["ready"] is True
    assert payload["channels"]["github_downloads"]["ready"] is True
    assert payload["channels"]["r2_papers"]["ready"] is True
    assert payload["channels"]["r2_reports"]["ready"] is True
    assert DataReleaseJobState().last_status == "idle"
