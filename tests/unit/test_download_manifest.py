import copy
import json

import pytest

from src.generation.direct_download.manifest import expand_manifest, manifest_bytes
from src.generation.direct_download_files import build_direct_download_files
from tests.unit.test_direct_download_files import _two_country_context


def test_compact_cells_round_trip_without_duplicate_disease_metadata(tmp_path):
    manifest = build_direct_download_files(_two_country_context(), tmp_path, workers=1,
        download_url_base='https://raw.githubusercontent.com/example/data/main')
    manifest.pop('generation')
    before = copy.deepcopy(manifest)
    packed = json.loads(manifest_bytes(manifest))
    assert manifest == before
    assert packed['cell_metadata'] == 'country_parts_v1'
    assert all(set(part) == {'country_code', 'id'} for part in packed['diseases'][0]['parts'])
    assert set(packed['countries'][0]['parts'][0]['files']['csv']) == {'bytes', 'sha256'}
    expanded = expand_manifest(packed)
    assert expanded == manifest
    expanded['diseases'][0]['parts'][0]['files']['csv']['bytes'] = 1
    assert expanded['countries'][0]['parts'][0]['files']['csv']['bytes'] != 1


def test_manifest_size_limit_is_checked_before_writing(monkeypatch):
    monkeypatch.setattr('src.generation.direct_download.manifest.MAX_MANIFEST_BYTES', 10)
    with pytest.raises(ValueError, match='100 MiB'):
        manifest_bytes({'schema_version': 4, 'countries': []})


def test_legacy_full_manifests_are_unchanged():
    document = {'schema_version': 4, 'countries': [], 'diseases': []}
    assert expand_manifest(json.loads(manifest_bytes(document))) == document
