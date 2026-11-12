"""Bounded compatibility retention when publishing from a fresh build directory."""
from __future__ import annotations

import math
import time

from .papers_bundle import LIBRARY_SHARD_PATH, LIBRARY_SHARD_RETENTION_SECONDS, is_obsolete_research_build_path


def retain_remote_dependencies(local: dict, previous: dict, inventory: dict, *, now=None) -> dict:
    """Keep previous assets and 48-hour shards without downloading their bodies.

    Local split retention alone is insufficient for isolated build directories.
    Retained entries stay in the completion manifest so the next release can
    retire them; unknown bucket objects are never claimed or swept here.
    """
    instant = time.time() if now is None else now
    active = {item['key'] for item in local['files']}
    current_assets = local.get('asset_generations', [[]])[0]
    generations = [current_assets]
    for group in previous.get('asset_generations', []):
        if group != current_assets:
            generations.append(group)
            break
    retained_assets = {path for group in generations for path in group}
    retired = dict(local.get('library_shard_retirements', {}))
    old_retired = previous.get('library_shard_retirements', {})
    additions = []
    for entry in previous.get('files', []):
        key, path = entry['key'], entry['path']
        if key in active or key not in inventory:
            continue
        keep = path in retained_assets
        if LIBRARY_SHARD_PATH.fullmatch(path) and not is_obsolete_research_build_path(path):
            timestamp = old_retired.get(path, instant)
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp > instant:
                timestamp = instant
            if instant - timestamp < LIBRARY_SHARD_RETENTION_SECONDS:
                keep = True
                retired[path] = timestamp
        if keep:
            additions.append(entry)
    files = sorted([*local['files'], *additions], key=lambda entry: entry['key'])
    return {**local, 'files': files, 'file_count': len(files),
            'asset_generations': generations, 'library_shard_retirements': retired}
