#!/usr/bin/env python3
"""Fail-closed drain, restart, and owner-scoped recovery for task runtime units."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.control_plane.runtime import runtime_registry  # noqa: E402


API_UNIT = "globalid-dashboard-api.service"
WORKER_UNIT = "globalid-dashboard-worker.service"
SCHEDULER_UNIT = "globalid-dashboard-scheduler.service"


def _emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, sort_keys=True), flush=True)


def _run_systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", "systemctl", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _unit_property(unit: str, name: str) -> str:
    result = subprocess.run(
        ["systemctl", "show", unit, f"--property={name}", "--value"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_unit_stopped(timeout_seconds: int) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_reported = ""
    while time.monotonic() < deadline:
        state = _unit_property(WORKER_UNIT, "ActiveState")
        if state in {"inactive", "failed"}:
            return state
        if state != last_reported:
            _emit("worker_draining", state=state)
            last_reported = state
        await asyncio.sleep(2)
    raise TimeoutError(
        "worker drain deadline exceeded; scheduler remains stopped and no force kill was issued"
    )


async def _wait_runtime_service(
    service: str, expected_pid: int, timeout_seconds: int
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        services, available = await runtime_registry.list_services()
        if not available:
            raise RuntimeError("runtime registry became unavailable")
        if any(
            item.get("service") == service
            and int(item.get("pid") or 0) == expected_pid
            for item in services
        ):
            return
        await asyncio.sleep(1)
    raise RuntimeError(
        f"{service} heartbeat for PID {expected_pid} did not become ready"
    )


def _ready_probe() -> dict:
    with request.urlopen("http://127.0.0.1:8000/health/ready", timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
    if value.get("status") != "ok":
        raise RuntimeError("runtime readiness probe is not ok")
    return value


async def _wait_ready_probe(timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncio.to_thread(_ready_probe)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError("runtime readiness probe did not become ready") from last_error


def _recover_owner(owner: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/recover_stale_tasks.py"),
            "--owner",
            owner,
            "--stale-after-seconds",
            "0",
            "--confirmed-dead-owner",
            "--apply",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    _emit("owner_tasks_recovered", owner=owner)


async def _restart(args: argparse.Namespace) -> int:
    old_owner, registry_available = await runtime_registry.lease_owner("worker")
    if not registry_available:
        raise RuntimeError("runtime registry is unavailable; refusing maintenance restart")
    old_pid = int(_unit_property(WORKER_UNIT, "MainPID") or 0)
    if old_owner and old_pid and f"-{old_pid}-" not in old_owner:
        raise RuntimeError("worker PID does not match Redis lease owner")

    _emit("scheduler_freeze_started")
    _run_systemctl("stop", SCHEDULER_UNIT)
    _emit("scheduler_frozen")

    # SIGTERM immediately stops new claims; the worker then drains tasks under
    # the unit's 15-minute TimeoutStopSec. Queued work remains durable.
    _run_systemctl("stop", "--no-block", WORKER_UNIT)
    stopped_state = await _wait_unit_stopped(args.drain_timeout_seconds)
    stop_result = _unit_property(WORKER_UNIT, "Result")
    _emit("worker_stopped", state=stopped_state, result=stop_result)

    if _pid_is_alive(old_pid):
        raise RuntimeError("old worker PID is still alive after systemd stop")

    remaining_owner, available = await runtime_registry.lease_owner("worker")
    if not available:
        raise RuntimeError("runtime registry became unavailable after worker stop")
    owner_was_cleaned = False
    if remaining_owner:
        if remaining_owner != old_owner or not old_owner:
            raise RuntimeError("a different worker acquired the lease during maintenance")
        cleaned = await runtime_registry.release_stopped_instance(
            "worker", old_owner
        )
        if not cleaned:
            raise RuntimeError("failed to compare-delete the stopped worker lease")
        owner_was_cleaned = True
        _emit("stopped_owner_released", owner=old_owner)
    elif old_owner:
        # Graceful shutdown releases the lease itself; remove the exact old
        # heartbeat immediately instead of waiting for its TTL.
        await runtime_registry.remove_heartbeat("worker", old_owner)
    hard_stop = stop_result not in {"", "success"}
    if old_owner:
        _recover_owner(old_owner)

    if args.include_api:
        _emit("api_restart_started")
        _run_systemctl("restart", API_UNIT)
        _emit("api_restarted", pid=int(_unit_property(API_UNIT, "MainPID") or 0))

    _run_systemctl("reset-failed", WORKER_UNIT, check=False)
    _run_systemctl("start", WORKER_UNIT)
    worker_pid = int(_unit_property(WORKER_UNIT, "MainPID") or 0)
    await _wait_runtime_service(
        "worker", worker_pid, args.readiness_timeout_seconds
    )
    _emit("worker_started", pid=worker_pid)

    _run_systemctl("start", SCHEDULER_UNIT)
    scheduler_pid = int(_unit_property(SCHEDULER_UNIT, "MainPID") or 0)
    await _wait_runtime_service(
        "scheduler", scheduler_pid, args.readiness_timeout_seconds
    )
    _emit("scheduler_started", pid=scheduler_pid)
    ready = await _wait_ready_probe(args.readiness_timeout_seconds)
    _emit(
        "runtime_ready",
        status=ready.get("status"),
        task_queue=ready.get("task_queue"),
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-api",
        action="store_true",
        help="restart the API between worker drain and worker startup",
    )
    parser.add_argument(
        "--drain-timeout-seconds",
        type=int,
        default=900,
        help=(
            "maximum time to observe cooperative worker drain; the systemd unit "
            "retains its own hard stop deadline"
        ),
    )
    parser.add_argument("--readiness-timeout-seconds", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.drain_timeout_seconds < 30 or args.drain_timeout_seconds > 3600:
        raise SystemExit("--drain-timeout-seconds must be between 30 and 3600")
    if args.readiness_timeout_seconds < 10 or args.readiness_timeout_seconds > 300:
        raise SystemExit("--readiness-timeout-seconds must be between 10 and 300")
    try:
        return asyncio.run(_restart(args))
    except Exception as exc:
        scheduler_stopped = False
        try:
            _run_systemctl("stop", SCHEDULER_UNIT)
            scheduler_stopped = _unit_property(
                SCHEDULER_UNIT, "ActiveState"
            ) in {"inactive", "failed"}
        except Exception:
            scheduler_stopped = False
        _emit(
            "maintenance_failed",
            error_type=type(exc).__name__,
            scheduler_left_stopped=scheduler_stopped,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
