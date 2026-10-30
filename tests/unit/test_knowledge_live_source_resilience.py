"""Regressions found while probing the real knowledge source APIs."""
from __future__ import annotations

import json

import pytest
import requests

from src.knowledge.sources import DiseaseKnowledgeFetcher, SourceCandidate


DISEASE = {"disease_id": "D021", "standard_name_en": "Dengue"}


def response(status: int, payload=None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload or {}).encode()
    return result


@pytest.mark.parametrize("item_url", [
    "/2024-DON526",
    "/emergencies/disease-outbreak-news/item/2024-DON526",
    "https://www.who.int/emergencies/disease-outbreak-news/item/2024-DON526",
])
def test_who_don_fetches_canonical_notice_path(monkeypatch, item_url):
    fetcher = DiseaseKnowledgeFetcher(source_hints_path=None)
    monkeypatch.setattr(fetcher, "_query_candidates", lambda _: ["Dengue"])
    monkeypatch.setattr(fetcher, "_get", lambda *args, **kwargs: response(200, {
        "value": [{"Title": "Dengue", "ItemDefaultUrl": item_url}],
    }))
    visited = []

    def crawl(**kwargs):
        visited.append(kwargs["url"])
        return SourceCandidate(
            disease_id="D021", source_type="who_don", source_name="WHO",
            url=kwargs["url"], content_text="Dengue outbreak evidence",
        )

    monkeypatch.setattr(fetcher, "_crawl_html_page", crawl)
    candidates = fetcher._fetch_who_don(DISEASE)
    assert len(candidates) == 1
    assert visited == ["https://www.who.int/emergencies/disease-outbreak-news/item/2024-DON526"]


def crossref_candidate():
    return SourceCandidate(
        disease_id="D021", source_type="web_search", source_name="Crossref",
        url="https://doi.org/10.1000/dengue", title="Dengue review",
    )


@pytest.mark.parametrize("failure", ["tls", "timeout", 202, 403, 503])
def test_failed_search_provider_preserves_crossref_and_stops_alias_requests(monkeypatch, failure):
    fetcher = DiseaseKnowledgeFetcher(source_hints_path=None, max_retries=0, min_interval_seconds=0)
    existing = crossref_candidate()
    monkeypatch.setattr(fetcher, "_fetch_crossref_metadata", lambda **kwargs: [existing])
    monkeypatch.setattr(fetcher, "_web_search_queries", lambda *_: ["first", "second", "third"])
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        if failure == "tls":
            raise requests.exceptions.SSLError("certificate verification failed")
        if failure == "timeout":
            raise requests.exceptions.ReadTimeout("source timeout")
        return response(failure)

    monkeypatch.setattr(fetcher.session, "get", get)
    assert fetcher._fetch_web_search(DISEASE) == [existing]
    assert len(calls) == 1
    assert all(call.get("verify") is not False for call in calls)
    assert fetcher._adapter_request_state.error_count == 1


def test_successful_empty_search_still_tries_other_aliases(monkeypatch):
    fetcher = DiseaseKnowledgeFetcher(source_hints_path=None, max_retries=0, min_interval_seconds=0)
    monkeypatch.setattr(fetcher, "_fetch_crossref_metadata", lambda **kwargs: [])
    monkeypatch.setattr(fetcher, "_web_search_queries", lambda *_: ["first", "second"])
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"]["q"])
        return response(200)

    monkeypatch.setattr(fetcher.session, "get", get)
    assert fetcher._fetch_web_search(DISEASE) == []
    assert calls == ["first", "second"]
    assert getattr(fetcher._adapter_request_state, "error_count", 0) == 0


def test_failed_crossref_still_reaches_independent_search_provider(monkeypatch):
    fetcher = DiseaseKnowledgeFetcher(source_hints_path=None, max_retries=0, min_interval_seconds=0)
    monkeypatch.setattr(fetcher, "_query_candidates", lambda _: ["dengue", "dengue fever"])
    monkeypatch.setattr(fetcher, "_web_search_queries", lambda *_: ["dengue site:who.int"])
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if "crossref.org" in url:
            raise requests.exceptions.ReadTimeout("Crossref unavailable")
        result = response(200)
        result._content = b'<a class="result__a" href="https://www.who.int/dengue">Dengue evidence</a>'
        return result
    monkeypatch.setattr(fetcher.session, "get", get)
    monkeypatch.setattr(fetcher, "_crawl_html_page", lambda **kwargs: None)
    candidates = fetcher._fetch_web_search(DISEASE)
    assert len(candidates) == 1
    assert "who.int" in candidates[0].url
    assert sum("crossref.org" in url for url in calls) == 1
    assert sum("duckduckgo.com" in url for url in calls) == 1
