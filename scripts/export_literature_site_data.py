#!/usr/bin/env python3
"""Regenerate only the public Research Radar artifacts from reviewed state."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.literature_publication_service import export_public_research_artifacts  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(asyncio.run(export_public_research_artifacts()), ensure_ascii=False, indent=2))
