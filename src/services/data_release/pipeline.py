"""Execution pipeline for a validated data release job."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SITUATION_ROOM_REFRESH_TIMEOUT_SECONDS = 30 * 60
# A large backfill can require many bounded Git pushes; keep the R2 timeout
# separate while allowing a full download snapshot to finish or resume.
DOWNLOAD_GIT_PUBLISH_TIMEOUT_SECONDS = 3 * 60 * 60


class ReleasePreflightError(RuntimeError):
    """A release preflight failure retaining integration diagnostics."""

    release_stage = "release_preflight"

    def __init__(self, checks: dict[str, Any]) -> None:
        self.checks = checks
        diagnostics = [str(item) for item in checks.get("blockers") or []]
        for section in ("cloudflare", "git", "raw_archive"):
            payload = checks.get(section) or {}
            for key in ("error", "read_check_output", "write_check_output"):
                value = str(payload.get(key) or "").strip()
                if value:
                    diagnostics.append(value)
        super().__init__("Release preflight failed: " + "; ".join(diagnostics))


class ReleaseVerificationError(RuntimeError):
    """Cloudflare production verification failure with stage context."""

    release_stage = "cloudflare_production_verification"


def _built_site_digest(directory: Path) -> str:
    """Hash deployable files, excluding release.json which carries this digest."""
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path != directory / "release.json"
    )
    if not files:
        raise RuntimeError(
            "Cloudflare deployment requires a nonempty Astro dist directory"
        )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleasePipelineRuntime:
    """External collaborators and stable release constants for one execution."""

    task_manager: Any
    get_config: Callable[[], Any]
    get_database: Callable[[], Any]
    task_model: Any
    root_dir: Path
    astro_dir: Path
    download_repo_branch: str
    raw_archive_branch: str
    generate_timeout_seconds: float
    astro_build_timeout_seconds: float
    download_publish_timeout_seconds: float
    cloudflare_deploy_timeout_seconds: float


async def _run_repository_publications(
    publications: list[tuple[str, Awaitable[None]]],
) -> set[str]:
    """Run isolated repository publishers concurrently and aggregate failures."""

    if not publications:
        return set()
    results = await asyncio.gather(
        *(publication for _name, publication in publications),
        return_exceptions=True,
    )
    completed: set[str] = set()
    failures: list[str] = []
    stage_errors: list[BaseException] = []
    for (name, _publication), result in zip(publications, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            failures.append(f"{name}: {result}")
            stage_errors.append(result)
        else:
            completed.add(name)
    if failures:
        # Prefer the first publisher's stage tag so transient R2/Git failures
        # stay eligible for the automatic release retry allow-list.
        primary = stage_errors[0]
        message = "Parallel repository publication failed:\n" + "\n".join(failures)
        stage = getattr(primary, "release_stage", None)
        if stage:
            from .process_runner import ReleaseCommandError

            raise ReleaseCommandError(
                message,
                release_stage=str(stage),
                title=str(getattr(primary, "title", None) or "Repository publication"),
                returncode=getattr(primary, "returncode", None),
                timed_out=bool(getattr(primary, "timed_out", False)),
            ) from primary
        raise RuntimeError(message)
    return completed


async def execute_release_task(
    service: Any, task: Any, *, runtime: ReleasePipelineRuntime
) -> dict[str, Any]:
    """Publish in place; upload errors retain artifacts without rolling them back."""
    if (getattr(task, "input_data", None) or {}).get("reports_only"):
        from .reports_pipeline import execute_reports_release
        return await execute_reports_release(service, task, runtime=runtime)
    import time

    release_started = time.monotonic()
    paths = [
        runtime.astro_dir / "src" / "data",
        runtime.astro_dir / "public" / "site-data",
        runtime.astro_dir / "dist",
        runtime.root_dir / "exports" / "site-downloads",
        runtime.root_dir / "exports" / "papers-site",
        runtime.root_dir / "exports" / "reports-site",
    ]
    from .artifact_checkpoint import ArtifactCheckpoint

    checkpoint = (
        ArtifactCheckpoint(runtime.root_dir, str(task.task_uuid), paths, dict(task.input_data or {}))
        if task is not None and hasattr(task, "input_data") else None
    )
    previous = runtime.root_dir / "exports" / "verified-pages-snapshot"
    try:
        result = await _execute_release_task(
            service, task, runtime=runtime, artifact_checkpoint=checkpoint,
            previous_site_dir=previous if previous.is_dir() else None,
        )
    except BaseException:
        # Keep the generated files and successful channel publications in place.
        # A retry can reuse their content hashes, Git receipt and deploy checkpoint.
        # Never move or restore the worktree after an external upload succeeded.
        if checkpoint is not None:
            await asyncio.to_thread(checkpoint.discard)
        raise
    if checkpoint is not None:
        await asyncio.to_thread(checkpoint.discard)
    backup_seconds = 0.0
    if task is not None and callable(getattr(runtime.task_manager, "add_workbook_entry", None)):
        timings = {
            "elapsed_seconds": round(time.monotonic() - release_started, 3),
            "backup_seconds": round(backup_seconds, 3),
            "target_seconds": 600,
        }
        timings["within_target"] = timings["elapsed_seconds"] <= timings["target_seconds"]
        result["release_timing"] = timings
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid, entry_type="info", title="Release Timing",
            content=json.dumps(timings), content_type="json",
            metadata={"event": "release_timing", **timings},
        )
    return result


async def _execute_release_task(
    service: Any, task: Any, *, runtime: ReleasePipelineRuntime,
    artifact_checkpoint: Any = None,
    previous_site_dir: Path | None = None,
) -> dict[str, Any]:
    input_data = dict(task.input_data or {})
    job_id = str(input_data.get("release_job_id") or "").strip()
    if not job_id:
        raise RuntimeError("Release task is missing release_job_id")
    job = next(
        (item for item in await service.load_jobs() if item.job_id == job_id), None
    )
    if job is None:
        raise RuntimeError(f"Release job not found: {job_id}")

    checks = await service.integration_checks(job.job_id)
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Release Preflight",
        content=json.dumps(checks, ensure_ascii=False, indent=2),
        content_type="json",
        metadata={"event": "release_preflight", "release_job_id": job.job_id},
    )
    if not checks["overall_ready"]:
        raise ReleasePreflightError(checks)

    tz = ZoneInfo(job.timezone or service._config().timezone)
    # Keep generation, publishing, and the release record on the exact same
    # data-repository branch.  A job may override the configured default.
    # Using the runtime default here while generation uses the job branch makes
    # the publisher reject the generated manifest's Raw URL base.
    branch = service._download_repo_branch(job)
    project_name = service._cloudflare_project_name(job.cloudflare_project_name)
    download_repo_url = service._download_repo_url()
    git_transport = str((checks.get("git") or {}).get("ssh_transport") or "default")
    git_env = service._build_git_env(
        download_repo_url,
        use_github_ssh_over_443=(git_transport == "github-ssh-over-443"),
    )
    download_url_base = service._download_repo_raw_base(job)
    publish_commit_message = service._render_commit_message(job, branch=branch, tz=tz)
    python_path = service._python_executable()
    cloudflare_check = checks.get("cloudflare") or {}
    deployment_branch = str(
        cloudflare_check.get("production_branch")
        or job.github_branch
        or await service._current_git_branch()
    ).strip()
    identity_loader = getattr(service, "_release_identity_for_task", None)
    if identity_loader is None:  # Compatibility for lightweight integrations/tests.
        release_identity = await service._site_release_identity(deployment_branch)
        release_checkpoints: dict[str, Any] = {}
    else:
        release_identity, release_checkpoints = await identity_loader(
            task,
            deployment_branch,
        )

    await runtime.task_manager.update_task_progress(task.task_uuid, 5)
    raw_archive_cfg = getattr(
        service, "_raw_archive_runtime", lambda: runtime.get_config().raw_archive
    )()
    raw_archive_branch = str(
        getattr(raw_archive_cfg, "branch", runtime.raw_archive_branch)
        or runtime.raw_archive_branch
    )
    literature_only = bool(input_data.get("incremental_site")) and not input_data.get(
        "incremental_country_codes"
    )
    if not raw_archive_cfg.enabled or literature_only:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Raw Crawler Archive Skipped",
            content=(
                "Literature-triggered incremental release; raw crawler archives publish on the full release path."
                if literature_only
                else "RAW_ARCHIVE__ENABLED is disabled for this run."
            ),
            content_type="text",
            metadata={
                "event": "raw_git_archive_skipped",
                "release_job_id": job.job_id,
            },
        )
    await runtime.task_manager.update_task_progress(task.task_uuid, 15)

    downloads_published = False
    raw_archive_published = False
    r2_papers_published = False
    r2_reports_published = False
    pages_deployed = False
    cloudflare_deployment = None
    r2_enabled_fn = getattr(service, "_r2_papers_enabled", None)
    r2_papers_enabled = (
        bool(r2_enabled_fn(job)) if callable(r2_enabled_fn) else False
    )
    r2_reports_enabled_fn = getattr(service, "_r2_reports_enabled", None)
    r2_reports_enabled = bool(r2_reports_enabled_fn(job)) if callable(r2_reports_enabled_fn) else False
    artifact_signature = None
    resumed_identity = None
    if artifact_checkpoint is not None:
        config = service._config()
        artifact_signature = await asyncio.to_thread(
            artifact_checkpoint.signature,
            {
                "config": config.model_dump() if hasattr(config, "model_dump") else vars(config),
                "job": job.model_dump() if hasattr(job, "model_dump") else vars(job),
                "download_url_base": download_url_base,
                "deployment_branch": deployment_branch,
            },
        )
        resumed_identity = await asyncio.to_thread(
            artifact_checkpoint.restore, artifact_signature, release_identity, _built_site_digest,
        )
    if resumed_identity is not None:
        release_identity = resumed_identity
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid, entry_type="info", title="Validated Release Candidate Reused",
            content="Resuming publication of the same frozen artifacts; generation and rendering were already validated.",
            content_type="text", metadata={"event": "release_candidate_reused", "release_job_id": job.job_id},
        )
    else:
        await service._run_logged_command(
            task.task_uuid,
            title="Export Situation",
            cmd=service._update_situation_notices_command(python_path=python_path),
            cwd=runtime.root_dir,
            env=git_env,
            metadata={"event": "refresh_situation_notices", "release_job_id": job.job_id},
            timeout_seconds=min(
                runtime.generate_timeout_seconds,
                SITUATION_ROOM_REFRESH_TIMEOUT_SECONDS,
            ),
        )
        await runtime.task_manager.update_task_progress(task.task_uuid, 22)

        # EXPORT_DATA holds the worker's collection-store exclusion until task
        # completion, covering both this read and commit_release_projection after
        # production verification. Waiting AI parents retain RUNNING until their
        # store completes, so their eventual writes cannot enter this interval.
        generate_cmd = service._generate_site_data_command(
            python_path=python_path,
            download_url_base=download_url_base,
            incremental_country_codes=input_data.get("incremental_country_codes") or [],
            incremental_site=bool(input_data.get("incremental_site")),
        )
        await service._run_logged_command(
            task.task_uuid,
            title="Generate Site Data",
            cmd=generate_cmd,
            cwd=runtime.root_dir,
            env={
                **git_env,
                "PYTHONUNBUFFERED": "1",
                "GLOBALID_RELEASE_FROM_OBSERVATIONS": "1",
            },
            metadata={"event": "generate_site_data", "release_job_id": job.job_id},
            timeout_seconds=runtime.generate_timeout_seconds,
        )
        await runtime.task_manager.update_task_progress(task.task_uuid, 35)

        # Complete every local, side-effect-free release gate before publishing to
        # remote repositories. A failed site build must never leave downloads and
        # raw archives ahead of the production site.
        await runtime.task_manager.update_task_progress(task.task_uuid, 45)
        await service._run_logged_command(
            task.task_uuid,
            title="Build Astro Site",
            cmd=["npm", "run", "build"],
            cwd=runtime.astro_dir,
            env={
                # The full static catalogue can exceed Node's default ~4 GiB old
                # space on the production worker. Keep the build within its
                # 14 GiB cgroup while giving Astro enough room to finish.
                "NODE_OPTIONS": "--max-old-space-size=6144",
                "GLOBALID_SKIP_SITE_DATA_GENERATION": "1",
                "PUBLIC_GIDS_RELEASE_ID": release_identity["release_id"],
                "PUBLIC_GIDS_SOURCE_COMMIT": release_identity["source_commit"],
                "PUBLIC_GIDS_SOURCE_BRANCH": release_identity["source_branch"],
                "PUBLIC_GIDS_DEPLOY_BRANCH": release_identity["deployment_branch"],
                "PUBLIC_GIDS_BUILT_AT": release_identity["built_at"],
            },
            metadata={"event": "astro_build", "release_job_id": job.job_id},
            timeout_seconds=runtime.astro_build_timeout_seconds,
        )
        await service._run_logged_command(
            task.task_uuid,
            title="Validate Situation Release Gate",
            cmd=service._validate_situation_release_command(python_path=python_path),
            cwd=runtime.root_dir,
            env=git_env,
            metadata={"event": "situation_release_gate", "release_job_id": job.job_id},
            timeout_seconds=120,
        )
        if job.include_cloudflare_deploy or r2_papers_enabled or r2_reports_enabled:
            # Move long-tail research pages out of the Pages upload set before the
            # deployable digest is computed. Local npm builds keep a full dist.
            await service._run_logged_command(
                task.task_uuid,
                title="Split Papers Bundle",
                cmd=service._split_papers_bundle_command(python_path=python_path),
                cwd=runtime.root_dir,
                env=git_env,
                metadata={"event": "split_papers_bundle", "release_job_id": job.job_id},
                timeout_seconds=1800,
            )
        if job.include_cloudflare_deploy or r2_reports_enabled:
            await service._run_logged_command(
                task.task_uuid,
                title="Split Reports Bundle",
                cmd=service._split_reports_bundle_command(python_path=python_path),
                cwd=runtime.root_dir,
                env=git_env,
                metadata={"event": "split_reports_bundle", "release_job_id": job.job_id},
                timeout_seconds=1800,
            )
        if job.include_cloudflare_deploy:
            # The public manifest identifies these exact bytes as well as the
            # source revision. A retry may rebuild different facts at the same
            # revision and must not verify an earlier production manifest.
            release_identity = {
                **release_identity,
                "build_digest": await asyncio.to_thread(
                    _built_site_digest, runtime.astro_dir / "dist"
                ),
            }
    release_manifest = service._write_site_release_manifest(release_identity)
    if artifact_checkpoint is not None and resumed_identity is None:
        await asyncio.to_thread(artifact_checkpoint.save_ready, artifact_signature, release_identity)
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Site Release Identity",
        content=json.dumps(release_manifest, ensure_ascii=False, indent=2),
        content_type="json",
        metadata={"event": "site_release_identity", "release_job_id": job.job_id},
    )
    await runtime.task_manager.update_task_progress(task.task_uuid, 60)

    if r2_papers_enabled and job.include_cloudflare_deploy:
        from .shared_assets import publish_shared_assets

        await publish_shared_assets(
            service, task, runtime, previous=previous_site_dir,
            project_name=project_name, deployment_branch=deployment_branch,
            release_identity=release_identity,
            papers_origin=str(service._config().r2_papers_public_base_url or "https://papers.globalinfectiousdisease.com").rstrip("/"),
        )

    repository_publications: list[tuple[str, Awaitable[None]]] = []
    if raw_archive_cfg.enabled and not literature_only:
        raw_git_transport = str(
            (checks.get("raw_archive") or {}).get("ssh_transport") or "default"
        )
        raw_git_env = service._build_git_env(
            raw_archive_cfg.repo_url,
            use_github_ssh_over_443=(raw_git_transport == "github-ssh-over-443"),
        )
        repository_publications.append(
            (
                "raw_archive",
                service._run_logged_command(
                    task.task_uuid,
                    title="Archive Raw Crawler Data",
                    cmd=service._publish_raw_archive_command(
                        python_path=python_path,
                        archive=raw_archive_cfg,
                    ),
                    cwd=runtime.root_dir,
                    env={**raw_git_env, "RAW_ARCHIVE__BRANCH": raw_archive_branch},
                    metadata={
                        "event": "raw_git_archive_publish",
                        "release_job_id": job.job_id,
                        "branch": raw_archive_branch,
                    },
                    timeout_seconds=float(raw_archive_cfg.git_timeout_seconds)
                    + 60 * 60,
                ),
            )
        )

    if job.include_git_push and not literature_only:
        repository_publications.append(
            (
                "direct_downloads",
                service._run_logged_command(
                    task.task_uuid,
                    title="Publish Partitioned Data Downloads",
                    cmd=service._publish_download_repo_command(
                        python_path=python_path,
                        repo_url=download_repo_url,
                        commit_message=publish_commit_message,
                        branch=branch,
                    ),
                    cwd=runtime.root_dir,
                    env=git_env,
                    metadata={
                        "event": "github_direct_download_publish",
                        "release_job_id": job.job_id,
                        "branch": branch,
                    },
                    timeout_seconds=max(
                        runtime.download_publish_timeout_seconds,
                        DOWNLOAD_GIT_PUBLISH_TIMEOUT_SECONDS,
                    ),
                ),
            )
        )
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Direct Download Publish Skipped",
            content=(
                "Literature-triggered release: existing GitHub download files remain published."
                if literature_only
                else "The validated CSV/JSON/XLSX partitions remain local for this run."
            ),
            content_type="text",
            metadata={
                "event": "github_direct_download_publish_skipped",
                "release_job_id": job.job_id,
            },
        )

    if r2_papers_enabled:
        r2_env = {
            **git_env,
            "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
            # Fallback when no R2 key pair is configured: the publisher derives
            # S3 credentials from this token.
            "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
            "R2_PAPERS_BUCKET": str(
                service._config().r2_papers_bucket or "globalid-papers"
            ),
            "R2_PAPERS_PUBLIC_BASE_URL": str(
                service._config().r2_papers_public_base_url
                or "https://papers.globalinfectiousdisease.com"
            ),
            "R2_ACCESS_KEY_ID": str(
                service._config().r2_access_key_id or ""
            ),
            "R2_SECRET_ACCESS_KEY": str(
                service._config().r2_secret_access_key or ""
            ),
            "R2_ENDPOINT_URL": str(
                service._config().r2_endpoint_url or ""
            ),
            "R2_ACCOUNT_ID": str(
                service._config().r2_account_id
                or service._cloudflare_account_id()
                or ""
            ),
        }
        repository_publications.append(
            (
                "r2_papers",
                service._run_logged_command(
                    task.task_uuid,
                    title="Publish Papers Bundle to R2",
                    cmd=service._publish_papers_r2_command(python_path=python_path),
                    cwd=runtime.root_dir,
                    env=r2_env,
                    metadata={
                        "event": "r2_papers_publish",
                        "release_job_id": job.job_id,
                    },
                    timeout_seconds=runtime.download_publish_timeout_seconds,
                ),
            )
        )
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Papers R2 Publish Skipped",
            content="R2 papers publication is disabled for this release job.",
            content_type="text",
            metadata={
                "event": "r2_papers_publish_skipped",
                "release_job_id": job.job_id,
            },
        )

    if r2_reports_enabled:
        reports_env = {
            **git_env,
            "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
            "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
            "R2_REPORTS_BUCKET": str(service._config().r2_reports_bucket or "globalid-reports"),
            "R2_REPORTS_PUBLIC_BASE_URL": str(service._config().r2_reports_public_base_url or "https://reports.globalinfectiousdisease.com"),
            "R2_ACCESS_KEY_ID": str(service._config().r2_access_key_id or ""),
            "R2_SECRET_ACCESS_KEY": str(service._config().r2_secret_access_key or ""),
            "R2_ENDPOINT_URL": str(service._config().r2_endpoint_url or ""),
            "R2_ACCOUNT_ID": str(service._config().r2_account_id or service._cloudflare_account_id() or ""),
        }
        repository_publications.append((
            "r2_reports",
            service._run_logged_command(
                task.task_uuid,
                title="Publish Reports Bundle to R2",
                cmd=service._publish_reports_r2_command(python_path=python_path),
                cwd=runtime.root_dir,
                env=reports_env,
                metadata={"event": "r2_reports_publish", "release_job_id": job.job_id},
                timeout_seconds=runtime.download_publish_timeout_seconds,
            ),
        ))
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Reports R2 Publish Skipped",
            content="R2 reports publication is disabled for this release job.",
            content_type="text",
            metadata={"event": "r2_reports_publish_skipped", "release_job_id": job.job_id},
        )

    completed_publications = await _run_repository_publications(repository_publications)
    raw_archive_published = "raw_archive" in completed_publications
    downloads_published = "direct_downloads" in completed_publications
    r2_papers_published = "r2_papers" in completed_publications
    r2_reports_published = "r2_reports" in completed_publications

    await runtime.task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Website Verification Skipped",
        content="Publication completes on successful upload/deploy commands. Papers, Reports and main-site public HTTP checks are disabled; artifacts are not rolled back.",
        content_type="text",
        metadata={"event": "website_verification_skipped", "release_job_id": job.job_id},
    )

    # The site only goes live after its direct public file links exist. This
    # prevents a deployed page from pointing at a package that failed to publish.
    subscription_options_synced = await service._sync_subscription_options_if_needed(
        task.task_uuid,
        job_id=job.job_id,
    )
    await runtime.task_manager.update_task_progress(task.task_uuid, 88)

    if job.include_cloudflare_deploy:
        if not deployment_branch:
            raise RuntimeError("Cloudflare production branch could not be resolved.")
        artifact_digest = release_identity["build_digest"]
        deploy_checkpoint = dict(release_checkpoints.get("cloudflare_deploy") or {})
        if (
            deploy_checkpoint.get("release_id") == release_identity["release_id"]
            and deploy_checkpoint.get("artifact_digest") == artifact_digest
        ):
            await runtime.task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Cloudflare Deploy Reused",
                content=(
                    "The prior attempt completed the Cloudflare deploy command for "
                    f"{release_identity['release_id']} with identical build content; "
                    "reusing that successful deployment."
                ),
                content_type="text",
                metadata={
                    "event": "cloudflare_pages_deploy_reused",
                    "release_job_id": job.job_id,
                    "release_id": release_identity["release_id"],
                },
            )
        else:
            await service._run_logged_command(
                task.task_uuid,
                title="Deploy Cloudflare Pages",
                cmd=service._cloudflare_deploy_command(
                    project_name=project_name,
                    branch=deployment_branch,
                    source_commit=release_identity["source_commit"],
                    commit_message=publish_commit_message,
                    commit_dirty=release_identity["commit_dirty"],
                ),
                cwd=runtime.astro_dir,
                env={
                    "CI": "1",
                    "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
                    "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
                },
                metadata={
                    "event": "cloudflare_pages_deploy",
                    "release_job_id": job.job_id,
                    "release_id": release_identity["release_id"],
                    "deployment_branch": deployment_branch,
                },
                timeout_seconds=runtime.cloudflare_deploy_timeout_seconds,
            )
            # Persist the successful deploy before the final DB transaction.
            # Reuse it only if rebuilt bytes still match. Facts may change, so
            # release_id alone must never authorize reusing an older deploy.
            await service._record_release_checkpoint(
                task.task_uuid,
                "cloudflare_deploy",
                {
                    "release_id": release_identity["release_id"],
                    "source_commit": release_identity["source_commit"],
                    "deployment_branch": deployment_branch,
                    "artifact_digest": artifact_digest,
                },
            )
        cloudflare_deployment = {
            "release_id": release_identity["release_id"],
            "status": "deploy_command_succeeded",
            "verification": "skipped",
        }
        pages_deployed = True
        # Keep the exact verified Pages artifact for the next shared-asset
        # compatibility deploy. Snapshot failure must not undo a live release.
        try:
            from .shared_assets import retain_verified_snapshot

            await asyncio.to_thread(
                retain_verified_snapshot,
                runtime.astro_dir / "dist",
                runtime.root_dir / "exports" / "verified-pages-snapshot",
                release_identity,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not retain the verified Pages snapshot; future compatibility deployments will fail closed"
            )
        # The v3 subscriber-alert dispatcher read analyst-reviewed statistical
        # signals; the notice-based Situation module publishes none, so no
        # alert step runs after deployment.
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Cloudflare Deploy Skipped",
            content="Cloudflare Pages deployment is disabled for this release job.",
            content_type="text",
            metadata={"event": "cloudflare_skip", "release_job_id": job.job_id},
        )

    # Consume selected rows after successful publication commands. Concurrent
    # collections outside the worker (for example maintenance imports) have
    # different IDs and remain pending for the next release.
    if pages_deployed:
        from .projection import commit_release_projection

        async with runtime.get_database() as db:
            await commit_release_projection(
                db,
                task_uuid=task.task_uuid,
                country_codes=input_data.get("incremental_country_codes") or [],
                full=bool(input_data.get("full")),
                dirty_ids=input_data.get("publish_dirty_ids") or [],
                marked_before=input_data.get("publish_dirty_before"),
                prepared_dir=(
                    runtime.astro_dir / "src" / "data" / "_release-projection"
                    if (runtime.astro_dir / "src" / "data" / "_release-projection" / "manifest.json").is_file()
                    else None
                ),
            )
            await db.commit()

    await runtime.task_manager.update_task_progress(task.task_uuid, 100)

    output = {
        "release_job_id": job.job_id,
        "release_job_name": job.name,
        "incremental_country_codes": input_data.get("incremental_country_codes") or [],
        "github_repo_url": download_repo_url,
        "download_repo_branch": branch,
        "download_url_base": download_url_base,
        "direct_downloads_published": downloads_published,
        "website_verification": "skipped",
        "artifact_rollback": False,
        "r2_papers_published": r2_papers_published,
        "r2_reports_published": r2_reports_published,
        "r2_reports_public_base_url": (
            service._config().r2_reports_public_base_url if r2_reports_enabled else None
        ),
        "r2_papers_public_base_url": (
            service._config().r2_papers_public_base_url
            if r2_papers_enabled
            else None
        ),
        "cloudflare_project_name": project_name,
        "cloudflare_deployment_branch": deployment_branch,
        "cloudflare_deployment": cloudflare_deployment,
        "site_release": release_manifest,
        "commit_message": publish_commit_message,
        "git_pushed": downloads_published,
        "raw_archive_published": raw_archive_published,
        "raw_archive_repo_url": raw_archive_cfg.repo_url or None,
        "raw_archive_branch": raw_archive_branch if raw_archive_cfg.enabled else None,
        "pages_deployed": pages_deployed,
        "subscription_options_synced": subscription_options_synced,
        "preflight": checks,
    }

    async with runtime.get_database() as db:
        task_obj = await db.get(runtime.task_model, task.id)
        if task_obj:
            task_obj.output_data = output
            await db.commit()

    await runtime.task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Data Release Completed",
        content=(
            f"Release job: {job.name}\n"
            f"GitHub direct downloads published: {'yes' if downloads_published else 'no'}\n"
            f"Papers R2 published: {'yes' if r2_papers_published else 'no'}\n"
            f"Reports R2 published: {'yes' if r2_reports_published else 'no'}\n"
            f"Raw crawler archive updated: {'yes' if raw_archive_published else 'no'}\n"
            f"Cloudflare deployed: {'yes' if pages_deployed else 'no'}\n"
            f"Production branch: {deployment_branch or '-'}\n"
            f"Site release: {release_identity['release_id']}\n"
            f"Production URL: {cloudflare_deployment.get('production_url') if cloudflare_deployment else '-'}\n"
            f"Subscription options synced: {'yes' if subscription_options_synced else 'no'}\n"
            f"Download repo: {download_repo_url or '-'}"
        ),
        content_type="text",
        metadata={"event": "release_completed", "release_job_id": job.job_id},
    )
    return output
