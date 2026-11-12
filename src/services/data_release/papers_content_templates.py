"""Attach reusable public JSON records to an immutable Papers catalogue."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .papers_template import assemble_paper
from .papers_templates_publish import PREFIX, validate_candidate

ARTICLE = re.compile(r'(?:(zh|fr)/)?research/articles/([a-z0-9][a-z0-9-]*)/index\.html\Z')


def prepare_template_descriptors(root: Path, files, directory: Path):
    """Prepare or resume immutable records and verify the frozen HTML identity.

    The catalogue keeps the reconstructed HTML digest. This preserves build
    receipts and public provenance while its storage key points at shared JSON.
    """
    from scripts.prepare_papers_templates import prepare

    directory = Path(directory).resolve()
    articles = {}
    for item in files:
        match = ARTICLE.fullmatch(item.path)
        if match:
            articles[(match[2], match[1] or 'en')] = item
    if not articles:
        return {}, (), directory
    if not directory.exists():
        prepare(root, directory)
    pointer, objects, _ = validate_candidate(directory)
    generation = json.loads(pointer)['generation']
    manifest = json.loads(objects[f'generations/{generation}.json'].read_bytes())
    templates = {}
    payloads = {}
    for locale, spec in manifest['locales'].items():
        digest = spec['template']
        relative = f'templates/{digest}.json'
        path = objects[relative]
        templates[locale] = (digest, json.loads(path.read_bytes()))
        payloads[relative] = {'path': relative, 'key': f'{PREFIX}/{relative}',
                              'sha256': digest, 'bytes': path.stat().st_size,
                              'content_type': 'application/json; charset=utf-8'}
    descriptors = {}
    for relative, path in objects.items():
        if not relative.startswith('records/'):
            continue
        raw = path.read_bytes()
        record = json.loads(raw)
        digest = path.stem
        for locale, localized in record['locales'].items():
            item = articles.get((record['slug'], locale))
            if item is None:
                raise ValueError('Template candidate contains an article outside the frozen manifest')
            template_digest, template = templates[locale]
            html = assemble_paper(template, {'schema': 1, 'slug': record['slug'], **localized}).encode()
            if len(html) != item.size or hashlib.sha256(html).hexdigest() != item.sha256:
                raise ValueError(f'Template candidate does not match frozen article: {item.path}')
            descriptors[item.path] = {'template_sha256': template_digest,
                                      'record_sha256': digest, 'locale': locale,
                                      'slug': record['slug']}
        payloads[relative] = {'path': relative, 'key': f'{PREFIX}/{relative}',
                              'sha256': digest, 'bytes': len(raw),
                              'content_type': 'application/json; charset=utf-8'}
    if len(descriptors) != len(articles):
        raise ValueError('Template candidate is missing a frozen article')
    return descriptors, tuple(payloads[key] for key in sorted(payloads)), directory
