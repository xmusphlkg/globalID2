#!/usr/bin/env python3
"""Prepare a Pages + direct-R2 research release without deploying it."""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.papers_bundle import (  # noqa: E402
    append_papers_redirects, assert_pages_budget, ensure_papers_cors_headers,
    split_papers_bundle,
)
from src.services.data_release.build_reuse import stamp_homepages  # noqa: E402
from src.services.data_release.pipeline import _built_site_digest  # noqa: E402

RESEARCH_ROUTES = {
    '/research', '/research/*', '/zh/research', '/zh/research/*',
    '/fr/research', '/fr/research/*', '/site-data/research/*',
}


def write_papers_discovery(work: Path) -> None:
    """Isolated research builds also need the Papers robots file and sitemap.

    Generate from actual rendered pages, including all languages and excluding
    noindex pages. Partition the sitemap as the article catalogue grows.
    """
    origin = 'https://papers.globalinfectiousdisease.com'
    host = work / 'papers-host'
    host.mkdir(exist_ok=True)
    urls = []
    for prefix in ('research', 'zh/research', 'fr/research'):
        for kind in ('articles', 'diseases'):
            for page in sorted((work / prefix / kind).glob('*/index.html')):
                with page.open() as stream:
                    head = stream.read(32768).split('</head>', 1)[0]
                if re.search(r'<meta\b[^>]*name=[\"\']robots[\"\'][^>]*content=[\"\'][^\"\']*noindex', head, re.I):
                    continue
                urls.append(origin + '/' + page.relative_to(work).as_posix().removesuffix('index.html'))
    ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    def write(name, tag, rows):
        root = ElementTree.Element(tag, xmlns=ns)
        for url in rows:
            ElementTree.SubElement(ElementTree.SubElement(root, 'url' if tag == 'urlset' else 'sitemap'), 'loc').text = url
        (host / name).write_bytes(ElementTree.tostring(root, encoding='utf-8', xml_declaration=True))
    if len(urls) <= 45000:
        write('sitemap.xml', 'urlset', urls)
    else:
        names = []
        for start in range(0, len(urls), 45000):
            name = f'sitemap-{start // 45000 + 1}.xml'
            write(name, 'urlset', urls[start:start + 45000])
            names.append(origin + '/' + name)
        write('sitemap.xml', 'sitemapindex', names)
    (host / 'robots.txt').write_text(f'User-agent: *\nAllow: /\n\nSitemap: {origin}/sitemap.xml\n')


def finalize_identity(output: Path, *, release_id: str, source_commit: str, source_branch: str, built_at: str) -> dict:
    """Stamp the prepared Pages tree and record its actual deployable digest."""
    output = Path(output).resolve()
    main = output / 'pages'
    review_path = output / 'review.json'
    review = json.loads(review_path.read_text())
    identity_path = main / 'release.json'
    identity = json.loads(identity_path.read_text())
    if (not review.get('release_identity_requires_refresh') or not release_id or not source_branch
            or len(source_commit) != 40 or any(c not in '0123456789abcdef' for c in source_commit)):
        raise ValueError('Invalid static research release identity')
    identity.update(release_id=release_id, built_at=built_at, source_commit=source_commit,
                    source_branch=source_branch, commit_dirty=True)
    if not stamp_homepages(main, identity):
        raise ValueError('Pages homepages do not contain the required release markers')
    identity['build_digest'] = _built_site_digest(main)
    identity_path.write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    review.update(release_identity_requires_refresh=False, release_id=release_id,
                  pages_build_digest=identity['build_digest'])
    review_path.write_text(json.dumps(review, indent=2) + '\n')
    return identity


def prepare(base: Path, stage: Path, output: Path) -> dict:
    base, stage, output = map(lambda p: Path(p).resolve(), (base, stage, output))
    if not base.is_dir() or not stage.is_dir() or output.exists():
        raise ValueError('Existing base and research stage, and a new output directory, are required')
    if any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in (base, stage)):
        raise ValueError('Output must be separate from the input directories')
    if not (base / 'release.json').is_file() or not (base / '_routes.json').is_file():
        raise ValueError('Base must be a verified Pages snapshot')
    if not (stage / 'research/index.html').is_file():
        raise ValueError('Research stage is incomplete')
    output.mkdir(parents=True)
    main, papers, work = output / 'pages', output / 'papers', output / 'research-work'
    shutil.copytree(base, main)
    shutil.copytree(stage, work)
    write_papers_discovery(work)
    split_papers_bundle(work, papers_dir=papers, update_redirects=False, update_headers=False)
    for relative in ('research', 'zh/research', 'fr/research'):
        target, source = main / relative, work / relative
        if target.is_symlink():
            raise ValueError(f'Unsafe Pages research path: {relative}')
        if target.exists():
            shutil.rmtree(target)
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
    graph = work / 'site-data/research-graph.json'
    if graph.is_file():
        target = main / 'site-data/research-graph.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(graph, target)
    # Hashed assets already in the live snapshot must never be overwritten by
    # different bytes; unchanged assets remain available to old nonresearch pages.
    assets = work / '_astro'
    if assets.is_dir():
        for source in assets.rglob('*'):
            if not source.is_file():
                continue
            target = main / '_astro' / source.relative_to(assets)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                    raise ValueError(f'Conflicting hashed asset: {target.name}')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    routes_path = main / '_routes.json'
    routes = json.loads(routes_path.read_text())
    if not isinstance(routes.get('include'), list):
        raise ValueError('Invalid Pages routing configuration')
    routes['include'] = [item for item in routes['include'] if item not in RESEARCH_ROUTES]
    probes = ['/research/', '/zh/research/', '/fr/research/', '/site-data/research/a.json']
    exclusions = routes.get('exclude', [])
    if not isinstance(exclusions, list) or any(not isinstance(rule, str) for rule in [*routes['include'], *exclusions]):
        raise ValueError('Invalid Pages routing configuration')
    if any(any(fnmatchcase(path, rule) for rule in routes['include'])
           and not any(fnmatchcase(path, rule) for rule in exclusions) for path in probes):
        raise ValueError('A broad Pages Worker route still captures research requests')
    routes_path.write_text(json.dumps(routes, separators=(',', ':')) + '\n')
    redirects = main / '_redirects'
    prior = redirects.read_text() if redirects.exists() else ''
    prior = '\n'.join(line for line in prior.splitlines() if not line.lstrip().startswith('/research/catalogue/studies/'))
    updated = append_papers_redirects(prior)
    updated = '\n'.join(line for line in updated.splitlines()
                        if not line.lstrip().startswith(('/research/catalogue/studies/', '/site-data/research/'))) + '\n'
    redirects.write_text(updated)
    headers = main / '_headers'
    headers.write_text(ensure_papers_cors_headers(headers.read_text() if headers.exists() else ''))
    budget = assert_pages_budget(main)
    shutil.rmtree(work)
    result = {'base_release': json.loads((base / 'release.json').read_text()).get('release_id'),
              'release_identity_requires_refresh': True,
              'pages_files': budget.file_count, 'papers_files': json.loads((papers / 'papers-manifest.json').read_text())['file_count'],
              'research_worker_routes': [item for item in routes['include'] if item in RESEARCH_ROUTES]}
    (output / 'review.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.base, args.stage, args.output), indent=2))


if __name__ == '__main__':
    main()
