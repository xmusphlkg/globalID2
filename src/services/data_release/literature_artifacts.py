"""Prepare content receipts without rereading reused research article pages."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.core import get_logger
from .artifact_index import ArtifactIndex
from .artifact_paths import artifact_files
from .papers_bundle import _object_key_for_papers

logger = get_logger(__name__)
ARTICLE = re.compile(r'(?:zh/|fr/)?research/articles/([a-z0-9][a-z0-9-]*)/index\.html\Z')
CARD = re.compile(r'research/cards/([a-z0-9][a-z0-9-]*)\.png\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')


def build_research_papers_manifest(root: Path, *, origin: str, build: dict[str, Any],
                                   previous: dict[str, Any] | None = None,
                                   index: ArtifactIndex | None = None) -> dict:
    root = Path(root).resolve()
    inputs = build.get('inputs')
    if (build.get('schema') != 1 or not isinstance(inputs, dict) or inputs.get('schema') != 1
            or not isinstance(inputs.get('articles'), dict) or not isinstance(build.get('reused'), list)):
        raise ValueError('Invalid isolated research build receipt')
    old_build = previous.get('build') if isinstance(previous, dict) else None
    old_inputs = old_build.get('inputs') if isinstance(old_build, dict) else None
    usable = (isinstance(old_inputs, dict) and old_inputs.get('shared') == inputs.get('shared')
              and isinstance(old_build.get('cache_version'), str)
              and old_build['cache_version'] == build.get('previous_cache_version'))
    old_files = {entry['path']: entry for entry in previous.get('files', [])
                 if isinstance(entry, dict) and isinstance(entry.get('path'), str)} if usable else {}
    reusable = set(build['reused']) if usable else set()
    index = index or ArtifactIndex(root)
    files, reused = [], 0
    for path, local in artifact_files(root):
        if path == 'papers-manifest.json':
            continue
        match = ARTICLE.fullmatch(path) or CARD.fullmatch(path)
        slug = match[1] if match else None
        old = old_files.get(path)
        prior_article = old_inputs.get('articles', {}).get(slug) if usable else None
        current_article = inputs['articles'].get(slug) if slug else None
        if (slug in reusable and prior_article == current_article and isinstance(old, dict)
                and isinstance(old.get('sha256'), str) and SHA.fullmatch(old['sha256'])
                and type(old.get('bytes')) is int and old['bytes'] >= 0):
            info = {'sha256': old['sha256'], 'bytes': old['bytes']}
            reused += 1
        else:
            info = index.describe(local)
        key = _object_key_for_papers(path)
        files.append({'path': path, 'key': key, **info, 'url': f"{origin.rstrip('/')}/{key}"})
    index.save()
    logger.info('Research build receipts prepared: files={} reused_article_pages={} source_reads={} bytes_read={}',
                len(files), reused, index.read_files, index.read_bytes)
    return {'schema_version': 1, 'papers_origin': origin.rstrip('/'), 'file_count': len(files), 'files': files}
