from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.data_release import papers_bundle
from src.services.data_release.papers_bundle import (
    assert_pages_budget,
    evaluate_pages_budget,
    is_papers_relative_path,
    rewrite_papers_html,
    split_papers_bundle,
)


@pytest.fixture(autouse=True)
def enable_cards_for_bundle_scenarios(monkeypatch):
    monkeypatch.setattr(papers_bundle, "research_social_cards_enabled", lambda: True)


def test_papers_path_classification() -> None:
    assert is_papers_relative_path("research/articles/slug/index.html")
    assert is_papers_relative_path("zh/research/articles/slug/index.html")
    assert is_papers_relative_path("fr/research/articles/slug/index.html")
    assert is_papers_relative_path("research/cards/slug.png")
    assert is_papers_relative_path("site-data/research/index.json")
    assert is_papers_relative_path("zh/research/diseases/influenza/index.html")
    assert is_papers_relative_path("research/catalogue/diseases/d038.json")
    assert is_papers_relative_path("research/catalogue/diseases/d038/0-0123456789abcdef.json")
    assert not is_papers_relative_path("research/catalogue/entities.json")
    assert not is_papers_relative_path("research/index.html")
    assert not is_papers_relative_path("site-data/countries/us.json")
    assert not is_papers_relative_path("_astro/BaseLayout.css")


def test_build_only_research_indexes_are_excluded_from_both_bundles(tmp_path):
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    obsolete = ["site-data/research/index.json", "site-data/research/catalogue.json",
                "site-data/research/shards/articles-old.json",
                "site-data/research/diseases/d038.json"]
    for root in (dist, papers):
        for name in obsolete:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
    collection = dist / "research/catalogue/diseases/d038.json"
    collection.parent.mkdir(parents=True, exist_ok=True)
    collection.write_text('{"articles":[]}')

    split_papers_bundle(dist, papers_dir=papers)

    for name in obsolete:
        assert not (dist / name).exists()
        assert not (papers / name).exists()
    assert (papers / "research/catalogue/diseases/d038.json").exists()
    manifest = json.loads((papers / "papers-manifest.json").read_text())
    assert not set(obsolete) & {item["path"] for item in manifest["files"]}


def test_disease_library_is_removed_from_pages_and_uses_same_build_styles_on_papers(tmp_path):
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    files = {
        "research/index.html": "<main>Research radar</main>",
        "research/catalogue/entities.json": '{"entities":[]}',
        "research/catalogue/diseases/d038.json": '{"shards":["/research/catalogue/diseases/d038/0-0123456789abcdef.json"]}',
        "research/catalogue/diseases/d038/0-0123456789abcdef.json": '{"articles":[]}',
        "_astro/base.css": '@font-face{src:url("./gids.woff2")} body{color:#102a43}',
        "_astro/gids.woff2": "shared font",
        "_astro/library.js": 'import("./loading.js")',
        "_astro/loading.js": "export default 1",
    }
    for locale in ["", "zh/", "fr/"]:
        files[f"{locale}research/diseases/influenza/index.html"] = (
            '<link rel="stylesheet" href="/_astro/base.css">'
            '<link rel="canonical" href="https://globalinfectiousdisease.com/' + locale + 'research/diseases/influenza/">'
            '<astro-island component-url="/_astro/library.js"></astro-island>'
            '<a href="/zh/diseases/influenza/">Surveillance</a>'
            '<a href="/research/catalogue/diseases/d038.json">Catalogue</a>'
        )
    for name, body in files.items():
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    split_papers_bundle(dist, papers_dir=papers)
    assert not (dist / "research/diseases").exists()
    assert not (dist / "research/catalogue/diseases").exists()
    assert (dist / "research/index.html").exists()
    assert (dist / "research/catalogue/entities.json").exists()
    for name in ["base.css", "gids.woff2", "library.js", "loading.js"]:
        assert (papers / "_astro" / name).read_bytes() == (dist / "_astro" / name).read_bytes()
    html = (papers / "zh/research/diseases/influenza/index.html").read_text()
    assert 'href="/_astro/base.css"' in html
    assert 'href="https://papers.globalinfectiousdisease.com/zh/research/diseases/influenza/"' in html
    assert 'href="https://globalinfectiousdisease.com/zh/diseases/influenza/"' in html
    assert 'href="/research/catalogue/diseases/d038.json"' in html
    redirects = (dist / "_redirects").read_text()
    assert "/zh/research/diseases/*  https://papers.globalinfectiousdisease.com/zh/research/diseases/:splat  301" in redirects


def test_disabled_cards_are_removed_from_legacy_build_and_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(papers_bundle, "research_social_cards_enabled", lambda: False)
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    for root in (dist, papers):
        (root / "research/cards").mkdir(parents=True)
        (root / "research/cards/old.png").write_bytes(b"old-card")
    (dist / "research/articles/paper").mkdir(parents=True)
    (dist / "research/articles/paper/index.html").write_text('<meta property="og:image" content="https://globalinfectiousdisease.com/og-default.png">')
    split_papers_bundle(dist, papers_dir=papers)
    assert not (dist / "research/cards/old.png").exists()
    assert not (papers / "research/cards/old.png").exists()
    manifest = json.loads((papers / "papers-manifest.json").read_text())
    assert all(not item["key"].startswith("research/cards/") for item in manifest["files"])
    assert (papers / "research/articles/paper/index.html").exists()


