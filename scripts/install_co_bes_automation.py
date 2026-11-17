#!/usr/bin/env python3
"""Pin the BES Python runtime and install its independent systemd schedule.

Does not change the accepted website release, task workers or global scheduler.
Environment, evidence and caches stay in the original checkout. No secrets are
copied into the content-addressed code snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ("globalid-co-bes-model-dispatch.service", "globalid-co-bes-refresh.service",
         "globalid-co-bes-refresh.timer", "globalid-co-flunet-refresh.service",
         "globalid-co-flunet-refresh.timer", "globalid-co-microdatos-refresh.service",
         "globalid-co-microdatos-refresh.timer")
SUFFIXES = {".py", ".json", ".csv", ".yml", ".yaml", ".txt", ".md", ".pem", ".sh",
            ".service", ".timer", ".conf", ".toml"}


def freeze_runtime(root: Path = ROOT) -> Path:
    """Publish a complete snapshot before atomically selecting it for next start."""
    files = {}
    for folder in ("src", "scripts", "configs"):
        for path in sorted((root / folder).rglob("*")):
            if (path.is_file() and not path.is_symlink() and path.suffix in SUFFIXES
                    and "__pycache__" not in path.parts and not path.name.startswith(".env")):
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(serialized).hexdigest()
    runtime = root / "data/runtime/co-bes"
    releases = runtime / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / digest
    if not target.exists():
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
        try:
            for relative, expected in files.items():
                payload = (root / relative).read_bytes()
                if hashlib.sha256(payload).hexdigest() != expected:
                    raise RuntimeError("Source changed during snapshot; rerun installation")
                copied = stage / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(payload)
            for name in ("data", "venv", ".env"):
                (stage / name).symlink_to(root / name, target_is_directory=name != ".env")
            (stage / "runtime-manifest.json").write_text(
                json.dumps({"sha256": digest, "files": files}, sort_keys=True, indent=2) + "\n")
            stage.rename(target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    # Validate an existing snapshot as well; never silently reuse changed code.
    for relative, expected in files.items():
        if hashlib.sha256((target / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Pinned BES runtime integrity mismatch")
    link = runtime / f".current-{os.getpid()}"
    try:
        link.symlink_to(target, target_is_directory=True)
        link.replace(runtime / "current")
    finally:
        link.unlink(missing_ok=True)
    return target


def install(root: Path = ROOT, *, apply: bool = False):
    if not apply:
        return {"mode": "preview", "units": UNITS, "schedule": "hourly at :45 America/Bogota"}
    pinned = freeze_runtime(root)
    for unit in UNITS:
        source = root / "configs/systemd" / unit
        subprocess.run(["sudo", "-n", "install", "-m", "0644", str(source),
                        f"/etc/systemd/system/{unit}"], check=True)
    subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "-n", "systemctl", "enable", "--now",
                    "globalid-co-bes-model-dispatch.service", "globalid-co-bes-refresh.timer",
                    "globalid-co-flunet-refresh.timer", "globalid-co-microdatos-refresh.timer"], check=True)
    return {"mode": "installed", "runtime": str(pinned), "units": UNITS,
            "note": "New code applies on next service start; a running dispatcher keeps its pinned code"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(install(apply=parser.parse_args().apply), indent=2))
