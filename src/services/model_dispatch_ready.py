"""Cross-process readiness for auxiliary dispatchers after lease recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path

READY_FILE = Path(__file__).resolve().parents[2] / "data/cache/model_dispatch_ready.json"


def _start_time(pid: int) -> str:
    # comm may contain spaces and parentheses; fields following its final ')'
    # start at field 3, and process starttime is field 22.
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(")", 1)[1].split()[19]


def publish_ready() -> None:
    pid = os.getpid()
    READY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = READY_FILE.with_name(f"{READY_FILE.name}.{pid}.tmp")
    tmp.write_text(json.dumps({"pid": pid, "start_time": _start_time(pid), "role": "ai"}))
    os.replace(tmp, READY_FILE)


def worker_ready() -> bool:
    try:
        marker = json.loads(READY_FILE.read_text())
        pid = int(marker["pid"])
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        return (
            marker.get("role") == "ai"
            and marker["start_time"] == _start_time(pid)
            and b"src.services.task_worker" in cmdline
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False
