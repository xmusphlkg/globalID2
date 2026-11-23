"""Offline organization cannot escape to a source, including session calls."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests

from src.data.crawlers.no import NorwayMSISCrawler
from src.services.ingestion.replay import offline_crawler_type


def artifact(tmp_path, raw=b'{"value": 0}'):
    path = Path('data/raw/no/replay/response.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw),
        source_url='https://fixture.test/query?year=2026&scope=national', kind='source_json',
        metadata_={'media_type': 'application/json'})


def test_offline_transport_replays_get_and_session_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = artifact(tmp_path)
    monkeypatch.setattr(requests.Session, 'get', lambda *a, **k: pytest.fail('HTTP escaped replay'))
    crawler = offline_crawler_type(NorwayMSISCrawler, [saved])(delay=0)
    try:
        expected = {'value': 0}
        assert crawler.get('https://fixture.test/query', params={'scope':'national','year':2026}).json() == expected
        assert crawler.session.get('https://fixture.test/query?scope=national&year=2026').json() == expected
        assert crawler.session.request('GET', 'https://fixture.test/query?year=2026&scope=national').json() == expected
        assert b''.join(crawler.get(saved.source_url).iter_content(2)) == b'{"value": 0}'
        with pytest.raises(RuntimeError, match='No captured'):
            crawler.get('https://fixture.test/uncaptured')
        with pytest.raises(RuntimeError):
            crawler.session.send(requests.Request('GET', 'https://fixture.test/uncaptured').prepare())
    finally:
        crawler.session.close()


def test_offline_transport_rejects_changed_artifact_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = artifact(tmp_path)
    Path(saved.relative_path).write_bytes(b'{"value": 9}')
    crawler = offline_crawler_type(NorwayMSISCrawler, [saved])(delay=0)
    try:
        with pytest.raises(ValueError, match='digest|integrity|sha256'):
            crawler.get(saved.source_url)
    finally:
        crawler.session.close()


def test_redirect_alias_preserves_final_url_and_http_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = artifact(tmp_path)
    saved.metadata_.update(request_url='https://old.test/data?year=2026', status_code=404)
    crawler = offline_crawler_type(NorwayMSISCrawler, [saved])(delay=0)
    try:
        with pytest.raises(requests.HTTPError) as caught:
            crawler.get('https://old.test/data', params={'year': 2026, 'api_key': 'secret'})
        assert caught.value.response.status_code == 404
        assert caught.value.response.url == saved.source_url
        assert 'secret' not in str(caught.value)
        with pytest.raises(RuntimeError, match='No captured') as missing:
            crawler.get('https://old.test/data?year=2025&token=secret')
        assert 'secret' not in str(missing.value)
    finally:
        crawler.session.close()


def test_readonly_json_post_replay_matches_body_and_method(tmp_path, monkeypatch):
    from src.services.ingestion.capture import json_request_digest
    monkeypatch.chdir(tmp_path)
    saved = artifact(tmp_path)
    body = {'months': ['2026-10'], 'geography': 'national'}
    saved.metadata_.update(request_method='POST', request_body_sha256=json_request_digest(body))
    crawler = offline_crawler_type(NorwayMSISCrawler, [saved])(delay=0)
    try:
        assert crawler.post(saved.source_url, json=body).json() == {'value': 0}
        assert crawler.session.request('POST', saved.source_url, json=dict(reversed(list(body.items())))).json() == {'value': 0}
        with pytest.raises(RuntimeError, match='No captured'):
            crawler.post(saved.source_url, json={**body, 'months': ['2026-09']})
        with pytest.raises(RuntimeError, match='No captured'):
            crawler.get(saved.source_url)
        with pytest.raises(RuntimeError, match='captured JSON'):
            crawler.post(saved.source_url, data='uncaptured')
    finally:
        crawler.session.close()
