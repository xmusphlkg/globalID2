"""Bounded asynchronous migration staging; never switches public pointers."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote
from xml.etree import ElementTree
import zlib

import httpx

from src.generation.literature_publication import _atomic_write
from .papers_content import _source_objects, _wire_body
from .papers_immutable import _source
from .papers_r2 import R2S3Error


class AsyncMigrationTransport:
    def __init__(self, signer, client, *, attempts=5, retry_delay=1.0):
        self.signer, self.client = signer, client
        self.attempts, self.retry_delay = attempts, retry_delay

    async def request(self, method, key, *, body=b'', limit=65536, **kwargs):
        for attempt in range(self.attempts):
            signed = self.signer._signed_request(method, key, body=body, **kwargs)
            try:
                async with self.client.stream(method, signed.full_url,
                    headers=dict(signed.header_items()), content=signed.data) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise R2S3Error('R2 migration response exceeds expected size')
                        chunks.append(chunk)
                    status, payload = response.status_code, b''.join(chunks)
                if status != 429 and not 500 <= status <= 599:
                    return status, payload
                if attempt + 1 == self.attempts:
                    raise R2S3Error(f'R2 migration request failed: HTTP {status}', status=status)
            except httpx.HTTPError as error:
                if attempt + 1 == self.attempts:
                    raise R2S3Error(f'R2 migration transport failed: {type(error).__name__}') from error
            await asyncio.sleep(self.retry_delay * 2 ** attempt)
        raise R2S3Error('R2 migration retries exhausted')

    async def equivalent(self, key, digest, size):
        status, body = await self.request('GET', key, limit=max(size, 65536))
        if status == 404:
            return False
        if status != 200:
            raise R2S3Error(f'R2 migration verification failed: HTTP {status}', status=status)
        if len(body) == size and hashlib.sha256(body).hexdigest() == digest:
            return True
        if body.startswith(b'\x1f\x8b'):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                decoded = decoder.decompress(body, size + 1)
            except zlib.error as error:
                raise R2S3Error('Invalid compressed migration object') from error
            if decoder.eof and not decoder.unused_data and len(decoded) == size and hashlib.sha256(decoded).hexdigest() == digest:
                return True
        raise R2S3Error(f'Immutable migration destination conflict: {key}')


async def _run(items, operation, *, workers, generation, label, checkpoint=None, initial_known=None):
    if type(workers) is not int or not 1 <= workers <= 96:
        raise ValueError('Migration workers must be 1–96')
    items = list(items)
    pending = iter(items)
    proven = set()
    completed = 0
    started = last_report = time.monotonic()
    def save():
        if checkpoint is not None:
            _atomic_write(Path(checkpoint), json.dumps({'schema': 1, 'generation': generation,
                'proven_keys': sorted(proven | (initial_known or set())), 'completed': completed, 'total': len(items)}, separators=(',', ':')).encode())
    async def worker():
        nonlocal completed, last_report
        while True:
            item = next(pending, None)
            if item is None:
                return
            key = await operation(item)
            if key is not None:
                proven.add(key)
            completed += 1
            now = time.monotonic()
            if now - last_report >= 15:
                print(f'{label}: {completed}/{len(items)}; proven={len(proven)}; seconds={now-started:.1f}', flush=True)
                save()
                last_report = now
    tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(items)))]
    print(f'{label}: {len(items)} objects; concurrency={workers}', flush=True)
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        save()
    print(f'{label}: {completed}/{len(items)}; proven={len(proven)}; seconds={time.monotonic()-started:.1f}', flush=True)
    return proven


async def copy_legacy_async(candidate, plan, signer, *, known=None, workers=96,
                            checkpoint=None, transport=None, retry_delay=1.0):
    if plan.generation != candidate.generation:
        raise ValueError('Legacy copy plan belongs to another candidate')
    entries = {(entry['key'], entry['logical_key']): entry for entry in candidate.entries if 'paper_template' not in entry}
    seen = set()
    for item in plan.copies:
        entry = entries.get((item.destination, item.source))
        if (entry is None or item.sha256 != entry['sha256'] or item.decoded_bytes != entry['bytes']
                or item.destination in seen or not re.fullmatch(r'[a-f0-9]{32}', item.etag)
                or type(item.stored_bytes) is not int or item.stored_bytes < 0):
            raise ValueError('Legacy copy plan does not match candidate')
        seen.add(item.destination)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=120,
        limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers, keepalive_expiry=60)) as client:
        remote = AsyncMigrationTransport(signer, client, retry_delay=retry_delay)
        async def copy(item):
            source = '/' + quote(signer.config.bucket, safe='') + '/' + quote(item.source, safe='/')
            status, payload = await remote.request('PUT', item.destination, conditional_headers={
                'x-amz-copy-source': source, 'x-amz-copy-source-if-match': f'"{item.etag}"',
                'cf-copy-destination-if-none-match': '*', 'x-amz-metadata-directive': 'COPY'})
            if status == 404:
                return None
            if status == 412:
                return item.destination if await remote.equivalent(item.destination, item.sha256, item.decoded_bytes) else None
            if status != 200:
                raise R2S3Error(f'R2 legacy copy failed: HTTP {status}', status=status)
            try:
                result = ElementTree.fromstring(payload)
                etag = (result.findtext('{*}ETag') or '').strip('"')
            except ElementTree.ParseError as error:
                raise R2S3Error('Invalid R2 CopyObject response') from error
            if result.tag.split('}')[-1] != 'CopyObjectResult' or etag != item.etag:
                raise R2S3Error('R2 CopyObject result does not match verified source')
            return item.destination
        return await _run((item for item in plan.copies if item.destination not in (known or set())), copy,
            workers=workers, generation=candidate.generation, label='R2 async server copy', checkpoint=checkpoint,
            initial_known=(known or set()) & candidate.object_keys)


async def stage_payloads_async(candidate, signer, *, known=None, workers=96,
                                checkpoint=None, transport=None, retry_delay=1.0):
    """Upload payloads only. Catalogue and the single pointer remain caller-owned."""
    async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=120,
        limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers, keepalive_expiry=60)) as client:
        remote = AsyncMigrationTransport(signer, client, retry_delay=retry_delay)
        preparation = asyncio.Semaphore(8)
        def prepare(source):
            entry, root = source
            raw = _source(root, entry['path']).read_bytes()
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError(f"Migration source changed: {entry['path']}")
            wire, metadata = _wire_body(raw, entry['content_type'])
            return wire, metadata
        async def upload(source):
            entry, _ = source
            async with preparation:
                wire, metadata = await asyncio.to_thread(prepare, source)
            status, _ = await remote.request('PUT', entry['key'], body=wire,
                content_type=entry['content_type'], cache_control='public, max-age=31536000, immutable',
                conditional_headers={'if-none-match': '*'}, **metadata)
            if status == 412:
                if not await remote.equivalent(entry['key'], entry['sha256'], entry['bytes']):
                    raise R2S3Error('Migration object vanished after conditional conflict')
            elif status not in (200, 201):
                raise R2S3Error(f'R2 migration upload failed: HTTP {status}', status=status)
            return entry['key']
        return await _run((source for source in _source_objects(candidate) if source[0]['key'] not in (known or set())), upload,
            workers=workers, generation=candidate.generation, label='R2 async payload staging', checkpoint=checkpoint,
            initial_known=(known or set()) & candidate.object_keys)


def copy_verified_legacy_objects_async(candidate, plan, signer, **kwargs):
    return asyncio.run(copy_legacy_async(candidate, plan, signer, **kwargs))


def stage_papers_payloads_async(candidate, signer, **kwargs):
    return asyncio.run(stage_payloads_async(candidate, signer, **kwargs))
