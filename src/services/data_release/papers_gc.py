"""Exact-key garbage collection of expired, immutable Papers generations.

The current catalogue and all catalogue roots within the retention window own
full dependency closures. Original data and other publication channels are
outside this collector. Apply requires an externally enforced publisher fence;
checking a pointer or taking an unshared file lock alone is not sufficient.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
from xml.etree import ElementTree

from .papers_cleanup import _save, json_bytes, sha

POINTER = 'site-data/research-publication/current.json'
PREFIXES = ('releases/sha256/', 'paper-template-v3/')
HASH = r'[a-f0-9]{64}'
CATALOGUE = re.compile(r'releases/sha256/catalogues/' + HASH + r'\.json\Z')
OBJECT = re.compile(r'(?:releases/sha256/objects/' + HASH + r'/[a-f0-9]{16}'
    r'|releases/sha256/catalogue-shards/' + HASH + r'\.json'
    r'|paper-template-v3/(?:records|templates|assets)/' + HASH + r'\.json)\Z')


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Object timestamp has no timezone')
    return parsed


def list_metadata(client, prefix: str) -> dict:
    """Read exact object identity and age, rejecting incomplete pagination."""
    objects, token, seen = {}, None, set()
    while True:
        query = {'list-type': '2', 'max-keys': '1000', 'prefix': prefix}
        if token:
            query['continuation-token'] = token
        status, body = client._request('GET', '', query=urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote))
        if status != 200:
            raise ValueError('Papers metadata inventory failed')
        root = ElementTree.fromstring(body)
        for item in root.findall('{*}Contents'):
            key = item.findtext('{*}Key')
            if not key or not key.startswith(prefix) or key in objects:
                raise ValueError('Unexpected or duplicate inventory key')
            objects[key] = {'bytes': int(item.findtext('{*}Size')), 'etag': item.findtext('{*}ETag').strip('"'),
                            'last_modified': item.findtext('{*}LastModified')}
        truncated = root.findtext('{*}IsTruncated')
        if truncated == 'false':
            return objects
        if truncated != 'true':
            raise ValueError('Inventory pagination marker missing')
        token = root.findtext('{*}NextContinuationToken')
        if not token or token in seen:
            raise ValueError('Invalid inventory pagination')
        seen.add(token)


def inventory_papers(client) -> dict:
    with ThreadPoolExecutor(max_workers=2) as pool:
        groups = list(pool.map(lambda prefix: list_metadata(client, prefix), PREFIXES))
    return {key: value for group in groups for key, value in group.items()}


def prepare_cleanup(client, objects: dict, *, bucket: str, now: datetime | None = None,
                    grace_hours: int = 48, cache_dir: Path | None = None) -> dict:
    """Plan a retirement using verified catalogue, shard and asset references."""
    now = now or datetime.now(timezone.utc)
    if grace_hours < 48 or now.tzinfo is None:
        raise ValueError('Papers retention requires at least 48 hours and an aware time')
    cutoff = now - timedelta(hours=grace_hours)
    before = client.get_object(POINTER)
    if before is None:
        raise ValueError('Current research pointer missing')
    current = json.loads(before)
    binding = current['papers']
    generation = binding['papers_generation_sha256']
    if not re.fullmatch(HASH, generation):
        raise ValueError('Invalid current generation')
    current_root = f'releases/sha256/catalogues/{generation}.json'
    roots = {key: value for key, value in sorted(objects.items()) if CATALOGUE.fullmatch(key)}
    if current_root not in roots:
        raise ValueError('Current catalogue missing from inventory')
    # Unknown or invalid ages must never make a root eligible for retirement.
    retired = set()
    for key, item in roots.items():
        try:
            expired = _time(item['last_modified']) <= cutoff
        except (ValueError, AttributeError, KeyError, TypeError):
            expired = False
        if key != current_root and expired:
            retired.add(key)
    retained = set(roots) - retired
    keep, current_keys, shards, current_shards, assets = set(retained), {current_root}, {}, set(), {}
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def load(key, digest=None, size=None):
        path = cache_dir / hashlib.sha256(key.encode()).hexdigest() if cache_dir else None
        raw = path.read_bytes() if path and path.exists() else client.get_object(key)
        if raw is None or (digest and sha(raw) != digest) or (size is not None and len(raw) != size):
            raise ValueError('Missing or corrupt Papers dependency: ' + key)
        if path and not path.exists():
            _save(path, raw)
        return json.loads(raw)

    for key in sorted(retained):
        catalogue = load(key, binding['papers_manifest_sha256'] if key == current_root else None)
        identity = catalogue.get('generation_sha256')
        if (catalogue.get('schema_version') != 2 or key != f'releases/sha256/catalogues/{identity}.json'
                or sha(json_bytes({k: v for k, v in catalogue.items() if k != 'generation_sha256'})) != identity):
            raise ValueError('Catalogue identity mismatch')
        for descriptor in catalogue['shards'].values():
            shard_key = descriptor['key']
            if shard_key != f"releases/sha256/catalogue-shards/{descriptor['sha256']}.json" or not re.fullmatch(HASH, descriptor['sha256']):
                raise ValueError('Unsafe catalogue shard reference')
            if shard_key in shards and shards[shard_key] != descriptor:
                raise ValueError('Conflicting catalogue shard reference')
            shards[shard_key] = descriptor
            if key == current_root:
                current_shards.add(shard_key)
    keep.update(shards)

    def read_shard(descriptor):
        shard = load(descriptor['key'], descriptor['sha256'], descriptor['bytes'])
        if shard.get('schema_version') != 1 or len(shard['items']) != descriptor['count']:
            raise ValueError('Invalid catalogue shard')
        refs, manifests = {descriptor['key']}, {}
        for entry in shard['items']:
            refs.add(entry['key'])
            spec = entry.get('paper_template')
            if not spec:
                continue
            schema = spec.get('schema', 2)
            if schema not in {2, 3}:
                raise ValueError('Unsupported historical template schema')
            groups = [('records', 'record_sha256'), ('templates', 'template_sha256')]
            if schema == 3:
                groups.append(('assets', 'assets_sha256'))
            for group, field in groups:
                if not re.fullmatch(HASH, spec[field]):
                    raise ValueError('Invalid template dependency')
                reference = f'paper-template-v{schema}/{group}/{spec[field]}.json'
                refs.add(reference)
                if group == 'assets':
                    manifests[reference] = spec[field]
        return descriptor['key'], refs, manifests

    with ThreadPoolExecutor(max_workers=16) as pool:
        for key, refs, manifests in pool.map(read_shard, shards.values()):
            keep.update(refs)
            assets.update(manifests)
            if key in current_shards:
                current_keys.update(refs)
    for key, digest in assets.items():
        manifest = load(key, digest)
        if manifest.get('schema') != 1:
            raise ValueError('Invalid template asset manifest')
        for item in manifest['items']:
            if (not re.fullmatch(HASH, item['sha256'])
                    or not re.fullmatch(r'releases/sha256/objects/' + item['sha256'] + r'/[a-f0-9]{16}', item['key'])):
                raise ValueError('Invalid nested asset dependency')
            keep.add(item['key'])
            if key in current_keys:
                current_keys.add(item['key'])
    missing = sorted(keep - objects.keys())
    if current_keys - objects.keys() or any(key.startswith(PREFIXES) for key in missing):
        raise ValueError('Missing current or collected-namespace retained dependency')
    candidates, grace_excluded, unknown_age = [], 0, 0
    for key, item in sorted(objects.items()):
        if key in keep or not (OBJECT.fullmatch(key) or key in retired):
            continue
        try:
            modified = _time(item['last_modified'])
        except (ValueError, AttributeError, KeyError, TypeError):
            unknown_age += 1
            continue
        if modified > cutoff:
            grace_excluded += 1
            continue
        if not isinstance(item['bytes'], int) or item['bytes'] < 0 or not item['etag']:
            raise ValueError('Invalid candidate object metadata')
        candidates.append({'key': key, **item})
    candidates.sort(key=lambda x: (x['key'] not in retired, x['key']))
    if client.get_object(POINTER) != before or list_metadata(client, 'releases/sha256/catalogues/') != roots:
        raise ValueError('Research pointer or catalogue roots changed during planning')
    return {'schema': 1, 'bucket': bucket, 'planned_at': now.isoformat(), 'grace_hours': grace_hours,
            'publication_sha256': sha(before), 'release_id': current['release_id'], 'roots': roots,
            'retained_roots': sorted(retained), 'retired_roots': sorted(retired),
            'retained_keys': sorted(keep), 'current_keys': sorted(current_keys), 'candidates': candidates,
            'stored_bytes': sum(item['bytes'] for item in candidates), 'grace_excluded': grace_excluded,
            'unknown_age_excluded': unknown_age, 'missing_historical_external_keys': missing}


def apply_cleanup(plan: dict, client, *, bucket: str, output: Path, assert_fenced,
                  batch_size: int = 1000, cache_dir: Path | None = None) -> dict:
    """Revalidate under an external publisher fence, then retire exact batches.

    assert_fenced must fail unless all publisher processes, including resumed
    prepared candidates, remain fenced. The caller must keep that fence for
    the entire call. Metadata is refreshed under that fence before deletion.
    """
    if plan.get('schema') != 1 or plan.get('bucket') != bucket or not 1 <= batch_size <= 1000:
        raise ValueError('Invalid GC target, schema or batch size')
    assert_fenced()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan_hash = sha(json_bytes(plan))
    checkpoint = output / 'checkpoint.json'
    if checkpoint.exists() and json.loads(checkpoint.read_bytes()) != {'plan_sha256': plan_hash}:
        raise ValueError('GC checkpoint belongs to a different plan')
    _save(checkpoint, json_bytes({'plan_sha256': plan_hash}))
    candidates = {item['key']: item for item in plan['candidates']}
    if len(candidates) != len(plan['candidates']):
        raise ValueError('Duplicate GC candidate')
    attempted = set()
    events = output / 'batches.jsonl'
    if events.exists():
        for line in events.read_text().splitlines():
            event = json.loads(line)
            if event.get('plan_sha256') != plan_hash or not set(event['keys']).issubset(candidates):
                raise ValueError('GC receipt does not match plan')
            if event['event'] == 'prepared':
                attempted.update(event['keys'])
    objects = inventory_papers(client)
    simulated = dict(objects)
    for key in attempted - objects.keys():
        simulated[key] = {k: v for k, v in candidates[key].items() if k != 'key'}
    # A previous successful root DELETE may precede its receipt after a crash.
    # The immutable checkpoint and fsynced prepared batch identify exactly which
    # missing objects can be treated as our own idempotent deletions.
    absent_roots = set(plan['roots']) - objects.keys()
    if not absent_roots.issubset(attempted & set(plan['retired_roots'])):
        raise ValueError('Unexplained missing catalogue root')

    class ResumedView:
        def get_object(self, key):
            return client.get_object(key)
        def _request(self, method, key, **kwargs):
            # Catalogue LIST is the only final planning request. Restore only
            # already-attempted retired roots to validate the original plan.
            status, body = client._request(method, key, **kwargs)
            if absent_roots and method == 'GET' and key == '' and 'prefix=releases%2Fsha256%2Fcatalogues%2F' in kwargs.get('query', ''):
                root = ElementTree.fromstring(body)
                for name in absent_roots:
                    item = ElementTree.SubElement(root, 'Contents')
                    meta = plan['roots'][name]
                    for field, value in [('Key', name), ('Size', meta['bytes']), ('ETag', '"' + meta['etag'] + '"'), ('LastModified', meta['last_modified'])]:
                        ElementTree.SubElement(item, field).text = str(value)
                body = ElementTree.tostring(root)
            return status, body

    refreshed = prepare_cleanup(ResumedView(), simulated, bucket=bucket, now=_time(plan['planned_at']),
                                grace_hours=plan['grace_hours'], cache_dir=cache_dir)
    if refreshed != plan:
        raise ValueError('GC plan changed during fenced revalidation')
    assert_fenced()
    remaining = [item['key'] for item in plan['candidates'] if item['key'] in objects]
    root_keys = [key for key in remaining if key in plan['retired_roots']]
    object_keys = [key for key in remaining if key not in plan['retired_roots']]
    # S3 DeleteObjects does not promise the order of keys inside a batch.
    # Finish retiring roots before sending any dependent object deletion.
    batches = [phase[offset:offset + batch_size] for phase in (root_keys, object_keys)
               for offset in range(0, len(phase), batch_size)]
    expected_roots = {k: v for k, v in plan['roots'].items() if k not in absent_roots}
    removed = len(plan['candidates']) - len(remaining)

    def fence():
        assert_fenced()
        pointer = client.get_object(POINTER)
        if pointer is None or sha(pointer) != plan['publication_sha256']:
            raise ValueError('Research pointer changed; GC stopped')
        if list_metadata(client, 'releases/sha256/catalogues/') != expected_roots:
            raise ValueError('Catalogue roots changed; GC stopped')

    with events.open('a', encoding='utf-8') as stream:
        def event(kind, keys):
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'event': kind,
                                    'plan_sha256': plan_hash, 'keys': keys}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        for batch in batches:
            fence()
            event('prepared', batch)
            client.delete_objects(batch)
            for key in batch:
                expected_roots.pop(key, None)
            event('deleted', batch)
            removed += len(batch)
            receipt = {'schema': 1, 'plan_sha256': plan_hash, 'removed': removed,
                       'total': len(candidates), 'stored_bytes': plan['stored_bytes'],
                       'completed': False, 'deleted_all': removed == len(candidates),
                       'publication_sha256': plan['publication_sha256']}
            _save(output / 'receipt.json', json_bytes(receipt))
            print(json.dumps(receipt), flush=True)
    fence()
    final_inventory = inventory_papers(client)
    if set(candidates) & final_inventory.keys() or not set(plan['current_keys']).issubset(final_inventory):
        raise ValueError('Post-cleanup exact absence or current dependency check failed')
    fence()
    receipt = {'schema': 1, 'plan_sha256': plan_hash, 'removed': len(candidates),
               'total': len(candidates), 'stored_bytes': plan['stored_bytes'], 'completed': True,
               'verified': True, 'publication_sha256': plan['publication_sha256']}
    _save(output / 'receipt.json', json_bytes(receipt))
    return receipt
