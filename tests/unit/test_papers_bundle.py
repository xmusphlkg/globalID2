from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.data_release.papers_bundle import (
    assert_pages_budget,
    evaluate_pages_budget,
    is_papers_relative_path,
    rewrite_papers_html,
    split_papers_bundle,
)


def test_papers_path_classification() -> None:
    assert is_papers_relative_path("research/articles/slug/index.html")
    assert is_papers_relative_path("zh/research/articles/slug/index.html")
    assert is_papers_relative_path("fr/research/articles/slug/index.html")
    assert is_papers_relative_path("research/cards/slug.png")
    assert is_papers_relative_path("site-data/research/index.json")
    assert not is_papers_relative_path("research/index.html")
    assert not is_papers_relative_path("site-data/countries/us.json")
    assert not is_papers_relative_path("_astro/BaseLayout.css")


def test_rewrite_keeps_papers_paths_and_absolutizes_shared_assets() -> None:
    html = """
    <html>
      <link rel="stylesheet" href="/_astro/BaseLayout.css" />
      <link rel="canonical" href="https://globalinfectiousdisease.com/research/articles/slug/" />
      <meta property="og:image" content="https://globalinfectiousdisease.com/research/cards/slug.png" />
      <a href="/research/">Radar</a>
      <a href="/research/articles/other/">Related</a>
      <a href="/zh/research/articles/slug/">中文</a>
      <img src="/logo.png" />
    </html>
    """
    rewritten = rewrite_papers_html(html)
    assert 'href="https://globalinfectiousdisease.com/_astro/BaseLayout.css"' in rewritten
    assert 'href="https://papers.globalinfectiousdisease.com/research/articles/slug/"' in rewritten
    assert (
        'content="https://papers.globalinfectiousdisease.com/research/cards/slug.png"'
        in rewritten
    )
    assert 'href="https://globalinfectiousdisease.com/research/"' in rewritten
    assert 'href="/research/articles/other/"' in rewritten
    assert 'href="/zh/research/articles/slug/"' in rewritten
    assert 'src="https://globalinfectiousdisease.com/logo.png"' in rewritten


def test_split_moves_papers_and_enforces_budget(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    papers = tmp_path / "papers"
    (dist / "research" / "articles" / "slug").mkdir(parents=True)
    (dist / "research" / "cards").mkdir(parents=True)
    (dist / "site-data" / "research").mkdir(parents=True)
    (dist / "research").mkdir(exist_ok=True)
    (dist / "_astro").mkdir()
    (dist / "research" / "articles" / "slug" / "index.html").write_text(
        '<a href="/_astro/app.js"></a><a href="/research/articles/slug/"></a>',
        encoding="utf-8",
    )
    (dist / "research" / "cards" / "slug.png").write_bytes(b"png")
    (dist / "site-data" / "research" / "index.json").write_text("{}", encoding="utf-8")
    (dist / "research" / "index.html").write_text("<html>list</html>", encoding="utf-8")
    (dist / "_astro" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "_redirects").write_text("/privacy  /terms/  301\n", encoding="utf-8")
    (dist / "_headers").write_text("/release.json\n  Cache-Control: no-store\n", encoding="utf-8")

    result = split_papers_bundle(dist, papers_dir=papers)
    assert (papers / "research" / "articles" / "slug" / "index.html").exists()
    assert not (dist / "research" / "articles").exists()
    assert (dist / "research" / "index.html").exists()
    assert (dist / "_astro" / "app.js").exists()
    html = (papers / "research" / "articles" / "slug" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="https://globalinfectiousdisease.com/_astro/app.js"' in html
    assert 'href="/research/articles/slug/"' in html
    redirects = (dist / "_redirects").read_text(encoding="utf-8")
    assert "papers.globalinfectiousdisease.com/research/articles/:splat" in redirects
    headers = (dist / "_headers").read_text(encoding="utf-8")
    assert "Access-Control-Allow-Origin: https://papers.globalinfectiousdisease.com" in headers
    manifest = json.loads((papers / "papers-manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] >= 3
    assert result.main_file_count == evaluate_pages_budget(dist).file_count

    budget = assert_pages_budget(dist, budget=100, warn_at=50)
    assert budget.ok
    with pytest.raises(RuntimeError, match="file budget exceeded"):
        assert_pages_budget(dist, budget=1, warn_at=1)


def test_split_moves_papers_host_files_to_bundle_root(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    papers = tmp_path / "papers"
    (dist / "papers-host").mkdir(parents=True)
    (dist / "papers-host" / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    (dist / "papers-host" / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
    (dist / "index.html").write_text("<html>home</html>", encoding="utf-8")

    split_papers_bundle(dist, papers_dir=papers)

    assert (papers / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\n"
    assert (papers / "sitemap.xml").exists()
    assert not (dist / "papers-host").exists()
    manifest = json.loads((papers / "papers-manifest.json").read_text(encoding="utf-8"))
    assert {item["key"] for item in manifest["files"]} >= {"robots.txt", "sitemap.xml"}
