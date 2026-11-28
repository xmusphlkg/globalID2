"""Pure/isolated configuration, Git inspection, manifest, and preflight helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from src.core.data_share import get_data_share_repo_branch
from src.domain import TaskType
from src.services.data_release import checks as release_checks
from src.services.data_release.commands import (
    build_publish_papers_r2_command,
    build_split_papers_bundle_command,
    build_split_reports_bundle_command,
    build_verify_reports_host_command,
)
from src.services.data_release.models import DataReleaseJobConfig
from src.services.data_release.papers_bundle import (
    DEFAULT_MAIN_SITE_ORIGIN,
    DEFAULT_PAPERS_ORIGIN,
    count_files,
)
from src.services.data_release.papers_r2 import (
    R2PapersConfig,
    derive_r2_credentials_from_api_token,
    r2_preflight_payload,
)
from src.services.data_release.reports_bundle import DEFAULT_REPORTS_ORIGIN

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv is part of project requirements.
    dotenv_values = None


GENERATED_DATA_PATHS = (
    "astro-site/src/data/about.json",
    "astro-site/src/data/countries",
    "astro-site/src/data/disease-knowledge",
    "astro-site/src/data/disease-ontology.json",
    "astro-site/src/data/diseases",
    "astro-site/src/data/downloads.json",
    "astro-site/src/data/meta.json",
    "astro-site/src/data/reports",
    "astro-site/src/data/research",
    "astro-site/src/data/situation",
    "astro-site/src/data/_release-projection",
    "data/current",
    "data/raw",
)
SITE_VISUAL_MODULE_PREFIXES = (
    "ChartFrame.",
    "ComparisonTable.",
    "CompareWorkspace.",
    "CountrySeasonalityPicker.",
    "DiseaseCountryCurve.",
    "DiseaseCoverageMap.",
    "DiseaseHeatmap.",
    "DiseaseSeasonalityHeatmap.",
    "EpidemicCurve.",
)
# These files are atomically maintained by background quality automation and
# are part of the generated public-data snapshot, not source code.  They must
# not make an otherwise valid unattended release fail its clean-worktree gate.
# Keep this list deliberately narrow: unknown tracked changes remain blocking.
RELEASE_RUNTIME_MUTABLE_PATHS = (
    "configs/literature/weekly_ai_reviews.json",
)
AUTO_RELEASE_TASK_TYPES = (
    TaskType.CRAWL_DATA,
    TaskType.PROCESS_DATA,
    TaskType.GENERATE_REPORT,
    TaskType.SYNC_LITERATURE,
    TaskType.ENRICH_LITERATURE,
    TaskType.DISCOVER_LITERATURE_GAPS,
    TaskType.REFRESH_SITUATION_SOURCES,
)
LITERATURE_RELEASE_TASK_TYPES = {
    TaskType.SYNC_LITERATURE,
    TaskType.ENRICH_LITERATURE,
    TaskType.DISCOVER_LITERATURE_GAPS,
}
GITHUB_SSH_PREFIXES = ("git@github.com:", "ssh://git@github.com/")
SUBSCRIPTION_SYNC_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
SUBSCRIPTION_SYNC_STRICT_VALUES = {"1", "true", "yes", "on", "required", "strict", "force"}
LOGGED_COMMAND_CANCEL_POLL_SECONDS = 1.0
DEFAULT_LOGGED_COMMAND_TIMEOUT_SECONDS = 15 * 60
# A complete export can revalidate several gigabytes of partitioned downloads.
# Allow the full catalogue to finish under the worker's bounded memory budget.
GENERATE_SITE_DATA_TIMEOUT_SECONDS = 2 * 60 * 60
# The multilingual static site renders tens of thousands of country/disease
# pages. Keep the build guard finite, but allow a full production render to
# finish on release workers instead of aborting at the one-hour legacy limit.
ASTRO_BUILD_TIMEOUT_SECONDS = 2 * 60 * 60
CLOUDFLARE_DEPLOY_TIMEOUT_SECONDS = 15 * 60
SUBSCRIPTION_SYNC_TIMEOUT_SECONDS = 10 * 60
# The initial partitioned download publication can introduce tens of thousands
# of files and a multi-gigabyte Git pack. Keep this bounded, while giving the
# first full sync enough time to upload after the raw archive is published.
DOWNLOAD_PUBLISH_TIMEOUT_SECONDS = 60 * 60


def python_executable(root_dir: Path) -> Path:
    venv_python = root_dir / "venv" / "bin" / "python3"
    return venv_python if venv_python.exists() else Path(sys.executable)


def render_commit_message(
    job: DataReleaseJobConfig,
    *,
    branch: str,
    tz: ZoneInfo,
    default_template: str,
    now: Optional[datetime] = None,
) -> str:
    current = now.astimezone(tz) if now else datetime.now(tz)
    template = job.commit_message_template or default_template
    variables = {
        "timestamp": current.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "date": current.strftime("%Y-%m-%d"),
        "branch": branch,
        "job_id": job.job_id,
    }
    try:
        return template.format(**variables)
    except Exception:
        return default_template.format(**variables)


def normalize_download_repo_branch(
    branch: Optional[str],
    *,
    default_branch: Optional[str] = None,
    legacy_branch: Optional[str] = None,
    fallback_branch: Optional[Callable[[], str]] = None,
) -> str:
    resolved_default = default_branch if default_branch is not None else get_data_share_repo_branch()
    candidate = (branch or "").strip()
    resolved_legacy = (legacy_branch or "").strip()
    if not resolved_legacy:
        resolved_legacy = fallback_branch() if fallback_branch is not None else "detached"
    if not candidate:
        return resolved_default
    if candidate == resolved_legacy and resolved_default != resolved_legacy:
        return resolved_default
    return candidate


def normalize_cloudflare_project_name(
    project_name: Optional[str],
    *,
    default_project_name: Optional[str] = None,
) -> str:
    resolved_default = (default_project_name or "").strip() or "globalid"
    candidate = (project_name or "").strip()
    legacy_default_project_name = "globalid"
    if not candidate:
        return resolved_default
    if candidate == legacy_default_project_name and resolved_default != legacy_default_project_name:
        return resolved_default
    return candidate


def resolve_raw_archive_runtime(
    *,
    config: Any,
    github_runtime: dict[str, Any],
) -> SimpleNamespace:
    """Resolve raw-archive settings from the same control-center source as downloads."""
    archive = config.raw_archive
    return SimpleNamespace(
        enabled=bool(github_runtime["raw_archive_enabled"]),
        repo_url=str(github_runtime["raw_archive_repo_url"] or "").strip(),
        branch=str(github_runtime["raw_archive_branch"] or "").strip() or "main",
        repository_dir=archive.repository_dir,
        git_timeout_seconds=archive.git_timeout_seconds,
    )


def _resolve_r2_credentials(
    release_config: Any,
    *,
    cloudflare_account_id: str = "",
    cloudflare_api_token: str = "",
) -> tuple[str, str, str]:
    account_id = (
        str(release_config.r2_account_id or "").strip()
        or str(cloudflare_account_id or "").strip()
    )
    access_key_id = str(release_config.r2_access_key_id or "").strip()
    secret_access_key = str(release_config.r2_secret_access_key or "").strip()
    if not (access_key_id and secret_access_key):
        derived = derive_r2_credentials_from_api_token(cloudflare_api_token)
        if derived:
            access_key_id, secret_access_key = derived
    return account_id, access_key_id, secret_access_key


def resolve_r2_papers_config(
    release_config: Any,
    *,
    cloudflare_account_id: str = "",
    cloudflare_api_token: str = "",
) -> R2PapersConfig | None:
    account_id, access_key_id, secret_access_key = _resolve_r2_credentials(
        release_config,
        cloudflare_account_id=cloudflare_account_id,
        cloudflare_api_token=cloudflare_api_token,
    )
    return R2PapersConfig.from_mapping(
        {
            "account_id": account_id,
            "bucket": release_config.r2_papers_bucket,
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "public_base_url": release_config.r2_papers_public_base_url or DEFAULT_PAPERS_ORIGIN,
            "endpoint_url": release_config.r2_endpoint_url,
        }
    )


def resolve_r2_reports_config(
    release_config: Any,
    *,
    cloudflare_account_id: str = "",
    cloudflare_api_token: str = "",
) -> R2PapersConfig | None:
    account_id, access_key_id, secret_access_key = _resolve_r2_credentials(
        release_config,
        cloudflare_account_id=cloudflare_account_id,
        cloudflare_api_token=cloudflare_api_token,
    )
    return R2PapersConfig.from_mapping(
        {
            "account_id": account_id,
            "bucket": release_config.r2_reports_bucket,
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "public_base_url": release_config.r2_reports_public_base_url or DEFAULT_REPORTS_ORIGIN,
            "endpoint_url": release_config.r2_endpoint_url,
            "manifest_key": "reports-manifest.json",
        }
    )


def r2_papers_enabled(release_config: Any, job: DataReleaseJobConfig | None = None) -> bool:
    if not bool(release_config.r2_papers_enabled):
        return False
    if job is not None and not job.include_cloudflare_deploy:
        # Papers hosting is required whenever the public site deploys; when
        # Cloudflare is off, skip remote papers publish as well.
        return False
    return True


def r2_reports_enabled(release_config: Any, job: DataReleaseJobConfig | None = None) -> bool:
    if not bool(release_config.r2_reports_enabled):
        return False
    if job is not None and not job.include_cloudflare_deploy:
        return False
    return True


def r2_papers_check(r2_config: R2PapersConfig | None, *, probe: bool = True) -> dict[str, Any]:
    payload = r2_preflight_payload(r2_config, probe=probe)
    blockers: list[str] = []
    if not payload.get("credentials_present"):
        blockers.append(
            "R2 papers credentials are missing "
            "(R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / account id)."
        )
    elif not payload.get("bucket_access_ok"):
        detail = str(payload.get("error") or "bucket probe failed").strip()
        blockers.append(f"R2 papers bucket is not reachable: {detail}")
    return {"payload": payload, "blockers": blockers}


def r2_reports_check(r2_config: R2PapersConfig | None, *, probe: bool = True) -> dict[str, Any]:
    payload = r2_preflight_payload(r2_config, probe=probe)
    if not payload.get("configured"):
        payload["public_base_url"] = DEFAULT_REPORTS_ORIGIN
    blockers: list[str] = []
    if not payload.get("credentials_present"):
        blockers.append(
            "R2 reports credentials are missing (R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / account id)."
        )
    elif not payload.get("bucket_access_ok"):
        detail = str(payload.get("error") or "bucket probe failed").strip()
        blockers.append(f"R2 reports bucket is not reachable: {detail}")
    return {"payload": payload, "blockers": blockers}


def pages_budget_snapshot(release_config: Any, *, astro_dir: Path) -> dict[str, Any]:
    dist_dir = astro_dir / "dist"
    file_count = count_files(dist_dir) if dist_dir.is_dir() else None
    budget = int(release_config.pages_file_budget)
    warn_at = int(release_config.pages_file_warn)
    return {
        "dist_present": dist_dir.is_dir(),
        "file_count": file_count,
        "budget": budget,
        "warn_at": warn_at,
        "ok": None if file_count is None else file_count < budget,
        "warn": None if file_count is None else file_count >= warn_at,
    }


def split_papers_bundle_command(
    release_config: Any,
    *,
    python_path: Path,
    dist_dir: Path,
    papers_dir: Path,
) -> list[str]:
    return build_split_papers_bundle_command(
        python_path=python_path,
        dist_dir=dist_dir,
        papers_dir=papers_dir,
        main_origin=str(release_config.main_site_origin or DEFAULT_MAIN_SITE_ORIGIN),
        papers_origin=str(release_config.r2_papers_public_base_url or DEFAULT_PAPERS_ORIGIN),
        pages_file_budget=int(release_config.pages_file_budget),
        pages_file_warn=int(release_config.pages_file_warn),
        # All long-tail research pages are published on the Papers host.
        # The literature flag controls data publication, not URL ownership.
        research_on_main=False,
    )


def publish_papers_r2_command(
    *,
    python_path: Path,
    source_dir: Path,
    astro_dir: Path,
    include_custom_astro_paths: bool = False,
) -> list[str]:
    command = build_publish_papers_r2_command(
        python_path=python_path,
        source_dir=source_dir,
        articles_dir=astro_dir / "src" / "data" / "research" / "articles",
    )
    if include_custom_astro_paths:
        command.extend(
            [
                "--site-dist",
                str(astro_dir / "dist"),
                "--research-source",
                str(astro_dir / "public/site-data/research"),
            ]
        )
    return command


def split_reports_bundle_command(
    release_config: Any,
    *,
    python_path: Path,
    dist_dir: Path,
    reports_dir: Path,
) -> list[str]:
    return build_split_reports_bundle_command(
        python_path=python_path,
        dist_dir=dist_dir,
        reports_dir=reports_dir,
        main_origin=str(release_config.main_site_origin or DEFAULT_MAIN_SITE_ORIGIN),
        reports_origin=str(release_config.r2_reports_public_base_url or DEFAULT_REPORTS_ORIGIN),
        pages_file_budget=int(release_config.pages_file_budget),
        pages_file_warn=int(release_config.pages_file_warn),
    )


def verify_reports_host_command(
    release_config: Any,
    *,
    python_path: Path,
    reports_dir: Path,
) -> list[str]:
    return build_verify_reports_host_command(
        python_path=python_path,
        reports_dir=reports_dir,
        reports_origin=str(release_config.r2_reports_public_base_url or DEFAULT_REPORTS_ORIGIN),
    )


def current_git_branch_fallback(*, root_dir: Path) -> str:
    branch = os.getenv("DATA_RELEASE_GIT_BRANCH", "").strip()
    if branch:
        return branch
    try:
        # In a linked worktree .git is a gitdir pointer file. Ask Git to
        # resolve it; reading .git/HEAD also truncated names with slashes.
        result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "detached"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "detached"


async def current_git_branch(
    *,
    root_dir: Path,
    run_capture_fn: Callable[..., Awaitable[dict[str, Any]]],
    fallback_fn: Callable[[], str],
) -> str:
    result = await run_capture_fn(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=root_dir,
    )
    branch = result["stdout"].strip() if result["returncode"] == 0 else ""
    if branch and branch != "HEAD":
        return branch
    return fallback_fn()


async def git_head_full(
    *,
    root_dir: Path,
    run_capture_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> str:
    result = await run_capture_fn(["git", "rev-parse", "HEAD"], cwd=root_dir)
    if result["returncode"] != 0:
        return ""
    return result["stdout"].strip()


async def git_branch_commit(
    branch: str,
    *,
    root_dir: Path,
    run_capture_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> str:
    """Resolve a local or origin branch without changing the working tree."""
    normalized = branch.strip()
    if not normalized:
        return ""
    for ref in (f"refs/remotes/origin/{normalized}", f"refs/heads/{normalized}"):
        result = await run_capture_fn(
            ["git", "rev-parse", "--verify", ref],
            cwd=root_dir,
        )
        if result["returncode"] == 0 and result["stdout"].strip():
            return result["stdout"].strip()
    return ""


async def site_release_identity(
    deployment_branch: str,
    *,
    git_head_full_fn: Callable[[], Awaitable[str]],
    current_git_branch_fn: Callable[[], Awaitable[str]],
    git_branch_commit_fn: Callable[[str], Awaitable[str]],
    git_status_paths_fn: Callable[[], Awaitable[list[str]]],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    source_commit = await git_head_full_fn()
    source_branch = await current_git_branch_fn()
    deployment_commit = await git_branch_commit_fn(deployment_branch)
    if source_commit and deployment_commit == source_commit:
        source_branch = deployment_branch
    current_utc = now.astimezone(ZoneInfo("UTC")) if now else datetime.now(ZoneInfo("UTC"))
    built_at = current_utc.isoformat()
    short_commit = source_commit[:12] if source_commit else "unknown"
    release_id = f"{current_utc.strftime('%Y%m%dT%H%M%SZ')}-{short_commit}"
    return {
        "release_id": release_id,
        "built_at": built_at,
        "source_branch": source_branch or "detached",
        "source_commit": source_commit or "unknown",
        "deployment_branch": deployment_branch,
        # Keep the provenance flag truthful: runtime-mutated registries do
        # not block the release, but they are still changes relative to
        # the source commit and must be surfaced to the deployment system.
        "commit_dirty": bool(await git_status_paths_fn()),
    }


def write_site_release_manifest(
    identity: dict[str, Any],
    *,
    astro_dir: Path,
    visual_module_prefixes: tuple[str, ...] = SITE_VISUAL_MODULE_PREFIXES,
) -> dict[str, Any]:
    dist_dir = astro_dir / "dist"
    if not dist_dir.is_dir():
        raise RuntimeError(f"Astro build output is missing: {dist_dir}")

    assets_dir = dist_dir / "_astro"
    visual_modules = sorted(
        path.name
        for path in assets_dir.glob("*.js")
        if path.is_file() and path.name.startswith(visual_module_prefixes)
    )
    payload = {
        **identity,
        "visual_modules": visual_modules,
    }
    from src.generation.site_data_writer import _write_if_changed

    _write_if_changed(
        dist_dir / "release.json",
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return payload


def read_env_file_values(env_path: Path) -> dict[str, str]:
    if dotenv_values is None or not env_path.exists():
        return {}
    values = dotenv_values(env_path)
    return {key: str(value) for key, value in values.items() if value is not None}


def resolve_env_value(
    name: str,
    *,
    env_file_values: dict[str, str],
    default: str = "",
) -> str:
    return (os.getenv(name) or env_file_values.get(name) or default).strip()


def subscription_options_sync_plan(
    *,
    env_value: Callable[..., str],
    subscriptions_script: Path,
    cloudflare_api_token: str,
) -> tuple[bool, str]:
    raw = env_value("SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE", "auto").lower()
    if raw in SUBSCRIPTION_SYNC_FALSE_VALUES:
        return False, "SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE is disabled."
    if not subscriptions_script.exists():
        return False, f"Subscription helper script is missing: {subscriptions_script}"

    missing = [
        name
        for name in ("SUBSCRIPTIONS__D1_DATABASE_NAME", "SUBSCRIPTIONS__D1_DATABASE_ID")
        if not env_value(name)
    ]
    target_environment = env_value("SUBSCRIPTIONS__REMOTE_ENVIRONMENT").lower()
    if target_environment not in {"staging", "production"}:
        missing.append("SUBSCRIPTIONS__REMOTE_ENVIRONMENT")
    if env_value("SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC") != target_environment:
        missing.append("SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC")
    if not cloudflare_api_token:
        missing.append("CLOUDFLARE_API_TOKEN")
    if missing and raw not in SUBSCRIPTION_SYNC_STRICT_VALUES:
        return (
            False,
            "Subscription D1 sync is in auto mode and skipped because settings are missing: "
            + ", ".join(missing),
        )

    return True, "Subscription D1 option sync is enabled."


def subscription_options_sync_is_strict(*, env_value: Callable[..., str]) -> bool:
    raw = env_value("SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE", "auto").lower()
    return raw in SUBSCRIPTION_SYNC_STRICT_VALUES


async def sync_subscription_options_if_needed(
    service: Any,
    task_uuid: str,
    *,
    job_id: str,
    subscriptions_script: Path,
    root_dir: Path,
    task_manager: Any,
    logger: Any,
) -> bool:
    should_sync, sync_reason = service._subscription_options_sync_plan()
    metadata = {"event": "subscription_options_sync", "release_job_id": job_id}
    if not should_sync:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title="Subscription Options Sync Skipped",
            content=sync_reason,
            content_type="text",
            metadata={**metadata, "event": "subscription_options_sync_skip"},
        )
        return False

    try:
        await service._run_logged_command(
            task_uuid,
            title="Sync Subscription Options",
            cmd=[
                str(subscriptions_script),
                "sync-options-remote",
                service._env_value("SUBSCRIPTIONS__REMOTE_ENVIRONMENT").lower(),
            ],
            cwd=root_dir,
            env={
                "CI": "1",
                "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
                "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
                "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC": service._env_value(
                    "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC"
                ),
            },
            metadata=metadata,
            timeout_seconds=SUBSCRIPTION_SYNC_TIMEOUT_SECONDS,
        )
        return True
    except RuntimeError as exc:
        if service._subscription_options_sync_is_strict():
            raise
        message = (
            f"{sync_reason}\n\n"
            "Subscription option sync is running in auto mode, so this release will continue. "
            "Set SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE=true to make this step blocking.\n\n"
            f"{exc}"
        )
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="warning",
            title="Subscription Options Sync Failed",
            content=message,
            content_type="text",
            metadata={**metadata, "event": "subscription_options_sync_failed"},
        )
        logger.warning("Subscription options sync failed in auto mode; release will continue: {}", exc)
        return False


def assemble_integration_checks_payload(
    job: DataReleaseJobConfig,
    *,
    code_only: bool,
    branch: str,
    download_repo: dict[str, Any],
    worktree_all: list[str],
    worktree: list[str],
    runtime_mutable_worktree: list[str],
    tracked_generated_paths: list[str],
    python_path: Path,
    python_exists: bool,
    situation_notices_check: dict[str, Any],
    wrangler_check: dict[str, Any],
    cloudflare: dict[str, Any],
    raw_archive: dict[str, Any],
    r2_enabled: bool,
    r2_papers: dict[str, Any],
    r2_reports_enabled: bool,
    r2_reports: dict[str, Any],
    pages_budget: dict[str, Any],
    code_only_snapshot_error: Optional[str] = None,
    changelog_blocker: Optional[str] = None,
    checked_at: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble blockers and normalized integration-check payload from probe results."""
    blockers: list[str] = []
    if job.include_git_push:
        blockers.extend(download_repo["blockers"])
    if job.include_cloudflare_deploy and not job.include_git_push and not code_only:
        blockers.append(
            "Cloudflare deployment requires direct-download publication so the "
            "new site never references unpublished CSV/JSON/XLSX partitions."
        )
    if job.include_cloudflare_deploy and not r2_enabled:
        blockers.append(
            "Cloudflare deployment requires R2 papers publication so article "
            "pages are not left only on the Pages file budget."
        )
    if job.include_cloudflare_deploy and not r2_reports_enabled:
        blockers.append(
            "Cloudflare deployment requires the reports R2 channel so report archives are not left on Pages."
        )
    if r2_enabled:
        blockers.extend(r2_papers["blockers"])
    if r2_reports_enabled:
        blockers.extend(r2_reports["blockers"])
    if job.include_cloudflare_deploy:
        blockers.extend(cloudflare["blockers"])
    if code_only_snapshot_error:
        blockers.append(code_only_snapshot_error)
    # The independent archive job owns its own access checks and retries.
    if not python_exists:
        blockers.append(f"Python executable not found: {python_path}")
    elif situation_notices_check["returncode"] != 0:
        output = str(situation_notices_check.get("stdout") or "").strip()
        detail = output.splitlines()[-1] if output else "command returned no diagnostic output"
        blockers.append(f"Situation Room refresh command is unavailable: {detail}")
    if job.include_cloudflare_deploy and wrangler_check["returncode"] != 0:
        blockers.append("Wrangler CLI is unavailable.")
    if (
        job.include_cloudflare_deploy
        and cloudflare["payload"].get("project_access_ok")
        and not cloudflare["payload"].get("production_branch")
    ):
        blockers.append("Cloudflare Pages project has no production branch configured.")
    if tracked_generated_paths:
        blockers.append(
            "Generated data is still tracked by the code repository: "
            + ", ".join(tracked_generated_paths)
        )
    if changelog_blocker:
        blockers.append(changelog_blocker)

    return {
        "checked_at": checked_at or datetime.now(ZoneInfo("UTC")).isoformat(),
        "overall_ready": not blockers,
        "blockers": blockers,
        "git": {
            "env_var": "GITHUB_DATA_SHARE_REPO_URL",
            "repo_url": download_repo["payload"]["repo_url"],
            "branch": branch,
            "raw_base_url": download_repo["payload"]["raw_base_url"],
            "read_access_ok": download_repo["payload"]["read_access_ok"],
            "write_access_ok": download_repo["payload"]["write_access_ok"],
            "read_check_output": download_repo["payload"]["read_check_output"],
            "write_check_output": download_repo["payload"]["write_check_output"],
            "ssh_transport": download_repo["payload"].get("ssh_transport"),
            "require_clean_worktree": job.require_clean_worktree,
            "dirty_paths": worktree_all,
            "dirty_blocking_paths": worktree,
            "dirty_runtime_mutable_paths": runtime_mutable_worktree,
        },
        "cloudflare": cloudflare["payload"],
        "raw_archive": raw_archive["payload"],
        "r2_papers": r2_papers["payload"],
        "r2_reports": r2_reports["payload"],
        "pages_budget": pages_budget,
        "channels": {
            "pages_main": {
                "enabled": bool(job.include_cloudflare_deploy),
                "ready": bool(
                    job.include_cloudflare_deploy
                    and cloudflare["payload"].get("project_access_ok")
                    and not cloudflare["blockers"]
                ),
                "file_count": pages_budget.get("file_count"),
                "file_budget": pages_budget.get("budget"),
            },
            "github_downloads": {
                "enabled": bool(job.include_git_push and not code_only),
                "ready": bool(
                    job.include_git_push
                    and not code_only
                    and download_repo["payload"].get("read_access_ok")
                    and download_repo["payload"].get("write_access_ok")
                ),
                "repo_url": download_repo["payload"].get("repo_url"),
            },
            "r2_papers": {
                "enabled": bool(r2_enabled),
                "ready": bool(
                    r2_enabled and r2_papers["payload"].get("bucket_access_ok")
                ),
                "public_base_url": r2_papers["payload"].get("public_base_url"),
                "bucket": r2_papers["payload"].get("bucket"),
            },
            "r2_reports": {
                "enabled": bool(r2_reports_enabled),
                "ready": bool(
                    r2_reports_enabled and r2_reports["payload"].get("bucket_access_ok")
                ),
                "public_base_url": r2_reports["payload"].get("public_base_url"),
                "bucket": r2_reports["payload"].get("bucket"),
            },
        },
        "commands": {
            "python_path": str(python_path),
            "python_exists": python_exists,
            "wrangler_available": wrangler_check["returncode"] == 0,
            "wrangler_version": wrangler_check["stdout"].strip() or None,
        },
        "repository_boundary": {
            "generated_paths": list(GENERATED_DATA_PATHS),
            "tracked_paths": tracked_generated_paths,
            "enforced": not tracked_generated_paths,
            "runtime_mutable_paths": list(RELEASE_RUNTIME_MUTABLE_PATHS),
        },
    }


