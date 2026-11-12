#!/usr/bin/env python3
"""Install research inputs matching the publication baseline before a style build."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.publish_papers_r2 import _config_from_env
from src.generation.published_research_reuse import prepare_research_for_baseline
from src.services.data_release.literature_publish import PREFIX
from src.services.data_release.surveillance_publish import _starting_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'astro-site/src/data')
    parser.add_argument('--public', type=Path, default=ROOT / 'astro-site/public/site-data')
    args = parser.parse_args()
    captured, previous = _starting_baseline(args.run, _config_from_env(), PREFIX)
    if not captured or previous is None:
        raise ValueError('Research reuse requires a previously captured published baseline')
    release_id = prepare_research_for_baseline(ROOT, args.output, args.public, json.loads(previous))
    print(json.dumps({'event': 'research_inputs_reused', 'source_release_id': release_id}))


if __name__ == '__main__':
    main()
