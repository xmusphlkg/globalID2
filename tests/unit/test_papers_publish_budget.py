"""Every Papers publication entry point passes its storage budget to the client."""
from dataclasses import replace

import pytest

from src.services.data_release.literature_publish import PREFIX
from src.services.data_release.surveillance_publish import capture_surveillance_baseline
from tests.unit.test_papers_content import make_candidate
from tests.unit.test_site_research_publish import sources
from tests.unit.test_surveillance_publish import CONFIG, Store, generation


@pytest.mark.parametrize('budget', [None, 123_456])
def test_direct_literature_publication_constructs_budgeted_client(tmp_path, monkeypatch, budget):
    from src.services.data_release import literature_publish as publisher

    config = replace(CONFIG, public_base_url='https://papers.example')
    run = tmp_path / 'run'
    run.mkdir()
    store = Store()
    capture_surveillance_baseline(run, config, prefix=PREFIX, client=store)
    generation(run, release='candidate')
    papers = make_candidate(tmp_path / 'papers', {'article.html': 'Article'})
    constructed, closed = [], []

    def client_factory(actual_config, *, max_total_bytes):
        constructed.append((actual_config, max_total_bytes))
        return store

    store.close = lambda: closed.append(True)
    monkeypatch.setattr(publisher, 'R2S3Client', client_factory)
    publisher.publish_literature_candidate(run=run, objects_dir=run / 'objects', papers=papers,
        config=config, receipt=tmp_path / 'receipt.json', max_total_bytes=budget)
    assert constructed == [(config, budget)]
    assert closed == [True]


def test_site_publication_constructs_budgeted_client_without_inventory(tmp_path, monkeypatch):
    from src.services.data_release import site_research_publish as publisher

    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    constructed = []

    def client_factory(config, *, max_total_bytes):
        constructed.append((config, max_total_bytes))
        return store

    store.close = lambda: None
    monkeypatch.setattr(publisher, 'R2S3Client', client_factory)
    result = publisher.publish_site_research(**args, max_total_bytes=345_678)
    assert result['release_id'] == args['release_id']
    assert constructed == [(args['config'], 345_678)]


@pytest.mark.parametrize('arguments,expected', [([], 8_000_000_000), (['--max-total-bytes', '123456'], 123_456)])
def test_cli_passes_budget_without_optional_inventory(tmp_path, monkeypatch, arguments, expected):
    from scripts import publish_papers_r2 as command
    from src.services.data_release import site_research_publish as publisher

    calls = []
    monkeypatch.delenv('R2_PAPERS_MAX_TOTAL_BYTES', raising=False)
    monkeypatch.setattr(command, '_config_from_env', lambda: CONFIG)
    monkeypatch.setattr(publisher, 'publish_site_research', lambda **kwargs: calls.append(kwargs) or {'ok': True})
    assert command.main(['--release-id', 'candidate', '--run', str(tmp_path / 'run'), *arguments]) == 0
    assert calls[0]['inventory'] is None
    assert calls[0]['max_total_bytes'] == expected


def test_cli_rejects_disabled_budget_before_loading_credentials(monkeypatch):
    from scripts import publish_papers_r2 as command

    monkeypatch.setattr(command, '_config_from_env', lambda: pytest.fail('Credentials must not be loaded'))
    with pytest.raises(SystemExit) as error:
        command.main(['--release-id', 'candidate', '--max-total-bytes', '0'])
    assert error.value.code == 2