async def run_integration_checks(
    service: Any,
    job_id: str,
    *,
    code_only: bool = False,
    root_dir: Path,
    astro_dir: Path,
    missing_changelog_fn: Callable[..., list[str]],
    changelog_blocker_fn: Callable[[list[str]], str],
) -> dict[str, Any]:
    """Run all release preflight checks via service hooks and return the combined payload."""
    await service.ensure_storage()
    job = next((item for item in await service.load_jobs() if item.job_id == job_id), None)
    if job is None:
        raise ValueError(f"Data release job not found: {job_id}")

    branch = service._download_repo_branch(job)
    download_repo = (
        await service._download_repo_check(job)
        if job.include_git_push and not code_only
        else {
            "payload": {
                "repo_url": service._download_repo_url() or None,
                "branch": branch,
                "raw_base_url": service._download_repo_raw_base(job) or None,
                "read_access_ok": False,
                "write_access_ok": False,
                "read_check_output": "Skipped: direct download publication is disabled.",
                "write_check_output": "Skipped: direct download publication is disabled.",
                "ssh_transport": "disabled",
                "publisher_enabled": False,
            },
            "blockers": [],
        }
    )
    worktree_all = await service._git_status_paths()
    worktree = release_checks.release_blocking_worktree_paths(
        worktree_all,
        runtime_mutable_paths=RELEASE_RUNTIME_MUTABLE_PATHS,
    )
    runtime_mutable_worktree = [path for path in worktree_all if path not in worktree]
    tracked_generated_paths = await service._tracked_generated_paths()

    python_path = service._python_executable()
    python_exists = python_path.exists()
    if code_only:
        situation_notices_check = {"returncode": 0, "stdout": "Skipped for code-only release."}
    elif python_exists:
        situation_notices_check = await service._run_capture(
            [*service._update_situation_notices_command(python_path=python_path), "--help"],
            cwd=root_dir,
            timeout=60,
        )
    else:
        situation_notices_check = {
            "returncode": 127,
            "stdout": f"Python executable not found: {python_path}",
        }
    wrangler_check = await service._run_capture(
        ["npm", "exec", "--", "wrangler", "--version"],
        cwd=astro_dir,
        timeout=60,
    )
    cloudflare = await service._cloudflare_check(job.cloudflare_project_name)
    archive_config = service._raw_archive_runtime()
    raw_archive = {
        "payload": {
            "enabled": archive_config.enabled,
            "repo_url": archive_config.repo_url or None,
            "branch": archive_config.branch,
            "independent": True,
            "read_access_ok": None,
            "write_access_ok": None,
            "ssh_transport": "deferred",
            "read_check_output": "Checked by the independent raw archive task.",
            "write_check_output": "Checked by the independent raw archive task.",
        },
        "blockers": [],
    }
    r2_enabled = service._r2_papers_enabled(job)
    r2_papers = (
        service._r2_papers_check(probe=True)
        if r2_enabled
        else {
            "payload": {
                **r2_preflight_payload(None, probe=False),
                "enabled": False,
                "error": "R2 papers publish is disabled for this release job.",
            },
            "blockers": [],
        }
    )
    if r2_enabled:
        r2_papers["payload"]["enabled"] = True
    r2_reports_enabled = service._r2_reports_enabled(job)
    r2_reports = (
        service._r2_reports_check(probe=True)
        if r2_reports_enabled
        else {
            "payload": {
                **r2_preflight_payload(None, probe=False),
                "enabled": False,
                "public_base_url": DEFAULT_REPORTS_ORIGIN,
                "error": "R2 reports publish is disabled for this release job.",
            },
            "blockers": [],
        }
    )
    if r2_reports_enabled:
        r2_reports["payload"]["enabled"] = True
    pages_budget = service._pages_budget_snapshot()

    code_only_snapshot_error: Optional[str] = None
    if code_only:
        from src.services.data_release.stage_receipts import StageReceipts

        try:
            await asyncio.to_thread(
                StageReceipts(root_dir, "preflight", {}, {}).published_data_paths,
                [astro_dir / "src/data", astro_dir / "public/site-data"],
            )
        except RuntimeError as exc:
            code_only_snapshot_error = str(exc)

    changelog_blocker: Optional[str] = None
    try:
        missing_changelog_versions = missing_changelog_fn(
            markdown_path=root_dir / "CHANGELOG.md",
            typescript_path=astro_dir / "src" / "data" / "changelog.ts",
        )
    except OSError as exc:
        changelog_blocker = f"Public changelog could not be compared with CHANGELOG.md: {exc}"
    else:
        if missing_changelog_versions:
            changelog_blocker = changelog_blocker_fn(missing_changelog_versions)

    return assemble_integration_checks_payload(
        job,
        code_only=code_only,
        branch=branch,
        download_repo=download_repo,
        worktree_all=worktree_all,
        worktree=worktree,
        runtime_mutable_worktree=runtime_mutable_worktree,
        tracked_generated_paths=tracked_generated_paths,
        python_path=python_path,
        python_exists=python_exists,
        situation_notices_check=situation_notices_check,
        wrangler_check=wrangler_check,
        cloudflare=cloudflare,
        raw_archive=raw_archive,
        r2_enabled=r2_enabled,
        r2_papers=r2_papers,
        r2_reports_enabled=r2_reports_enabled,
        r2_reports=r2_reports,
        pages_budget=pages_budget,
        code_only_snapshot_error=code_only_snapshot_error,
        changelog_blocker=changelog_blocker,
    )
