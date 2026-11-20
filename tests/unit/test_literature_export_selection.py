import hashlib
import json
from types import SimpleNamespace

import pytest

from src.generation.literature_projection_state import canonical_bytes
from src.generation.literature_publication import freeze_literature_json
from src.literature.publication_changes import build_literature_change_set
from src.services.data_release.literature_export_selection import incremental_export_arguments
from src.services.data_release.papers_r2 import R2PapersConfig
from src.services.data_release.surveillance_publish import capture_surveillance_baseline


@pytest.fixture
def published_baseline(tmp_path):
    task_id = '15dc0691-e834-4fe8-9c86-60c199cbe800'
    previous = tmp_path / 'exports/research-runs' / task_id / 'snapshot'
    source = previous / 'public/research'
    source.mkdir(parents=True)
    (source / 'index.json').write_text('{"articles":[]}')
    manifest = freeze_literature_json(source, previous.parent / 'json',
        release_id=f'literature-{task_id}', generated_at='t')
    manifest['papers'] = {'papers_generation_sha256': 'a' * 64}
    receipt = tmp_path / 'exports/research-current-receipt.json'
    receipt.write_bytes(canonical_bytes({'schema': 1, 'task_uuid': task_id,
        'release_id': manifest['release_id'], 'papers_generation_sha256': 'a' * 64}))
    run = tmp_path / 'exports/research-runs/new'
    config = R2PapersConfig('account', 'papers', 'id', 'secret')
    capture_surveillance_baseline(run, config, prefix='site-data/research-publication',
        client=SimpleNamespace(get_object=lambda _key: canonical_bytes(manifest)))
    return SimpleNamespace(root=tmp_path, run=run, previous=previous, config=config,
                           manifest=manifest, receipt=receipt)


def test_precise_selection_uses_exact_published_baseline_and_freezes_retry(published_baseline):
    p = published_baseline
    arguments = dict(root=p.root, run=p.run, config=p.config, dirty_ids=[9, 7],
        change_sets=[build_literature_change_set(['b', 'a']), build_literature_change_set(['a'])])
    result = incremental_export_arguments(**arguments)
    expected_hash = hashlib.sha256(canonical_bytes(p.manifest)).hexdigest()
    assert result == ['--previous-snapshot', str(p.previous), '--article-ids-json',
        str(p.run / 'selected-article-ids.json'), '--expected-previous-manifest-sha256', expected_hash]
    assert json.loads((p.run / 'selected-article-ids.json').read_bytes()) == ['a', 'b']
    assert json.loads((p.run / 'incremental-selection.json').read_bytes()) == {
        'schema': 1, 'dirty_ids': [7, 9], 'article_ids': ['a', 'b'], 'baseline_sha256': expected_hash}
    assert incremental_export_arguments(**arguments) == result
    with pytest.raises(ValueError, match='Frozen literature article selection changed'):
        incremental_export_arguments(**{**arguments, 'change_sets': [build_literature_change_set(['c'])]})


@pytest.mark.parametrize('incompatible', ['coarse', 'stale_release', 'stale_generation'])
def test_coarse_or_stale_selection_uses_full_export(published_baseline, incompatible):
    p = published_baseline
    changes = [build_literature_change_set(['a'])]
    if incompatible == 'coarse':
        changes.append(None)
    else:
        receipt = json.loads(p.receipt.read_bytes())
        if incompatible == 'stale_release':
            receipt['task_uuid'] = '7c9f9aaa-d0af-4b8c-a173-3d0aa8ff2e43'
            receipt['release_id'] = 'literature-' + receipt['task_uuid']
        else:
            receipt['papers_generation_sha256'] = 'b' * 64
        p.receipt.write_bytes(canonical_bytes(receipt))
    assert incremental_export_arguments(root=p.root, run=p.run, config=p.config,
        dirty_ids=[7], change_sets=changes) == []
    assert not (p.run / 'selected-article-ids.json').exists()
    assert not (p.run / 'incremental-selection.json').exists()
