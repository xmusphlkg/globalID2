#!/usr/bin/env python3
"""Capture published surveillance and Situation inputs for a research candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.publish_papers_r2 import _config_from_env  # noqa: E402
from src.generation.literature_publication import _atomic_write  # noqa: E402
from src.services.data_release.literature_context import capture_literature_context  # noqa: E402
from src.services.data_release.papers_r2 import R2S3Client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / 'data/cache/isolated-research-context')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Research context is already frozen')
    client = R2S3Client(_config_from_env())
    try:
        context = capture_literature_context(client=client, root=ROOT, cache=args.cache, workers=args.workers)
    finally:
        client.close()
    _atomic_write(args.output, json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    print(json.dumps({'event': 'research_context_frozen', 'publication_inputs': context['publication_inputs'],
                      'disease_count': len(context['diseases'])}))


if __name__ == '__main__':
    main()
