"""Deploy a built site and persist recovery checkpoints after Pages succeeds."""

from __future__ import annotations

import asyncio
import logging
from typing import Any


async def deploy_cloudflare_site(
    *, service: Any, task: Any, runtime: Any, job: Any,
    stage_receipts: Any, release_identity: dict[str, Any],
    release_checkpoints: dict[str, Any], project_name: str,
    deployment_branch: str, publish_commit_message: str,
) -> dict[str, Any]:
    """Deploy or resume an identical current publication before recording success."""
    if not deployment_branch:
        raise RuntimeError("Cloudflare production branch could not be resolved.")
    artifact_digest = release_identity["build_digest"]
    deploy_checkpoint = dict(release_checkpoints.get("cloudflare_deploy") or {})
    if (
        deploy_checkpoint.get("release_id") == release_identity["release_id"]
        and deploy_checkpoint.get("artifact_digest") == artifact_digest
        and deploy_checkpoint.get("project_name") == project_name
        and deploy_checkpoint.get("account_id") == service._cloudflare_account_id()
        and deploy_checkpoint.get("deployment_branch") == deployment_branch
        and await asyncio.to_thread(stage_receipts.is_current_publication, release_identity)
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
        await asyncio.to_thread(
            stage_receipts.mark_published, release_identity,
            data_paths=[runtime.astro_dir / "src/data", runtime.astro_dir / "public/site-data"],
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
        # Record external success before any database checkpoint can fail.
        await asyncio.to_thread(
            stage_receipts.mark_published, release_identity,
            data_paths=[runtime.astro_dir / "src/data", runtime.astro_dir / "public/site-data"],
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
                "project_name": project_name,
                "account_id": service._cloudflare_account_id(),
            },
        )
    cloudflare_deployment = {
        "release_id": release_identity["release_id"],
        "status": "deploy_command_succeeded",
        "verification": "skipped",
    }
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
    return cloudflare_deployment
