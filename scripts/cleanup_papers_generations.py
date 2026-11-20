#!/usr/bin/env python3
"""Plan or apply exact cleanup of Papers generations older than 48 hours.

Apply requires an operator-enforced publisher fence, including queued and
recoverable prepared releases. --fence-check names an executable that returns
zero only while that fence remains enforced; it is run before every batch.
The fence must cover all publishers, including direct and external publishers.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from scripts.publish_papers_r2 import _config_from_env
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.papers_gc import (
    apply_cleanup, inventory_papers, prepare_cleanup, validate_protected_keys,
)
from src.services.data_release.papers_cleanup import _save, json_bytes


def load_protected_keys(path: Path, *, bucket: str, endpoint: str) -> set[str]:
    """Read a reviewed full candidate closure bound to this exact R2 target."""
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get('schema') != 1
            or value.get('bucket') != bucket or value.get('endpoint') != endpoint
            or not isinstance(value.get('keys'), list)):
        raise ValueError('Protected keys must identify the exact bucket and endpoint')
    return validate_protected_keys(value['keys'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--inventory', type=Path, help='Optional saved planning inventory; apply always refreshes it')
    parser.add_argument('--protect-keys', type=Path,
        help='Reviewed candidate closure JSON: schema=1, bucket, endpoint, keys; preserved in the plan')
    parser.add_argument('--grace-hours', default=48, type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--publication-sha256')
    parser.add_argument('--fence-check', type=Path, help='Executable: no arguments, zero iff publication fence is still held')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--lock-dir', type=Path, default=ROOT / 'data/cache/r2-publish')
    args = parser.parse_args()
    if args.apply and (not args.fence_check or not args.output or not args.publication_sha256):
        parser.error('--apply requires --fence-check, --output and --publication-sha256')
    for key, value in dotenv_values(args.env_file).items():
        if value is not None and key.startswith(('R2_', 'CLOUDFLARE_')):
            os.environ[key] = value
    config = _config_from_env()
    protected = (load_protected_keys(args.protect_keys, bucket=config.bucket, endpoint=config.s3_endpoint)
                 if args.protect_keys else set())
    client = R2S3Client(config)
    try:
        if not args.apply:
            if args.inventory:
                saved = json.loads(args.inventory.read_bytes())
                if saved['bucket'] != config.bucket:
                    raise ValueError('Inventory belongs to a different bucket')
                inventory = saved['objects']
            else:
                inventory = inventory_papers(client)
            plan = prepare_cleanup(client, inventory, bucket=config.bucket, grace_hours=args.grace_hours,
                                   cache_dir=args.cache_dir, protected_keys=protected)
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            _save(args.plan, json_bytes(plan))
            print(json.dumps({'mode': 'plan', 'candidates': len(plan['candidates']), 'stored_bytes': plan['stored_bytes'],
                              'retained_catalogues': len(plan['retained_roots']), 'retired_catalogues': len(plan['retired_roots']),
                              'missing_historical_external_keys': len(plan['missing_historical_external_keys']),
                              'protected_keys': len(plan['protected_keys']),
                              'publication_sha256': plan['publication_sha256'],
                              'plan_sha256': hashlib.sha256(json_bytes(plan)).hexdigest()}))
            return
        plan = json.loads(args.plan.read_bytes())
        if plan.get('publication_sha256') != args.publication_sha256:
            raise ValueError('Explicit publication fence differs from reviewed plan')
        if args.protect_keys and protected != set(plan.get('protected_keys', [])):
            raise ValueError('Protected candidate keys differ from the reviewed plan')
        checker = args.fence_check.resolve(strict=True)
        if not checker.is_file() or not os.access(checker, os.X_OK):
            raise ValueError('Fence checker must be an executable file')
        def assert_fenced():
            result = subprocess.run([str(checker)], capture_output=True, timeout=30, check=False)
            if result.returncode:
                # The checker may read credentials or service internals. Never
                # relay arbitrary captured output into a public task log.
                raise ValueError('External publication fence check failed')
        args.lock_dir.mkdir(parents=True, exist_ok=True)
        target = hashlib.sha256(f'{config.s3_endpoint}/{config.bucket}'.encode()).hexdigest()
        with (args.lock_dir / f'{target}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = apply_cleanup(plan, client, bucket=config.bucket, output=args.output,
                                   assert_fenced=assert_fenced, cache_dir=args.cache_dir)
            print(json.dumps(result))
    finally:
        client.close()


if __name__ == '__main__':
    main()
