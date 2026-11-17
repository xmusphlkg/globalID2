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
        # Reports and Papers partitioning can intentionally rewrite links after
        # collection rendering. Accept only transformations bound by their
        # source and result hashes, in the order they were applied.
        expected = digest
        rewrite_receipts = receipt.get('post_build_rewrites', {})
        if not isinstance(rewrite_receipts, dict):
            raise ValueError('Invalid rendered Research collection rewrite receipt')
        chain = rewrite_receipts.get(relative, [])
        if not isinstance(chain, list):
            raise ValueError('Invalid rendered Research collection rewrite receipt')
        for rewrite in [*chain, (rewritten or {}).get(relative, {})]:
            if not rewrite:
                continue
            if (not isinstance(rewrite, dict) or rewrite.get('source_sha256') != expected
                    or not isinstance(rewrite.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', rewrite['sha256'])):
                raise ValueError(f'Invalid rendered Research collection rewrite chain: {relative}')
            expected = rewrite['sha256']
        if actual != expected:
            raise ValueError(f'Rendered Research collection missing or changed: {relative}')
        verified_pages[relative] = actual
    return {**receipt, 'pages': verified_pages, 'post_build_rewrites': {}}
