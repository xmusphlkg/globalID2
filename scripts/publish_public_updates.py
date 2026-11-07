#!/usr/bin/env python3
"""Reconcile public monthly update JSON from acknowledged publication tasks."""
from __future__ import annotations

import asyncio
import fcntl
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from scripts.publish_papers_r2 import _config_from_env
from src.core.database import dispose_database, get_session_maker
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.public_updates import project_updates, read_published_markers


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


def main():
    lock_path = ROOT / 'exports' / '.public-updates.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = asyncio.run(snapshot())
        client = R2S3Client(_config_from_env())
        try:
            changed = publish(files, client)
        finally:
            client.close()
    print(f'Public update feed reconciled: {len(files) - 1} months, {changed} files changed')


if __name__ == '__main__':
    main()
