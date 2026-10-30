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
