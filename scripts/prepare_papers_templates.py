#!/usr/bin/env python3
"""Build an isolated JSON/template candidate from a verified Papers bundle.

No publication or removal occurs. Each paper can be reconstructed exactly
from public HTML fragments and a shared locale shell. Content-addressed
records and index shards allow a shell-only update without rewriting records.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.data_release.papers_template import assemble_paper, factor_paper, template_digest


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _put(root: Path, group: str, value: dict) -> str:
    data = _json(value)
    digest = hashlib.sha256(data).hexdigest()
    target = root / group / f'{digest}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != data:
        raise ValueError('Conflicting content-addressed object')
    if not target.exists(): target.write_bytes(data)
    return digest


def prepare(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_dir() or destination.exists():
        raise ValueError('A source directory and a new destination are required')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Source and destination must be separate')
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.papers-template-', dir=destination.parent))
    try:
        manifest = {'schema': 1, 'locales': {}}
        counts = {}
        for locale, prefix in [('en', ''), ('zh', 'zh'), ('fr', 'fr')]:
            pages = sorted((source / prefix / 'research/articles').glob('*/index.html'))
            if not pages: raise ValueError(f'No paper pages for {locale}')
            shell = None
            shards = {}
            for page in pages:
                if page.is_symlink(): raise ValueError('Symlink paper page')
                html = page.read_text(encoding='utf-8')
                pair = factor_paper(html, page.parent.name)
                if assemble_paper(pair.template, pair.record) != html:
                    raise ValueError(f'Paper round trip mismatch: {page}')
                identity = template_digest(pair.template)
                if shell is None:
                    shell = identity
                    if _put(work, 'templates', pair.template) != shell: raise ValueError('Template digest mismatch')
                elif shell != identity:
                    raise ValueError(f'Multiple {locale} template shapes; inspect before migration: {page}')
                digest = _put(work, 'records', pair.record)
                bucket = hashlib.sha256(pair.record['slug'].encode()).hexdigest()[:2]
                entries = shards.setdefault(bucket, {})
                if pair.record['slug'] in entries: raise ValueError('Duplicate slug')
                entries[pair.record['slug']] = digest
            indexes = {bucket: _put(work, 'indexes', {'schema': 1, 'locale': locale, 'items': entries})
                       for bucket, entries in sorted(shards.items())}
            manifest['locales'][locale] = {'template': shell, 'indexes': indexes}
            counts[locale] = len(pages)
        generation = _put(work, 'generations', manifest)
        (work / 'current.json').write_bytes(_json({'schema': 1, 'generation': generation}))
        os.rename(work, destination)
        return {'generation': generation, 'pages': counts, 'destination': str(destination)}
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.source, a.output)))


if __name__ == '__main__': main()
