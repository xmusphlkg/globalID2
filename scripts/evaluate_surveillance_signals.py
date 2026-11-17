#!/usr/bin/env python3
"""Evaluate a local exported country snapshot without DB, network or notifications."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.surveillance_signal_pilot import SignalPolicy, evaluate_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Local country JSON containing registered source_series")
    parser.add_argument("--as-of", required=True, type=date.fromisoformat, help="Explicit evaluation date, YYYY-MM-DD")
    parser.add_argument("--replay", action="store_true", help="Replay current snapshot; does not measure historical warning performance")
    parser.add_argument("--sigma", type=float, default=2.0)
    parser.add_argument("--minimum-increase", type=float, default=10.0)
    parser.add_argument("--minimum-ratio", type=float, default=1.5)
    args = parser.parse_args()
    try:
        policy = SignalPolicy(sigma=args.sigma, minimum_increase=args.minimum_increase,
                              minimum_ratio=args.minimum_ratio)
        snapshot_bytes = args.snapshot.read_bytes()
        snapshot = json.loads(snapshot_bytes)
        result = evaluate_snapshot(snapshot, as_of=args.as_of, replay=args.replay, policy=policy)
        result["snapshot_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
