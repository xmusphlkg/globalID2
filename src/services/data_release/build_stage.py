"""Build, validate and split the local site before any remote publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any


async def build_release_site(
    *, service: Any, task: Any, runtime: Any, job: Any, python_path: Path,
    git_env: dict[str, str], release_identity: dict[str, Any],
    r2_papers_enabled: bool, r2_reports_enabled: bool,
    built_site_digest: Callable[[Path], str],
) -> dict[str, Any]:
    """Run local build gates in order and bind deployment identity to output bytes."""
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
                built_site_digest, runtime.astro_dir / "dist"
            ),
        }
    return release_identity
