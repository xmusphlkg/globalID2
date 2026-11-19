"""Freeze article selection against the captured, already published baseline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from src.generation.literature_publication import _atomic_write
from src.generation.literature_projection_state import canonical_bytes
from src.literature.publication_changes import merge_literature_change_sets
from .surveillance_publish import _starting_baseline


def incremental_export_arguments(*, root: Path, run: Path, change_sets: list,
                                 dirty_ids: list[int], config) -> list[str]:
    """Unknown changes or an unavailable published baseline use the full exporter.

    The exporter independently verifies the private projection state and its
    implementation fingerprint. A local receipt alone never authorizes reuse.
    """
    merged = merge_literature_change_sets(change_sets)
    if merged is None:
        return []
    captured, previous = _starting_baseline(run, config, 'site-data/research-publication')
    if not captured or previous is None:
        return []
    manifest = json.loads(previous)
    try:
        receipt = json.loads((root / 'exports/research-current-receipt.json').read_bytes())
        task_id = str(UUID(receipt['task_uuid']))
        if receipt['release_id'] != f'literature-{task_id}':
            return []
        if (manifest['release_id'] != receipt['release_id'] or
                manifest.get('papers', {}).get('papers_generation_sha256') != receipt['papers_generation_sha256']):
            return []
    except (OSError, ValueError, KeyError, TypeError):
        return []
    previous_snapshot = root / 'exports/research-runs' / task_id / 'snapshot'
    if previous_snapshot.is_symlink() or not previous_snapshot.is_dir():
        return []
    selection = {'schema': 1, 'dirty_ids': sorted(dirty_ids), 'article_ids': merged['article_ids'],
                 'baseline_sha256': hashlib.sha256(canonical_bytes(manifest)).hexdigest()}
    path = run / 'incremental-selection.json'
    if path.exists() and json.loads(path.read_bytes()) != selection:
        raise ValueError('Frozen literature article selection changed')
    _atomic_write(path, canonical_bytes(selection))
    articles_path = run / 'selected-article-ids.json'
    _atomic_write(articles_path, canonical_bytes(merged['article_ids']))
    return ['--previous-snapshot', str(previous_snapshot), '--article-ids-json', str(articles_path),
            '--expected-previous-manifest-sha256', selection['baseline_sha256']]
