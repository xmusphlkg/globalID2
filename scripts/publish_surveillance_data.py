#!/usr/bin/env python3
"""Publish browser JSON without building or deploying the static website."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.publish_papers_r2 import _config_from_env
from src.services.data_release.surveillance_publish import capture_surveillance_baseline, publish_surveillance_data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "exports/surveillance-data")
    parser.add_argument("--prefix", default=os.environ.get("SURVEILLANCE_DATA_R2_PREFIX", "site-data/surveillance"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--objects-dir", type=Path, help="Shared immutable object store for a frozen task manifest")
    parser.add_argument("--capture-baseline-only", action="store_true",
        help="Before generation, retain the remote pointer once; no local current.json is required")
    parser.add_argument("--require-starting-baseline", action="store_true",
        help="Fail closed when a managed candidate lacks its pre-generation baseline")
    args = parser.parse_args(argv)
    config = _config_from_env()
    result = (capture_surveillance_baseline(args.source_dir, config, prefix=args.prefix)
        if args.capture_baseline_only else publish_surveillance_data(args.source_dir, config,
            prefix=args.prefix, workers=args.workers, objects_dir=args.objects_dir,
            require_starting_baseline=args.require_starting_baseline))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
