"""Materialize release inputs from one read-only database snapshot."""
from __future__ import annotations

import json
import time

from src.core.database_snapshot import SNAPSHOT_ENV


async def generate_release_inputs(*, service, task, runtime, job, python_path,
                                  git_env, download_url_base, input_data):
    started = time.monotonic()
    async with runtime.read_snapshot() as snapshot_env:
        # Only the reader subprocesses receive this environment. Build/upload
        # and final publication acknowledgements must never import it.
        export_env = {**git_env, **snapshot_env}
        report_check = runtime.astro_dir / "scripts/check-report-inputs.mjs"
        if report_check.is_file():
            await service._run_logged_command(
                task.task_uuid, title="Refresh Report Inputs",
                cmd=[str(python_path), "scripts/generate_site_data.py", "--reports-only"],
                cwd=runtime.root_dir, env=export_env,
                metadata={"event": "report_inputs_refresh"}, timeout_seconds=300,
            )
            await service._run_logged_command(
                task.task_uuid, title="Validate Report Inputs",
                cmd=["node", "--experimental-strip-types", str(report_check)],
                cwd=runtime.astro_dir, metadata={"event": "report_inputs_validate"}, timeout_seconds=120,
            )
        await service._run_logged_command(
            task.task_uuid, title="Export Situation",
            cmd=service._update_situation_notices_command(python_path=python_path),
            cwd=runtime.root_dir, env=export_env,
            metadata={"event": "refresh_situation_notices", "release_job_id": job.job_id},
            timeout_seconds=min(runtime.generate_timeout_seconds, 30 * 60),
        )
        await runtime.task_manager.update_task_progress(task.task_uuid, 22)
        await service._run_logged_command(
            task.task_uuid, title="Generate Site Data",
            cmd=service._generate_site_data_command(
                python_path=python_path, download_url_base=download_url_base,
                incremental_country_codes=input_data.get("incremental_country_codes") or [],
                incremental_site=bool(input_data.get("incremental_site")),
                country_scopes_exact=bool(input_data.get("country_scopes_exact")),
            ),
            cwd=runtime.root_dir,
            env={**export_env, "PYTHONUNBUFFERED": "1", "GLOBALID_RELEASE_FROM_OBSERVATIONS": "1"},
            metadata={"event": "generate_site_data", "release_job_id": job.job_id},
            timeout_seconds=runtime.generate_timeout_seconds,
        )
        await runtime.task_manager.update_task_progress(task.task_uuid, 35)
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid, entry_type="info", title="Release Read Snapshot Closed",
        content=json.dumps({"snapshot_id": snapshot_env[SNAPSHOT_ENV],
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "collection_writes_paused": False}),
        content_type="json", metadata={"event": "release_read_snapshot_closed"},
    )
