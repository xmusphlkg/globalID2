"""A title cache cannot carry editorial state across export snapshots."""
import hashlib
import json

import pytest

from src.generation.literature_title_cache import LiteratureTitleCache, title_cache_identity
from src.generation.site_data_literature import _prepare_title_disease_aliases, _title_estimated_literature_links


def identity(diseases=None, aliases=None, implementation=b'matcher-v1'):
    diseases = diseases or {'D001': {'name_en': 'Dengue', 'slug': 'dengue'}}
    aliases = aliases or {}
    return title_cache_identity(diseases=diseases, aliases=aliases, implementation=implementation,
                                prepared=_prepare_title_disease_aliases(diseases, aliases))


def forbidden():
    pytest.fail('A matching cache entry must not recompute')


def test_hits_include_negative_results_and_do_not_cache_caller_mutations(tmp_path):
    path = tmp_path / 'titles.json'
    cache = LiteratureTitleCache(path, identity())
    returned = cache.links('a', 'Dengue incidence', lambda: [{'disease_id': 'D001'}])
    returned[0]['source'] = 'stored_relation'
    cache.links('b', 'Unrelated title', lambda: [])
    cache.save()
    warm = LiteratureTitleCache(path, identity())
    assert warm.links('a', 'Dengue incidence', forbidden) == [{'disease_id': 'D001'}]
    assert warm.links('b', 'Unrelated title', forbidden) == []
    assert warm.hits == 2 and warm.misses == 0


def test_title_id_and_identity_changes_invalidate(tmp_path):
    path = tmp_path / 'titles.json'
    cache = LiteratureTitleCache(path, identity())
    cache.links('a', 'Dengue', lambda: [{'disease_id': 'D001'}])
    cache.save()
    warm = LiteratureTitleCache(path, identity())
    assert warm.links('a', 'No dengue', lambda: []) == []
    assert warm.links('different-id', 'Dengue', lambda: []) == []
    assert warm.misses == 2
    identities = [identity(aliases={'D001': ['breakbone fever']}),
                  identity(diseases={'D001': {'name_en': 'Dengue', 'name_zh': '登革热', 'slug': 'dengue'}}),
                  identity(diseases={'D001': {'name_en': 'Dengue', 'slug': 'new-slug'}}),
                  identity(implementation=b'matcher-v2')]
    for changed in identities:
        assert changed != identity()
        invalidated = LiteratureTitleCache(path, changed)
        assert invalidated.links('a', 'Dengue', lambda: []) == []
        assert invalidated.misses == 1


def test_current_snapshot_prunes_deleted_retracted_and_policy_ineligible_rows(tmp_path):
    path = tmp_path / 'titles.json'
    old = LiteratureTitleCache(path, identity())
    for article_id in ('live', 'deleted', 'retracted', 'newly-full-detail'):
        old.links(article_id, 'Dengue', lambda: [])
    old.save()
    current = LiteratureTitleCache(path, identity())
    current.links('live', 'Dengue', forbidden)
    current.save()
    assert len(json.loads(path.read_text())['payload']['entries']) == 1
    after = LiteratureTitleCache(path, identity())
    assert after.links('deleted', 'Dengue', lambda: [{'new': True}]) == [{'new': True}]
    empty = LiteratureTitleCache(path, identity())
    empty.save()
    assert json.loads(path.read_text())['payload']['entries'] == {}


@pytest.mark.parametrize('damage', ['json', 'checksum', 'schema'])
def test_corrupt_cache_is_a_miss(tmp_path, damage):
    path = tmp_path / 'titles.json'
    cache = LiteratureTitleCache(path, identity())
    cache.links('a', 'Dengue', lambda: [])
    cache.save()
    if damage == 'json':
        path.write_text('{broken')
    else:
        data = json.loads(path.read_text())
        if damage == 'checksum':
            data['payload']['entries'] = {}
        else:
            data['payload']['schema'] = 999
            encoded = json.dumps(data['payload'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            data['sha256'] = hashlib.sha256(encoded).hexdigest()
        path.write_text(json.dumps(data))
    cold = LiteratureTitleCache(path, identity())
    assert cold.links('a', 'Dengue', lambda: [{'fresh': True}]) == [{'fresh': True}]
    assert cold.misses == 1


def test_failed_atomic_replace_preserves_previous_cache_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'titles.json'
    cache = LiteratureTitleCache(path, identity())
    cache.links('a', 'Dengue', lambda: [])
    cache.save()
    original = path.read_bytes()
    replacement = LiteratureTitleCache(path, identity())
    replacement.links('new', 'Dengue', lambda: [])

    def fail_replace(*args):
        raise OSError('disk unavailable')

    monkeypatch.setattr('src.generation.literature_title_cache.os.replace', fail_replace)
    replacement.save()
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_prepared_alias_order_is_canonical():
    diseases = {'D001': {'name_en': 'Dengue', 'name_zh': '登革热'}}
    aliases = {'D001': ['breakbone fever', 'DENGUE', 'dengue']}
    prepared = _prepare_title_disease_aliases(diseases, aliases)
    assert [raw for _, raw, _, _ in prepared] == sorted({
        'Dengue', '登革热', 'breakbone fever', 'DENGUE', 'dengue',
    })


def test_cold_and_warm_results_equal_uncached_real_matcher(tmp_path):
    diseases = {'D001': {'name_en': 'Dengue', 'name_zh': '登革热', 'slug': 'dengue'},
                'D002': {'name_en': 'SARS', 'slug': 'sars'},
                'D003': {'name_en': 'COVID-19', 'slug': 'covid'}}
    aliases = {'D003': ['SARS-CoV-2']}
    prepared = _prepare_title_disease_aliases(diseases, aliases)
    titles = ['登革热流行趋势', 'Without dengue infection', 'Dengue-like syndrome',
              'SARS-CoV-2 and SARS', 'DENGUÉ infection', 'Unrelated title']
    expected = [_title_estimated_literature_links(title, diseases, aliases, prepared) for title in titles]
    path = tmp_path / 'titles.json'
    cold = LiteratureTitleCache(path, identity(diseases, aliases))
    for index, title in enumerate(titles):
        assert cold.links(str(index), title, lambda: _title_estimated_literature_links(title, diseases, aliases, prepared)) == expected[index]
    cold.save()
    warm = LiteratureTitleCache(path, identity(diseases, aliases))
    assert [warm.links(str(i), title, forbidden) for i, title in enumerate(titles)] == expected
    assert warm.hits == len(titles)
