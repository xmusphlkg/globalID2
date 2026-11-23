"""Reclaim byte-identical Git-archived manifests from completed surveillance runs."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db
from src.domain.task import Task, TaskStatus, TaskType
from src.services.data_release.download_manifest_retention import retire_download_manifest


async def cleanup(*, apply: bool) -> int:
    ids = []
    for manifest in (ROOT / "exports/surveillance-runs").glob("*/downloads/manifest.json"):
        task_id = manifest.parents[1].name
        try:
            if str(UUID(task_id)) == task_id:
                ids.append(task_id)
        except ValueError:
            continue
    total = matched = errors = 0
    for offset in range(0, len(ids), 100):
        async with get_db() as db:
            rows = (await db.execute(select(
                Task.task_uuid, Task.task_type, Task.status, Task.input_data, Task.output_data,
            ).where(Task.task_uuid.in_(ids[offset:offset + 100]), Task.task_type == TaskType.EXPORT_DATA,
                    Task.status == TaskStatus.COMPLETED))).all()
        for row in rows:
            try:
                size = retire_download_manifest(ROOT, row, apply=apply)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
                print(f"Skipped {row.task_uuid}: {exc}", file=sys.stderr)
                errors += 1
                continue
            if size:
                matched += 1
                total += size
    print(json.dumps({"applied": apply, "candidates": len(ids), "matched": matched,
                      "allocated_bytes": total, "errors": errors}))
    return int(errors > 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete verified copies (default: report only)")
    sys.exit(asyncio.run(cleanup(apply=parser.parse_args().apply)))
