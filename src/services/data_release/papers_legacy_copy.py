"""Copy proven canonical objects into immutable research storage inside R2.

This migration helper never deletes or overwrites an object. Both the source
ETag and destination absence are enforced in the same server-side operation.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import gzip
import hashlib
import json
import re
import time
from urllib.parse import quote
from xml.etree import ElementTree

from .papers_content import _wire_body
from .papers_immutable import _source
from .papers_r2 import R2S3Error, _content_type_for


@dataclass(frozen=True)
class LegacyCopy:
    source: str
    destination: str
    etag: str
    stored_bytes: int
    sha256: str
    decoded_bytes: int


@dataclass(frozen=True)
class LegacyCopyPlan:
    generation: str
    copies: tuple[LegacyCopy, ...]

    @property
    def raw_bytes(self) -> int:
        return sum(item.decoded_bytes for item in self.copies)

    @property
    def stored_bytes(self) -> int:
        return sum(item.stored_bytes for item in self.copies)


def plan_verified_legacy_copies(candidate, legacy_manifest: bytes | dict, inventory: dict,
                                 *, known: set[str] | None = None, workers: int = 8) -> LegacyCopyPlan:
    """Read-only plan: decoded identity plus exact raw/gzip wire proof."""
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError('Legacy copy planning workers must be 1–32')
    document = json.loads(legacy_manifest) if isinstance(legacy_manifest, bytes) else legacy_manifest
    if not isinstance(document, dict) or not isinstance(document.get('files'), list):
        raise ValueError('Invalid legacy manifest')
    legacy = {}
    for item in document['files']:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str) or item['key'] in legacy:
            raise ValueError('Invalid or duplicate legacy descriptor')
        legacy[item['key']] = item
    known = known or set()
    seen = set()
    pending = []
    for entry in candidate.entries:
        if 'paper_template' in entry or entry['key'] in known or entry['key'] in seen:
            continue
        seen.add(entry['key'])
        previous = legacy.get(entry['logical_key'])
        remote = inventory.get(entry['logical_key'])
        if not isinstance(previous, dict) or not isinstance(remote, dict):
            continue
        if (previous.get('sha256') != entry['sha256'] or previous.get('bytes') != entry['bytes']
                or not isinstance(previous.get('path'), str)
                or _content_type_for(previous['path']) != entry['content_type']):
            continue
        pending.append((entry, remote))

    def prove(pair):
        entry, remote = pair
        raw = _source(candidate.root, entry['path']).read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f"Legacy copy source changed locally: {entry['path']}")
        etag = str(remote.get('etag', '')).strip('"')
        if not re.fullmatch(r'[a-f0-9]{32}', etag):
            return None
        stored_bytes = remote.get('bytes')
        matched = (stored_bytes == len(raw) and hashlib.md5(raw, usedforsecurity=False).hexdigest() == etag)
        if not matched:
            wire, _ = _wire_body(raw, entry['content_type'])
            matched = stored_bytes == len(wire) and hashlib.md5(wire, usedforsecurity=False).hexdigest() == etag
        if not matched:
            return None
        return LegacyCopy(entry['logical_key'], entry['key'], etag, stored_bytes, entry['sha256'], entry['bytes'])

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='legacy-copy-plan') as pool:
        copies = tuple(item for item in pool.map(prove, pending) if item is not None)
    return LegacyCopyPlan(candidate.generation, copies)


def copy_verified_legacy_objects(candidate, legacy_manifest, inventory, client, known=None,
                                 *, workers: int = 64, plan: LegacyCopyPlan | None = None) -> set[str]:
    """Return newly proven destination keys; the caller stages any remainder.

    An existing destination after 412 is accepted only after decoded content
    verification. If the source changed and no destination exists, it is left
    for ordinary upload from the frozen local candidate.
    """
    if type(workers) is not int or not 1 <= workers <= 96:
        raise ValueError('Legacy copy workers must be 1–96')
    plan = plan or plan_verified_legacy_copies(candidate, legacy_manifest, inventory, known=known)
    if plan.generation != candidate.generation:
        raise ValueError('Legacy copy plan belongs to another candidate')
    entries = {(entry['key'], entry['logical_key']): entry for entry in candidate.entries if 'paper_template' not in entry}
    if len({item.destination for item in plan.copies}) != len(plan.copies):
        raise ValueError('Duplicate legacy copy destination')
    for item in plan.copies:
        entry = entries.get((item.destination, item.source))
        if (entry is None or item.source != entry['logical_key'] or item.sha256 != entry['sha256']
                or item.decoded_bytes != entry['bytes'] or not re.fullmatch(r'[a-f0-9]{32}', item.etag)):
            raise ValueError('Legacy copy plan does not match candidate')

    def reconcile(item):
        body = client.get_object(item.destination)
        if body is None:
            return None
        if len(body) == item.decoded_bytes and hashlib.sha256(body).hexdigest() == item.sha256:
            return item.destination
        if body.startswith(b'\x1f\x8b'):
            body = gzip.decompress(body)
        if len(body) != item.decoded_bytes or hashlib.sha256(body).hexdigest() != item.sha256:
            raise R2S3Error(f'Immutable copy destination conflict: {item.destination}')
        return item.destination

    def copy(item):
        source = '/' + quote(client.config.bucket, safe='') + '/' + quote(item.source, safe='/')
        headers = {'x-amz-copy-source': source, 'x-amz-copy-source-if-match': f'"{item.etag}"',
                   'cf-copy-destination-if-none-match': '*', 'x-amz-metadata-directive': 'COPY'}
        try:
            status, payload = client._request('PUT', item.destination, conditional_headers=headers)
        except R2S3Error as error:
            if error.status == 412:
                return reconcile(item)
            if error.status == 404:
                return None
            raise
        if status == 412:
            return reconcile(item)
        if status == 404:
            return None
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

    proven = set()
    entries = iter(plan.copies)
    started = last_report = time.monotonic()
    completed = 0
    print(f'R2 server-side copy: {len(plan.copies)} verified objects; raw_bytes={plan.raw_bytes}; stored_bytes={plan.stored_bytes}', flush=True)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='legacy-copy') as pool:
        pending = set()
        try:
            while True:
                while len(pending) < workers * 2:
                    item = next(entries, None)
                    if item is None:
                        break
                    pending.add(pool.submit(copy, item))
                if not pending:
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    key = future.result()
                    if key is not None:
                        proven.add(key)
                    completed += 1
                now = time.monotonic()
                if now - last_report >= 15:
                    print(f'R2 server-side copy: {completed}/{len(plan.copies)}; proven={len(proven)}; seconds={now-started:.1f}', flush=True)
                    last_report = now
        finally:
            for future in pending:
                future.cancel()
    return proven
