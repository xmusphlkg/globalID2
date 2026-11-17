#!/usr/bin/env python3
"""Export independent Situation JSON using the caller's database read snapshot."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_notices.publication import export_json_publication
from scripts.validate_situation_release import validate


async def export_and_validate(state_dir: Path, public_dir: Path, *, release_id: str) -> dict:
    """A failed semantic gate must stop the caller before candidate freezing."""
    summary = await export_json_publication(state_dir, public_dir, release_id=release_id)
    result = validate(public_dir, require_pages=False, require_points=False, require_filter_index=True)
    if not result["passed"]:
        failures = "; ".join(f"{check['id']}: {check['detail']}" for check in result["checks"] if not check["passed"])
        raise ValueError(f"Situation export validation failed: {failures}")
    return {**summary, "validation_checks": len(result["checks"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "exports/situation-state")
    parser.add_argument("--public-dir", type=Path, default=ROOT / "exports/situation-data")
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(export_and_validate(args.state_dir, args.public_dir,
        release_id=args.release_id)), ensure_ascii=False))


if __name__ == "__main__":
    main()
