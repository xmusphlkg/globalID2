"""Compact cell metadata on disk; expand the existing catalogue API in memory."""
from __future__ import annotations

import json
from pathlib import Path

from src.generation.site_data_writer import _write_if_changed

ENCODING = 'country_parts_v1'
MAX_MANIFEST_BYTES = 100 * 1024 * 1024


def _key(part):
    return (part['country_code'], part['disease_id'], part['id'])


def _disease_part(part):
    prefix = f"{part['disease_id']} · "
    label = part.get('label', part['id']).removeprefix(prefix)
    return {**part, 'label': f"{part['country_code']} · {label}",
            'files': {kind: dict(meta) for kind, meta in part['files'].items()}}


def expand_manifest(document: dict) -> dict:
    """Hydrate a parsed manifest in place, accepting previous full v4/v5 files."""
    encoding = document.pop('cell_metadata', None)
    if encoding is None:
        return document
    if encoding != ENCODING or document.get('schema_version') != 5:
        raise ValueError('Unsupported download cell metadata encoding')
    indexed = {}
    for entry in document.get('countries', []):
        for part in entry.get('parts', []):
            country, disease, window = _key(part)
            for kind, meta in part['files'].items():
                relative = f'cells/{country.lower()}/{disease.lower()}/{window}.{kind}'
                meta.setdefault('relative_path', relative)
                meta.setdefault('url', f"{document['download_url_base'].rstrip('/')}/{relative}")
                meta.setdefault('filename', f'globalid-{country.lower()}-{disease.lower()}-{window}.{kind}')
            part.setdefault('content_sha256', part['files']['json']['sha256'])
            if _key(part) in indexed:
                raise ValueError('Duplicate country cell in download manifest')
            indexed[_key(part)] = part
    for entry in document.get('diseases', []):
        entry['parts'] = [part if 'files' in part else _disease_part(
            indexed[(part['country_code'], entry['disease_id'], part['id'])])
            for part in entry.get('parts', [])]
    return document


def compact_manifest(document: dict) -> dict:
    """Store each cell once; paths and names follow the public cell contract."""
    if document.get('schema_version') != 5 or document.get('cell_metadata') == ENCODING:
        return document
    result = {**document, 'cell_metadata': ENCODING, 'countries': [], 'diseases': []}
    indexed = {}
    for entry in document.get('countries', []):
        parts = []
        for part in entry.get('parts', []):
            indexed[_key(part)] = part
            country, disease, window = _key(part)
            files = {}
            for kind, meta in part['files'].items():
                relative = f'cells/{country.lower()}/{disease.lower()}/{window}.{kind}'
                derived = {'relative_path': relative,
                    'url': f"{document['download_url_base'].rstrip('/')}/{relative}",
                    'filename': f'globalid-{country.lower()}-{disease.lower()}-{window}.{kind}'}
                files[kind] = {key: value for key, value in meta.items()
                               if key not in derived or derived[key] != value}
            packed = {**part, 'files': files}
            if packed.get('content_sha256') == part['files']['json']['sha256']:
                packed.pop('content_sha256')
            parts.append(packed)
        result['countries'].append({**entry, 'parts': parts})
    for entry in document.get('diseases', []):
        parts = []
        for part in entry.get('parts', []):
            original = indexed.get(_key(part))
            if original is not None and part == _disease_part(original):
                parts.append({'country_code': part['country_code'], 'id': part['id']})
            else:
                parts.append(part)  # Preserve nonstandard metadata for validation.
        result['diseases'].append({**entry, 'parts': parts})
    return result


def manifest_bytes(document: dict) -> bytes:
    body = json.dumps(compact_manifest(document), ensure_ascii=False,
                      separators=(',', ':'), sort_keys=True).encode('utf-8')
    if len(body) >= MAX_MANIFEST_BYTES:
        raise ValueError('Download manifest exceeds the GitHub 100 MiB file limit')
    return body


def write_download_manifest(path: Path, document: dict) -> bool:
    return _write_if_changed(path, manifest_bytes(document))
