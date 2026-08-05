"""Execution pipeline for a validated data release job."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReleasePipelineRuntime:
    """External collaborators and stable release constants for one execution."""

    task_manager: Any
    get_config: Callable[[], Any]
    get_database: Callable[[], Any]
    task_model: Any
    root_dir: Path
    astro_dir: Path
    github_snapshot_branch: str
    raw_archive_branch: str
    generate_timeout_seconds: float
    astro_build_timeout_seconds: float
    github_publish_timeout_seconds: float
    cloudflare_deploy_timeout_seconds: float


async def execute_release_task(service: Any, task: Any, *, runtime: ReleasePipelineRuntime) -> dict[str, Any]:
    input_data = dict(task.input_data or {})
    job_id = str(input_data.get("release_job_id") or "").strip()
    if not job_id:
        raise RuntimeError("Release task is missing release_job_id")
    job = next((item for item in await service.load_jobs() if item.job_id == job_id), None)
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
        raise RuntimeError("Release preflight failed: " + "; ".join(checks["blockers"]))

    tz = ZoneInfo(job.timezone or service._config().timezone)
    branch = runtime.github_snapshot_branch
    project_name = service._cloudflare_project_name(job.cloudflare_project_name)
    download_repo_url = service._download_repo_url()
    git_transport = str((checks.get("git") or {}).get("ssh_transport") or "default")
    git_env = service._build_git_env(
        download_repo_url,
        use_github_ssh_over_443=(git_transport == "github-ssh-over-443"),
    )
    snapshot_url_base = service._github_snapshot_raw_base()
    publish_commit_message = service._render_commit_message(job, branch=branch, tz=tz)
    python_path = service._python_executable()
    cloudflare_check = checks.get("cloudflare") or {}
    deployment_branch = str(
        cloudflare_check.get("production_branch")
        or job.github_branch
        or await service._current_git_branch()
    ).strip()
    release_identity = await service._site_release_identity(deployment_branch)

    await runtime.task_manager.update_task_progress(task.task_uuid, 5)
    raw_archive_cfg = runtime.get_config().raw_archive
    raw_archive_published = False
    if raw_archive_cfg.enabled:
        raw_git_transport = str(
            (checks.get("raw_archive") or {}).get("ssh_transport") or "default"
        )
        raw_git_env = service._build_git_env(
            raw_archive_cfg.repo_url,
            use_github_ssh_over_443=(raw_git_transport == "github-ssh-over-443"),
        )
        await service._run_logged_command(
            task.task_uuid,
            title="Archive Raw Crawler Data",
            cmd=service._publish_raw_archive_command(python_path=python_path),
            cwd=runtime.root_dir,
            env=raw_git_env,
            metadata={
                "event": "raw_git_archive_publish",
                "release_job_id": job.job_id,
                "branch": runtime.raw_archive_branch,
            },
            timeout_seconds=float(raw_archive_cfg.git_timeout_seconds) + 60 * 60,
        )
        raw_archive_published = True
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Raw Crawler Archive Skipped",
            content="RAW_ARCHIVE__ENABLED is disabled for this run.",
            content_type="text",
            metadata={
                "event": "raw_git_archive_skipped",
                "release_job_id": job.job_id,
            },
        )
    await runtime.task_manager.update_task_progress(task.task_uuid, 15)

    generate_cmd = service._generate_site_data_command(
        python_path=python_path,
        snapshot_url_base=snapshot_url_base,
    )
    await service._run_logged_command(
        task.task_uuid,
        title="Generate Site Data",
        cmd=generate_cmd,
        cwd=runtime.root_dir,
        env=git_env,
        metadata={"event": "generate_site_data", "release_job_id": job.job_id},
        timeout_seconds=runtime.generate_timeout_seconds,
    )
    await runtime.task_manager.update_task_progress(task.task_uuid, 35)

    await service._run_logged_command(
        task.task_uuid,
        title="Build Astro Site",
        cmd=["npm", "run", "build"],
        cwd=runtime.astro_dir,
        env={
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
    release_manifest = service._write_site_release_manifest(release_identity)
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Site Release Identity",
        content=json.dumps(release_manifest, ensure_ascii=False, indent=2),
        content_type="json",
        metadata={"event": "site_release_identity", "release_job_id": job.job_id},
    )
    await runtime.task_manager.update_task_progress(task.task_uuid, 60)

    subscription_options_synced = await service._sync_subscription_options_if_needed(
        task.task_uuid,
        job_id=job.job_id,
    )

    github_snapshot_published = False
    pages_deployed = False
    cloudflare_deployment = None

    if job.include_git_push:
        await service._run_logged_command(
            task.task_uuid,
            title="Publish GitHub Snapshot v2",
            cmd=service._publish_github_snapshot_command(
                python_path=python_path,
                repo_url=download_repo_url,
                commit_message=publish_commit_message,
            ),
            cwd=runtime.root_dir,
            env=git_env,
            metadata={
                "event": "github_snapshot_v2_publish",
                "release_job_id": job.job_id,
                "branch": runtime.github_snapshot_branch,
            },
            timeout_seconds=runtime.github_publish_timeout_seconds,
        )
        github_snapshot_published = True
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="GitHub Snapshot Publish Skipped",
            content="The validated snapshot-v2 tree remains local for this run.",
            content_type="text",
            metadata={
                "event": "github_snapshot_v2_publish_skipped",
                "release_job_id": job.job_id,
            },
        )

    await runtime.task_manager.update_task_progress(task.task_uuid, 88)

    if job.include_cloudflare_deploy:
        if not deployment_branch:
            raise RuntimeError("Cloudflare production branch could not be resolved.")
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
        cloudflare_deployment = await service._verify_cloudflare_production_release(
            project_name=project_name,
            subdomain=str(cloudflare_check.get("subdomain") or "").strip(),
            release_identity=release_identity,
        )
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Production Release Verified",
            content=json.dumps(cloudflare_deployment, ensure_ascii=False, indent=2),
            content_type="json",
            metadata={
                "event": "cloudflare_production_verified",
                "release_job_id": job.job_id,
                "release_id": release_identity["release_id"],
            },
        )
        pages_deployed = True
    else:
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Cloudflare Deploy Skipped",
            content="Cloudflare Pages deployment is disabled for this release job.",
            content_type="text",
            metadata={"event": "cloudflare_skip", "release_job_id": job.job_id},
        )

    await runtime.task_manager.update_task_progress(task.task_uuid, 100)

    output = {
        "release_job_id": job.job_id,
        "release_job_name": job.name,
        "github_repo_url": download_repo_url,
        "github_snapshot_branch": branch,
        "snapshot_url_base": snapshot_url_base,
        "cloudflare_project_name": project_name,
        "cloudflare_deployment_branch": deployment_branch,
        "cloudflare_deployment": cloudflare_deployment,
        "site_release": release_manifest,
        "commit_message": publish_commit_message,
        "git_pushed": github_snapshot_published,
        "github_snapshot_published": github_snapshot_published,
        "raw_archive_published": raw_archive_published,
        "raw_archive_repo_url": raw_archive_cfg.repo_url or None,
        "raw_archive_branch": runtime.raw_archive_branch if raw_archive_cfg.enabled else None,
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
            f"GitHub snapshot-v2 published: {'yes' if github_snapshot_published else 'no'}\n"
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
