"""Explicit, fenced cleanup of replaced static paper pages.

Never discovers deletions from an arbitrary prefix at apply time. The reviewed
plan contains exact keys from a saved inventory and verified local originals.
Historical versioned releases, shared assets and data channels are untouched.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .papers_r2 import R2S3Error

POINTER = 'site-data/research-publication/current.json'
ARTICLE = re.compile(r'(?:(zh|fr)/)?research/articles/([a-z0-9][a-z0-9-]*)/\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
PROTOTYPE = re.compile(r'paper-template-v1/(?:records|indexes|templates|generations)/[a-f0-9]{64}\.json\Z')
PRODUCTION_ORIGINS = ('https://globalinfectiousdisease.com', 'https://www.globalinfectiousdisease.com',
                      'https://papers.globalinfectiousdisease.com')


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def _save(path: Path, body: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.cleanup-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_plan(manifest: bytes, inventory: dict, source: Path, *, bucket: str) -> dict:
    """Verify every original article before making an explicit deletion plan."""
    root = Path(source).resolve()
    original = json.loads(manifest)
    files = original.get('files')
    if not isinstance(files, list):
        raise ValueError('Invalid legacy Papers manifest')
    entries = []
    seen = set()
    for item in files:
        key = item.get('key', '')
        if not ARTICLE.fullmatch(key):
            continue
        if key in seen:
            raise ValueError('Duplicate legacy article')
        seen.add(key)
        relative = key + 'index.html'
        if item.get('path') != relative or not HASH.fullmatch(item.get('sha256', '')):
            raise ValueError('Invalid article backup descriptor')
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe article backup path')
        data = path.read_bytes()
        if sha(data) != item['sha256'] or len(data) != item['bytes']:
            raise ValueError(f'Article backup mismatch: {relative}')
        if key not in inventory:
            raise ValueError(f'Legacy article absent from audited inventory: {key}')
        entries.append({'key': key, 'path': relative, 'sha256': item['sha256'],
                        'bytes': item['bytes'], 'stored_bytes': inventory[key]['bytes'],
                        'etag': inventory[key]['etag']})
    if not entries:
        raise ValueError('No legacy articles to clean')
    prototype = [{'key': key, 'stored_bytes': item['bytes'], 'etag': item['etag']}
                 for key, item in sorted(inventory.items()) if PROTOTYPE.fullmatch(key)]
    if 'papers-manifest.json' not in inventory:
        raise ValueError('Canonical manifest missing from inventory')
    return {'schema': 1, 'bucket': bucket, 'source': str(root),
            'manifest_etag': inventory['papers-manifest.json']['etag'],
            'legacy_manifest_sha256': sha(manifest), 'articles': sorted(entries, key=lambda x: x['key']),
            'prototype': prototype, 'stored_bytes': sum(x['stored_bytes'] for x in [*entries, *prototype])}


def validate_plan(plan: dict, manifest: bytes) -> None:
    if plan.get('schema') != 1 or plan.get('legacy_manifest_sha256') != sha(manifest):
        raise ValueError('Cleanup plan does not match original manifest')
    original = {x['key']: x for x in json.loads(manifest)['files']}
    root = Path(plan['source']).resolve()
    seen = set()
    if not plan.get('articles'):
        raise ValueError('Empty cleanup plan')
    for item in plan['articles']:
        key = item['key']
        if not ARTICLE.fullmatch(key) or key in seen or key not in original:
            raise ValueError('Unsafe or duplicate deletion key')
        seen.add(key)
        prior = original[key]
        if (item['path'] != key + 'index.html' or item['path'] != prior['path']
                or item['sha256'] != prior['sha256'] or item['bytes'] != prior['bytes']):
            raise ValueError('Changed article backup descriptor')
        path = root / item['path']
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe article backup path')
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or sha(raw) != item['sha256']:
            raise ValueError('Changed article backup')
    for item in plan.get('prototype', []):
        if not PROTOTYPE.fullmatch(item['key']) or item['key'] in seen:
            raise ValueError('Unsafe prototype deletion key')
        seen.add(item['key'])


def _object(client, key: str, expected: str | None = None) -> bytes:
    raw = client.get_object(key)
    if raw is None:
        raise R2S3Error(f'Missing publication object: {key}')
    if raw.startswith(b'\x1f\x8b'):
        raw = gzip.decompress(raw)
    if expected and sha(raw) != expected:
        raise R2S3Error(f'Publication checksum mismatch: {key}')
    return raw


def active_article_coverage(client, pointer: bytes, *, workers: int = 16) -> dict:
    current = json.loads(pointer)
    binding = current.get('papers', {})
    generation = binding.get('papers_generation_sha256', '')
    expected = binding.get('papers_manifest_sha256', '')
    if not HASH.fullmatch(generation) or not HASH.fullmatch(expected):
        raise ValueError('Invalid active Papers binding')
    catalogue = json.loads(_object(client, f'releases/sha256/catalogues/{generation}.json', expected))
    if catalogue.get('schema_version') != 2 or catalogue.get('generation_sha256') != generation:
        raise ValueError('Invalid active Papers catalogue')
    descriptors = list(catalogue['shards'].values())
    def read(descriptor):
        digest = descriptor.get('sha256', '')
        if not HASH.fullmatch(digest) or descriptor.get('key') != f'releases/sha256/catalogue-shards/{digest}.json':
            raise ValueError('Invalid catalogue shard descriptor')
        raw = _object(client, descriptor['key'], digest)
        if len(raw) != descriptor['bytes']:
            raise ValueError('Catalogue shard size mismatch')
        value = json.loads(raw)
        if value.get('schema_version') != 1 or not isinstance(value.get('items'), list):
            raise ValueError('Invalid catalogue shard')
        return value['items']
    coverage = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for items in pool.map(read, descriptors):
            for item in items:
                key = item.get('logical_key', '')
                match = ARTICLE.fullmatch(key)
                if not match:
                    continue
                spec = item.get('paper_template')
                if (not isinstance(spec, dict) or spec.get('locale') != (match[1] or 'en')
                        or spec.get('slug') != match[2]
                        or not HASH.fullmatch(spec.get('template_sha256', ''))
                        or not HASH.fullmatch(spec.get('record_sha256', ''))
                        or item.get('key') != f"paper-template-v2/records/{spec['record_sha256']}.json"
                        or not HASH.fullmatch(item.get('sha256', '')) or key in coverage):
                    raise ValueError(f'Article is not safely served from templates: {key}')
                coverage[key] = item
    return coverage


def verify_public_samples(plan: dict, coverage: dict, current: dict, *, opener=urlopen,
                          public_origins: tuple[str, ...] = ()) -> None:
    origin = current['papers']['papers_origin'].rstrip('/')
    origins = set(public_origins) | {origin}
    if origin in PRODUCTION_ORIGINS:
        origins.update(PRODUCTION_ORIGINS)
    for value in origins:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Invalid public origin')
    samples = {}
    for item in plan['articles']:
        key = item['key']
        match = ARTICLE.fullmatch(key)
        samples.setdefault(match[1] or 'en', key)
    if set(samples) != {'en', 'zh', 'fr'}:
        raise ValueError('Missing locale coverage')
    for public_origin in sorted(origins):
        for key in samples.values():
            with opener(Request(public_origin + '/' + key, headers={
                    'Cache-Control': 'no-cache',
                    'User-Agent': 'GlobalIDReleaseVerifier/1.0 (+https://globalinfectiousdisease.com)',
            }), timeout=30) as response:
                if (response.status != 200 or response.headers.get('X-GIDS-Paper-Mode') != 'template'
                        or response.headers.get('X-GIDS-Paper-SHA256') != coverage[key]['sha256']
                        or response.headers.get('X-GIDS-Research-Release') != current['release_id']):
                    raise R2S3Error(f'Public template cutover not verified: {public_origin}/{key}')
                response.read(1024)


def apply_plan(plan: dict, manifest: bytes, client, *, publication_sha256: str,
               bucket: str, output: Path, public_opener=urlopen, batch_size: int = 1000,
               public_origins: tuple[str, ...] = ()) -> dict:
    """Apply exact reviewed keys only after full coverage and public canaries.

    Callers must fence all publication workers for the short cleanup window.
    The research pointer is checked again before every deletion batch.
    """
    if plan.get('bucket') != bucket or not HASH.fullmatch(publication_sha256):
        raise ValueError('Cleanup target or publication identity mismatch')
    if not 1 <= batch_size <= 1000:
        raise ValueError('Invalid deletion batch size')
    validate_plan(plan, manifest)
    pointer = _object(client, POINTER, publication_sha256)
    current = json.loads(pointer)
    coverage = active_article_coverage(client, pointer)
    keys = {item['key'] for item in plan['articles']}
    if not keys.issubset(coverage):
        raise ValueError('Active generation does not cover every legacy article')
    for article in plan['articles']:
        replacement_article = coverage[article['key']]
        if (replacement_article.get('sha256') != article['sha256']
                or replacement_article.get('bytes') != article['bytes']):
            raise ValueError(f"Active article differs from verified original: {article['key']}")
    verify_public_samples(plan, coverage, current, opener=public_opener, public_origins=public_origins)
    if client.get_object('paper-template-v1/current.json') is not None:
        raise ValueError('Prototype v1 has an active pointer')
    original = json.loads(manifest)
    pruned = {**original, 'files': [x for x in original['files'] if x['key'] not in keys],
              'article_storage': 'paper-template-v2', 'article_publication_pointer': POINTER}
    pruned['file_count'] = len(pruned['files'])
    pruned.pop('template_legacy_asset_keys', None)
    replacement = json_bytes(pruned)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    observed = _object(client, 'papers-manifest.json')
    if observed not in (manifest, replacement):
        raise R2S3Error('Canonical Papers manifest changed after cleanup planning')
    checkpoint = {'schema': 1, 'plan_sha256': sha(json_bytes(plan)), 'publication_sha256': publication_sha256,
                  'legacy_manifest_sha256': sha(manifest), 'replacement_manifest_sha256': sha(replacement)}
    checkpoint_path = output / 'cleanup-checkpoint.json'
    if checkpoint_path.exists():
        if json.loads(checkpoint_path.read_bytes()) != checkpoint:
            raise R2S3Error('Cleanup checkpoint belongs to a different plan or publication')
    elif observed == replacement:
        raise R2S3Error('Already-pruned manifest has no matching cleanup checkpoint')
    _save(output / 'legacy-papers-manifest.json', manifest)
    _save(output / 'replacement-papers-manifest.json', replacement)
    _save(output / 'publication-pointer.json', pointer)
    _save(checkpoint_path, json_bytes(checkpoint))
    if _object(client, POINTER) != pointer:
        raise R2S3Error('Research publication changed before cleanup')
    if observed != replacement:
        # Canonical manifests may be gzip encoded; their ETag is over stored
        # bytes, not get_object()'s transparently decoded JSON.
        status, _ = client._request('PUT', 'papers-manifest.json', body=replacement,
            content_type='application/json', cache_control='no-cache',
            conditional_headers={'if-match': '"' + plan['manifest_etag'] + '"'})
        if status not in (200, 201):
            raise R2S3Error('Conditional canonical manifest update failed', status=status)
    ordered = sorted(keys) + [item['key'] for item in plan.get('prototype', [])]
    removed = 0
    for offset in range(0, len(ordered), batch_size):
        if _object(client, POINTER) != pointer or client.get_object('paper-template-v1/current.json') is not None:
            raise R2S3Error('Publication changed during cleanup; remaining deletions stopped')
        batch = ordered[offset:offset + batch_size]
        client.delete_objects(batch)
        removed += len(batch)
        receipt = {'schema': 1, 'publication_sha256': publication_sha256, 'removed': removed,
                   'total': len(ordered), 'completed': removed == len(ordered)}
        _save(output / 'cleanup-receipt.json', json_bytes(receipt))
        print(f'Legacy Papers cleanup: {removed}/{len(ordered)}', flush=True)
    return receipt
