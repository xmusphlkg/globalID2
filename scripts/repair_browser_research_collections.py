#!/usr/bin/env python3
"""Repair public Ask collections from the existing canonical JSON; no DB/build."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.browser_research_collections import publish_browser_research_collections  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "astro-site/src/data/research/index.json")
    parser.add_argument("--output", type=Path, default=ROOT / "astro-site/public/site-data/research")
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError("Canonical research snapshot must contain an articles array")
    print(json.dumps(publish_browser_research_collections(payload, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
