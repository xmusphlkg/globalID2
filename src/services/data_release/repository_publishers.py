"""Prepare independent download and R2 publications for one release."""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Any


def papers_environment(service: Any, base: dict[str, str]) -> dict[str, str]:
    config = service._config()
    return {**base,
        'CLOUDFLARE_ACCOUNT_ID': service._cloudflare_account_id(),
        'CLOUDFLARE_API_TOKEN': service._cloudflare_api_token(),
        'R2_PAPERS_BUCKET': str(config.r2_papers_bucket or 'globalid-papers'),
        'R2_PAPERS_PUBLIC_BASE_URL': str(config.r2_papers_public_base_url or 'https://papers.globalinfectiousdisease.com'),
        'R2_ACCESS_KEY_ID': str(config.r2_access_key_id or ''),
        'R2_SECRET_ACCESS_KEY': str(config.r2_secret_access_key or ''),
        'R2_ENDPOINT_URL': str(config.r2_endpoint_url or ''),
        'R2_ACCOUNT_ID': str(config.r2_account_id or service._cloudflare_account_id() or '')}


async def prepare_repository_publications(
    *, service: Any, task: Any, runtime: Any, job: Any, python_path: Path,
    git_env: dict[str, str], branch: str, download_repo_url: str,
    publish_commit_message: str, download_git_timeout_seconds: float,
    literature_only: bool, code_only: bool, r2_papers_enabled: bool,
    r2_reports_enabled: bool,
) -> list[tuple[str, Awaitable[None]]]:
    """Create publication commands without starting them; record disabled channels."""
    repository_publications: list[tuple[str, Awaitable[None]]] = []
    if job.include_git_push and not literature_only and not code_only:
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
                        download_git_timeout_seconds,
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
        r2_env = papers_environment(service, git_env)
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

    return repository_publications
