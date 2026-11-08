from __future__ import annotations

from src.services.data_release.papers_bundle import (
    DEFAULT_PAPERS_ORIGIN,
    PAPERS_CORS_PATHS,
    ensure_papers_cors_headers,
)

MARKER = f"Access-Control-Allow-Origin: {DEFAULT_PAPERS_ORIGIN}"


def _paths_with_cors(text: str) -> set[str]:
    covered: set[str] = set()
    current = None
    for line in text.splitlines():
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
            current = line.strip()
        elif current and line.strip() == MARKER:
            covered.add(current)
    return covered


def test_empty_headers_get_every_papers_path() -> None:
    result = ensure_papers_cors_headers("")
    assert _paths_with_cors(result) == {path for path, _ in PAPERS_CORS_PATHS}


def test_partially_covered_source_receives_only_the_missing_paths() -> None:
    source = (
        "/_astro/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        f"  {MARKER}\n"
        "\n"
        "/site-data/*\n"
        "  Cache-Control: public, max-age=300\n"
    )
    result = ensure_papers_cors_headers(source)
    assert _paths_with_cors(result) == {path for path, _ in PAPERS_CORS_PATHS}
    # The existing rule is kept and /_astro/* is not duplicated.
    assert result.startswith(source.rstrip())
    assert result.splitlines().count("/_astro/*") == 1


def test_ensure_papers_cors_headers_is_idempotent() -> None:
    once = ensure_papers_cors_headers("/*.html\n  Cache-Control: public, max-age=0\n")
    assert ensure_papers_cors_headers(once) == once


def test_site_data_and_release_json_allow_cross_origin_fetch() -> None:
    result = ensure_papers_cors_headers("")
    for path in ("/site-data/*", "/release.json"):
        block = result.split(f"\n{path}\n", 1)[1].split("\n\n", 1)[0]
        assert MARKER in block
        assert "Access-Control-Allow-Methods: GET, HEAD, OPTIONS" in block
