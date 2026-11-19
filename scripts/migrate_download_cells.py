#!/usr/bin/env python3
"""Migrate a frozen v4 download tree into resumable, verified v5 cells."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.direct_download import cells
from src.generation.direct_download.manifest import write_download_manifest
from src.generation.direct_download_files import (
    _build_dataset_parts_task, _entry_files_available, _file_sha256,
    _projection_code_sha256, _row_provenance_counts, _sort_rows,
    _process_pool_context,
)
from src.generation.site_data_writer import write_compact_json
from src.services.data_release.git_downloads.assets import validate_source, _manifest_assets


def migrate_country(entry, source, destination, base, limit, disease_ids):
    """Read verified JSON only; keep every original row and provenance field."""
    checkpoint = destination / '.migration' / f"{entry['id']}.json"
    if checkpoint.exists():
        cached = json.loads(checkpoint.read_text())
        if _entry_files_available(cached, destination, 'countries', entry['id'], layout='cells'):
            return cached
    rows = []
    for part in entry['parts']:
        meta = part['files']['json']
        path = source / meta['relative_path']
        if _file_sha256(path) != meta['sha256']:
            raise ValueError(f'Baseline checksum mismatch: {path}')
        payload = json.loads(path.read_text())
        records = payload['records']
        if len(records) != part['record_count']:
            raise ValueError(f'Baseline count mismatch: {path}')
        for row in records:
            if row.get('country_code') != entry['code'] or row.get('disease_id') not in disease_ids:
                raise ValueError(f'Baseline row identity mismatch: {path}')
        rows.extend(records)
    if len(rows) != entry['record_count']:
        raise ValueError(f"Baseline country count mismatch: {entry['id']}")
    grouped = {}
    for row in _sort_rows(rows):
        grouped.setdefault(row['disease_id'], []).append(row)
    result = {**entry, 'parts': [], 'disease_counts': {}}
    result.pop('input_sha256', None)  # A future selected scope must fingerprint live input.
    for disease_id, records in sorted(grouped.items()):
        dataset = dict(kind='country', directory='cells', dataset_id=entry['id'],
            path_id=f"{entry['id']}/{disease_id.lower()}",
            filename_id=f"{entry['id']}-{disease_id.lower()}",
            slug=entry['id'], name=entry.get('name') or entry.get('name_en'))
        built = _build_dataset_parts_task(kind='country', dataset=dataset, rows=records,
            output_dir=destination, download_url_base=base, max_file_bytes=limit, existing_parts={})
        if sum(part['record_count'] for part in built['parts']) != len(records):
            raise ValueError(f"Partitioning lost rows: {entry['id']}/{disease_id}")
        result['parts'].extend(cells.annotate_country_part(part, disease_id=disease_id,
            country_code=entry['code']) for part in built['parts'])
        result['disease_counts'][disease_id] = dict(record_count=len(records), **_row_provenance_counts(records))
    write_compact_json(checkpoint, result)
    return result


def migrate(source: Path, destination: Path, *, workers=4) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Migration source and destination must be separate trees')
    destination.mkdir(parents=True, exist_ok=True)
    # Share the production writer lock. Fail promptly instead of hanging behind a release.
    with (source.parent / f'.{source.name}.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        body = (source / 'manifest.json').read_bytes()
        manifest = json.loads(body)
        if manifest['schema_version'] != 4:
            raise ValueError('Migration requires a schema 4 source')
        branch = manifest['download_url_base'].rsplit('/', 1)[-1]
        validate_source(source, branch)
        identity = {'source_sha256': hashlib.sha256(body).hexdigest(),
                    'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'projection_sha256': _projection_code_sha256()}
        receipt = destination / '.migration' / 'source.json'
        if receipt.exists() and json.loads(receipt.read_text()) != identity:
            raise ValueError('Source or migration code changed; use a new destination')
        write_compact_json(receipt, identity)
        disease_ids = {entry['disease_id'] for entry in manifest['diseases']}
        results = {}
        with ProcessPoolExecutor(max_workers=workers, mp_context=_process_pool_context()) as pool:
            pending = {pool.submit(migrate_country, entry, source, destination,
                manifest['download_url_base'], manifest['partitioning']['target_file_bytes'], disease_ids): entry
                for entry in manifest['countries']}
            for future in as_completed(pending):
                entry = future.result()
                results[entry['id']] = entry
                print(f"Migrated {len(results)}/{len(pending)}: {entry['code']} ({entry['record_count']} rows)", flush=True)
        migrated = copy.deepcopy(manifest)
        migrated['schema_version'] = 5
        migrated['partitioning']['grain'] = 'country_disease_window'
        migrated['countries'] = [results[entry['id']] for entry in manifest['countries']]
        for entry in migrated['diseases']:
            disease_id = entry['disease_id']
            counts = cells.sum_disease_counts(migrated['countries'], disease_id)
            for field in ('record_count', 'projection_record_count', 'source_observation_count'):
                if counts[field] != entry[field]:
                    raise ValueError(f'Disease count mismatch: {disease_id}/{field}: {counts[field]} != {entry[field]}')
            entry.update(counts)
            entry['parts'] = cells.assemble_disease_parts(migrated['countries'], disease_id)
            entry.pop('input_sha256', None)
            entry.pop('dependency_sha256', None)
        write_download_manifest(destination / 'manifest.json', migrated)
        validate_source(destination, branch)
        for relative, (_, expected) in _manifest_assets(destination / 'manifest.json').items():
            if _file_sha256(destination / relative) != expected:
                raise ValueError(f'Migrated checksum mismatch: {relative}')
        write_compact_json(destination / '.migration' / 'verified.json', {
            **identity, 'manifest_sha256': _file_sha256(destination / 'manifest.json')})
        print('Verified all cell assets and per-disease row counts.', flush=True)
        return migrated


def activate(source: Path, destination: Path) -> Path:
    """Install verified cells under the writer lock, retaining a rollback baseline."""
    source, destination = source.resolve(), destination.resolve()
    with (source.parent / f'.{source.name}.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = json.loads((destination / '.migration' / 'verified.json').read_text())
        if _file_sha256(source / 'manifest.json') != identity['source_sha256']:
            raise ValueError('Production baseline changed since migration; regenerate before activation')
        if _file_sha256(destination / 'manifest.json') != identity['manifest_sha256']:
            raise ValueError('Verified migration manifest changed')
        manifest = json.loads((destination / 'manifest.json').read_text())
        validate_source(destination, manifest['download_url_base'].rsplit('/', 1)[-1])
        backup = source.parent / f".{source.name}.v4-{identity['source_sha256'][:12]}"
        backup.mkdir()  # Never overwrite a previous rollback baseline.
        shutil.copy2(source / 'manifest.json', backup / 'manifest.json')
        installed = False
        try:
            os.rename(destination / 'cells', source / 'cells')
            installed = True
            for name in ('countries', 'diseases'):
                if (source / name).exists():
                    os.rename(source / name, backup / name)
            write_download_manifest(source / 'manifest.json', manifest)
        except BaseException:
            for name in ('countries', 'diseases'):
                if (backup / name).exists():
                    os.rename(backup / name, source / name)
            if installed:
                os.rename(source / 'cells', destination / 'cells')
            write_compact_json(source / 'manifest.json', json.loads((backup / 'manifest.json').read_text()))
            raise
        print(f'Activated schema 5. Rollback baseline: {backup}', flush=True)
        return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'exports/site-downloads')
    parser.add_argument('--destination', type=Path, default=ROOT / 'exports/site-downloads-cells')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--activate', action='store_true', help='Install verified cells locally, keeping a v4 backup')
    args = parser.parse_args()
    migrate(args.source, args.destination, workers=args.workers)
    if args.activate:
        activate(args.source, args.destination)


if __name__ == '__main__':
    main()
