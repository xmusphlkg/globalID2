"""Reuse one already-published research snapshot in a static site build."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID


def install_published_research(root: Path, output: Path, public: Path) -> str:
    """Install a coherent published snapshot without re-querying the database.

    Both trees are prepared before replacing either. Roll back the first swap if
    the second fails; an interrupted run never acknowledges a publication.
    """
    receipt = json.loads((root / "exports/research-current-receipt.json").read_bytes())
    task_id = str(UUID(receipt["task_uuid"]))
    release_id = receipt["release_id"]
    if release_id != f"literature-{task_id}":
        raise ValueError("Research receipt does not identify its snapshot")
    snapshot = root / "exports/research-runs" / task_id / "snapshot"
    sources = [snapshot / "build/research", snapshot / "public/research"]
    for path in (sources[0] / "routes.json", sources[0] / "index.json", sources[1] / "index.json"):
        if not path.is_file():
            raise ValueError(f"Published research snapshot is missing {path.name}")
    destinations = [output / "research", public / "research"]
    if any(path.is_symlink() for path in sources + destinations):
        raise ValueError("Research snapshot directories must not be symlinks")

    lock_path = root / "data/cache/research-public-artifacts.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        staged: list[Path] = []
        backups: list[tuple[Path, Path | None]] = []
        try:
            for source, dest in zip(sources, destinations):
                dest.parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".research-stage-", dir=dest.parent))
                staged.append(stage)
                # GNU cp uses copy-on-write when the filesystem supports it.
                import subprocess
                subprocess.run(["cp", "-a", "--reflink=auto", str(source) + "/.", str(stage)], check=True)
            for stage, dest in zip(staged, destinations):
                backup = None
                if dest.exists():
                    backup = Path(tempfile.mkdtemp(prefix=".research-backup-", dir=dest.parent))
                    backup.rmdir()
                    os.replace(dest, backup)
                backups.append((dest, backup))
                os.replace(stage, dest)
        except BaseException:
            for dest, backup in reversed(backups):
                if dest.exists():
                    shutil.rmtree(dest)
                if backup is not None:
                    os.replace(backup, dest)
            raise
        finally:
            for path in staged:
                if path.exists():
                    shutil.rmtree(path)
        for _, backup in backups:
            if backup is not None:
                shutil.rmtree(backup)
    print(f"  Published research snapshot reused: {release_id}", flush=True)
    return release_id
