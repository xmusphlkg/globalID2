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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "exports/situation-state")
    parser.add_argument("--public-dir", type=Path, default=ROOT / "exports/situation-data")
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(export_json_publication(args.state_dir, args.public_dir,
        release_id=args.release_id)), ensure_ascii=False))


if __name__ == "__main__":
    main()
