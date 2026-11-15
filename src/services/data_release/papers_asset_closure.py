"""Verify collection/template resources against the merged immutable catalogue."""
from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlsplit

from .artifact_index import ArtifactIndex
from .bundle_assets import _asset_references
from .papers_immutable import _source


def verify_candidate_asset_closure(candidate) -> None:
    """Follow the existing bundle parser without copying or inventing entries.

    Root navigation/logos may deliberately belong to the main site. Compiled
    /_astro resources, relative assets and every included dependency must exist
    in this exact catalogue with matching bytes, including transitive CSS/fonts
    and JS/hydration modules. Old retained template assets are checked as well.
    """
    entries = {entry['path']: entry for entry in candidate.entries if 'paper_template' not in entry}
    index = ArtifactIndex(candidate.root)
    pending = [path for path in entries if path.endswith(('.html', '.htm'))
               or path.startswith('_astro/') and path.endswith(('.js', '.css'))]
    visited = set()
    local_hosts = {urlsplit(candidate.origin).netloc, 'globalinfectiousdisease.com',
                   'www.globalinfectiousdisease.com', 'papers.globalinfectiousdisease.com'}
    while pending:
        owner = pending.pop()
        if owner in visited:
            continue
        visited.add(owner)
        body, digests = index.read(_source(candidate.root, owner))
        entry = entries[owner]
        if digests['sha256'] != entry['sha256'] or digests['bytes'] != entry['bytes']:
            raise ValueError(f'Publication asset changed: {owner}')
        if not owner.endswith(('.html', '.htm', '.js', '.css')):
            continue
        for token in _asset_references(candidate.root / owner, index, body=body):
            parsed = urlsplit(token)
            if parsed.scheme or parsed.netloc:
                if parsed.netloc not in local_hosts or parsed.scheme not in ('', 'http', 'https'):
                    continue
            ref = unquote(parsed.path)
            if not ref:
                continue
            # A quoted runtime name such as "Node.js", or a template such as
            # `${a}.png`, ends in an asset suffix but is not a static path.
            if '$' in ref or '{' in ref or '}' in ref:
                continue
            if '/' not in ref and not ref.startswith('.') and posixpath.splitext(ref)[1].lower() == '.js':
                continue
            relative = (ref.lstrip('/') if ref.startswith(('/', '_astro/')) else
                        posixpath.normpath(posixpath.join(posixpath.dirname(owner), ref)))
            if relative.startswith('../') or '\\' in relative:
                raise ValueError(f'Publication asset escapes candidate: {owner} -> {ref}')
            required = ref.startswith(('/_astro/', '_astro/')) or not ref.startswith('/')
            if relative not in entries:
                if required:
                    raise ValueError(f'Publication asset dependency missing: {owner} -> {ref}')
                continue
            pending.append(relative)
    index.save()
