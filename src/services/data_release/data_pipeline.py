"""Surveillance JSON release, independent of static templates and HTML output."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path

from src.generation.release_projection_snapshot import validate_read_snapshot_projection

from .data_selection import validate_data_selection
from .projection import commit_release_projection
from .surveillance_publish import validate_manifest


def _validate_projection_scopes(directory: Path, codes: list[str], *, full: bool, available_codes: list[str] | None = None) -> dict:
    projection = validate_read_snapshot_projection(directory)
    scopes = set(projection["countries"])
    if (not scopes or (not full and scopes != set(codes)) or not set(codes) <= scopes):
        raise ValueError("Frozen JSON projection does not match selected country scopes")
    if full and available_codes is not None and scopes != set(available_codes):
        raise ValueError("Full JSON projection does not match all available country scopes")
    return projection


def _ready_candidate(run: Path, *, task_uuid: str, codes: list[str], dirty_ids: list[int], available_codes: list[str] | None = None, downloads: bool = False) -> dict | None:
    try:
        receipt = json.loads((run / "ready.json").read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise RuntimeError("Frozen JSON candidate receipt is damaged") from error
    if (receipt.get("schema") != 1 or receipt.get("task_uuid") != task_uuid
            or receipt.get("country_codes") != sorted(codes) or receipt.get("dirty_ids") != sorted(dirty_ids)):
        raise RuntimeError("Frozen JSON candidate does not match the selected database changes")
    body = (run / "current.json").read_bytes()
    if hashlib.sha256(body).hexdigest() != receipt.get("manifest_sha256"):
        raise RuntimeError("Frozen JSON candidate manifest changed")
    manifest, _ = validate_manifest(body)
    if manifest["release_id"] != f"data-{task_uuid}":
        raise ValueError("Frozen JSON manifest has a different task identity")
    _validate_projection_scopes(run / "_release-projection", codes, full=receipt["projection_full"], available_codes=available_codes)
    if downloads:
        body = (run / "downloads/manifest.json").read_bytes()
        if (hashlib.sha256(body).hexdigest() != receipt.get("downloads_manifest_sha256")
                or json.loads(body).get("surveillance_release_id") != f"data-{task_uuid}"):
            raise RuntimeError("Frozen download candidate does not match surveillance release")
    return receipt


def _freeze_candidate(run: Path, state: Path, public: Path, *, task_uuid: str,
                      codes: list[str], dirty_ids: list[int], full: bool, available_codes: list[str] | None = None, downloads: bool = False) -> dict:
    body = (public / "current.json").read_bytes()
    manifest, objects = validate_manifest(body)
    if manifest["release_id"] != f"data-{task_uuid}":
        raise ValueError("Generated JSON manifest has a different task identity")
    _validate_projection_scopes(state / "_release-projection", codes, full=full, available_codes=available_codes)
    run.mkdir(parents=True, exist_ok=True)
    # A failed pre-receipt freeze is never reusable. Rebuild this task's private
    # projection. Objects stay in the shared immutable store: hardlinking them
    # would change inode ctime and invalidate the completed-generation receipt.
    for name in ("_release-projection",):
        target = run / name
        if target.exists():
            shutil.rmtree(target)
    for relative in objects:
        source = public / relative
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(public.resolve()):
            raise ValueError("Unsafe immutable data object")
    shutil.copytree(state / "_release-projection", run / "_release-projection")
    _validate_projection_scopes(run / "_release-projection", codes, full=full, available_codes=available_codes)
    if (public / "current.json").read_bytes() != body:
        raise RuntimeError("Surveillance generation changed while freezing the candidate")
    (run / "current.json").write_bytes(body)
    receipt = {"schema": 1, "task_uuid": task_uuid, "country_codes": sorted(codes),
        "dirty_ids": sorted(dirty_ids), "projection_full": full,
        "manifest_sha256": hashlib.sha256(body).hexdigest()}
    if downloads:
        download_body = (run / "downloads/manifest.json").read_bytes()
        if json.loads(download_body).get("surveillance_release_id") != f"data-{task_uuid}":
            raise ValueError("Download candidate has a different release identity")
        receipt["downloads_manifest_sha256"] = hashlib.sha256(download_body).hexdigest()
    temporary = run / ".ready.json"
    temporary.write_text(json.dumps(receipt, sort_keys=True))
    temporary.replace(run / "ready.json")
    return receipt


async def execute_data_release(service, task, *, runtime) -> dict:
    data = dict(task.input_data or {})
    if any(data.get(flag) for flag in ("reports_only", "code_only", "raw_archive_only")):
        raise ValueError("JSON release cannot include a static publication mode")
    if data.get("data_line") == "situation":
        from .situation_data_pipeline import execute_situation_data_release
        return await execute_situation_data_release(service, task, runtime=runtime)
    if data.get("data_line") == "literature":
        from .literature_data_pipeline import execute_literature_data_release
        return await execute_literature_data_release(service, task, runtime=runtime)
    if data.get("data_line") not in (None, "surveillance"):
        raise ValueError("Unknown JSON publication line")
    codes = sorted(data.get("incremental_country_codes") or [])
    dirty_ids = sorted(data.get("publish_dirty_ids") or [])
    if not codes or not dirty_ids or any(type(value) is not int or value <= 0 for value in dirty_ids):
        raise ValueError("JSON release requires exact country scopes and dirty IDs")
    if Path(task.task_uuid).name != task.task_uuid or task.task_uuid in {"", ".", ".."}:
        raise ValueError("Invalid data release task UUID")
    started = time.monotonic()
    async with runtime.get_database() as db:
        selected_plan = await validate_data_selection(db, country_codes=codes, dirty_ids=dirty_ids, expected_task_uuid=task.task_uuid)
    root = runtime.root_dir
    state, public = root / "exports/surveillance-state", root / "exports/surveillance-data"
    run = root / "exports/surveillance-runs" / task.task_uuid
    downloads = bool(data.get("include_git_push"))
    receipt = await asyncio.to_thread(_ready_candidate, run, task_uuid=task.task_uuid,
        codes=codes, dirty_ids=dirty_ids, available_codes=selected_plan["available_country_codes"], downloads=downloads)
    python = service._python_executable()
    config = service._config()
    env = {"CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
        "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
        "R2_PAPERS_BUCKET": str(getattr(config, "r2_papers_bucket", "globalid-papers")),
        "R2_ACCESS_KEY_ID": str(getattr(config, "r2_access_key_id", "")),
        "R2_SECRET_ACCESS_KEY": str(getattr(config, "r2_secret_access_key", "")),
        "R2_ENDPOINT_URL": str(getattr(config, "r2_endpoint_url", "")),
        "R2_ACCOUNT_ID": str(getattr(config, "r2_account_id", "") or service._cloudflare_account_id())}
    export_seconds = 0.0
    if receipt is None:
        from src.generation.surveillance_data_export import completed_generation
        from src.generation.surveillance_lock import (
            LOCKED_ENV,
            surveillance_output_lock,
        )
        export_started = time.monotonic()
        run.mkdir(parents=True, exist_ok=True)
        await service._run_logged_command(task.task_uuid, title="Capture Surveillance Publication Baseline",
            cmd=[str(python), "scripts/publish_surveillance_data.py", "--source-dir", str(run),
                "--capture-baseline-only"], cwd=root, env=env,
            metadata={"event": "surveillance_json_publish", "operation": "capture_baseline"},
            timeout_seconds=runtime.download_publish_timeout_seconds)
        async with surveillance_output_lock(state, public):
            full = await asyncio.to_thread(completed_generation, state, public) is None
            if downloads:
                from src.generation.surveillance_downloads import downloads_initialized
                # First coupled publication catches up the old standalone
                # archive, including countries outside this batch's dirty IDs.
                full = full or not downloads_initialized(root / "exports/site-downloads")
            cmd = [str(python), "scripts/generate_site_data.py", "--data-only",
                "--surveillance-state", str(state), "--surveillance-output", str(public),
                "--data-release-id", f"data-{task.task_uuid}", "--exact-country-scopes"]
            if downloads:
                cmd.extend(["--surveillance-downloads", str(run / "downloads"),
                    "--direct-download-output", str(root / "exports/site-downloads"),
                    "--direct-download-url-base", data["download_url_base"]])
            changes = selected_plan.get("changes")
            if changes:
                run.mkdir(parents=True, exist_ok=True)
                changes_file = run / "selected-changes.json"
                changes_file.write_text(json.dumps(changes, ensure_ascii=False))
                cmd.extend(["--surveillance-changes-file", str(changes_file)])
            if not full:
                for code in codes:
                    cmd.extend(["--incremental-country", code])
            async with runtime.read_snapshot() as snapshot_env:
                await service._run_logged_command(task.task_uuid, title="Export Surveillance JSON",
                    cmd=cmd, cwd=root, env={**snapshot_env, "PYTHONUNBUFFERED": "1", LOCKED_ENV: "1"},
                    metadata={"event": "surveillance_json_export", "initialization": full},
                    timeout_seconds=runtime.generate_timeout_seconds)
            receipt = await asyncio.to_thread(_freeze_candidate, run, state, public,
                task_uuid=task.task_uuid, codes=codes, dirty_ids=dirty_ids, full=full,
                available_codes=selected_plan["available_country_codes"], downloads=downloads)
        export_seconds = time.monotonic() - export_started
    async with runtime.get_database() as db:
        before_publish = await validate_data_selection(db, country_codes=codes, dirty_ids=dirty_ids, expected_task_uuid=task.task_uuid)
    await asyncio.to_thread(_validate_projection_scopes, run / "_release-projection", codes,
        full=receipt["projection_full"], available_codes=before_publish["available_country_codes"])
    await runtime.task_manager.update_task_progress(task.task_uuid, 65)
    publish_started = time.monotonic()
    await service._run_logged_command(task.task_uuid, title="Publish Surveillance JSON",
        cmd=[str(python), "scripts/publish_surveillance_data.py", "--source-dir", str(run),
             "--objects-dir", str(public / "objects"), "--require-starting-baseline"],
        cwd=root, env=env, metadata={"event": "surveillance_json_publish"},
        timeout_seconds=runtime.download_publish_timeout_seconds)
    if downloads:
        from .surveillance_downloads import publish_download_candidate
        await publish_download_candidate(service, task, runtime=runtime, run=run,
            manifest_sha256=receipt["downloads_manifest_sha256"])
    # Upload success publishes only the frozen database snapshot. New dirty IDs
    # arriving during export/upload remain pending for the next JSON release.
    async with runtime.get_database() as db:
        await validate_data_selection(db, country_codes=codes, dirty_ids=dirty_ids, expected_task_uuid=task.task_uuid)
        await commit_release_projection(db, task_uuid=task.task_uuid, country_codes=codes,
            full=receipt["projection_full"], dirty_ids=dirty_ids, country_scopes_exact=True,
            prepared_dir=run / "_release-projection", require_prepared=True)
        result = {"data_only": True, "data_line": "surveillance", "release_id": f"data-{task.task_uuid}",
            "country_codes": codes, "manifest_sha256": receipt["manifest_sha256"],
            "direct_downloads_published": downloads, "git_pushed": downloads,
            "downloads_manifest_sha256": receipt.get("downloads_manifest_sha256"),
            "release_timing": {"export_seconds": round(export_seconds, 3),
                "publish_and_ack_seconds": round(time.monotonic() - publish_started, 3),
                "elapsed_seconds": round(time.monotonic() - started, 3)}, "pages_deployed": False}
        if getattr(task, "id", None) is not None:
            stored = await db.get(runtime.task_model, task.id)
            if stored is not None:
                stored.output_data = result
        await db.commit()
    await runtime.task_manager.add_workbook_entry(task.task_uuid, entry_type="info",
        title="Surveillance JSON Published", content=json.dumps(result), content_type="json",
        metadata={"event": "surveillance_json_completed", **result})
    if downloads:
        # The durable publication receipt makes finalization retries safe even
        # after these private hard links have been removed.
        for name in ("countries", "diseases"):
            await asyncio.to_thread(shutil.rmtree, run / "downloads" / name, True)
    return result