def test_papers_copy_transitive_assets_and_retain_previous_generation(tmp_path: Path) -> None:
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    page = dist / "research/articles/example/index.html"
    page.parent.mkdir(parents=True)
    assets = dist / "_astro"
    assets.mkdir()
    (assets / "a.css").write_text('@font-face { src: url("./font.woff2"); }')
    (assets / "font.woff2").write_bytes(b"font")
    (assets / "island.js").write_text('import("./lazy.js")')
    (assets / "lazy.js").write_text('export default 1')
    page.write_text('<link href="/_astro/a.css"><astro-island component-url="/_astro/island.js"></astro-island>')
    split_papers_bundle(dist, papers_dir=papers)
    for name in ['a.css', 'font.woff2', 'island.js', 'lazy.js']:
        assert (papers / "_astro" / name).read_bytes() == (assets / name).read_bytes()
    page.parent.mkdir(parents=True, exist_ok=True)
    (assets / "b.css").write_text('body { color: blue; }')
    page.write_text('<link href="/_astro/b.css">')
    split_papers_bundle(dist, papers_dir=papers)
    manifest = json.loads((papers / 'papers-manifest.json').read_text())
    assert manifest['local_build_assets'] is True
    assert len(manifest['asset_generations']) == 2
    assert (papers / '_astro/a.css').exists()
    assert (papers / '_astro/b.css').exists()


def test_missing_papers_build_asset_stops_split(tmp_path: Path) -> None:
    dist = tmp_path / 'dist'
    page = dist / 'research/articles/example/index.html'
    page.parent.mkdir(parents=True)
    page.write_text('<link href="/_astro/missing.css">')
    with pytest.raises(FileNotFoundError, match='Bundle asset missing'):
        split_papers_bundle(dist, papers_dir=tmp_path / 'papers')


def test_rewrite_keeps_papers_and_build_assets_local() -> None:
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
    assert 'href="/_astro/BaseLayout.css"' in rewritten
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
    assert 'href="/_astro/app.js"' in html
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


@pytest.mark.parametrize("change", ["none", "source", "destination", "origin"])
def test_split_reuses_verified_html_and_removes_obsolete_files(tmp_path, monkeypatch, change):
    from src.services.data_release import papers_bundle

    dist, papers = tmp_path / "dist", tmp_path / "papers"
    relative = Path("research/articles/example/index.html")
    original = '<a href="/_astro/app.js">paper</a>'
    (dist / '_astro').mkdir(parents=True)
    (dist / '_astro/app.js').write_text('console.log(1)')

    def build(content):
        (dist / relative).parent.mkdir(parents=True, exist_ok=True)
        (dist / relative).write_text(content)

    build(original)
    split_papers_bundle(dist, papers_dir=papers)
    modified = (papers / relative).stat().st_mtime_ns
    (papers / "obsolete.json").write_text("old")
    if change == "destination":
        (papers / relative).write_text("damaged")
    build(original + ("changed" if change == "source" else ""))
    calls = []
    original_rewrite = papers_bundle.rewrite_papers_html

    def rewrite(*args, **kwargs):
        calls.append(args)
        return original_rewrite(*args, **kwargs)

    monkeypatch.setattr(papers_bundle, "rewrite_papers_html", rewrite)
    origin = "https://new.example" if change == "origin" else papers_bundle.DEFAULT_MAIN_SITE_ORIGIN
    split_papers_bundle(dist, papers_dir=papers, main_origin=origin)
    assert len(calls) == (0 if change == "none" else 1)
    assert not (papers / "obsolete.json").exists()
    assert not (dist / relative).exists()
    manifest = json.loads((papers / "papers-manifest.json").read_text())
    assert {item["path"] for item in manifest["files"]} == {relative.as_posix(), "_astro/app.js"}
    assert 'href="/_astro/app.js"' in (papers / relative).read_text()
    if change == "none":
        assert (papers / relative).stat().st_mtime_ns == modified


@pytest.mark.parametrize("kind", ["disease"])
def test_library_shards_survive_cached_indexes_for_48_hours_without_extending_retirement(tmp_path, monkeypatch, kind):
    clock = [papers_bundle.time.time()]
    monkeypatch.setattr(papers_bundle.time, "time", lambda: clock[0])
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    old = "research/catalogue/diseases/d038/0-" + "a" * 16 + ".json"
    new = "research/catalogue/diseases/d038/0-" + "b" * 16 + ".json"
    if kind == "study":
        old, new = [f"research/catalogue/studies/mathematical-modelling-{letter * 16}.json" for letter in "ab"]

    def build(shard):
        target = dist / shard
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"articles": [shard]}))
        if kind == "disease":
            (dist / "research/catalogue/diseases/d038.json").write_text(json.dumps({"shard": shard}))
        split_papers_bundle(dist, papers_dir=papers)
        return json.loads((papers / "papers-manifest.json").read_text())

    build(old)
    clock[0] += 100
    retired_at = clock[0]
    manifest = build(new)
    assert (papers / old).is_file()
    assert manifest["library_shard_retirements"] == {old: retired_at}
    clock[0] += 3600
    assert build(new)["library_shard_retirements"] == {old: retired_at}
    clock[0] = retired_at + papers_bundle.LIBRARY_SHARD_RETENTION_SECONDS - 1
    assert old in {item["path"] for item in build(new)["files"]}
    clock[0] += 1
    manifest = build(new)
    assert not (papers / old).exists()
    assert old not in {item["path"] for item in manifest["files"]}
    assert manifest["library_shard_retirements"] == {}


