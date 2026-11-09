"""Execution pipeline for a validated data release job."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from src.core.database_snapshot import export_read_snapshot

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
    read_snapshot: Callable = export_read_snapshot


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
        # Preserve the typed cause for a single failure. For several failures,
        # keep the aggregate terminal unless every failure is a pool timeout.
        from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
        if len(stage_errors) == 1 or all(
            isinstance(error, SQLAlchemyTimeoutError)
            and "queuepool limit of size" in str(error).casefold()
            for error in stage_errors
        ):
            raise RuntimeError(message) from primary
        raise RuntimeError(message)
    return completed


async def execute_release_task(
    service: Any, task: Any, *, runtime: ReleasePipelineRuntime
) -> dict[str, Any]:
    """Publish in place; upload errors retain artifacts without rolling them back."""
    if (getattr(task, "input_data", None) or {}).get("data_only"):
        from .data_pipeline import execute_data_release
        return await execute_data_release(service, task, runtime=runtime)
    if (getattr(task, "input_data", None) or {}).get("raw_archive_only"):
        from .raw_archive_jobs import execute
        return await execute(task, service=service, runtime=runtime)
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
    if input_data.get("publish_dirty_before"):
        raise RuntimeError("Legacy release uses a timestamp cutoff; enqueue a new release with exact dirty IDs")
    code_only = bool(input_data.get("code_only"))
    content_only = bool(input_data.get("content_only"))
    dispatch_subscriptions = not code_only and not bool(input_data.get("suppress_subscription_dispatch"))
    situation_detached = bool(getattr(getattr(service, "_config", lambda: None)(), "situation_json_enabled", False))
    if situation_detached and not code_only and not input_data.get("situation_detached"):
        raise RuntimeError("Static release predates the independent Situation JSON contract; enqueue a new static release")
    if ((runtime.astro_dir / "src/lib/surveillance-data.ts").is_file()
            and not getattr(service._config(), "surveillance_json_enabled", False)):
        # Check before any retained generation/render receipt can skip npm.
        raise RuntimeError("Runtime data templates require an initialized JSON channel and Worker before static publication")
    job_id = str(input_data.get("release_job_id") or "").strip()
    if not job_id:
        raise RuntimeError("Release task is missing release_job_id")
    job = next(
        (item for item in await service.load_jobs() if item.job_id == job_id), None
    )
    if job is None:
        raise RuntimeError(f"Release job not found: {job_id}")

    checks = await service.integration_checks(job.job_id, **({"code_only": True} if code_only else {}))
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
    research_subscription_dispatch: dict[str, Any] = {"status": "disabled"}
    notice_subscription_dispatch: dict[str, Any] = {"status": "disabled"}
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
    from .stage_receipts import StageReceipts
    config = service._config()
    stage_receipts = StageReceipts(runtime.root_dir, task.task_uuid, input_data, {
        "config": config.model_dump() if hasattr(config, "model_dump") else vars(config),
        "job": job.model_dump() if hasattr(job, "model_dump") else vars(job),
        "download_url_base": download_url_base, "deployment_branch": deployment_branch,
    })
    ready_signature = await asyncio.to_thread(stage_receipts.signature, "ready", release_identity)
    generated_signature = await asyncio.to_thread(stage_receipts.signature, "generated", release_identity)
    ready_record = await asyncio.to_thread(stage_receipts.matches, "ready", ready_signature)
    if resumed_identity is None and ready_record:
        resumed_identity = ready_record["identity"]
    generated_record = None if resumed_identity else await asyncio.to_thread(
        stage_receipts.matches, "generated", generated_signature,
    )
    needs_projection = not code_only and not content_only and bool(input_data.get("full") or input_data.get("incremental_country_codes"))
    projection_manifest = runtime.astro_dir / "src/data/_release-projection/manifest.json"
    if needs_projection and not projection_manifest.is_file():
        # Never acknowledge fresh DB facts against a reused older site snapshot.
        resumed_identity = None
        generated_record = None
    if resumed_identity is not None:
        release_identity = resumed_identity
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid, entry_type="info", title="Validated Release Candidate Reused",
            content="Resuming publication of the same frozen artifacts; generation and rendering were already validated.",
            content_type="text", metadata={"event": "release_candidate_reused", "release_job_id": job.job_id},
        )
    else:
        if code_only:
            await asyncio.to_thread(
                stage_receipts.assert_published_data,
                [runtime.astro_dir / "src/data", runtime.astro_dir / "public/site-data"],
            )
            for required in ("meta.json", "downloads.json", "research/routes.json"):
                if not (runtime.astro_dir / "src/data" / required).is_file():
                    raise RuntimeError("Code-only release requires an existing data snapshot: " + required)
        elif generated_record:
            await runtime.task_manager.add_workbook_entry(
                task.task_uuid, entry_type="info", title="Generated Data Reused",
                content="The frozen data export and downloads still match their stage receipt; rebuilding templates only.",
                content_type="text", metadata={"event": "generated_data_reused"},
            )
        else:
            from .input_snapshot import generate_release_inputs

            await generate_release_inputs(
                service=service, task=task, runtime=runtime, job=job,
                python_path=python_path, git_env=git_env,
                download_url_base=download_url_base, input_data=input_data,
            )
            if needs_projection:
                from src.generation.release_projection_snapshot import validate_read_snapshot_projection
                await asyncio.to_thread(validate_read_snapshot_projection, projection_manifest.parent)

            generated_paths = [runtime.astro_dir / "src/data", runtime.astro_dir / "public/site-data"]
            if not content_only:
                generated_paths.extend(runtime.root_dir / name for name in (
                    "exports/site-downloads/countries", "exports/site-downloads/diseases", "exports/site-downloads/manifest.json"))
            await asyncio.to_thread(stage_receipts.save, "generated", generated_signature,
                generated_paths, identity=release_identity)

        if needs_projection and generated_record:
            from src.generation.release_projection_snapshot import validate_read_snapshot_projection
            await asyncio.to_thread(validate_read_snapshot_projection, projection_manifest.parent)
        from .build_stage import build_release_site

        release_identity = await build_release_site(
            service=service, task=task, runtime=runtime, job=job, python_path=python_path,
            git_env=git_env, release_identity=release_identity,
            r2_papers_enabled=r2_papers_enabled, r2_reports_enabled=r2_reports_enabled,
            built_site_digest=_built_site_digest,
        )
    if needs_projection and not projection_manifest.is_file():
        raise RuntimeError("Data release is missing its frozen publication projection")
    if needs_projection and resumed_identity is not None:
        from src.generation.release_projection_snapshot import validate_read_snapshot_projection
        await asyncio.to_thread(validate_read_snapshot_projection, projection_manifest.parent)
    release_manifest = service._write_site_release_manifest(release_identity)
    if resumed_identity is None:
        stage_paths = [runtime.astro_dir / "src/data", runtime.astro_dir / "public/site-data", runtime.astro_dir / "dist"]
        stage_paths.extend(path for path in [
            runtime.root_dir / "exports/site-downloads/countries",
            runtime.root_dir / "exports/site-downloads/diseases",
            runtime.root_dir / "exports/site-downloads/manifest.json",
            runtime.root_dir / "exports/papers-site",
            runtime.root_dir / "exports/reports-site",
        ] if path.exists())
        await asyncio.to_thread(stage_receipts.save, "ready", ready_signature, stage_paths, identity=release_identity)
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

    from .repository_publishers import prepare_repository_publications

    repository_publications = await prepare_repository_publications(
        service=service, task=task, runtime=runtime, job=job, python_path=python_path,
        git_env=git_env, branch=branch, download_repo_url=download_repo_url,
        publish_commit_message=publish_commit_message,
        download_git_timeout_seconds=DOWNLOAD_GIT_PUBLISH_TIMEOUT_SECONDS,
        literature_only=literature_only or content_only, code_only=code_only,
        r2_papers_enabled=r2_papers_enabled, r2_reports_enabled=r2_reports_enabled,
    )
    completed_publications = await _run_repository_publications(repository_publications)
    raw_archive_published = "raw_archive" in completed_publications
    downloads_published = "direct_downloads" in completed_publications
    r2_papers_published = "r2_papers" in completed_publications
    r2_reports_published = "r2_reports" in completed_publications

    report_subscription_dispatch = None

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
        from .cloudflare_deploy import deploy_cloudflare_site

        cloudflare_deployment = await deploy_cloudflare_site(
            service=service, task=task, runtime=runtime, job=job,
            stage_receipts=stage_receipts, release_identity=release_identity,
            release_checkpoints=release_checkpoints, project_name=project_name,
            deployment_branch=deployment_branch, publish_commit_message=publish_commit_message,
        )
        pages_deployed = True
        # Research delivery is explicitly activated with a start week and
        # only runs after the exact built release is visible on the public site.
        # The outbox stores public content before any Worker request, so a
        # release retry can resume the same immutable campaign safely.
        env_value = getattr(service, "_env_value", lambda _name, default="": default)
        from .subscription_reconciliation import dispatch_safely

        if dispatch_subscriptions and env_value("SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
            from .research_subscription import dispatch_after_release

            research_subscription_dispatch = await dispatch_safely(
                "research", dispatch_after_release,
                root=runtime.root_dir,
                astro_dir=runtime.astro_dir,
                identity=release_identity,
                worker_base_url=env_value("SUBSCRIPTIONS__PUBLIC_BASE_URL"),
                public_base_url=env_value("GIDS_PUBLIC_BASE_URL", "https://globalinfectiousdisease.com"),
                token=env_value("SUBSCRIPTIONS__ADMIN_API_TOKEN"),
                start_week=env_value("SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK"),
                mode=env_value("SUBSCRIPTIONS__DELIVERY_MODE", "pilot").strip().lower(),
                guard_activation=True,
            )
        if dispatch_subscriptions and env_value("SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
            from .notice_subscription import dispatch_after_release as dispatch_notices

            notice_subscription_dispatch = await dispatch_safely(
                "notices", dispatch_notices,
                root=runtime.root_dir,
                astro_dir=runtime.astro_dir,
                identity=release_identity,
                worker_base_url=env_value("SUBSCRIPTIONS__PUBLIC_BASE_URL"),
                public_base_url=env_value("GIDS_PUBLIC_BASE_URL", "https://globalinfectiousdisease.com"),
                token=env_value("SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN"),
                start_date=env_value("SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE"),
                mode=env_value("SUBSCRIPTIONS__DELIVERY_MODE", "pilot").strip().lower(),
                guard_activation=True,
            )
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Cloudflare Deploy Skipped",
            content="Cloudflare Pages deployment is disabled for this release job.",
            content_type="text",
            metadata={"event": "cloudflare_skip", "release_job_id": job.job_id},
        )

    # The reports bundle has already been published and, when requested, the
    # main site deploy has succeeded. A subscription failure must not mark an
    # already-published release failed or prevent the Pages deployment.
    if r2_reports_published and dispatch_subscriptions:
        from .report_subscription import dispatch_if_enabled

        report_subscription_dispatch = await dispatch_if_enabled(
            service, runtime,
            reports_origin=str(service._config().r2_reports_public_base_url or "https://reports.globalinfectiousdisease.com").rstrip("/"),
        )

    raw_archive_task = {"status": "disabled"}
    if raw_archive_cfg.enabled and not literature_only and not code_only and not content_only:
        from .raw_archive_jobs import enqueue
        raw_archive_task = await enqueue(task, service=service, runtime=runtime)

    # Consume selected rows after successful publication commands. Concurrent
    # collections outside the worker (for example maintenance imports) have
    # different IDs and remain pending for the next release.
    if pages_deployed and not code_only and content_only:
        from datetime import datetime, timezone
        from sqlalchemy import update
        from src.domain.collection import PublishDirty
        # Static content never advances surveillance projections or consumes
        # surveillance markers, even if an old/manual task supplied their IDs.
        async with runtime.get_database() as db:
            await db.execute(update(PublishDirty).where(
                PublishDirty.id.in_(input_data.get("publish_dirty_ids") or []),
                (PublishDirty.line.not_in(["surveillance", "situation"]) if situation_detached
                 else PublishDirty.line != "surveillance"), PublishDirty.published_at.is_(None),
            ).values(published_at=datetime.now(timezone.utc), published_task_uuid=task.task_uuid))
            await db.commit()
    elif pages_deployed and not code_only:
        from .projection import commit_release_projection

        async with runtime.get_database() as db:
            await commit_release_projection(
                db,
                task_uuid=task.task_uuid,
                country_codes=input_data.get("incremental_country_codes") or [],
                full=bool(input_data.get("full")),
                country_scopes_exact=bool(input_data.get("country_scopes_exact")),
                dirty_ids=input_data.get("publish_dirty_ids") or [],
                marked_before=input_data.get("publish_dirty_before"),
                require_prepared=True,
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
        "code_only": code_only,
        "content_only": content_only,
        "subscription_dispatch_suppressed": bool(input_data.get("suppress_subscription_dispatch")),
        "release_plan": input_data.get("release_plan"),
        "incremental_country_codes": input_data.get("incremental_country_codes") or [],
        "github_repo_url": download_repo_url,
        "download_repo_branch": branch,
        "download_url_base": download_url_base,
        "direct_downloads_published": downloads_published,
        "website_verification": "skipped",
        "artifact_rollback": False,
        "r2_papers_published": r2_papers_published,
        "r2_reports_published": r2_reports_published,
        "report_subscription_dispatch": report_subscription_dispatch,
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
        "raw_archive_task": raw_archive_task,
        "raw_archive_repo_url": raw_archive_cfg.repo_url or None,
        "raw_archive_branch": raw_archive_branch if raw_archive_cfg.enabled else None,
        "pages_deployed": pages_deployed,
        "subscription_options_synced": subscription_options_synced,
        "research_subscription_dispatch": research_subscription_dispatch,
        "notice_subscription_dispatch": notice_subscription_dispatch,
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
            f"Raw crawler archive: {raw_archive_task['status']}\n"
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
    await asyncio.to_thread(stage_receipts.discard)
    return output
