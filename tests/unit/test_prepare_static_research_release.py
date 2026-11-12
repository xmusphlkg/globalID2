import json
import pytest

from scripts.prepare_static_research_release import finalize_identity, prepare
from src.services.data_release.pipeline import _built_site_digest


def test_static_package_preserves_other_worker_routes_and_moves_papers(tmp_path):
    base, stage, output = (tmp_path / name for name in ('base', 'stage', 'output'))
    base.mkdir()
    (base / 'release.json').write_text('{"release_id":"live"}')
    (base / '_routes.json').write_text(json.dumps({'version': 1, 'include': ['/research/*', '/site-data/current.json'], 'exclude': []}))
    (base / '_worker.js').write_text('other data worker')
    (base / '_redirects').write_text('/research/catalogue/studies/*  /old/:splat  301\n')
    (base / 'research').mkdir()
    (base / 'research/old.html').write_text('old')
    (stage / 'research/articles/a').mkdir(parents=True)
    (stage / 'research/articles/a/index.html').write_text('<html>paper</html>')
    (stage / 'research/index.html').write_text('<html>new research</html>')
    result = prepare(base, stage, output)
    assert result['research_worker_routes'] == []
    assert result['release_identity_requires_refresh']
    assert json.loads((output / 'pages/_routes.json').read_text())['include'] == ['/site-data/current.json']
    assert (output / 'pages/_worker.js').read_text() == 'other data worker'
    assert not (output / 'pages/research/old.html').exists()
    assert (output / 'pages/research/index.html').exists()
    assert (output / 'papers/research/articles/a/index.html').exists()
    assert 'https://papers.globalinfectiousdisease.com/research/articles/a/' in (output / 'papers/sitemap.xml').read_text()
    assert 'Sitemap:' in (output / 'papers/robots.txt').read_text()
    redirects = (output / 'pages/_redirects').read_text()
    assert '/research/articles/*' in redirects
    assert '/research/catalogue/studies/*' not in redirects


def test_papers_discovery_excludes_noindex_and_includes_locales(tmp_path):
    from scripts.prepare_static_research_release import write_papers_discovery
    for name, html in [('research/articles/private', '<meta name="robots" content="noindex, follow">'),
                       ('zh/research/diseases/influenza', '<html>public</html>')]:
        page = tmp_path / name / 'index.html'
        page.parent.mkdir(parents=True)
        page.write_text(html)
    write_papers_discovery(tmp_path)
    sitemap = (tmp_path / 'papers-host/sitemap.xml').read_text()
    assert 'private' not in sitemap
    assert '/zh/research/diseases/influenza/' in sitemap


def test_static_package_rejects_broad_worker_routes(tmp_path):
    base, stage, output = (tmp_path / name for name in ('base', 'stage', 'output'))
    base.mkdir()
    (base / 'release.json').write_text('{"release_id":"live"}')
    (base / '_routes.json').write_text(json.dumps({'version': 1, 'include': ['/*'], 'exclude': []}))
    (stage / 'research').mkdir(parents=True)
    (stage / 'research/index.html').write_text('<html>research</html>')
    with pytest.raises(ValueError, match='broad Pages Worker route'):
        prepare(base, stage, output)


def test_finalized_identity_matches_the_pages_tree(tmp_path):
    output = tmp_path / 'output'
    main = output / 'pages'
    main.mkdir(parents=True)
    (main / 'release.json').write_text('{"deployment_branch":"master"}')
    (output / 'review.json').write_text('{"release_identity_requires_refresh":true}')
    body = '<html data-release-id="old"><head><meta name="gids-source-commit" content="old"><meta name="gids-deployment-branch" content="master"></head></html>'
    for name in ('index.html', 'fr/index.html', 'zh/index.html'):
        path = main / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    identity = finalize_identity(output, release_id='new', source_commit='a' * 40,
                                 source_branch='development', built_at='2026-11-12T00:00:00Z')
    assert identity['build_digest'] == _built_site_digest(main)
    assert 'data-release-id="new"' in (main / 'index.html').read_text()
    assert not json.loads((output / 'review.json').read_text())['release_identity_requires_refresh']
