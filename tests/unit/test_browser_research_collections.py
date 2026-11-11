from copy import deepcopy
import json
from pathlib import Path
import re

from src.generation.browser_research_collections import (
    PUBLIC_ARTICLE_FIELDS, compact_public_research_article, publish_browser_research_collections,
)


def article(article_id='one', disease='D038', country='US', topic='One Health'):
    return {'article_id': article_id, 'title': f'Title {article_id}', 'slug': article_id,
            'editorial_status': 'published', 'peer_review_status': 'peer_reviewed', 'indexable': True,
            'diseases': [{'disease_id': disease, 'name_en': 'Influenza'}],
            'countries': [{'code': country}], 'topics': [{'name': topic}],
            'summary': {'en': {'main_findings': 'Current findings', 'public_health_relevance': 'Relevant'}},
            'why_it_matters_en': 'Why this matters', 'published_at': '2026-11-11',
            'knowledge_graph': {'huge': [1, 2]}, 'abstract_text': 'private',
            'source_payload': {'private': True}, 'related_articles': ['unused']}


def load(root, kind='diseases', scope='d038'):
    return json.loads((root / kind / f'{scope}.json').read_text())


def test_full_membership_updates_delete_and_preserve_unrelated_bytes(tmp_path):
    one, unrelated = article(), article('unrelated', 'D999', 'CN', 'Diagnostics')
    first = publish_browser_research_collections({'articles': [one, unrelated]}, tmp_path)
    assert first['scopes'] == 6 and first['written'] == 6
    stable = tmp_path / 'diseases/d999.json'
    old_bytes, old_mtime = stable.read_bytes(), stable.stat().st_mtime_ns
    two = article('two')
    one['summary']['en']['main_findings'] = 'Corrected findings'
    updated = publish_browser_research_collections({'articles': [one, two, unrelated]}, tmp_path)
    assert load(tmp_path)['count'] == 2
    assert load(tmp_path)['articles'][0]['summary']['en']['main_findings'] == 'Corrected findings'
    assert updated['written'] == 3 and updated['unchanged'] == 3
    assert stable.read_bytes() == old_bytes and stable.stat().st_mtime_ns == old_mtime
    # Relation deletion removes old scopes and creates their replacements.
    two['diseases'] = [{'disease_id': 'D002'}]
    publish_browser_research_collections({'articles': [two, unrelated]}, tmp_path)
    assert not (tmp_path / 'diseases/d038.json').exists()
    assert load(tmp_path, scope='d002')['count'] == 1
    result = publish_browser_research_collections({'articles': []}, tmp_path)
    assert result['removed'] == 6
    assert not list(tmp_path.rglob('*.json'))


def test_only_canonical_published_reviewed_evidence_and_no_private_heavy_fields(tmp_path):
    good = article()
    variants = []
    for key, value in [('editorial_status', 'review'), ('peer_review_status', 'preprint'),
                       ('detail_available', False),
                       ('integrity_status', 'retracted'), ('integrity_status', 'expression_of_concern')]:
        bad = deepcopy(good)
        bad[key] = value
        variants.append(bad)
    publish_browser_research_collections({'articles': [good, *variants],
                                         'preprints': [article('preprint')],
                                         'bibliographic_articles': [article('bib')],
                                         'title_estimated_related_records': [article('title-estimate')]}, tmp_path)
    result = load(tmp_path)
    assert result['count'] == 1
    public = result['articles'][0]
    assert public['summary'] == good['summary']
    assert public['why_it_matters_en'] == good['why_it_matters_en']
    assert public['diseases'] == good['diseases']
    assert not {'knowledge_graph', 'abstract_text', 'source_payload', 'related_articles'} & public.keys()


def test_compact_projection_matches_browser_field_contract():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'astro-site/src/lib/research-catalogue.ts').read_text()
    browser_fields = set(re.findall(r'^  ([a-z_]+):', source, re.MULTILINE))
    assert set(PUBLIC_ARTICLE_FIELDS) == browser_fields
    full = {key: f'value-{key}' for key in PUBLIC_ARTICLE_FIELDS}
    full.update(knowledge_graph={'private': 1}, abstract_text='private')
    assert set(compact_public_research_article(full)) == browser_fields
    assert compact_public_research_article({})['summary'] == {}
    assert compact_public_research_article({})['authors'] == []


def test_duplicate_scope_relations_do_not_duplicate_records(tmp_path):
    item = article()
    item['diseases'] *= 2
    publish_browser_research_collections({'articles': [item]}, tmp_path)
    assert load(tmp_path)['count'] == 1


def test_canonical_legacy_historical_seed_without_editorial_field_is_retained(tmp_path):
    historical = article('historical')
    historical.pop('editorial_status')
    historical.pop('peer_review_status')
    historical['source_kind'] = 'historical_seed'
    publish_browser_research_collections({'articles': [historical]}, tmp_path)
    assert load(tmp_path)['count'] == 1
    assert load(tmp_path)['articles'][0]['source_kind'] == 'historical_seed'
    for key, value in [('editorial_status', 'review'), ('integrity_status', 'retracted'),
                       ('peer_review_status', 'preprint')]:
        excluded = {**historical, key: value}
        publish_browser_research_collections({'articles': [excluded]}, tmp_path)
        assert not (tmp_path / 'diseases/d038.json').exists()


def test_public_noindex_article_remains_searchable(tmp_path):
    item = article('public-noindex')
    item.update(indexable=False, detail_available=True)
    publish_browser_research_collections({'articles': [item]}, tmp_path)
    for kind, scope in [('diseases', 'd038'), ('countries', 'us'), ('topics', 'one-health')]:
        assert load(tmp_path, kind, scope)['articles'][0]['article_id'] == 'public-noindex'
