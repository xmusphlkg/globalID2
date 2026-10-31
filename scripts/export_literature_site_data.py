#!/usr/bin/env python3
"""Queue the unified site release for reviewed Research Radar changes."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release_service import data_release_service  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(asyncio.run(data_release_service.trigger_job("site-release", manual=True, trigger="literature-export-cli")), ensure_ascii=False, indent=2))