@pytest.mark.parametrize("kind", ["disease"])
def test_library_shard_reactivation_clears_its_retirement_clock(tmp_path, monkeypatch, kind):
    clock = [papers_bundle.time.time()]
    monkeypatch.setattr(papers_bundle.time, "time", lambda: clock[0])
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    shards = [f"research/catalogue/diseases/d038/{i}-{letter * 16}.json" for i, letter in enumerate("ab")]
    if kind == "study":
        shards = [f"research/catalogue/studies/review-{letter * 16}.json" for letter in "ab"]

    def build(relative):
        target = dist / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"articles": []}')
        split_papers_bundle(dist, papers_dir=papers)
        return json.loads((papers / "papers-manifest.json").read_text())

    build(shards[0])
    clock[0] += 60
    assert shards[0] in build(shards[1])["library_shard_retirements"]
    clock[0] += 60
    assert shards[0] not in build(shards[0])["library_shard_retirements"]
    clock[0] += 60
    assert build(shards[1])["library_shard_retirements"][shards[0]] == clock[0]


@pytest.mark.parametrize("kind", ["disease"])
def test_library_retention_ignores_arbitrary_paths_and_rejects_modified_shard_bytes(tmp_path, kind):
    from src.services.data_release.artifact_index import ArtifactIndex
    root = tmp_path / "papers"
    relative = "research/catalogue/diseases/d038/0-" + "a" * 16 + ".json"
    if kind == "study":
        relative = "research/catalogue/studies/mathematical-modelling-" + "a" * 16 + ".json"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text("original")
    index = ArtifactIndex(root)
    entry = {"path": relative, **index.describe(path)}
    escaped = tmp_path / "outside.json"
    escaped.write_text("outside")
    expected = set()
    retained = papers_bundle.retain_library_shards(root, expected, {"files": [
        {"path": "../outside.json"},
        {"path": "research/catalogue/diseases/../outside.json"},
        {"path": "research/catalogue/diseases/d038/not-a-hash.json"},
        {"path": "research/catalogue/diseases/d038.json"},
        {"path": "research/catalogue/studies/../outside.json"},
        {"path": "research/catalogue/studies/mathematical-modelling.json"},
        {"path": "research/catalogue/studies/review-not-a-hash.json"},
    ]}, index)
    assert retained == {}
    assert expected == set()
    assert escaped.read_text() == "outside"
    path.write_text("modified")
    with pytest.raises(ValueError, match="Retained literature shard checksum mismatch"):
        papers_bundle.retain_library_shards(root, set(), {"files": [entry]}, index)


def test_main_origin_research_keeps_article_links_and_drops_papers_redirects(tmp_path):
    dist, papers = tmp_path / "dist", tmp_path / "papers"
    weekly = dist / "research/weekly/index.html"
    article = dist / "research/articles/example/index.html"
    weekly.parent.mkdir(parents=True)
    article.parent.mkdir(parents=True)
    weekly.write_text('<a href="/research/articles/example/">paper</a>')
    article.write_text("<p>article</p>")
    (dist / "_redirects").write_text(
        "/research/articles/*  https://papers.globalinfectiousdisease.com/research/articles/:splat  301\n"
        "/zh/research/diseases/*  https://papers.globalinfectiousdisease.com/zh/research/diseases/:splat  301\n"
        "/privacy/  /terms/  301\n"
    )
    split_papers_bundle(dist, papers_dir=papers, research_on_main=True)
    redirects = (dist / "_redirects").read_text()
    assert "papers.globalinfectiousdisease.com" not in redirects
    assert "/privacy/  /terms/  301" in redirects
    assert weekly.read_text() == '<a href="/research/articles/example/">paper</a>'
    assert not article.exists()
    assert (papers / "research/articles/example/index.html").is_file()


def test_obsolete_study_collections_are_not_published(tmp_path):
    dist, papers = tmp_path / 'dist', tmp_path / 'papers'
    relative = 'research/catalogue/studies/mathematical-modelling-0123456789abcdef.json'
    for root in (dist, papers):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"articles":[]}')
    page = dist / 'research/articles/model/index.html'
    page.parent.mkdir(parents=True)
    page.write_text('<html>model</html>')
    split_papers_bundle(dist, papers_dir=papers)
    assert not (dist / relative).exists()
    assert not (papers / relative).exists()
    manifest = json.loads((papers / 'papers-manifest.json').read_text())
    assert all(item['path'] != relative for item in manifest['files'])
