#!/usr/bin/env python3
"""Read-only readiness check for the API, scheduler, worker, and task queue."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.control_plane.health import readiness_payload
from src.control_plane.runtime import runtime_registry
from src.core.database import dispose_database


async def _main() -> int:
    try:
        payload = await readiness_payload()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if payload.get("status") == "ok" else 1
    finally:
        await runtime_registry.close()
        await dispose_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
