"""Isolated report export, build and R2 publication."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from .reports_bundle import DEFAULT_REPORTS_ORIGIN
from .papers_bundle import DEFAULT_MAIN_SITE_ORIGIN


async def execute_reports_release(service, task, *, runtime):
    started = time.monotonic()
    job_id = str((task.input_data or {}).get("release_job_id") or "")
    job = next((item for item in await service.load_jobs() if item.job_id == job_id), None)
    if job is None:
        raise RuntimeError(f"Release job not found: {job_id}")
    if not service._r2_reports_enabled():
        raise RuntimeError("Reports R2 publication is disabled")
    check = await asyncio.to_thread(service._r2_reports_check, probe=True)
    if check["blockers"]:
        raise RuntimeError("Reports release preflight failed: " + "; ".join(check["blockers"]))

    root, astro = runtime.root_dir, runtime.astro_dir
    reports_dir = service._reports_site_dir()
    stage = root / "exports" / f"reports-stage-{task.task_uuid}"
    paths = [astro / "src/data/reports", astro / "public/site-data/reports", reports_dir]
    python = service._python_executable()
    config = service._config()
    origin = str(config.r2_reports_public_base_url or DEFAULT_REPORTS_ORIGIN).rstrip("/")
    env = {
        "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
        "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
        "R2_REPORTS_BUCKET": str(config.r2_reports_bucket or "globalid-reports"),
        "R2_REPORTS_PUBLIC_BASE_URL": origin,
        "R2_ACCESS_KEY_ID": str(config.r2_access_key_id or ""),
        "R2_SECRET_ACCESS_KEY": str(config.r2_secret_access_key or ""),
        "R2_ENDPOINT_URL": str(config.r2_endpoint_url or ""),
        "R2_ACCOUNT_ID": str(config.r2_account_id or service._cloudflare_account_id() or ""),
    }
    async def run(title, cmd, *, cwd=root, environment=None, timeout=1800):
        await service._run_logged_command(task.task_uuid, title=title, cmd=cmd, cwd=cwd,
            env=environment or {}, metadata={"release_job_id": job_id, "reports_only": True},
            timeout_seconds=timeout)

    old_index_path = paths[0] / "index.json"
    old_index = json.loads(old_index_path.read_text()) if old_index_path.is_file() else []
    try:
        await runtime.task_manager.update_task_progress(task.task_uuid, 5)
        await run("Export Reports Data", [str(python), "scripts/generate_site_data.py", "--reports-only"], timeout=runtime.generate_timeout_seconds)
        new_index = paths[0] / "index.json"
        if not new_index.is_file():
            raise RuntimeError("Reports export did not produce an index")
        if old_index and not json.loads(new_index.read_text()):
            raise RuntimeError("Reports export became empty; refusing to replace a nonempty published report catalogue")
        await runtime.task_manager.update_task_progress(task.task_uuid, 25)
        await run("Build Reports Site", ["node", "scripts/build-reports.mjs"], cwd=astro,
            environment={"GIDS_REPORTS_BUILD_OUTPUT": str(stage)}, timeout=runtime.astro_build_timeout_seconds)
        await runtime.task_manager.update_task_progress(task.task_uuid, 55)
        await run("Split Reports Bundle", [str(python), "scripts/split_reports_bundle.py", "--dist-dir", str(stage),
            "--reports-dir", str(reports_dir), "--main-origin", str(config.main_site_origin or DEFAULT_MAIN_SITE_ORIGIN),
            "--reports-origin", origin, "--skip-budget"])
        await runtime.task_manager.update_task_progress(task.task_uuid, 70)
        await run("Publish Reports Bundle to R2", service._publish_reports_r2_command(python_path=python),
            environment=env, timeout=runtime.download_publish_timeout_seconds)
        from .report_subscription import dispatch_if_enabled

        report_subscription_dispatch = await dispatch_if_enabled(service, runtime, reports_origin=origin)
        await runtime.task_manager.add_workbook_entry(
            task.task_uuid, entry_type="info", title="Website Verification Skipped",
            content="Reports upload completed; public HTTP verification is disabled. Artifacts are retained without rollback.",
            content_type="text", metadata={"event": "website_verification_skipped", "release_job_id": job_id},
        )
        result = {"release_job_id": job_id, "reports_only": True, "r2_reports_published": True,
            "r2_reports_public_base_url": origin, "pages_deployed": False, "website_verification": "skipped", "artifact_rollback": False,
            "report_subscription_dispatch": report_subscription_dispatch,
            "release_timing": {"elapsed_seconds": round(time.monotonic() - started, 3)}}
        async with runtime.get_database() as db:
            stored = await db.get(runtime.task_model, task.id)
            if stored is not None:
                stored.output_data = result
                await db.commit()
        await runtime.task_manager.add_workbook_entry(task.task_uuid, entry_type="success",
            title="Reports Release Completed", content=json.dumps(result), content_type="json",
            metadata={"event": "reports_release_completed", "release_job_id": job_id})
        await runtime.task_manager.update_task_progress(task.task_uuid, 100)
        return result
    finally:
        if stage.exists():
            await asyncio.to_thread(shutil.rmtree, stage)
