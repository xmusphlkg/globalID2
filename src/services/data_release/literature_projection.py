"""Freeze compatible v3 template dependencies and attest a complete snapshot."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import shutil

from src.generation.literature_publication import _atomic_write
from .papers_content import _hash, _json
from .papers_immutable import ImmutablePapersError, _source
from .papers_projection import HASH, LOCALES, SLUG, locale_shell_template, save_projection_candidate
from .site_research_baseline import _local_candidate


def freeze_projection_build_baseline(*, run: Path, previous: dict | None, origin: str) -> dict:
    """A missing/incompatible local cache requests the existing full build.

    Only a candidate matching the captured public generation and manifest may
    supply templates. All reused bytes are copied and hash-checked before the
    build so a later cleanup or a different publication cannot alter retries.
    """
    marker = run / 'projection-build-baseline.json'
    if marker.exists():
        return json.loads(marker.read_bytes())
    result = {'schema': 1, 'mode': 'full', 'reason': 'compatible_local_template_unavailable'}
    base = None
    try:
        if previous and previous.get('papers'):
            base = _local_candidate(run, previous, origin)
        if base is not None and base.template_root is not None:
            compatibility = _source(base.template_root, 'template-compatibility.json')
            data = json.loads(compatibility.read_bytes())
            if (data.get('schema') != 1 or data.get('generation') != base.generation
                    or not isinstance(data.get('template_fingerprint'), str)
                    or not HASH.fullmatch(data['template_fingerprint'])):
                base = None
    except (OSError, ValueError, KeyError, TypeError, ImmutablePapersError):
        base = None
    if base is not None:
        directory = run / 'template-baseline'
        if directory.is_symlink():
            raise ValueError('Invalid frozen template baseline directory')
        if directory.exists():
            shutil.rmtree(directory)
        root, objects = directory / 'resources', directory / 'objects'
        root.mkdir(parents=True)
        objects.mkdir()
        try:
            sources = [(item, base.template_root, objects) for item in base.template_objects]
            # Old collection/article HTML is not a dependency of the v3 shell.
            # Landing pages are retained for an intentionally empty catalogue.
            sources.extend((item, base.root, root) for item in base.entries
                if 'paper_template' not in item and (
                    not item['path'].lower().endswith(('.html', '.htm'))
                    or item['logical_key'] in {'research/', 'zh/research/', 'fr/research/'}))
            for item, source_root, target_root in sources:
                body = _source(source_root, item['path']).read_bytes()
                if _hash(body) != item['sha256'] or len(body) != item['bytes']:
                    raise ValueError('Frozen template baseline dependency changed')
                destination = target_root / item['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(body)
            frozen = replace(base, root=root, template_root=objects)
            save_projection_candidate(frozen, directory / 'candidate.json', base_generation=base.generation)
            result = {'schema': 1, 'template_fingerprint': data['template_fingerprint'],
                'candidate': str((directory / 'candidate.json').resolve()), 'generation': base.generation}
        except (OSError, ValueError, KeyError, TypeError, ImmutablePapersError):
            # A missing cache must trigger a complete build; it must never
            # authorize partially restored templates or resource dependencies.
            shutil.rmtree(directory)
    _atomic_write(marker, _json(result))
    return result


def save_template_compatibility(candidate, build: dict) -> None:
    fingerprint = build.get('template_fingerprint')
    if isinstance(fingerprint, str) and HASH.fullmatch(fingerprint):
        _atomic_write(candidate.template_root / 'template-compatibility.json', _json({
            'schema': 1, 'generation': candidate.generation, 'template_fingerprint': fingerprint,
        }))


def complete_snapshot_articles(snapshot: Path, build: dict) -> list[dict]:
    """Require matching route/index/input sets and exact article byte hashes."""
    if Path(build.get('snapshot', '')).resolve() != snapshot.resolve():
        raise ValueError('Research collection build belongs to a different snapshot')
    values = {}
    for relative, field in (
        ('article-inputs.json', 'article_inputs_sha256'),
        ('build/research/routes.json', 'routes_sha256'),
        ('build/research/index.json', 'research_index_sha256'),
        ('public/research/index.json', 'public_research_index_sha256'),
        ('public/research/catalogue.json', 'public_research_catalogue_sha256'),
    ):
        body = _source(snapshot, relative).read_bytes()
        if _hash(body) != build.get(field):
            raise ValueError('Research snapshot changed after collection rendering')
        values[field] = json.loads(body)
    inputs = values['article_inputs_sha256']
    routes = values['routes_sha256'].get('articles')
    index = values['research_index_sha256']
    if inputs.get('schema') != 1 or not isinstance(inputs.get('articles'), dict) or not isinstance(routes, list):
        raise ValueError('Incomplete article snapshot receipt')
    slugs = [route.get('slug') for route in routes if isinstance(route, dict)]
    index_slugs = [article.get('slug') for group in ('articles', 'preprints') for article in index.get(group, [])]
    if (len(slugs) != len(routes) or any(not isinstance(slug, str) or not SLUG.fullmatch(slug) for slug in slugs)
            or len(set(slugs)) != len(slugs) or len(set(index_slugs)) != len(index_slugs)
            or set(slugs) != set(inputs['articles']) or set(slugs) != set(index_slugs)):
        raise ValueError('Complete article routes, index and input receipts disagree')
    articles = []
    for slug in sorted(slugs):
        body = _source(snapshot, f'build/research/articles/{slug}.json').read_bytes()
        if _hash(body) != inputs['articles'][slug]:
            raise ValueError('Frozen public article changed after export')
        article = json.loads(body)
        if article.get('slug') != slug:
            raise ValueError('Frozen public article belongs to a different route')
        articles.append(article)
    return articles


def verify_snapshot_collections(stage: Path, build: dict) -> None:
    if (build.get('schema') != 1 or build.get('scope') != 'collections'
            or build.get('article_pages_rendered') != 0 or build.get('article_pages_restored') != 0
            or not isinstance(build.get('pages'), dict)):
        raise ValueError('Full snapshot requires a verified collection-only build')
    actual = {path.relative_to(stage).as_posix() for path in stage.rglob('*.html')}
    if actual != set(build['pages']):
        raise ValueError('Research collection page set changed after rendering')
    for relative, digest in build['pages'].items():
        if (re.fullmatch(r'(?:zh/|fr/)?research/articles/[^/]+/index\.html', relative)
                or _hash(_source(stage, relative).read_bytes()) != digest):
            raise ValueError('Research collection page changed or contains article HTML')
    if not {'research/index.html', 'zh/research/index.html', 'fr/research/index.html'}.issubset(actual):
        raise ValueError('Research collection build requires all three landing pages')


ARTICLE_PAGE = re.compile(r'(?:(?P<prefix>zh|fr)/)?research/articles/(?P<slug>[a-z0-9][a-z0-9-]{0,239})/index\.html\Z')


def verify_snapshot_shell(stage: Path, build: dict) -> str:
    """Accept one representative article page per locale and the collection build."""
    slug = build.get('shell_slug')
    if (build.get('schema') != 1 or build.get('scope') != 'shell'
            or build.get('article_pages_rendered') != 3 or build.get('article_pages_restored') != 0
            or not isinstance(build.get('pages'), dict) or not isinstance(slug, str) or not SLUG.fullmatch(slug)):
        raise ValueError('Shell update requires one rendered article in every locale')
    expected = {f'{prefix}research/articles/{slug}/index.html' for prefix in ('', 'zh/', 'fr/')}
    actual = {path.relative_to(stage).as_posix() for path in stage.rglob('*.html')}
    if actual != set(build['pages']):
        raise ValueError('Research shell page set changed after rendering')
    rendered = {relative for relative in actual if ARTICLE_PAGE.fullmatch(relative)}
    if rendered != expected:
        raise ValueError('Shell update must render exactly one article in every locale')
    for relative, digest in build['pages'].items():
        if _hash(_source(stage, relative).read_bytes()) != digest:
            raise ValueError('Research shell page changed after rendering')
    if not {'research/index.html', 'zh/research/index.html', 'fr/research/index.html'}.issubset(actual):
        raise ValueError('Shell update requires all three landing pages')
    return slug


def compile_locale_shells(stage: Path, slug: str) -> dict[str, dict]:
    """Factor the representative pages into v3 shells. Records stay in JSON."""
    from .papers_template import assemble_paper, factor_paper
    if not SLUG.fullmatch(slug):
        raise ValueError('Shell update requires one rendered article in every locale')
    templates = {}
    for locale, relative in (('en', f'research/articles/{slug}/index.html'),
                             ('zh', f'zh/research/articles/{slug}/index.html'),
                             ('fr', f'fr/research/articles/{slug}/index.html')):
        html = _source(stage, relative).read_text(encoding='utf-8')
        pair = factor_paper(html, slug)
        if assemble_paper(pair.template, pair.record) != html:
            raise ValueError(f'Paper shell round trip mismatch: {relative}')
        templates[locale] = locale_shell_template(locale, pair.template['chunks'], pair.record['main'])
    if set(templates) != set(LOCALES):
        raise ValueError('Shell update requires all three locale templates')
    return templates
