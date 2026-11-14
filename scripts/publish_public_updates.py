#!/usr/bin/env python3
"""Reconcile public monthly update JSON from acknowledged publication tasks."""
from __future__ import annotations

import asyncio
import fcntl
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from scripts.publish_papers_r2 import _config_from_env
from src.core.database import dispose_database, get_session_maker
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.public_updates import project_updates, read_published_markers


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


def publish(files, client):
    # Upload month files first. The index never points to a file not yet written.
    changed = 0
    for name in sorted(files, key=lambda value: (value == 'latest.json', value)):
        key, body = f'site-data/updates/{name}', files[name]
        previous = client.get_object(key)
        if previous != body:
            client.put_object_conditional(key, body, previous=previous, cache_control='no-store')
            changed += 1
    return changed


def reconcile(client, *, history=website_history, data=None):
    """Attempt both feeds even when one input is temporarily unavailable."""
    changed, months, failures = 0, 0, []
    for name, read in [('website', history), ('data', data or (lambda: asyncio.run(snapshot())))]:
        try:
            files = read()
            changed += publish(files, client)
            if name == 'data':
                months = len(files) - 1
        except Exception as error:
            failures.append(error)
    if failures:
        raise failures[0]
    return months, changed


def main():
    lock_path = ROOT / 'exports' / '.public-updates.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        client = R2S3Client(_config_from_env())
        try:
            months, changed = reconcile(client)
        finally:
            client.close()
    print(f'Public update feed reconciled: website history, {months} months, {changed} files changed')


if __name__ == '__main__':
    main()
