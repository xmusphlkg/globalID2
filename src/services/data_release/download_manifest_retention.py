"""Retire private download manifests already archived by completed releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID

from src.domain.task import TaskStatus, TaskType


def retire_download_manifest(root: Path, task, *, apply: bool) -> int:
    """Return allocated bytes eligible for removal, verifying the exact Git blob."""
    result = task.output_data
    if (task.task_type != TaskType.EXPORT_DATA or task.status != TaskStatus.COMPLETED
            or not isinstance(result, dict) or not isinstance(task.input_data, dict)
            or result.get("data_line") != "surveillance" or result.get("direct_downloads_published") is not True):
        return 0
    task_id = task.task_uuid
    if str(UUID(task_id)) != task_id or result.get("release_id") != f"data-{task_id}":
        return 0
    exports = (root / "exports").resolve()
    run = exports / "surveillance-runs" / task_id
    manifest = run / "downloads/manifest.json"
    if manifest.resolve() != manifest or not manifest.is_file():
        return 0

    ready = json.loads((run / "ready.json").read_bytes())
    published = json.loads((run / "downloads-published.json").read_bytes())
    expected = result.get("downloads_manifest_sha256")
    branch = task.input_data.get("download_repo_branch")
    repo_url = task.input_data.get("github_repo_url")
    if (not branch or not repo_url or not re.fullmatch("[0-9a-f]{64}", expected or "")
            or ready.get("task_uuid") != task_id or ready.get("downloads_manifest_sha256") != expected
            or published != {"task_uuid": task_id, "manifest_sha256": expected,
                             "repo_url": repo_url, "branch": branch}):
        return 0

    workdir = exports / "site-downloads"
    if not (workdir / ".git").exists():
        return 0
    subject = f"chore(data): publish surveillance data-{task_id}"
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--fixed-strings", f"--grep={subject}",
         f"refs/heads/{branch}", "--"],
        cwd=workdir, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not commit:
        return 0
    # Prove the archived bytes can actually be restored, including packed deltas.
    with subprocess.Popen(["git", "cat-file", "blob", f"{commit}:manifest.json"], cwd=workdir,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as archived:
        digest = hashlib.file_digest(archived.stdout, "sha256").hexdigest()
        if archived.wait() or digest != expected:
            return 0
    try:
        with manifest.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_nlink != 1 or hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                return 0
            current = manifest.lstat()
            if (stat.st_dev, stat.st_ino, stat.st_ctime_ns) != (current.st_dev, current.st_ino, current.st_ctime_ns):
                return 0
            if apply:
                manifest.unlink()
        return stat.st_blocks * 512
    except FileNotFoundError:
        # A simultaneous maintenance command can retire this copy first.
        return 0
