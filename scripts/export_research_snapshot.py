#!/usr/bin/env python3
"""Build a private Research Radar candidate from a managed DB read snapshot."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config  # noqa: E402
from src.core.database import get_db  # noqa: E402
from src.core.database_snapshot import SNAPSHOT_ENV, validate_snapshot_id  # noqa: E402
from src.generation.literature_publication import export_literature_snapshot  # noqa: E402


async def run(context_path: Path, candidate: Path, cache_dir: Path | None = None) -> None:
    # An exporter without its owning transaction could combine reads from
    # different database versions or expose a partially reviewed record.
    validate_snapshot_id(os.environ.get(SNAPSHOT_ENV, ""))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    diseases = context.get("diseases")
    coverage = context.get("surveillance_coverage")
    situation = context.get("situation")
    if not isinstance(diseases, dict) or not isinstance(coverage, dict) or not isinstance(situation, dict):
        raise ValueError("Candidate context requires published disease, coverage and Situation snapshots")
    if any(not isinstance(codes, list) or any(not isinstance(code, str) for code in codes)
           for codes in coverage.values()):
        raise ValueError("Invalid surveillance coverage in candidate context")
    async with get_db() as db:
        await export_literature_snapshot(
            db, diseases_by_id=diseases,
            surveillance_coverage={key: set(codes) for key, codes in coverage.items()},
            situation_snapshot=situation, private_dir=candidate,
            limit=get_config().literature.public_article_limit,
            title_cache_path=cache_dir / "title-associations.json" if cache_dir else None,
            related_cache_path=cache_dir / "related.json" if cache_dir else None,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/cache/isolated-research")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    with (args.cache_dir / "export.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        asyncio.run(run(args.context, args.candidate, args.cache_dir))
