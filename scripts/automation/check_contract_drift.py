#!/usr/bin/env python3
"""Fail when generated Situation contract paths differ from the checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATHS = (
    "configs/situation_room.v3.schema.json",
    "dashboard/openapi.json",
    "dashboard/src/generated/api.d.ts",
    "astro-site/src/generated/api.d.ts",
)


def changed_contracts(root: Path = ROOT) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *CONTRACT_PATHS,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def main() -> None:
    changed = changed_contracts()
    if changed:
        print("Generated Situation contracts are stale or untracked:", file=sys.stderr)
        for line in changed:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)
    print("Situation contracts match the checked-out source.")


if __name__ == "__main__":
    main()
