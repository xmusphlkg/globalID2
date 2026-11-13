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
    assert not (run / 'authority-committed.json').exists()
    store.failure = None
    result = publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                          config=config, receipt=receipt, client=store)
    pointer = json.loads(store.objects[f"{PREFIX}/current.json"])
    assert pointer["papers"]["papers_generation_sha256"] == papers.generation
    assert result["release_id"] == pointer["release_id"] == "candidate"
    assert receipt.exists()
    authority = (run / 'authority-committed.json').read_bytes()
    assert json.loads(authority)['authoritative_committed'] is True
    assert store.writes[-1][0] == f"{PREFIX}/current.json"
    writes = list(store.writes)
    resumed = publish_literature_candidate(run=run, objects_dir=run / "objects", papers=papers,
                                            config=config, receipt=receipt, client=store)
    assert resumed["json"]["pointer_changed"] is False
    assert store.writes == writes
    assert (run / 'authority-committed.json').read_bytes() == authority


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


def test_verified_pre_staged_templates_are_not_reuploaded_before_pointer(tmp_path):
    import hashlib
    from src.services.data_release.papers_content import publish_papers_content, verified_existing_objects
    from tests.unit.test_papers_content import make_template_candidate
    config = replace(CONFIG, public_base_url='https://papers.example')
    run = tmp_path / 'run'
    run.mkdir()
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    generation(run, release='candidate')
    papers = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    publish_papers_content(papers, client=store)
    inventory = {key: {'bytes': len(body), 'etag': hashlib.md5(body, usedforsecurity=False).hexdigest()}
                 for key, body in store.objects.items()}
    known = verified_existing_objects(papers, inventory)
    store.writes.clear()
    result = publish_literature_candidate(run=run, objects_dir=run / 'objects', papers=papers,
        config=config, receipt=tmp_path / 'receipt', client=store, known_staged_objects=known)
    assert result['papers']['uploaded_objects'] == 0
    assert not any(key in papers.object_keys for key, _ in store.writes)
    assert store.writes[-1][0] == f'{PREFIX}/current.json'


def test_managed_candidate_freezes_and_resumes_without_a_second_upload(tmp_path, monkeypatch):
    from scripts.publish_research_candidate import publish
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    config = replace(CONFIG, public_base_url='https://papers.example')
    run, snapshot, stage = tmp_path / 'run', tmp_path / 'snapshot', tmp_path / 'stage'
    run.mkdir()
    public = snapshot / 'public/research'
    public.mkdir(parents=True)
    (public / 'index.json').write_text('{"articles":[]}')
    (public / 'catalogue.json').write_text('{}')
    articles = snapshot / 'build/research/articles'
    articles.mkdir(parents=True)
    (articles / 'paper-a.json').write_text(json.dumps({
        'slug': 'paper-a', 'article_id': 'lit-paper-a', 'title': 'Paper paper-a',
        'editorial_status': 'published',
        'summary': {'en': {'main_findings': 'Public finding'}, 'zh': {'main_findings': '中文发现'},
                    'fr': {'main_findings': 'Résultats'}},
    }))
    from tests.unit.test_prepare_papers_templates import page as paper_html
    for prefix in ('', 'zh', 'fr'):
        page = stage / prefix / 'research/articles/paper-a/index.html'
        page.parent.mkdir(parents=True)
        page.write_text(paper_html('paper-a', 'v1'))
    Path(f'{stage}.inputs.json').write_text(json.dumps({'schema': 1, 'inputs': {'schema': 1, 'shared': 's',
        'articles': {'paper-a': {'digest': 'd', 'indexable': True}}}, 'reused': [], 'cache_version': 'cache-1'}))
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    args = dict(run=run, snapshot=snapshot, stage=stage, shared=tmp_path / 'shared', receipt=tmp_path / 'receipt',
                release_id='research-test', config=config, client=store,
                projection_verifier=lambda _candidate: {'status': 'verified'})
    first = publish(**args)
    assert first['release_id'] == 'research-test'
    writes = list(store.writes)
    second = publish(**args)
    assert second['json']['pointer_changed'] is False
    assert store.writes == writes
    assert json.loads((run / 'published.json').read_text())['release_id'] == 'research-test'
    pointer = json.loads(store.objects[f'{PREFIX}/current.json'])
    catalogue = json.loads(store.objects[pointer['papers']['papers_manifest_url'].removeprefix('https://papers.example/')])
    entries = [entry for shard in catalogue['shards'].values()
               for entry in json.loads(store.objects[shard['key']])['items']]
    assert entries and all(entry['paper_template']['schema'] == 3 and entry['integrity'] == 'independent-v3' for entry in entries)
    assert not any(key.startswith('paper-template-v2/') for key in store.objects)


def test_legacy_v2_marker_cannot_be_published(tmp_path, monkeypatch):
    from scripts.publish_research_candidate import publish
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    config = replace(CONFIG, public_base_url='https://papers.example')
    run, snapshot, stage = tmp_path / 'run', tmp_path / 'snapshot', tmp_path / 'stage'
    run.mkdir()
    (run / 'papers-prepared.json').write_text(json.dumps({'schema': 1, 'release_id': 'legacy-run'}))
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=Store())
    with pytest.raises(ValueError, match='Legacy v2'):
        publish(run=run, snapshot=snapshot, stage=stage, shared=tmp_path / 'shared', receipt=tmp_path / 'receipt',
                release_id='legacy-run', config=config, client=Store())
