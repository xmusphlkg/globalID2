from __future__ import annotations

from types import SimpleNamespace

from src.services.agent_workflow import search
from src.services.agent_workflow_service import agent_workflow_service


class _Response:
    def __init__(self, text: str, url: str = "https://example.test/final") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_search_query_module_matches_service_compatibility_method():
    arguments = {
        "queries": ["influenza surveillance", "influenza surveillance"],
        "prompt": "Assess influenza surveillance trends",
        "max_rounds": 3,
    }

    assert agent_workflow_service._build_search_queries(**arguments) == search.build_search_queries(**arguments)


def test_duckduckgo_adapter_preserves_request_and_result_shape():
    response = _Response(
        """
        <div class="result">
          <a class="result__a" href="https://www.who.int/example">Search title</a>
          <div class="result__snippet">Search snippet</div>
        </div>
        """
    )
    session = _Session(response)

    results = search.duckduckgo_search(
        session,
        "influenza",
        1,
        page_fetcher=lambda url: ("Fetched title", "Fetched snippet", f"{url}/resolved"),
    )

    assert session.calls == [
        (search.WEB_SEARCH_ENDPOINT, {"params": {"q": "influenza"}, "timeout": 20})
    ]
    assert [item.to_dict() for item in results] == [
        {
            "evidence_type": "web",
            "source_type": "who",
            "source_name": "www.who.int",
            "title": "Fetched title",
            "url": "https://www.who.int/example",
            "resolved_url": "https://www.who.int/example/resolved",
            "content_snippet": "Fetched snippet",
            "content_hash": results[0].content_hash,
            "confidence": 0.7,
            "weight": 1.0,
            "metadata": {"query": "influenza"},
        }
    ]


def test_web_page_adapter_preserves_redirect_and_extracts_content():
    session = _Session(
        _Response(
            "<html><head><title> Example title </title></head>"
            "<body><main><h1>Heading</h1><p>Evidence paragraph</p></main></body></html>",
            url="https://example.test/redirected",
        )
    )

    result = search.fetch_web_page(session, "https://example.test/source")

    assert session.calls == [
        ("https://example.test/source", {"timeout": 20, "allow_redirects": True})
    ]
    assert result == (
        "Example title",
        "Heading Evidence paragraph Heading Evidence paragraph",
        "https://example.test/redirected",
    )


def test_database_row_formatters_match_service_wrappers():
    row = SimpleNamespace(
        title="Evidence row",
        to_dict=lambda: {"zeta": ["one", "two"], "alpha": "first", "empty": None},
    )

    assert agent_workflow_service._row_title(row, "reports") == search.row_title(row, "reports")
    assert agent_workflow_service._row_to_snippet(row) == search.row_to_snippet(row)
    assert search.row_to_snippet(row) == 'alpha=first | zeta=["one", "two"]'


def test_source_classification_matches_service_wrappers():
    urls = [
        "https://www.who.int/a",
        "https://www.cdc.gov/a",
        "https://ncbi.nlm.nih.gov/a",
        "https://en.wikipedia.org/a",
        "not-a-url",
    ]

    for url in urls:
        assert agent_workflow_service._guess_source_type(url) == search.guess_source_type(url)
        assert agent_workflow_service._guess_source_name(url) == search.guess_source_name(url)
