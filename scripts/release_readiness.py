#!/usr/bin/env python3
"""Read-only release readiness report for a reproducible deployment."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def git_status_paths(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"],
        cwd=root, text=False, capture_output=True, check=False,
    )
    if result.returncode != 0:
        return ["<git status unavailable>"]
    paths: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", "replace")
        if len(text) < 4:
            paths.append(text)
            continue
        path = text[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        paths.append(path)
    return paths


def report(*, root: Path = ROOT, contract: dict | None = None, now: datetime | None = None) -> dict:
    paths = git_status_paths(root)
    contract = contract or {"ready": False, "reason": "not_checked"}
    blockers = []
    if paths:
        blockers.append("worktree_dirty")
    if not contract.get("ready"):
        blockers.append("dashboard_contract_unverified")
    return {
        "schema_version": 1,
        "checked_at": (now or datetime.now(timezone.utc)).isoformat(),
        "repository": {
            "branch": subprocess.run(["git", "branch", "--show-current"], cwd=root,
                                      text=True, capture_output=True, check=False).stdout.strip() or None,
            "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                     text=True, capture_output=True, check=False).stdout.strip() or None,
            "commit_dirty": bool(paths),
            "dirty_paths": paths,
        },
        "dashboard_contract": contract,
        "ready": not blockers,
        "blockers": blockers,
    }


def main() -> int:
    from check_dashboard_openapi import check

    result = report(contract=check())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
