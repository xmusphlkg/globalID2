from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.services.data_release.literature_publish import PREFIX, publish_literature_candidate
from src.services.data_release.papers_r2 import R2S3Error
from src.services.data_release.surveillance_publish import capture_surveillance_baseline
from tests.unit.test_papers_content import make_candidate
from tests.unit.test_surveillance_publish import CONFIG, Store, generation


def test_failed_pointer_switch_is_retryable_and_only_then_records_reuse(tmp_path):
    config = replace(CONFIG, public_base_url="https://papers.example")
    run = tmp_path / "run"
    run.mkdir()
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    generation(run, release="candidate")
    papers = make_candidate(tmp_path / "papers", {"research/articles/a/index.html": "A"})
    receipt = tmp_path / "receipt.json"
    store.failure = "/current.json"
    with pytest.raises(R2S3Error):
        publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                     config=config, receipt=receipt, client=store)
    assert f"{PREFIX}/current.json" not in store.objects
    assert not receipt.exists()
    store.failure = None
    result = publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                          config=config, receipt=receipt, client=store)
    pointer = json.loads(store.objects[f"{PREFIX}/current.json"])
    assert pointer["papers"]["papers_generation_sha256"] == papers.generation
    assert result["release_id"] == pointer["release_id"] == "candidate"
    assert receipt.exists()
    assert store.writes[-1][0] == f"{PREFIX}/current.json"
    writes = list(store.writes)
    resumed = publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                            config=config, receipt=receipt, client=store)
    assert resumed["json"]["pointer_changed"] is False
    assert store.writes == writes


def test_newer_pointer_prevents_any_paper_upload(tmp_path):
    config = replace(CONFIG, public_base_url="https://papers.example")
    run = tmp_path / "run"
    run.mkdir()
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    generation(run, release="candidate")
    papers = make_candidate(tmp_path / "papers", {"research/articles/a/index.html": "A"})
    store.objects[f"{PREFIX}/current.json"] = b'{"newer":true}'
    with pytest.raises(R2S3Error, match="baseline changed"):
        publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                     config=config, receipt=tmp_path / "receipt", client=store)
    assert not store.writes


def test_managed_candidate_freezes_and_resumes_without_a_second_upload(tmp_path):
    from scripts.publish_research_candidate import publish
    config = replace(CONFIG, public_base_url='https://papers.example')
    run, snapshot, stage = tmp_path / 'run', tmp_path / 'snapshot', tmp_path / 'stage'
    run.mkdir()
    public = snapshot / 'public/research'
    public.mkdir(parents=True)
    (public / 'index.json').write_text('{"articles":[]}')
    (public / 'catalogue.json').write_text('{}')
    page = stage / 'research/articles/a/index.html'
    page.parent.mkdir(parents=True)
    page.write_text('<html>A</html>')
    Path(f'{stage}.inputs.json').write_text(json.dumps({'schema': 1, 'inputs': {'schema': 1, 'shared': 's',
        'articles': {'a': {'digest': 'd', 'indexable': True}}}, 'reused': [], 'cache_version': 'cache-1'}))
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    args = dict(run=run, snapshot=snapshot, stage=stage, shared=tmp_path / 'shared', receipt=tmp_path / 'receipt',
                release_id='research-test', config=config, client=store)
    first = publish(**args)
    assert first['release_id'] == 'research-test'
    writes = list(store.writes)
    second = publish(**args)
    assert second['json']['pointer_changed'] is False
    assert store.writes == writes
    assert json.loads((run / 'published.json').read_text())['release_id'] == 'research-test'
