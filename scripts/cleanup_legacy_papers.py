#!/usr/bin/env python3
"""Plan or apply fenced deletion of replaced static paper pages.

Apply requires a reviewed exact-key plan, preserved local originals, a verified
active template publication, and publication workers fenced by the operator.
No shared assets, versioned releases or research data are deleted.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publish_papers_r2 import _config_from_env
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.papers_cleanup import prepare_plan, apply_plan, json_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publication-sha256')
    parser.add_argument('--publication-fenced', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--public-origin', action='append', default=[],
                        help='Additional public origin to probe; production main/www/papers are always checked.')
    parser.add_argument('--lock-dir', type=Path, default=ROOT / 'data/cache/r2-publish')
    args = parser.parse_args()
    manifest = args.manifest.read_bytes()
    if not args.apply:
        if not args.inventory or not args.source:
            parser.error('Planning requires --inventory and --source')
        inventory = json.loads(args.inventory.read_bytes())
        plan = prepare_plan(manifest, inventory['objects'], args.source, bucket=inventory['bucket'])
        args.plan.write_bytes(json_bytes(plan))
        print(json.dumps({'articles': len(plan['articles']), 'prototype_objects': len(plan['prototype']),
                          'stored_bytes': plan['stored_bytes'], 'plan': str(args.plan)}))
        return
    if not args.publication_sha256 or not args.publication_fenced or not args.output:
        parser.error('Apply requires --publication-sha256, --publication-fenced and --output')
    config = _config_from_env()
    args.lock_dir.mkdir(parents=True, exist_ok=True)
    target = hashlib.sha256(f'{config.s3_endpoint}/{config.bucket}'.encode()).hexdigest()
    with (args.lock_dir / f'{target}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        client = R2S3Client(config)
        try:
            result = apply_plan(json.loads(args.plan.read_bytes()), manifest, client,
                publication_sha256=args.publication_sha256, bucket=config.bucket, output=args.output,
                public_origins=tuple(args.public_origin))
            print(json.dumps(result))
        finally:
            client.close()


if __name__ == '__main__':
    main()
