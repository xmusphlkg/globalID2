"""Situation's exact dirty-ID publication, independent of observation projection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from src.generation.surveillance_lock import LOCKED_ENV, surveillance_output_lock
from .surveillance_publish import validate_manifest
from .global_selection import acknowledge_global_selection, validate_global_selection


async def validate_situation_selection(db, dirty_ids: list[int], *, task_uuid: str | None = None) -> None:
    await validate_global_selection(db, dirty_ids, line="situation", task_uuid=task_uuid)


def _ready_candidate(run: Path, *, task_uuid: str, dirty_ids: list[int]) -> dict | None:
    try:
        receipt = json.loads((run / "ready.json").read_text())
    except FileNotFoundError:
        return None
    if (receipt.get("schema") != 1 or receipt.get("task_uuid") != task_uuid
            or receipt.get("dirty_ids") != sorted(dirty_ids)):
        raise ValueError("Frozen Situation candidate selection changed")
    body = (run / "current.json").read_bytes()
    manifest, _ = validate_manifest(body)
    if (manifest["release_id"] != f"situation-{task_uuid}"
            or hashlib.sha256(body).hexdigest() != receipt.get("manifest_sha256")):
        raise ValueError("Frozen Situation candidate changed")
    return receipt


def _freeze_candidate(run: Path, public: Path, *, task_uuid: str, dirty_ids: list[int]) -> dict:
    body = (public / "current.json").read_bytes()
    manifest, objects = validate_manifest(body)
    if manifest["release_id"] != f"situation-{task_uuid}":
        raise ValueError("Situation generation belongs to another task")
    for relative, expected in objects.items():
        path = public / relative
        if path.is_symlink() or not path.resolve().is_relative_to(public.resolve()):
            raise ValueError("Unsafe immutable Situation object")
        payload = path.read_bytes()
        if len(payload) != expected["bytes"] or hashlib.sha256(payload).hexdigest() != expected["sha256"]:
            raise ValueError("Situation object checksum mismatch")
    run.mkdir(parents=True, exist_ok=True)
    (run / "current.json").write_bytes(body)
    receipt = {"schema": 1, "task_uuid": task_uuid, "dirty_ids": sorted(dirty_ids),
        "manifest_sha256": hashlib.sha256(body).hexdigest()}
    temporary = run / ".ready.json"
    temporary.write_text(json.dumps(receipt, sort_keys=True))
    temporary.replace(run / "ready.json")
    return receipt


async def execute_situation_data_release(service, task, *, runtime) -> dict:
    data = dict(task.input_data or {})
    dirty_ids = sorted(data.get("publish_dirty_ids") or [])
    if data.get("incremental_country_codes"):
        raise ValueError("Situation JSON does not accept surveillance scopes")
    if Path(task.task_uuid).name != task.task_uuid or task.task_uuid in {"", ".", ".."}:
        raise ValueError("Invalid Situation release task UUID")
    started = time.monotonic()
    async with runtime.get_database() as db:
        await validate_situation_selection(db, dirty_ids, task_uuid=task.task_uuid)
    root = runtime.root_dir
    state, public = root / "exports/situation-state", root / "exports/situation-data"
    run = root / "exports/situation-runs" / task.task_uuid
    receipt = await asyncio.to_thread(_ready_candidate, run, task_uuid=task.task_uuid, dirty_ids=dirty_ids)
    from .publication_recovery import require_candidate, save_recovery
    require_candidate(service, receipt is not None)
    python = service._python_executable()
    config = service._config()
    env = {"CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
        "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
        "R2_PAPERS_BUCKET": str(getattr(config, "r2_papers_bucket", "globalid-papers")),
        "R2_ACCESS_KEY_ID": str(getattr(config, "r2_access_key_id", "")),
        "R2_SECRET_ACCESS_KEY": str(getattr(config, "r2_secret_access_key", "")),
        "R2_ENDPOINT_URL": str(getattr(config, "r2_endpoint_url", "")),
        "R2_ACCOUNT_ID": str(getattr(config, "r2_account_id", "") or service._cloudflare_account_id())}
    expected_generations = data.get("publication_expected_generations") or {}
    if "situation" in expected_generations:
        env["GIDS_PUBLICATION_EXPECTED_BASELINE_JSON"] = json.dumps(expected_generations["situation"])
    export_seconds = 0.0
    if receipt is None:
        export_started = time.monotonic()
        run.mkdir(parents=True, exist_ok=True)
        await service._run_logged_command(task.task_uuid, title="Capture Situation Publication Baseline",
            cmd=[str(python), "scripts/publish_surveillance_data.py", "--source-dir", str(run),
                "--prefix", "site-data/situation", "--capture-baseline-only"],
            cwd=root, env=env, metadata={"event": "situation_json_publish", "operation": "capture_baseline"},
            timeout_seconds=runtime.download_publish_timeout_seconds)
        async with surveillance_output_lock(state, public):
            async with runtime.read_snapshot() as snapshot_env:
                await service._run_logged_command(task.task_uuid, title="Export Situation JSON",
                    cmd=[str(python), "scripts/export_situation_data.py", "--state-dir", str(state),
                        "--public-dir", str(public), "--release-id", f"situation-{task.task_uuid}"],
                    cwd=root, env={**snapshot_env, "PYTHONUNBUFFERED": "1", LOCKED_ENV: "1"},
                    metadata={"event": "situation_json_export"}, timeout_seconds=runtime.generate_timeout_seconds)
            receipt = await asyncio.to_thread(_freeze_candidate, run, public, task_uuid=task.task_uuid, dirty_ids=dirty_ids)
        export_seconds = time.monotonic() - export_started
    await asyncio.to_thread(save_recovery, root, task, run / "ready.json")
    async with runtime.get_database() as db:
        await validate_situation_selection(db, dirty_ids, task_uuid=task.task_uuid)
    await runtime.task_manager.update_task_progress(task.task_uuid, 65)
    publish_started = time.monotonic()
    await service._run_logged_command(task.task_uuid, title="Publish Situation JSON",
        cmd=[str(python), "scripts/publish_surveillance_data.py", "--source-dir", str(run),
            "--objects-dir", str(public / "objects"), "--prefix", "site-data/situation", "--require-starting-baseline"],
        cwd=root, env=env, metadata={"event": "situation_json_publish"},
        timeout_seconds=runtime.download_publish_timeout_seconds)
    async with runtime.get_database() as db:
        await acknowledge_global_selection(db, dirty_ids, line="situation", task_uuid=task.task_uuid)
        result = {"data_only": True, "data_line": "situation", "release_id": f"situation-{task.task_uuid}",
            "manifest_sha256": receipt["manifest_sha256"], "publish_dirty_ids": dirty_ids,
            "release_timing": {"export_seconds": round(export_seconds, 3),
                "publish_and_ack_seconds": round(time.monotonic() - publish_started, 3),
                "elapsed_seconds": round(time.monotonic() - started, 3)}, "pages_deployed": False}
        if getattr(task, "id", None) is not None:
            stored = await db.get(runtime.task_model, task.id)
            if stored is not None:
                stored.output_data = result
        await db.commit()
    await runtime.task_manager.add_workbook_entry(task.task_uuid, entry_type="info",
        title="Situation JSON Published", content=json.dumps(result), content_type="json",
        metadata={"event": "situation_json_completed", **result})
    return result
