#!/usr/bin/env python3
"""Run the gated Situation Room static-release pipeline.

This command deliberately stops at a deployment-ready ``astro-site/dist``.
Cloudflare deployment is a separate CI job so a failed analysis, export, build,
or release gate can never advance to deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
ASTRO_DIR = ROOT / "astro-site"
DEFAULT_ARTIFACT_ROOT = ROOT / "exports" / "automation" / "situation-release"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RELEASE_PATHS = frozenset(
    {
        "situation/index.html",
        "sitemaps/situation.xml",
        "site-data/situation/v3/latest.json",
        "site-data/situation/latest.json",
    }
)


@dataclass(frozen=True)
class PipelineStep:
    name: str
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int


def build_steps(
    *,
    python_executable: str,
    live_timeout_seconds: int = 30 * 60,
    export_timeout_seconds: int = 30 * 60,
    build_timeout_seconds: int = 20 * 60,
) -> list[PipelineStep]:
    """Return the single authoritative, fail-closed release sequence."""

    return [
        PipelineStep(
            "live_source_analysis",
            (python_executable, "scripts/update_situation_room.py"),
            ROOT,
            live_timeout_seconds,
        ),
        PipelineStep(
            "contract_export",
            (python_executable, "scripts/export_situation_v3_contracts.py"),
            ROOT,
            10 * 60,
        ),
        PipelineStep(
            "contract_drift_check",
            (python_executable, "scripts/automation/check_contract_drift.py"),
            ROOT,
            2 * 60,
        ),
        PipelineStep(
            "site_data_export",
            (python_executable, "scripts/generate_site_data.py"),
            ROOT,
            export_timeout_seconds,
        ),
        PipelineStep(
            "astro_build",
            ("npm", "run", "build:astro"),
            ASTRO_DIR,
            build_timeout_seconds,
        ),
        PipelineStep(
            "release_gate",
            (
                python_executable,
                "scripts/validate_situation_release.py",
                "--site-dir",
                "astro-site/dist",
            ),
            ROOT,
            5 * 60,
        ),
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dist_inventory(dist_dir: Path, *, root_label: str | None = None) -> dict:
    """Return a content-addressed inventory for the complete static artifact.

    The tree digest binds every relative path, byte size, and file digest.  A
    deploy job can therefore prove that the downloaded artifact is exactly the
    one that passed the release gate, instead of trusting only an artifact name
    or a boolean in the manifest.
    """

    if not dist_dir.is_dir():
        raise RuntimeError(f"Astro output does not exist: {dist_dir}")
    paths = sorted(dist_dir.rglob("*"))
    symlinks = [path for path in paths if path.is_symlink()]
    if symlinks:
        raise RuntimeError(
            "Astro output must not contain symbolic links: "
            + ", ".join(path.relative_to(dist_dir).as_posix() for path in symlinks[:10])
        )
    files = [path for path in paths if path.is_file()]
    if not files:
        raise RuntimeError(f"Astro output is empty: {dist_dir}")
    entries: list[tuple[str, int, str]] = []
    for path in files:
        relative = path.relative_to(dist_dir).as_posix()
        entries.append((relative, path.stat().st_size, _sha256(path)))
    relative_paths = {relative for relative, _, _ in entries}
    missing_release_paths = sorted(RELEASE_PATHS - relative_paths)
    if missing_release_paths:
        raise RuntimeError(
            "Astro output is missing required release files: "
            + ", ".join(missing_release_paths)
        )
    tree_digest = hashlib.sha256()
    for relative, size, digest in entries:
        tree_digest.update(
            json.dumps(
                [relative, size, digest],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        tree_digest.update(b"\n")
    if root_label is None:
        try:
            root_label = str(dist_dir.relative_to(ROOT))
        except ValueError:
            root_label = dist_dir.name
    return {
        "root": root_label,
        "file_count": len(entries),
        "total_bytes": sum(size for _, size, _ in entries),
        "tree_sha256": tree_digest.hexdigest(),
        "release_files": {
            relative: {
                "bytes": size,
                "sha256": digest,
            }
            for relative, size, digest in entries
            if relative in RELEASE_PATHS
        },
    }


# Backwards-compatible internal alias used by existing tests and callers.
_dist_inventory = dist_inventory


def _validate_runtime(*, require_env: bool, python_executable: str) -> list[str]:
    warnings: list[str] = []
    required_paths = [
        ROOT / "scripts" / "update_situation_room.py",
        ROOT / "scripts" / "export_situation_v3_contracts.py",
        ROOT / "scripts" / "automation" / "check_contract_drift.py",
        ROOT / "scripts" / "generate_site_data.py",
        ROOT / "scripts" / "validate_situation_release.py",
        ASTRO_DIR / "package-lock.json",
        ROOT / "dashboard" / "package-lock.json",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise RuntimeError("Required release files are missing: " + ", ".join(missing))
    if not Path(python_executable).exists() and shutil.which(python_executable) is None:
        raise RuntimeError(f"Python executable is unavailable: {python_executable}")
    for executable in ("git", "npm"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"Required executable is unavailable: {executable}")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if require_env and not database_url:
        raise RuntimeError("DATABASE_URL must be provided by the CI secret store")
    if database_url and not database_url.lower().startswith(("postgresql://", "postgresql+asyncpg://")):
        raise RuntimeError("DATABASE_URL must use a PostgreSQL scheme")
    if not os.getenv("SITUATION_HISTORY_DATABASE_URL", "").strip():
        warnings.append(
            "SITUATION_HISTORY_DATABASE_URL is unset; the application will derive the history database from DATABASE_URL."
        )
    return warnings


def _run_step(step: PipelineStep, log_path: Path) -> dict:
    started_at = _utc_now()
    started = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"step={step.name}\nstarted_at={started_at}\ncwd={step.cwd}\n")
        log.flush()
        try:
            process = subprocess.Popen(
                list(step.command),
                cwd=step.cwd,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "CI": "1"},
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            try:
                exit_code = int(process.wait(timeout=step.timeout_seconds) or 0)
            except subprocess.TimeoutExpired:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if hasattr(os, "killpg"):
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                raise
            if exit_code != 0:
                raise subprocess.CalledProcessError(exit_code, step.command)
        except subprocess.TimeoutExpired:
            duration = round(time.monotonic() - started, 3)
            log.write(f"\nstatus=timed_out\nduration_seconds={duration}\n")
            return {
                "name": step.name,
                "status": "timed_out",
                "started_at": started_at,
                "finished_at": _utc_now(),
                "duration_seconds": duration,
                "timeout_seconds": step.timeout_seconds,
                "log": log_path.name,
            }
        except subprocess.CalledProcessError as exc:
            duration = round(time.monotonic() - started, 3)
            log.write(f"\nstatus=failed\nexit_code={exc.returncode}\nduration_seconds={duration}\n")
            return {
                "name": step.name,
                "status": "failed",
                "exit_code": exc.returncode,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "duration_seconds": duration,
                "timeout_seconds": step.timeout_seconds,
                "log": log_path.name,
            }

    sys.stdout.write(log_path.read_text(encoding="utf-8", errors="replace"))
    sys.stdout.flush()
    duration = round(time.monotonic() - started, 3)
    return {
        "name": step.name,
        "status": "passed",
        "exit_code": 0,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_seconds": duration,
        "timeout_seconds": step.timeout_seconds,
        "log": log_path.name,
    }


def _run_id(value: str | None) -> str:
    candidate = value or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not RUN_ID_RE.fullmatch(candidate):
        raise ValueError("run id must contain only letters, digits, '.', '_', and '-'")
    return candidate


def _dry_run_payload(steps: Sequence[PipelineStep]) -> dict:
    return {
        "status": "dry_run",
        "deployment_ready": False,
        "steps": [
            {
                **asdict(step),
                "command": list(step.command),
                "cwd": str(step.cwd),
            }
            for step in steps
        ],
    }


def run(args: argparse.Namespace) -> int:
    run_id = _run_id(args.run_id)
    artifact_dir = args.artifact_root.resolve() / run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = artifact_dir / "manifest.json"
    steps = build_steps(
        python_executable=args.python,
        live_timeout_seconds=args.live_timeout_seconds,
        export_timeout_seconds=args.export_timeout_seconds,
        build_timeout_seconds=args.build_timeout_seconds,
    )
    if min(args.live_timeout_seconds, args.export_timeout_seconds, args.build_timeout_seconds) <= 0:
        raise ValueError("step timeouts must be positive")
    try:
        warnings = _validate_runtime(require_env=args.require_env, python_executable=args.python)
    except Exception as exc:
        payload = {
            "run_id": run_id,
            "created_at": _utc_now(),
            "status": "failed",
            "deployment_ready": False,
            "failed_step": "preflight",
            "error": str(exc),
            "steps": [],
        }
        _write_json(manifest_path, payload)
        print(f"Situation release preflight failed: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        payload = {"run_id": run_id, "created_at": _utc_now(), "warnings": warnings, **_dry_run_payload(steps)}
        _write_json(manifest_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    manifest = {
        "run_id": run_id,
        "source_commit": os.getenv("GITHUB_SHA", "unknown"),
        "started_at": _utc_now(),
        "finished_at": None,
        "status": "running",
        "deployment_ready": False,
        "warnings": warnings,
        "steps": [],
        "dist": None,
    }
    _write_json(manifest_path, manifest)
    for index, step in enumerate(steps, 1):
        print(f"\n[{index}/{len(steps)}] {step.name}", flush=True)
        result = _run_step(step, artifact_dir / f"{index:02d}-{step.name}.log")
        manifest["steps"].append(result)
        if result["status"] != "passed":
            manifest.update(status="failed", finished_at=_utc_now())
            _write_json(manifest_path, manifest)
            print(f"Situation release stopped at {step.name}; deployment is blocked.", file=sys.stderr)
            return 1
        _write_json(manifest_path, manifest)

    manifest.update(
        status="passed",
        deployment_ready=True,
        finished_at=_utc_now(),
        dist=dist_inventory(ASTRO_DIR / "dist"),
    )
    _write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a gated Situation Room deployment artifact")
    parser.add_argument("--python", default=sys.executable, help="Python executable used by child steps")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--run-id")
    parser.add_argument("--require-env", action="store_true", help="Require DATABASE_URL in the process environment")
    parser.add_argument("--dry-run", action="store_true", help="Validate prerequisites and print the step plan")
    parser.add_argument("--live-timeout-seconds", type=int, default=30 * 60)
    parser.add_argument("--export-timeout-seconds", type=int, default=30 * 60)
    parser.add_argument("--build-timeout-seconds", type=int, default=20 * 60)
    return parser.parse_args(argv)


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
