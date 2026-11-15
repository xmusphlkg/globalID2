#!/usr/bin/env python3
"""Reconcile public monthly update JSON from acknowledged publication tasks."""
from __future__ import annotations

import asyncio
import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from scripts.publish_papers_r2 import _config_from_env
from src.core.database import dispose_database, get_session_maker
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.public_updates import project_updates, read_published_markers

UPDATE_PREFIX = 'site-data/updates'
DEFAULT_VERIFY_INTERVAL_SECONDS = 24 * 60 * 60


def _target(config, prefix):
    # Credentials never enter the private receipt. Bind it to the storage
    # destination, not a public hostname or a machine-local export directory.
    value = [config.s3_endpoint, config.account_id, config.bucket, prefix]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


class PublicationReceipt:
    """Confirmed content only; a missing/invalid receipt requires remote reads.

    The existing process lock must cover loading, publishing and saving. The
    per-file verification time is never refreshed by a skipped or failed read.
    """

    def __init__(self, path, config, *, prefix=UPDATE_PREFIX, verify_remote=False,
                 verify_interval_seconds=DEFAULT_VERIFY_INTERVAL_SECONDS, now=None):
        if not math.isfinite(verify_interval_seconds) or verify_interval_seconds < 0:
            raise ValueError('Remote verification interval must be finite and non-negative')
        self.path = Path(path)
        self.prefix = prefix
        self.target = _target(config, prefix)
        self.verify_remote = verify_remote
        self.verify_interval_seconds = verify_interval_seconds
        self.now = time.time() if now is None else now
        self.files = {}
        self.dirty = False
        try:
            value = json.loads(self.path.read_bytes())
            if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
                    or value['schema_version'] != 1 or value.get('target_sha256') != self.target
                    or value.get('prefix') != prefix or not isinstance(value.get('files'), dict)):
                raise ValueError('receipt schema or target mismatch')
            for name, entry in value['files'].items():
                if (not isinstance(name, str) or not isinstance(entry, dict)
                        or not isinstance(entry.get('sha256'), str) or len(entry['sha256']) != 64
                        or any(char not in '0123456789abcdef' for char in entry['sha256'])
                        or type(entry.get('verified_at')) not in (int, float)
                        or not math.isfinite(entry['verified_at']) or entry['verified_at'] < 0):
                    raise ValueError('invalid receipt file entry')
            self.files = value['files']
        except (OSError, ValueError, TypeError, OverflowError):
            print('Public update receipt unavailable or invalid; checking remote content', flush=True)

    def matches(self, name, digest):
        entry = self.files.get(name)
        return bool(not self.verify_remote and entry and entry['sha256'] == digest
                    and 0 <= self.now - entry['verified_at'] < self.verify_interval_seconds)

    def forget(self, name):
        # Invalidate before remote I/O: a failed forced repair must not leave a
        # fresh-looking receipt that lets the next ordinary run skip the file.
        if name in self.files:
            del self.files[name]
            self.dirty = True

    def confirm(self, name, digest):
        self.files[name] = {'sha256': digest, 'verified_at': self.now}
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, prefix=f'.{self.path.name}.', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'schema_version': 1, 'target_sha256': self.target, 'prefix': self.prefix,
                           'files': self.files}, stream, sort_keys=True, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.dirty = False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@dataclass
class PublishStats:
    remote_reads: int = 0
    remote_writes: int = 0
    receipt_skips: int = 0


def website_history(root=ROOT):
    """Export the authored release catalogue without a site build or database."""
    node = root / 'astro-site/node_modules/node/bin/node'
    result = subprocess.run(
        [str(node) if node.is_file() else 'node', '--experimental-strip-types',
         str(root / 'astro-site/scripts/export-changelog.mjs')],
        cwd=root / 'astro-site', capture_output=True, check=True, timeout=15,
    )
    return {'changelog.json': result.stdout}


async def snapshot():
    async with get_session_maker()() as db:
        try:
            if db.get_bind().dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION READ ONLY"))
                await db.execute(text("SET LOCAL statement_timeout = '5s'"))
            return project_updates(await read_published_markers(db))
        finally:
            await db.rollback()
            await dispose_database()


def publish(files, client, *, receipt=None, stats=None, prefix=UPDATE_PREFIX):
    if receipt is not None and (receipt.prefix != prefix or receipt.target != _target(client.config, prefix)):
        raise ValueError('Public update receipt belongs to another destination')
    stats = stats if stats is not None else PublishStats()
    # Upload month files first. The index never points to a file not yet written.
    changed = 0
    for name in sorted(files, key=lambda value: (value == 'latest.json', value)):
        key, body = f'{prefix}/{name}', files[name]
        digest = hashlib.sha256(body).hexdigest()
        if receipt is not None and receipt.matches(name, digest):
            stats.receipt_skips += 1
            continue
        if receipt is not None:
            receipt.forget(name)
            # A crash during a forced repair must leave the file unconfirmed
            # on disk too, even if its previous verification was still fresh.
            receipt.save()
        stats.remote_reads += 1
        previous = client.get_object(key)
        if previous != body:
            stats.remote_writes += 1
            client.put_object_conditional(key, body, previous=previous, cache_control='no-store')
            changed += 1
        if receipt is not None:
            receipt.confirm(name, digest)
    return changed


def reconcile(client, *, history=website_history, data=None, receipt=None, stats=None):
    """Attempt both feeds even when one input is temporarily unavailable."""
    changed, months, failures = 0, 0, []
    for name, read in [('website', history), ('data', data or (lambda: asyncio.run(snapshot())))]:
        try:
            files = read()
            changed += publish(files, client, receipt=receipt, stats=stats)
            if name == 'data':
                months = len(files) - 1
        except Exception as error:
            print(f'Public update {name} feed failed: {type(error).__name__}', flush=True)
            failures.append(error)
    # Persist successful files even when the other feed (or a later index PUT)
    # failed. Failed/ambiguous writes have already lost their old receipts.
    if receipt is not None:
        try:
            receipt.save()
        except Exception as error:
            failures.append(error)
    if failures:
        raise failures[0]
    return months, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-remote', action='store_true', help='Compare every generated file with R2, ignoring cached receipts')
    parser.add_argument('--verify-interval-seconds', type=float, default=DEFAULT_VERIFY_INTERVAL_SECONDS,
                        help='Maximum age of a confirmed file before remote comparison (default: 86400; 0 checks every run)')
    parser.add_argument('--receipt-path', type=Path, default=ROOT / 'exports' / '.public-updates-receipt.json',
                        help='Private target-bound receipt; missing or invalid files trigger remote comparison')
    args = parser.parse_args(argv)
    if not math.isfinite(args.verify_interval_seconds) or args.verify_interval_seconds < 0:
        parser.error('--verify-interval-seconds must be finite and non-negative')
    lock_path = ROOT / 'exports' / '.public-updates.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        client = R2S3Client(_config_from_env())
        stats = PublishStats()
        try:
            receipt = PublicationReceipt(args.receipt_path, client.config, verify_remote=args.verify_remote,
                                         verify_interval_seconds=args.verify_interval_seconds)
            months, changed = reconcile(client, receipt=receipt, stats=stats)
        finally:
            client.close()
            print(f'Public update I/O: remote_reads={stats.remote_reads}, remote_writes={stats.remote_writes}, '
                  f'receipt_skips={stats.receipt_skips}, force_verify={args.verify_remote}, '
                  f'verify_interval_seconds={args.verify_interval_seconds:g}', flush=True)
    print(f'Public update feed reconciled: website history, {months} months, {changed} files changed')


if __name__ == '__main__':
    main()
