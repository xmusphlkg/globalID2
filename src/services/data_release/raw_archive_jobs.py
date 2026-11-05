"""Durable raw-source archival, independent of website publication and dirty ACKs."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from src.domain import DataReleaseJob, Task, TaskPriority, TaskStatus, TaskType


async def enqueue(parent_task, *, service, runtime) -> dict[str, Any]:
    archive = service._raw_archive_runtime()
    if not archive.enabled:
        return {"status": "disabled"}

    parent_input = dict(parent_task.input_data or {})
    job_id = parent_input.get("release_job_id") or (parent_task.metadata_ or {}).get("release_job_id")
    async with runtime.get_database() as db:
        # The same registry lock used by the release scheduler serializes the
        # check/insert across processes without holding a lock during archival.
        await db.execute(select(DataReleaseJob.id).order_by(DataReleaseJob.id).with_for_update())
        existing = (await db.execute(
            select(Task).where(
                Task.task_type == TaskType.EXPORT_DATA,
                Task.input_data["raw_archive_only"].as_boolean().is_(True),
                Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RETRYING]),
            ).order_by(Task.created_at).with_for_update()
        )).scalars().first()
        if existing is not None:
            # A queued/retrying job will scan after this publication. A running
            # scan may have passed the new files, so it needs a trailing job.
            existing.input_data = {
                **(existing.input_data or {}),
                "latest_parent_release_task_uuid": parent_task.task_uuid,
            }
            await db.commit()
            return {"status": "coalesced", "task_uuid": existing.task_uuid}

        task = Task(
            task_type=TaskType.EXPORT_DATA,
            task_name="Raw Crawler Archive",
            description="Archive source files independently after website publication.",
            status=TaskStatus.QUEUED,
            priority=TaskPriority.LOW,
            max_retries=service._automatic_retry_settings()[0],
            input_data={
                "raw_archive_only": True,
                "release_job_id": job_id,
                "parent_release_task_uuid": parent_task.task_uuid,
                "trigger": "upstream_completion",
                "manual_trigger": False,
            },
            metadata_={
                "raw_archive_only": True, "release_job_id": job_id,
                "parent_release_task_uuid": parent_task.task_uuid,
                "automatic_retry": {
                    "policy_version": "release-transient-v1",
                    "status": "not_scheduled", "scheduled_attempts": 0,
                },
            },
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        result = {"status": "queued", "task_uuid": task.task_uuid}
    return result


async def execute(task, *, service, runtime) -> dict[str, Any]:
    """Run only the archive publisher; the script also holds a repository flock."""
    from .pipeline import ReleasePreflightError

    archive = service._raw_archive_runtime()
    input_data = task.input_data or {}
    output = {
        "raw_archive_only": True,
        "release_job_id": input_data.get("release_job_id"),
        "parent_release_task_uuid": input_data.get("parent_release_task_uuid"),
        "raw_archive_published": False,
    }
    if archive.enabled:
        await runtime.task_manager.update_task_progress(task.task_uuid, 10)
        check = await service._raw_archive_check()
        if check.get("blockers"):
            raise ReleasePreflightError({
                "blockers": check["blockers"], "raw_archive": check.get("payload") or {},
            })
        transport = (check.get("payload") or {}).get("ssh_transport", "default")
        env = service._build_git_env(
            archive.repo_url, use_github_ssh_over_443=transport == "github-ssh-over-443",
        )
        await runtime.task_manager.update_task_progress(task.task_uuid, 30)
        await service._run_logged_command(
            task.task_uuid,
            title="Archive Raw Crawler Data",
            cmd=service._publish_raw_archive_command(
                python_path=service._python_executable(), archive=archive,
            ),
            cwd=runtime.root_dir,
            env={**env, "RAW_ARCHIVE__BRANCH": archive.branch},
            metadata={
                "event": "raw_git_archive_publish", "release_job_id": input_data.get("release_job_id"),
                "branch": archive.branch, "raw_archive_only": True,
            },
            timeout_seconds=float(archive.git_timeout_seconds) + 60 * 60,
        )
        output.update({
            "raw_archive_published": True, "raw_archive_repo_url": archive.repo_url,
            "raw_archive_branch": archive.branch,
        })
    else:
        output["skipped_reason"] = "raw_archive_disabled"

    async with runtime.get_database() as db:
        stored = await db.get(runtime.task_model, task.id)
        if stored is not None:
            stored.output_data = output
            await db.commit()
    await runtime.task_manager.update_task_progress(task.task_uuid, 100)
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid, entry_type="success", title="Raw Archive Completed",
        content="Raw source archival completed independently of website publication.",
        content_type="text", metadata={"event": "raw_archive_completed", **output},
    )
    return output
