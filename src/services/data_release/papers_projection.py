"""Opt-in structured Papers v3 candidates, using the existing research catalogue.

Only already-public export projections are accepted. No DB serialization, full
Astro render, independent pointer, or remote operation is performed here.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from .papers_content import PapersContentCandidate, _hash, _json, catalogue_from_entries
from .papers_immutable import _source

PREFIX = 'paper-template-v3'
LOCALES = ('en', 'zh', 'fr')
# Preserve every already-public route in the frozen catalogue. One historical
# article uses a 201-character slug; the projection must not silently drop it
# during a complete migration merely because the old validator capped routes
# at 200 characters.
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,239}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
FIELDS = ('title', 'doi', 'pmid', 'pmcid', 'openalex_id', 'journal', 'publisher', 'authors',
          'article_type', 'study_type', 'published_at', 'updated_at', 'publication_date_label_en',
          'publication_date_label_zh', 'publication_date_label_fr', 'open_access_status',
          'peer_review_status', 'editorial_status', 'integrity_status', 'indexable',
          'why_it_matters_en', 'why_it_matters_zh', 'why_it_matters_fr', 'discovery_score',
          'open_access_url', 'license_url', 'historical_baseline', 'source_kind')
SUMMARY_FIELDS = ('research_question', 'study_design', 'population_setting', 'main_findings',
                  'public_health_relevance', 'limitations', 'gids_interpretation')


def project_article(article: dict) -> dict:
    # A small historical seed predates the editorial_status field. These rows
    # are already public catalogue entries and carry the stronger historical
    # baseline/indexable markers; treat that explicit public provenance as the
    # publication decision instead of dropping otherwise public routes during
    # the full v3 migration.
    historical_public = (
        article.get('editorial_status') is None
        and article.get('historical_baseline') is True
        and article.get('indexable') is True
        and isinstance(article.get('article_id'), str)
        and article['article_id'].startswith('lit_hist_')
    ) if isinstance(article, dict) else False
    if (not isinstance(article, dict) or not isinstance(article.get('slug'), str)
            or not SLUG.fullmatch(article['slug']) or not isinstance(article.get('article_id'), str)
            or not article['article_id']
            or (article.get('editorial_status') != 'published' and not historical_public)
            or not isinstance(article.get('title'), str) or not article['title'].strip()):
        raise ValueError('Expected an already-public article projection')
    content = {key: article[key] for key in FIELDS if key in article}
    for key, value in content.items():
        if key == 'authors':
            if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
                raise ValueError('Authors must be public display names')
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError('Public paper display fields must be scalar')
    summaries = article.get('summary', {})
    if not isinstance(summaries, dict):
        raise ValueError('Invalid public summaries')
    content['summary'] = {}
    for locale in LOCALES:
        summary = summaries.get(locale, {})
        if not isinstance(summary, dict):
            raise ValueError('Invalid localized summary')
        localized = {key: summary[key] for key in SUMMARY_FIELDS if isinstance(summary.get(key), str)}
        provenance = summary.get('provenance') or {}
        localized['provenance'] = {'editorially_approved': provenance.get('editorially_approved') is True,
                                   'quality_checked': provenance.get('quality_score') is not None}
        content['summary'][locale] = localized
    source_urls = article.get('source_urls') or {}
    content['source_urls'] = {key: source_urls[key] for key in ('doi', 'pubmed', 'pmc', 'publisher', 'openalex')
                              if isinstance(source_urls.get(key), str)}
    content['integrity_events'] = [{key: value[key] for key in ('event_type', 'effective_at', 'recorded_at', 'source', 'source_url')
                                    if isinstance(value.get(key), str)}
                                   for value in article.get('integrity_events', []) if isinstance(value, dict)]
    content['related_articles'] = [{key: value[key] for key in ('slug', 'title') if isinstance(value.get(key), str)}
                                   for value in article.get('related_articles', []) if isinstance(value, dict)]
    def public_rows(name, fields):
        return [{key: row[key] for key in fields if key in row and isinstance(row[key], (str, int, float, bool, list))}
                for row in article.get(name, []) if isinstance(row, dict)]
    for name in ('diseases', 'countries', 'topics', 'pathogens', 'populations'):
        content[name] = public_rows(name, ('disease_id', 'slug', 'code', 'id', 'name', 'name_en', 'name_zh', 'name_fr'))
    content['version_relations'] = public_rows('version_relations', ('preprint_doi', 'peer_reviewed_doi', 'preprint_url', 'peer_reviewed_url'))
    content['related_surveillance'] = public_rows('related_surveillance', ('url', 'disease_name_en', 'disease_name_zh', 'disease_name_fr', 'country_code', 'country_name_en', 'country_name_zh', 'country_name_fr'))
    content['related_signals'] = []
    for row in article.get('related_signals', []):
        if not isinstance(row, dict) or row.get('visibility') != 'public':
            continue
        signal = {key: row[key] for key in ('signal_id', 'disease_id', 'situation_url', 'data_through',
                  'relation_level', 'disease_name_en', 'disease_name_zh', 'disease_name_fr') if isinstance(row.get(key), str)}
        signal['geographies'] = [{key: place[key] for key in ('code', 'name_en', 'name_zh', 'name_fr') if isinstance(place.get(key), str)}
                                 for place in row.get('geographies', []) if isinstance(place, dict)]
        window = row.get('window') or {}
        change = window.get('change_pct') if isinstance(window, dict) else None
        signal['window'] = {'change_pct': change if isinstance(change, (int, float)) and not isinstance(change, bool) else None}
        content['related_signals'].append(signal)
    graph = article.get('knowledge_graph') or {}
    content['knowledge_graph'] = {name: [{key: row[key] for key in fields if isinstance(row.get(key), str)}
        for row in graph.get(name, []) if isinstance(row, dict)] for name, fields in
        (('nodes', ('id', 'type', 'label', 'name_zh', 'name_fr', 'code', 'url', 'slug')), ('edges', ('id', 'relation', 'target')))}
    content['related_articles'] = public_rows('related_articles', ('slug', 'title', 'journal', 'study_type', 'reasons_en', 'reasons_zh', 'reasons_fr'))
    result = {'schema': 3, 'slug': article['slug'], 'article_id': article['article_id'], 'content': content}
    if len(_json(result)) > 2 * 1024 * 1024:
        raise ValueError('Public paper projection exceeds size limit')
    return result


def _payload(root: Path, group: str, value: dict) -> dict:
    body = _json(value)
    digest = _hash(body)
    relative = f'{PREFIX}/{group}/{digest}.json'
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise ValueError('Immutable projection object changed')
    if not path.exists():
        path.write_bytes(body)
    return {'path': relative, 'key': relative, 'sha256': digest, 'bytes': len(body),
            'content_type': 'application/json; charset=utf-8'}


def _read_object(candidate: PapersContentCandidate, key: str) -> dict:
    descriptor = next((value for value in candidate.template_objects if value['key'] == key), None)
    if not descriptor or candidate.template_root is None:
        raise ValueError('Missing frozen paper dependency')
    raw = _source(candidate.template_root, descriptor['path']).read_bytes()
    if len(raw) != descriptor['bytes'] or _hash(raw) != descriptor['sha256']:
        raise ValueError('Frozen paper dependency changed')
    return json.loads(raw)


def _template(base: PapersContentCandidate, locale: str, entry: dict) -> dict:
    descriptor = entry['paper_template']
    if descriptor.get('schema') == 3:
        return _read_object(base, f'{PREFIX}/templates/{descriptor["template_sha256"]}.json')
    old = _read_object(base, f'paper-template-v2/templates/{descriptor["template_sha256"]}.json')
    record = _read_object(base, entry['key'])
    # One verified locale shell is migrated once. Article records are generated
    # directly from JSON thereafter; no per-article HTML splitting takes place.
    chunks, inserted = [], False
    for chunk in old['chunks']:
        if isinstance(chunk, dict) and chunk.get('field') == 'head':
            if not inserted:
                chunks.append({'field': 'head', 'index': 0})
                inserted = True
        else:
            chunks.append(chunk)
    if not inserted:
        raise ValueError('Locale shell has no SEO slot')
    scope = sorted(set(re.findall(r'data-astro-cid-[a-z0-9]{4,32}', record['locales'][locale]['main'])))
    return {'schema': 3, 'content_schema': 3, 'renderer': 'gids-evidence-v1', 'locale': locale,
            'shell': {'schema': 1, 'chunks': chunks}, 'scope_attributes': scope}


def assert_v3_candidate(candidate: PapersContentCandidate) -> None:
    """Reject a catalogue that would publish or resume template v2 articles."""
    articles = [entry for entry in candidate.entries if 'paper_template' in entry]
    if not articles:
        raise ValueError('Papers publication has no projected articles')
    for entry in articles:
        descriptor = entry.get('paper_template') or {}
        if (descriptor.get('schema') != 3 or entry.get('integrity') != 'independent-v3'
                or 'sha256' in entry or 'bytes' in entry
                or not str(entry.get('key', '')).startswith(f'{PREFIX}/records/')):
            raise ValueError('Papers publication must be projection v3')
    if any(str(item.get('key', '')).startswith('paper-template-v2/')
           for item in (*candidate.entries, *candidate.template_objects)):
        raise ValueError('Papers publication still contains template v2 objects')


def prepare_projection_candidate(base: PapersContentCandidate, *, articles: list[dict], directory: Path,
                                 release_id: str, migration: bool = False,
                                 templates: dict[str, dict] | None = None) -> tuple[PapersContentCandidate, dict]:
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Projection candidate directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    by_slug: dict[str, list[dict]] = {}
    for entry in base.entries:
        descriptor = entry.get('paper_template')
        if isinstance(descriptor, dict):
            by_slug.setdefault(descriptor['slug'], []).append(entry)
    records = {article['slug']: project_article(article) for article in articles}
    if len(records) != len(articles):
        raise ValueError('Duplicate projected article slug')
    if not records and templates is None:
        raise ValueError('No paper projections or templates changed')
    if any(slug not in by_slug for slug in records):
        raise ValueError('New article routes require the regular research build')
    if migration and set(records) != set(by_slug):
        raise ValueError('Initial migration must cover the complete article catalogue')
    if not migration:
        for slug, record in records.items():
            old_entry = by_slug[slug][0]
            if old_entry['paper_template'].get('schema') != 3:
                raise ValueError('Body-only publication requires a migrated v3 base')
            old = _read_object(base, old_entry['key'])
            # Changes to directory titles, links, routing, sources or integrity
            # must use the regular projection/build workflow, not stale HTML.
            old_scope = {key: value for key, value in old['content'].items() if key not in {'summary', 'updated_at'}}
            new_scope = {key: value for key, value in record['content'].items() if key not in {'summary', 'updated_at'}}
            if old_scope != new_scope or old['article_id'] != record['article_id']:
                raise ValueError('Non-body change requires directory/SEO projection rebuild')
    payloads: dict[str, dict] = {}
    # Keep all old immutable objects available for unchanged entries and open
    # pages. Hardlinks reuse bytes without rendering or interpreting records.
    for descriptor in base.template_objects:
        # v2 shells are read from the base while building the v3 template.
        # They are not part of the published candidate.
        if str(descriptor.get('key', '')).startswith('paper-template-v2/'):
            continue
        source = _source(base.template_root, descriptor['path'])
        target = directory / descriptor['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        payloads[descriptor['key']] = descriptor
    asset_items = [{key: item[key] for key in ('path', 'key', 'sha256', 'bytes')}
                   for item in base.entries if item['path'].startswith('_astro/')]
    asset = _payload(directory, 'assets', {'schema': 1, 'items': sorted(asset_items, key=lambda item: item['path'])})
    payloads[asset['key']] = asset
    locale_templates = {}
    for locale in LOCALES:
        reference = next((entry for entries in by_slug.values() for entry in entries if entry['paper_template']['locale'] == locale), None)
        if reference is None:
            raise ValueError('Migration requires all three locale shells')
        value = templates[locale] if templates is not None else _template(base, locale, reference)
        if value.get('schema') != 3 or value.get('locale') != locale or value.get('renderer') != 'gids-evidence-v1':
            raise ValueError('Invalid projected locale template')
        # Freeze every local compiled reference in the assets manifest.
        references = set(re.findall(r'/_astro/([A-Za-z0-9._/-]+)', _json(value).decode()))
        available = {item['path'].removeprefix('_astro/') for item in asset_items}
        if not references.issubset(available):
            raise ValueError('Template references an asset outside the retained catalogue')
        spec = _payload(directory, 'templates', value)
        payloads[spec['key']] = spec
        locale_templates[locale] = spec
    entries = []
    generated = {}
    for slug, record in records.items():
        spec = _payload(directory, 'records', record)
        payloads[spec['key']] = spec
        generated[slug] = spec
    for entry in base.entries:
        descriptor = entry.get('paper_template')
        if not isinstance(descriptor, dict) or descriptor['slug'] not in records and templates is None:
            entries.append(entry)
            continue
        if descriptor.get('schema') != 3 and descriptor['slug'] not in records:
            raise ValueError('Template update requires a fully migrated base')
        spec = generated.get(descriptor['slug'])
        record_sha = spec['sha256'] if spec else descriptor['record_sha256']
        # Explicitly omit legacy HTML bytes/SHA. They must never be mistaken for
        # a full-HTML provenance check under the independent-hash protocol.
        entries.append({'path': entry['path'], 'logical_key': entry['logical_key'],
                        'key': f'{PREFIX}/records/{record_sha}.json', 'content_type': 'text/html; charset=utf-8',
                        'integrity': 'independent-v3', 'paper_template': {'schema': 3, 'slug': descriptor['slug'],
                        'locale': descriptor['locale'], 'record_sha256': record_sha,
                        'template_sha256': locale_templates[descriptor['locale']]['sha256'], 'assets_sha256': asset['sha256']}})
    candidate = catalogue_from_entries(root=base.root, origin=base.origin, release_id=release_id, entries=entries,
                                        template_root=directory, template_objects=tuple(payloads.values()))
    changed_entries = [entry['logical_key'] for entry in candidate.entries
                       if entry.get('paper_template', {}).get('slug') in records]
    candidate = replace(candidate, verification_keys=tuple(changed_entries))
    receipt = {'schema': 1, 'protocol': 'paper-template-v3', 'base_generation': base.generation,
               'generation': candidate.generation, 'content_record_count': len(generated), 'content_render_count': 0,
               'html_assembly_count': 0, 'template_compile_count': 0, 'astro_build_count': 0,
               'changed_slugs': sorted(records), 'migration': migration,
               'directory_projection_policy': 'regular_build_required_for_non_body_changes'}
    save_projection_candidate(candidate, directory / 'candidate.json', base_generation=base.generation)
    (directory / 'projection-receipt.json').write_bytes(_json(receipt))
    return candidate, receipt


def save_projection_candidate(candidate: PapersContentCandidate, path: Path, *, base_generation: str) -> None:
    Path(path).write_bytes(_json({'schema': 1, 'protocol': 'paper-template-v3', 'root': str(candidate.root),
        'origin': candidate.origin, 'release_id': candidate.release_id, 'generation': candidate.generation, 'base_generation': base_generation,
        'entries': candidate.entries, 'verification_keys': candidate.verification_keys, 'template_root': str(candidate.template_root),
        'template_objects': candidate.template_objects, 'manifest_sha256': _hash(candidate.manifest)}))


def load_projection_candidate(path: Path) -> PapersContentCandidate:
    data = json.loads(Path(path).read_bytes())
    if data.get('schema') != 1 or data.get('protocol') != 'paper-template-v3':
        raise ValueError('Invalid frozen projection candidate')
    candidate = catalogue_from_entries(root=Path(data['root']), origin=data['origin'], release_id=data['release_id'],
        entries=data['entries'], template_root=Path(data['template_root']), template_objects=tuple(data['template_objects']))
    if candidate.generation != data['generation'] or _hash(candidate.manifest) != data['manifest_sha256']:
        raise ValueError('Frozen projection candidate identity changed')
    return replace(candidate, verification_keys=tuple(data.get('verification_keys', [])))


def verify_projected_publication(candidate: PapersContentCandidate, *, opener=None) -> dict:
    """Verify live canonical canaries after the single research pointer commits.

    Failure intentionally leaves the committed candidate recoverable; it must
    not be reported as fully published or acknowledged before a successful retry.
    """
    from urllib.request import Request, build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    open_url = opener or build_opener(NoRedirect()).open
    selected = {}
    for entry in candidate.entries:
        descriptor = entry.get('paper_template', {})
        if descriptor.get('schema') == 3 and (not candidate.verification_keys or entry['logical_key'] in candidate.verification_keys):
            selected.setdefault(descriptor['locale'], entry)
    if set(selected) != set(LOCALES):
        raise ValueError('Projected publication has no complete locale canaries')
    checked = []
    for locale, entry in selected.items():
        descriptor = entry['paper_template']
        url = candidate.origin + '/' + entry['logical_key']
        # Cloudflare may reject urllib's implicit `Python-urllib` user agent
        # with a challenge even though the public route is healthy.  Use the
        # same explicit operator identity as the readiness probes so canary
        # verification measures the Worker response rather than bot filtering.
        request = Request(url, headers={'Cache-Control': 'no-cache',
                                        'Accept-Encoding': 'identity',
                                        'User-Agent': 'GlobalID-Papers-Canary/1'})
        with open_url(request, timeout=20) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError('Projected public canary was not served directly')
            expected = {'X-GIDS-Paper-Mode': 'projection-v3', 'X-GIDS-Research-Release': candidate.release_id,
                        'X-GIDS-Paper-Content-SHA256': descriptor['record_sha256'],
                        'X-GIDS-Paper-Template-SHA256': descriptor['template_sha256'],
                        'X-GIDS-Paper-Assets-SHA256': descriptor['assets_sha256']}
            if any(response.headers.get(key) != value for key, value in expected.items()):
                raise ValueError('Projected public canary version mismatch')
            raw = response.read(3 * 1024 * 1024 + 1)
            if len(raw) > 3 * 1024 * 1024 or f'data-research-article-sha256="{descriptor["record_sha256"]}"'.encode() not in raw:
                raise ValueError('Projected public canary body mismatch')
            checked.append({'locale': locale, 'url': url, 'html_sha256': _hash(raw)})
    return {'status': 'verified', 'generation': candidate.generation, 'canaries': checked}
