"""Bind newly rendered collection pages to the frozen public Research inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .papers_immutable import ImmutablePapersError, _source


def verify_collection_build(path: Path, *, bundle: Path, index_sha256: str, rewritten: dict | None = None) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Independent site publication requires newly rendered Research collections')
    receipt = json.loads(path.read_bytes())
    if (receipt.get('schema') != 1 or receipt.get('scope') != 'collections'
            or receipt.get('article_pages_rendered') != 0
            or receipt.get('research_index_sha256') != index_sha256
            or not isinstance(receipt.get('pages'), dict)):
        raise ValueError('Research collection build does not match the frozen public index')
    verified_pages = {}
    for relative, digest in receipt['pages'].items():
        if (not re.fullmatch(r'(?:zh/|fr/)?research/(?:countries|diseases|topics|weekly)/[A-Za-z0-9][A-Za-z0-9_()-]*/index\.html', relative)
                or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('Invalid rendered Research collection receipt')
        try:
            source = _source(bundle, relative)
        except ImmutablePapersError as error:
            raise ValueError(f'Rendered Research collection missing or unsafe: {relative}') from error
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        # Papers partitioning rewrites origin references. Its build manifest
        # binds the renderer's original bytes to the final rewritten bytes.
        rewrite = (rewritten or {}).get(relative, {})
        if actual != digest and not (rewrite.get('source_sha256') == digest and rewrite.get('sha256') == actual):
            raise ValueError(f'Rendered Research collection missing or changed: {relative}')
        verified_pages[relative] = actual
    return {**receipt, 'pages': verified_pages}
