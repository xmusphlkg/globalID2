import hashlib
import json
from pathlib import Path

import pytest

from src.services.data_release.literature_context import capture_literature_context


def object_entry(value):
    body = json.dumps(value).encode()
    digest = hashlib.sha256(body).hexdigest()
    return {'url': f'objects/{digest}.json', 'sha256': digest, 'bytes': len(body)}, body


def test_context_uses_published_versions_and_reuses_coverage_without_rereading(tmp_path):
    disease, disease_body = object_entry({'series': [{'cc': 'US'}, {'cc': 'FR'}, {'cc': 'US'}]})
    situation, situation_body = object_entry({'as_of': '2026-11-10', 'generated_at': '2026-11-10T00:00:00Z',
        'outbreaks': [{'outbreak_id': 'event-1', 'disease_id': 'D1', 'disease_name_en': 'Example',
                       'last_published': '2026-11-09', 'country_codes': ['US'], 'status': 'active'},
                      {'outbreak_id': 'old', 'last_published': '2020-01-01'}],
        'points': [{'outbreak_id': 'event-1', 'country_code': 'US', 'country_name_en': 'United States',
                    'published_date': '2026-11-09', 'source_id': 'source', 'alert_id': 'a'}]})
    manifests = {
        'site-data/surveillance/current.json': {'schema_version': 1, 'release_id': 'data-1', 'files': {'diseases/d1.json': disease}},
        'site-data/situation/current.json': {'schema_version': 1, 'release_id': 'situation-1', 'files': {'map.json': situation}},
    }
    local = tmp_path / 'exports/surveillance-data' / disease['url']
    local.parent.mkdir(parents=True)
    local.write_bytes(disease_body)
    class Store:
        reads = []
        def get_object(self, key):
            self.reads.append(key)
            if key in manifests:
                return json.dumps(manifests[key]).encode()
            if key == 'site-data/situation/' + situation['url']:
                return situation_body
            raise AssertionError(f'unexpected remote read: {key}')
    store = Store()
    args = dict(client=store, root=tmp_path, cache=tmp_path / 'cache', diseases={'D1': {'disease_id': 'D1'}})
    result = capture_literature_context(**args)
    assert result['surveillance_coverage'] == {'D1': ['FR', 'US']}
    assert result['publication_inputs'] == {'surveillance': 'data-1', 'situation': 'situation-1'}
    assert [item['id'] for item in result['situation']['emerging']] == ['event-1']
    assert result['situation']['emerging'][0]['geographies'] == [{'code': 'US', 'name': 'United States'}]
    local.write_bytes(b'do not reread')
    assert capture_literature_context(**args)['surveillance_coverage'] == {'D1': ['FR', 'US']}


def test_context_fails_when_published_pointer_is_missing(tmp_path):
    class Empty:
        def get_object(self, _key): return None
    with pytest.raises(ValueError, match='pointer is unavailable'):
        capture_literature_context(client=Empty(), root=tmp_path, cache=tmp_path / 'cache', diseases={})
